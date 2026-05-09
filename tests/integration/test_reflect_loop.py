"""Integration: P4 Reflect Loop with rules and LLM.

Tests the hybrid Reflector (rule-based + LLM) end-to-end, including
cross-stage data flow via ReflectionFeedbackProvider.
"""

import json
from unittest.mock import MagicMock

from agent_nexus.platform.agency.context_provider import ReflectionFeedbackProvider
from agent_nexus.platform.agency.llm_client import LLMResponse
from agent_nexus.platform.agency.reflector import (
    EmptyResultRule,
    LLMReflector,
    MaxIterationRule,
    Reflector,
)

# ---------------------------------------------------------------------------
# 1. Single pass — sufficient result
# ---------------------------------------------------------------------------


class TestReflectLoopSinglePass:
    """Good result -> rules pass -> no retry needed."""

    def test_sufficient_result_no_retry(self):
        reflector = Reflector(rules=[EmptyResultRule()])

        result = reflector.evaluate(
            task="Analyze the codebase architecture",
            result=(
                "The codebase follows a layered architecture with clear separation of concerns. "
                "The platform layer handles orchestration, the agency layer manages LLM interactions, "
                "and the runtime layer provides execution capabilities. Key design patterns include "
                "dependency injection and strategy pattern for LLM provider selection."
            ),
            attempt=1,
        )

        assert result.sufficient is True
        assert result.feedback == ""

    def test_no_rules_no_llm_defaults_pass(self):
        reflector = Reflector()  # no rules, no LLM

        result = reflector.evaluate(
            task="Any task",
            result="Any result that is long enough to pass basic checks",
            attempt=1,
        )

        assert result.sufficient is True


# ---------------------------------------------------------------------------
# 2. Retry on insufficient result
# ---------------------------------------------------------------------------


class TestReflectLoopRetryOnInsufficient:
    """Empty result -> EmptyResultRule triggers -> feedback injected -> retry."""

    def test_empty_result_triggers_retry(self):
        reflector = Reflector(rules=[EmptyResultRule()])

        result = reflector.evaluate(
            task="Write a detailed analysis",
            result="",  # empty
            attempt=1,
        )

        assert result.sufficient is False
        assert result.feedback != ""
        assert "详细" in result.reason or "空" in result.reason

    def test_short_result_triggers_retry(self):
        reflector = Reflector(rules=[EmptyResultRule()])

        result = reflector.evaluate(
            task="Explain the system",
            result="It works",  # too short (< 50 chars)
            attempt=1,
        )

        assert result.sufficient is False

    def test_feedback_flows_to_provider_for_retry(self):
        """Verify feedback from EmptyResultRule flows into ReflectionFeedbackProvider."""
        reflector = Reflector(rules=[EmptyResultRule()])
        feedback_provider = ReflectionFeedbackProvider()

        result = reflector.evaluate(
            task="Research topic X",
            result="",
            attempt=1,
        )

        # Feed reflection feedback into provider for next Executor round
        feedback_provider.update(result.feedback)

        # Next round can read the feedback
        assert feedback_provider.get_context() != ""
        assert (
            "详细" in feedback_provider.get_context() or "内容" in feedback_provider.get_context()
        )

    def test_retry_loop_simulation(self):
        """Simulate a full retry loop: empty -> feedback -> sufficient."""
        reflector = Reflector(rules=[EmptyResultRule()])
        feedback_provider = ReflectionFeedbackProvider()

        # Iteration 1: empty result
        reflection_1 = reflector.evaluate("Analyze code", "", attempt=1)
        assert reflection_1.sufficient is False

        # Feed feedback for retry
        feedback_provider.update(reflection_1.feedback)

        # Iteration 2: improved result (simulated)
        improved_result = (
            "The code analysis reveals several key patterns: "
            + "dependency injection for testability, strategy pattern for algorithm selection, "
            + "and observer pattern for event handling. The module structure follows domain-driven "
            + "design principles with clear bounded contexts."
        )
        reflection_2 = reflector.evaluate("Analyze code", improved_result, attempt=2)
        assert reflection_2.sufficient is True

        # Clear feedback after success
        feedback_provider.update("")


# ---------------------------------------------------------------------------
# 3. Max iterations exit
# ---------------------------------------------------------------------------


