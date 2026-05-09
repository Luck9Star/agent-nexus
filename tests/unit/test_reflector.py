"""Tests for Reflector — hybrid rule-based + LLM evaluation system."""

import json
from unittest.mock import MagicMock

from agent_nexus.platform.agency.reflector import (
    EmptyResultRule,
    LLMReflector,
    MaxIterationRule,
    Reflection,
    Reflector,
)

# ---------------------------------------------------------------------------
# Reflection dataclass
# ---------------------------------------------------------------------------


class TestReflection:
    def test_creation(self):
        r = Reflection(sufficient=True, reason="looks good")
        assert r.sufficient is True
        assert r.reason == "looks good"
        assert r.feedback == ""
        assert r.next_queries == []

    def test_with_feedback(self):
        r = Reflection(
            sufficient=False,
            reason="too short",
            feedback="Add more detail",
            next_queries=["search X", "check Y"],
        )
        assert r.feedback == "Add more detail"
        assert len(r.next_queries) == 2


# ---------------------------------------------------------------------------
# EmptyResultRule
# ---------------------------------------------------------------------------


class TestEmptyResultRule:
    def test_empty_result_caught(self):
        """Empty string triggers reflection."""
        rule = EmptyResultRule()
        result = rule.check("do something", "")
        assert result is not None
        assert result.sufficient is False
        assert "空" in result.reason or "短" in result.reason

    def test_short_result_caught(self):
        """Result < 50 chars triggers reflection."""
        rule = EmptyResultRule()
        short = "x" * 49
        result = rule.check("task", short)
        assert result is not None
        assert result.sufficient is False

    def test_whitespace_only_caught(self):
        """Whitespace-only result triggers reflection."""
        rule = EmptyResultRule()
        result = rule.check("task", "   \n\t   ")
        assert result is not None
        assert result.sufficient is False

    def test_adequate_result_passes(self):
        """Result >= 50 chars passes (returns None)."""
        rule = EmptyResultRule()
        adequate = "x" * 50
        result = rule.check("task", adequate)
        assert result is None


# ---------------------------------------------------------------------------
# MaxIterationRule
# ---------------------------------------------------------------------------


class TestMaxIterationRule:
    def test_forces_pass_at_max(self):
        """attempt >= max_iterations → sufficient=True."""
        rule = MaxIterationRule(max_iterations=3)
        result = rule.check("task", "anything", attempt=3)
        assert result is not None
        assert result.sufficient is True

    def test_forces_pass_above_max(self):
        """attempt > max_iterations also forces pass."""
        rule = MaxIterationRule(max_iterations=3)
        result = rule.check("task", "result", attempt=5)
        assert result is not None
        assert result.sufficient is True

    def test_returns_none_below_max(self):
        """attempt < max_iterations → no opinion (None)."""
        rule = MaxIterationRule(max_iterations=3)
        result = rule.check("task", "result", attempt=2)
        assert result is None

    def test_attempt_zero(self):
        """attempt=0 with max=3 → None."""
        rule = MaxIterationRule(max_iterations=3)
        assert rule.check("task", "result", attempt=0) is None


# ---------------------------------------------------------------------------
# LLMReflector
# ---------------------------------------------------------------------------


class TestLLMReflector:
    def test_parse_valid_json(self):
        """Valid JSON response is parsed correctly."""
        client = MagicMock()
        reflector = LLMReflector(client)

        json_text = json.dumps(
            {
                "sufficient": True,
                "reason": "looks good",
                "feedback": "",
                "next_queries": ["q1"],
            }
        )
        result = reflector._parse_reflection(json_text)
        assert result.sufficient is True
        assert result.reason == "looks good"
        assert result.next_queries == ["q1"]

    def test_parse_json_with_markdown_fences(self):
        """JSON wrapped in ```...``` fences is handled."""
        client = MagicMock()
        reflector = LLMReflector(client)

        text = '```json\n{"sufficient": false, "reason": "bad"}\n```'
        result = reflector._parse_reflection(text)
        assert result.sufficient is False
        assert result.reason == "bad"

    def test_parse_invalid_json_defaults_to_reject(self):
        """Invalid JSON → fail closed (don't bypass quality gate)."""
        client = MagicMock()
        reflector = LLMReflector(client)

        result = reflector._parse_reflection("not json at all {{{")
        assert result.sufficient is False
        assert "parse" in result.reason.lower() or "Failed" in result.reason

    def test_evaluate_handles_llm_exception(self):
        """LLM call failure → fail closed (don't bypass quality gate)."""
        client = MagicMock()
        client.call.side_effect = RuntimeError("LLM down")
        reflector = LLMReflector(client)

        result = reflector.evaluate("task", "result", attempt=1)
        assert result.sufficient is False
        assert "failed" in result.reason.lower() or "reject" in result.reason.lower()


