"""Tests for LLMPlanner — LLM-powered task decomposition."""

import json
from unittest.mock import MagicMock

from agent_nexus.platform.agency.llm_client import LLMResponse
from agent_nexus.platform.agency.llm_planner import LLMPlanner, PlannerOutput
from agent_nexus.platform.agency.registry import ExpertRegistry


def _make_registry():
    registry = ExpertRegistry()
    registry.add("agency.code-reviewer", {
        "id": "agency.code-reviewer",
        "name": "Code Reviewer",
        "capabilities": ["code_review", "security_review"],
    }, ["code_review", "security_review"])
    registry.add("agency.architect", {
        "id": "agency.architect",
        "name": "System Architect",
        "capabilities": ["system_design", "architecture_review"],
    }, ["system_design", "architecture_review"])
    return registry


def test_planner_output_from_llm_response():
    """PlannerOutput parses structured JSON from LLM."""
    raw = json.dumps({
        "capabilities": ["code_review", "system_design"],
        "focus_hints": {
            "agency.code-reviewer": "Focus on security vulnerabilities",
            "agency.architect": "Focus on scalability concerns",
        },
        "decomposition_strategy": "parallel",
    })
    output = PlannerOutput.from_json(raw)
    assert output.capabilities == ["code_review", "system_design"]
    assert output.decomposition_strategy == "parallel"
    assert "agency.code-reviewer" in output.focus_hints


def test_planner_output_from_invalid_json_falls_back():
    """PlannerOutput gracefully handles malformed JSON."""
    output = PlannerOutput.from_json("not json at all")
    assert output.capabilities == []
    assert output.decomposition_strategy == "parallel"


def test_llm_planner_analyze():
    mock_client = MagicMock()
    mock_client.call.return_value = LLMResponse(
        text=json.dumps({
            "capabilities": ["code_review", "system_design"],
            "focus_hints": {"agency.code-reviewer": "security"},
            "decomposition_strategy": "parallel",
        }),
        model="test-model",
        provider="test",
    )

    registry = _make_registry()
    planner = LLMPlanner(registry=registry, client=mock_client)
    result = planner.analyze_task("Review the payment system architecture for security issues")

    assert "code_review" in result.capabilities or "system_design" in result.capabilities
    mock_client.call.assert_called_once()
    # Verify system prompt contains expert info
    call_args = mock_client.call.call_args
    assert (
        "code_review" in call_args.kwargs["system_prompt"]
        or "system_design" in call_args.kwargs["system_prompt"]
    )


def test_llm_planner_fallback_to_keywords():
    """When no client provided, falls back to keyword inference."""
    registry = _make_registry()
    planner = LLMPlanner(registry=registry, client=None)
    result = planner.analyze_task("Review the architecture design")
    # Should use keyword matching as fallback
    assert isinstance(result, PlannerOutput)
    assert isinstance(result.capabilities, list)
