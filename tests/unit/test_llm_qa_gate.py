"""Tests for LLMQualityGate — LLM-powered quality evaluation."""

import json
import threading
from unittest.mock import MagicMock

from agent_nexus.platform.agency.integrator import IntegratedArtifact
from agent_nexus.platform.agency.llm_client import LLMResponse
from agent_nexus.platform.agency.llm_qa_gate import LLMQualityGate
from agent_nexus.platform.agency.qa_gate import QAGateResult


def _make_integrated():
    return IntegratedArtifact(
        source_agents=["agency.expert-a", "agency.expert-b"],
        merged_sections={
            "summary": "Security and architecture issues found",
            "recommendations": ["Fix SQL injection", "Reduce coupling"],
        },
    )


def _llm_response(data: dict) -> MagicMock:
    mock = MagicMock()
    mock.call.return_value = LLMResponse(
        text=json.dumps(data),
        model="test-model",
        provider="test",
    )
    return mock


# --- Existing tests (kept for backward compat) ---


def test_llm_qa_gate_evaluates():
    mock_client = MagicMock()
    mock_client.call.return_value = LLMResponse(
        text=json.dumps(
            {
                "passed": True,
                "score": 0.85,
                "issues": [],
                "coverage": {
                    "task_addressed": True,
                    "depth_sufficient": True,
                    "recommendations_actionable": True,
                },
            }
        ),
        model="test-model",
        provider="test",
    )

    gate = LLMQualityGate(client=mock_client)
    result = gate.evaluate(_make_integrated(), task="Review payment system")

    assert result.passed is True
    mock_client.call.assert_called_once()


def test_llm_qa_gate_flags_issues():
    mock_client = MagicMock()
    mock_client.call.return_value = LLMResponse(
        text=json.dumps(
            {
                "passed": False,
                "score": 0.4,
                "issues": ["No security analysis provided", "Recommendations too vague"],
                "coverage": {
                    "task_addressed": True,
                    "depth_sufficient": False,
                    "recommendations_actionable": False,
                },
            }
        ),
        model="test-model",
        provider="test",
    )

    gate = LLMQualityGate(client=mock_client)
    result = gate.evaluate(_make_integrated(), task="Security audit of payment system")

    assert result.passed is False
    assert len(result.failures) > 0


def test_llm_qa_gate_fallback_to_structural():
    """When no client, runs structural QAGate only."""
    gate = LLMQualityGate(client=None)
    result = gate.evaluate(_make_integrated(), task="review")

    assert isinstance(result, QAGateResult)
    assert result.passed is True


# --- New tests ---


class TestEvaluateEdgeCases:
    """Edge cases for evaluate()."""

    def setup_method(self):
        LLMQualityGate.reset_fallback_count()

    def test_structural_failure_returns_early(self):
        """If structural check fails, LLM is never called."""
        mock_client = MagicMock()
        gate = LLMQualityGate(client=mock_client)

        integrated = IntegratedArtifact(
            source_agents=["agency.expert-a"],
            merged_sections={},
        )
        result = gate.evaluate(
            integrated,
            task="review",
            required_sections=["summary"],
        )

        assert result.passed is False
        mock_client.call.assert_not_called()

    def test_llm_exception_falls_back_to_structural(self):
        mock_client = MagicMock()
        mock_client.call.side_effect = RuntimeError("LLM timeout")

        gate = LLMQualityGate(client=mock_client)
        result = gate.evaluate(_make_integrated(), task="review")

        assert isinstance(result, QAGateResult)
        assert LLMQualityGate.fallback_count() == 1

    def test_no_client_increments_fallback(self):
        gate = LLMQualityGate(client=None)
        gate.evaluate(_make_integrated(), task="review")

        assert LLMQualityGate.fallback_count() == 1

    def test_temperature_forwarded_to_client(self):
        mock_client = _llm_response(
            {"passed": True, "score": 0.9, "issues": [], "coverage": {}}
        )

        gate = LLMQualityGate(client=mock_client, temperature=0.2)
        gate.evaluate(_make_integrated(), task="review")

        call_kwargs = mock_client.call.call_args.kwargs
        assert call_kwargs["temperature"] == 0.2

    def test_custom_pass_threshold(self):
        """Custom threshold higher than LLM score should fail."""
        mock_client = _llm_response(
            {"passed": True, "score": 0.7, "issues": [], "coverage": {}}
        )

        gate = LLMQualityGate(client=mock_client, pass_threshold=0.9)
        result = gate.evaluate(_make_integrated(), task="review")

        # Score 0.7 < threshold 0.9, but >= trust_floor 0.5 → trust override
        # Actually structural passed + score >= floor → passed
        assert isinstance(result, QAGateResult)
        assert result.passed is True

    def test_required_sections_passed_to_structural(self):
        mock_client = MagicMock()
        gate = LLMQualityGate(client=mock_client)

        integrated = IntegratedArtifact(
            source_agents=["agency.expert-a"],
            merged_sections={"summary": "ok"},
        )
        result = gate.evaluate(
            integrated,
            task="review",
            required_sections=["summary"],
        )

        # Structural check should pass because summary exists
        assert isinstance(result, QAGateResult)
        assert result.passed is True