# ---------------------------------------------------------------------------
# Reflector (hybrid)
# ---------------------------------------------------------------------------


class TestReflector:
    def test_no_rules_no_llm_defaults_to_pass(self):
        """No rules and no LLM → default pass."""
        reflector = Reflector(rules=[], llm=None)
        result = reflector.evaluate("task", "result", attempt=1)
        assert result.sufficient is True

    def test_rule_layer_returns_first_non_none(self):
        """First rule with opinion wins."""
        rule1 = MagicMock()
        rule1.check.return_value = None  # no opinion
        rule1.__module__ = "test"
        rule1.__qualname__ = "rule1"

        rule2 = MagicMock()
        rule2.check.return_value = Reflection(sufficient=False, reason="rule2 says no")
        rule2.__module__ = "test"
        rule2.__qualname__ = "rule2"

        reflector = Reflector(rules=[rule1, rule2], llm=None)
        result = reflector.evaluate("task", "result")
        assert result.sufficient is False
        assert result.reason == "rule2 says no"

    def test_rule_failure_skips_to_next(self):
        """A rule that raises is skipped, next rule runs."""
        bad_rule = MagicMock()
        bad_rule.check.side_effect = ValueError("broken")
        bad_rule.__module__ = "test"
        bad_rule.__qualname__ = "bad_rule"

        # inspect.signature needs a real function; use a lambda-based approach
        good_rule = MagicMock()
        good_rule.check.return_value = Reflection(sufficient=True, reason="good")
        good_rule.__module__ = "test"
        good_rule.__qualname__ = "good_rule"

        reflector = Reflector(rules=[bad_rule, good_rule], llm=None)
        result = reflector.evaluate("task", "result")
        assert result.sufficient is True
        assert result.reason == "good"

    def test_llm_layer_used_when_rules_have_no_opinion(self):
        """If all rules return None, LLM layer is consulted."""
        llm = MagicMock()
        llm.evaluate.return_value = Reflection(sufficient=True, reason="LLM says ok")

        rule = MagicMock()
        rule.check.return_value = None
        rule.__module__ = "test"
        rule.__qualname__ = "rule"

        reflector = Reflector(rules=[rule], llm=llm)
        result = reflector.evaluate("task", "result", attempt=1)
        assert result.sufficient is True
        assert result.reason == "LLM says ok"
        llm.evaluate.assert_called_once_with("task", "result", 1, 3)


# ---------------------------------------------------------------------------
# Reflector.create_default factory
# ---------------------------------------------------------------------------


class TestReflectorFactory:
    def test_create_default_with_client(self):
        """Factory with client includes EmptyResultRule, MaxIterationRule, LLM."""
        client = MagicMock()
        reflector = Reflector.create_default(client=client, max_iterations=5)
        assert len(reflector._rules) == 2
        assert isinstance(reflector._rules[0], EmptyResultRule)
        assert isinstance(reflector._rules[1], MaxIterationRule)
        assert reflector._llm is not None
        assert reflector.max_iterations == 5

    def test_create_default_without_client(self):
        """Factory without client has rules but no LLM layer."""
        reflector = Reflector.create_default(client=None, max_iterations=3)
        assert len(reflector._rules) == 2
        assert reflector._llm is None
        assert reflector.max_iterations == 3
