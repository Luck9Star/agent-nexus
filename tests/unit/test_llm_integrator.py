"""Tests for LLMIntegrator — LLM-powered artifact synthesis."""

import json
from unittest.mock import MagicMock

from agent_nexus.platform.agency.integrator import Artifact, IntegratedArtifact
from agent_nexus.platform.agency.llm_client import LLMResponse
from agent_nexus.platform.agency.llm_integrator import LLMIntegrator


def _make_artifacts():
    return [
        Artifact(
            source_agent="agency.expert-a",
            artifact_type="report",
            sections={
                "summary": "Security risk: SQL injection",
                "recommendations": ["Use parameterized queries"],
            },
            metadata={"llm": True},
        ),
        Artifact(
            source_agent="agency.expert-b",
            artifact_type="report",
            sections={
                "summary": "Architecture issue: tight coupling",
                "recommendations": ["Introduce interfaces"],
            },
            metadata={"llm": True},
        ),
    ]


def test_llm_integrator_synthesizes():
    mock_client = MagicMock()
    mock_client.call.return_value = LLMResponse(
        text=json.dumps({
            "summary": "Combined analysis reveals both security and architecture concerns",
            "recommendations": ["Use parameterized queries", "Introduce interfaces"],
            "conflicts": [],
            "gaps": [],
        }),
        model="test-model",
        provider="test",
    )

    integrator = LLMIntegrator(client=mock_client)
    result = integrator.synthesize(_make_artifacts(), task="Review payment system")

    assert isinstance(result, IntegratedArtifact)
    assert len(result.source_agents) == 2
    mock_client.call.assert_called_once()


def test_llm_integrator_fallback_to_rules():
    """When no client, falls back to Integrator.merge."""
    integrator = LLMIntegrator(client=None)
    result = integrator.synthesize(_make_artifacts(), task="Review payment system")

    assert isinstance(result, IntegratedArtifact)
    assert len(result.source_agents) == 2
    # Should contain mechanically merged data
    assert "summary" in result.merged_sections


def test_llm_integrator_single_artifact():
    """Single artifact should work without LLM call."""
    single = [_make_artifacts()[0]]
    mock_client = MagicMock()

    integrator = LLMIntegrator(client=mock_client)
    result = integrator.synthesize(single, task="review")

    assert isinstance(result, IntegratedArtifact)
    # Single artifact: no synthesis needed, direct pass-through
    mock_client.call.assert_not_called()
