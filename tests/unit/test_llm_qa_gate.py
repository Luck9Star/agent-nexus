"""Tests for LLMQualityGate — LLM-powered quality evaluation."""

import json
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

    # Structural check passes because sections exist
    assert isinstance(result, QAGateResult)