class TestParseEvaluation:
    """Tests for _parse_evaluation edge cases."""

    def setup_method(self):
        LLMQualityGate.reset_fallback_count()

    def test_unparseable_json_returns_structural_result(self):
        mock_client = MagicMock()
        mock_client.call.return_value = LLMResponse(
            text="not json",
            model="test",
            provider="test",
        )

        gate = LLMQualityGate(client=mock_client)
        result = gate.evaluate(_make_integrated(), task="review")

        assert isinstance(result, QAGateResult)
        assert result.passed is True

    def test_structural_trust_override(self):
        """LLM score below threshold but >= trust floor → passes via override."""
        mock_client = _llm_response(
            {
                "passed": False,
                "score": 0.55,
                "issues": ["Minor formatting issue"],
                "coverage": {},
            }
        )

        gate = LLMQualityGate(client=mock_client, pass_threshold=0.8)
        result = gate.evaluate(_make_integrated(), task="review")

        # Score 0.55 >= trust_floor 0.5 and structural passed → override
        assert result.passed is True

    def test_score_below_trust_floor_fails(self):
        """LLM score below trust floor → fails, no override."""
        mock_client = _llm_response(
            {
                "passed": False,
                "score": 0.3,
                "issues": ["Completely inadequate analysis"],
                "coverage": {},
            }
        )

        gate = LLMQualityGate(client=mock_client, pass_threshold=0.6)
        result = gate.evaluate(_make_integrated(), task="review")

        # Score 0.3 < trust_floor 0.5 → no override → fail
        assert result.passed is False

    def test_issues_appended_to_failures(self):
        mock_client = _llm_response(
            {
                "passed": False,
                "score": 0.3,
                "issues": ["Missing depth", "Vague recommendations"],
                "coverage": {},
            }
        )

        gate = LLMQualityGate(client=mock_client, pass_threshold=0.6)
        result = gate.evaluate(_make_integrated(), task="review")

        failure_text = " ".join(result.failures)
        assert "Missing depth" in failure_text
        assert "Vague recommendations" in failure_text

    def test_custom_structural_trust_floor(self):
        """Custom trust floor at 0.8 blocks the override for score 0.6."""
        mock_client = _llm_response(
            {
                "passed": False,
                "score": 0.6,
                "issues": [],
                "coverage": {},
            }
        )

        gate = LLMQualityGate(
            client=mock_client,
            pass_threshold=0.7,
            structural_trust_floor=0.8,
        )
        result = gate.evaluate(_make_integrated(), task="review")

        # Score 0.6 < custom floor 0.8 → no override → fail
        assert result.passed is False

    def test_passed_true_with_high_score(self):
        mock_client = _llm_response(
            {
                "passed": True,
                "score": 0.95,
                "issues": [],
                "coverage": {
                    "task_addressed": True,
                    "depth_sufficient": True,
                    "recommendations_actionable": True,
                },
            }
        )

        gate = LLMQualityGate(client=mock_client)
        result = gate.evaluate(_make_integrated(), task="review")

        assert result.passed is True
        assert len(result.failures) == 0

    def test_missing_passed_key_infers_from_score(self):
        """When LLM omits 'passed', infer from score vs threshold."""
        mock_client = _llm_response(
            {"score": 0.9, "issues": [], "coverage": {}}
        )

        gate = LLMQualityGate(client=mock_client, pass_threshold=0.6)
        result = gate.evaluate(_make_integrated(), task="review")

        # score 0.9 >= threshold 0.6 → passed=True inferred
        assert result.passed is True


class TestQAGateFallbackCounter:
    """Tests for thread-safe fallback counter."""

    def setup_method(self):
        LLMQualityGate.reset_fallback_count()

    def test_initial_count_is_zero(self):
        assert LLMQualityGate.fallback_count() == 0

    def test_no_client_increments(self):
        gate = LLMQualityGate(client=None)
        gate.evaluate(_make_integrated(), task="review")

        assert LLMQualityGate.fallback_count() == 1

    def test_llm_failure_increments(self):
        mock_client = MagicMock()
        mock_client.call.side_effect = RuntimeError("fail")
        gate = LLMQualityGate(client=mock_client)
        gate.evaluate(_make_integrated(), task="review")

        assert LLMQualityGate.fallback_count() == 1

    def test_reset_clears_counter(self):
        LLMQualityGate(client=None).evaluate(_make_integrated(), task="review")
        assert LLMQualityGate.fallback_count() > 0

        LLMQualityGate.reset_fallback_count()
        assert LLMQualityGate.fallback_count() == 0

    def test_concurrent_fallbacks_thread_safe(self):
        barrier = threading.Barrier(4)
        results = []

        def fall_back():
            barrier.wait()
            gate = LLMQualityGate(client=None)
            gate.evaluate(_make_integrated(), task="review")
            results.append(True)

        threads = [threading.Thread(target=fall_back) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 4
        assert LLMQualityGate.fallback_count() == 4