class TestReflectLoopMaxIterations:
    """Loop exits after max_iterations regardless of result quality.

    Note: Rules are evaluated in order. MaxIterationRule must be listed
    BEFORE EmptyResultRule so it can force-pass on empty results at the
    iteration limit. This mirrors the order in Reflector.create_default.
    """

    def test_max_iterations_forces_pass(self):
        reflector = Reflector(
            rules=[MaxIterationRule(max_iterations=2), EmptyResultRule()],
        )

        # Attempt 2: MaxIterationRule fires first (rules are ordered), forces pass
        result = reflector.evaluate(
            task="Do something",
            result="",  # still empty, but max reached
            attempt=2,
        )

        assert result.sufficient is True
        assert "最大迭代" in result.reason or "2" in result.reason

    def test_max_iterations_not_triggered_before_limit(self):
        reflector = Reflector(
            rules=[MaxIterationRule(max_iterations=3), EmptyResultRule()],
        )

        # Attempt 1: not yet at max, EmptyResultRule catches the empty result
        result = reflector.evaluate("Task", "", attempt=1)
        assert result.sufficient is False

        # Attempt 2: still not at max
        result = reflector.evaluate("Task", "", attempt=2)
        assert result.sufficient is False

        # Attempt 3: max reached -> MaxIterationRule fires first, forces pass
        result = reflector.evaluate("Task", "", attempt=3)
        assert result.sufficient is True

    def test_reflector_respects_custom_max_iterations(self):
        reflector = Reflector(
            rules=[MaxIterationRule(max_iterations=1), EmptyResultRule()],
            max_iterations=1,
        )

        # Attempt 1: max_iterations=1 means attempt 1 is already at max
        result = reflector.evaluate("Task", "", attempt=1)
        assert result.sufficient is True


# ---------------------------------------------------------------------------
# 4. ReflectionFeedbackProvider integration
# ---------------------------------------------------------------------------


class TestReflectionFeedbackProviderIntegration:
    """Reflector produces feedback -> Provider stores -> available for next round."""

    def test_feedback_provider_lifecycle_in_loop(self):
        feedback_provider = ReflectionFeedbackProvider()
        reflector = Reflector(rules=[EmptyResultRule()])

        results = ["", "Short", "A" * 100]  # empty, too short, sufficient
        attempts = []

        for i, result_text in enumerate(results, start=1):
            reflection = reflector.evaluate("Test task", result_text, attempt=i)
            attempts.append(reflection)

            if reflection.sufficient:
                feedback_provider.update("")  # clear on success
            else:
                feedback_provider.update(reflection.feedback)

        # First two should fail, third should pass
        assert attempts[0].sufficient is False
        assert attempts[1].sufficient is False
        assert attempts[2].sufficient is True

        # Provider should be cleared after success
        assert feedback_provider.get_context() == ""

    def test_feedback_available_for_prompt_construction(self):
        """Verify feedback can be injected into StructuredPrompt for retry."""
        from agent_nexus.platform.agency.token_counter import StructuredPrompt

        feedback_provider = ReflectionFeedbackProvider()
        reflector = Reflector(rules=[EmptyResultRule()])

        # Evaluate empty result -> get feedback
        reflection = reflector.evaluate("Research topic", "", attempt=1)
        feedback_provider.update(reflection.feedback)

        # Build prompt for retry using feedback
        prompt = StructuredPrompt()
        prompt.add("Role", "You are a researcher", priority=1)
        prompt.add_from_providers({"feedback": feedback_provider}, priority=7)

        rendered = prompt.render()
        assert feedback_provider.get_context() in rendered


# ---------------------------------------------------------------------------
# 5. LLM Reflector
# ---------------------------------------------------------------------------


class TestReflectorWithLLM:
    """LLMReflector parses JSON response from LLM correctly."""

    def test_llm_returns_sufficient(self):
        mock_client = MagicMock()
        mock_client.call.return_value = LLMResponse(
            text=json.dumps(
                {
                    "sufficient": True,
                    "reason": "Result covers all required aspects",
                    "feedback": "",
                    "next_queries": [],
                }
            ),
            model="test:model",
            provider="test",
        )

        reflector = LLMReflector(mock_client, max_iterations=3)
        result = reflector.evaluate("Analyze X", "Detailed analysis...", attempt=1)

        assert result.sufficient is True
        assert result.reason == "Result covers all required aspects"
        mock_client.call.assert_called_once()

    def test_llm_returns_insufficient_with_feedback(self):
        mock_client = MagicMock()
        mock_client.call.return_value = LLMResponse(
            text=json.dumps(
                {
                    "sufficient": False,
                    "reason": "Missing depth",
                    "feedback": "Add more technical details about the architecture",
                    "next_queries": ["search for design patterns", "review module structure"],
                }
            ),
            model="test:model",
            provider="test",
        )

        reflector = LLMReflector(mock_client, max_iterations=3)
        result = reflector.evaluate("Analyze X", "Brief mention of topic", attempt=1)

        assert result.sufficient is False
        assert "architecture" in result.feedback
        assert len(result.next_queries) == 2

    def test_llm_handles_markdown_fenced_json(self):
        mock_client = MagicMock()
        mock_client.call.return_value = LLMResponse(
            text='```json\n{"sufficient": true, "reason": "OK", "feedback": "", "next_queries": []}\n```',
            model="test:model",
            provider="test",
        )

        reflector = LLMReflector(mock_client)
        result = reflector.evaluate("Task", "Result", attempt=1)

        assert result.sufficient is True

    def test_hybrid_rules_before_llm(self):
        """Rules evaluate first; LLM only called when rules have no opinion."""
        mock_client = MagicMock()
        mock_client.call.return_value = LLMResponse(
            text=json.dumps({"sufficient": True, "reason": "LLM says OK"}),
            model="test:model",
            provider="test",
        )

        llm = LLMReflector(mock_client)
        reflector = Reflector(rules=[EmptyResultRule()], llm=llm)

        # Empty result -> EmptyResultRule fires, LLM NOT called
        result = reflector.evaluate("Task", "", attempt=1)
        assert result.sufficient is False
        mock_client.call.assert_not_called()

        # Non-empty result -> rule passes, LLM is called
        result = reflector.evaluate("Task", "Sufficient result " * 10, attempt=1)
        mock_client.call.assert_called_once()


# ---------------------------------------------------------------------------
# 6. LLM failure graceful degradation
# ---------------------------------------------------------------------------


class TestReflectorLLMFailureGraceful:
    """LLM exceptions should not crash the pipeline."""

    def test_llm_exception_defaults_to_reject(self):
        mock_client = MagicMock()
        mock_client.call.side_effect = RuntimeError("API unavailable")

        reflector = LLMReflector(mock_client)
        result = reflector.evaluate("Task", "Some result", attempt=1)

        assert result.sufficient is False
        assert "failed" in result.reason.lower() or "reject" in result.reason.lower()

    def test_llm_invalid_json_defaults_to_reject(self):
        mock_client = MagicMock()
        mock_client.call.return_value = LLMResponse(
            text="This is not JSON at all",
            model="test:model",
            provider="test",
        )

        reflector = LLMReflector(mock_client)
        result = reflector.evaluate("Task", "Result", attempt=1)

        assert result.sufficient is False

    def test_llm_missing_fields_defaults(self):
        mock_client = MagicMock()
        mock_client.call.return_value = LLMResponse(
            text=json.dumps({"unexpected_key": "value"}),
            model="test:model",
            provider="test",
        )

        reflector = LLMReflector(mock_client)
        result = reflector.evaluate("Task", "Result", attempt=1)

        # Missing 'sufficient' defaults to False (fail-closed)
        assert result.sufficient is False

    def test_rule_exception_skipped_gracefully(self):
        """Broken rule should be skipped, not crash the reflector."""

        class BrokenRule:
            def check(self, task, result):
                raise ValueError("Rule is broken")

        reflector = Reflector(rules=[BrokenRule()])
        result = reflector.evaluate("Task", "Good result " * 10, attempt=1)

        # Broken rule skipped, no LLM -> default pass
        assert result.sufficient is True


# ---------------------------------------------------------------------------
# 7. Reflector.create_default factory
# ---------------------------------------------------------------------------


class TestCreateDefaultReflector:
    """Factory creates properly configured hybrid reflector."""

    def test_with_mocked_client(self):
        mock_client = MagicMock()
        reflector = Reflector.create_default(client=mock_client, max_iterations=5)

        # Should have EmptyResultRule and MaxIterationRule
        assert len(reflector._rules) == 2
        assert any(isinstance(r, EmptyResultRule) for r in reflector._rules)
        assert any(isinstance(r, MaxIterationRule) for r in reflector._rules)

        # Should have LLM configured
        assert reflector._llm is not None

        # max_iterations should be set
        assert reflector.max_iterations == 5

    def test_without_client(self):
        reflector = Reflector.create_default(max_iterations=3)

        assert len(reflector._rules) == 2
        assert reflector._llm is None  # No LLM without client
        assert reflector.max_iterations == 3

    def test_default_rules_work_with_factory(self):
        reflector = Reflector.create_default(max_iterations=3)

        # Factory uses order: [EmptyResultRule, MaxIterationRule]
        # EmptyResultRule fires first for empty/short results regardless of attempt
        result = reflector.evaluate("Task", "", attempt=1)
        assert result.sufficient is False

        # Non-empty short result (< 50 chars) -> EmptyResultRule fires first
        result = reflector.evaluate("Task", "Short", attempt=1)
        assert result.sufficient is False

        # Good result (> 50 chars) -> no rules fire, no LLM -> default pass
        result = reflector.evaluate("Task", "A" * 100, attempt=1)
        assert result.sufficient is True

        # MaxIterationRule only gets a chance when result is long enough
        # (EmptyResultRule returns None for results >= 50 chars)
        # With a long result at max iteration, MaxIterationRule fires
        result = reflector.evaluate("Task", "B" * 60, attempt=3)
        assert result.sufficient is True
