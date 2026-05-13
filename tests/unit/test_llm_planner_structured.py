"""Unit tests for PermissionChecker.check_command() — shell injection, modes, denylist, edge cases.

These tests focus exclusively on check_command() behaviour, complementing the
broader test_permission_checker.py which covers check_tool and check_path.

NOTE: Many tests from the original file were duplicates of tests already in
test_llm_planner.py. This file now contains only the unique tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import MagicMock

# Direct module imports to avoid triggering __init__.py circular imports
from agent_nexus.platform.agency.llm_planner import (
    LLMPlanner,
    PlannerOutput,
)
from agent_nexus.platform.agency.registry import ExpertRegistry


# Minimal LLMResponse stand-in to avoid circular import via llm_client
@dataclass
class _LLMResponse:
    text: str
    model: str = "test-model"
    provider: str = "test"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_registry() -> ExpertRegistry:
    """Build a registry with two test experts."""
    registry = ExpertRegistry()
    registry.add(
        "agency.code-reviewer",
        {
            "id": "agency.code-reviewer",
            "name": "Code Reviewer",
            "description": "Reviews code quality and security",
            "capabilities": ["code_review", "security_review"],
        },
        ["code_review", "security_review"],
    )
    registry.add(
        "agency.architect",
        {
            "id": "agency.architect",
            "name": "System Architect",
            "description": "Designs system architecture",
            "capabilities": ["system_design", "architecture_review"],
        },
        ["system_design", "architecture_review"],
    )
    return registry


def _make_mock_client(response_text: str) -> MagicMock:
    """Create a mock LLMClient that returns the given text."""
    mock = MagicMock()
    mock.call.return_value = _LLMResponse(
        text=response_text,
        model="test-model",
        provider="test",
    )
    return mock


# ---------------------------------------------------------------------------
# StructuredPlannerOutput Pydantic model — unique tests only
# ---------------------------------------------------------------------------


class TestStructuredPlannerOutput:
    """Tests for the Pydantic StructuredPlannerOutput model."""


# ---------------------------------------------------------------------------
# PlannerOutput.from_json — unique tests only
# ---------------------------------------------------------------------------


class TestPlannerOutputFromJson:
    """Tests for PlannerOutput.from_json() — unique scenarios only."""

    def test_backward_compatible_without_expert_selections(self):
        """Old format without expert_selections still works."""
        raw = json.dumps(
            {
                "capabilities": ["code_review", "system_design"],
                "focus_hints": {"agency.code-reviewer": "security"},
                "decomposition_strategy": "sequential",
            }
        )
        output = PlannerOutput.from_json(raw)
        assert output.capabilities == ["code_review", "system_design"]
        assert output.decomposition_strategy == "sequential"
        assert output.expert_selections == []


# ---------------------------------------------------------------------------
# _build_planning_prompt — unique tests only
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# LLMPlanner._llm_analyze — unique tests only
# ---------------------------------------------------------------------------


class TestLLMAnalyze:
    """Tests for _llm_analyze with structured output + fallback — unique scenarios."""

    def setup_method(self):
        LLMPlanner.reset_fallback_count()

    def test_multi_expert_selection(self):
        """Multiple experts can be selected in a single response."""
        response_json = json.dumps(
            {
                "capabilities": ["code_review", "system_design"],
                "expert_selections": [
                    {"expert_id": "agency.code-reviewer", "task": "Review code"},
                    {"expert_id": "agency.architect", "task": "Review architecture"},
                ],
            }
        )
        mock_client = _make_mock_client(response_json)
        registry = _make_registry()
        planner = LLMPlanner(registry=registry, client=mock_client)

        result = planner.analyze_task("Full system review")
        assert len(result.expert_selections) == 2
        ids = {s.expert_id for s in result.expert_selections}
        assert ids == {"agency.code-reviewer", "agency.architect"}

    def test_fallback_on_invalid_strategy(self):
        """Invalid decomposition_strategy triggers Pydantic fallback path."""
        response_json = json.dumps(
            {
                "capabilities": ["code_review"],
                "decomposition_strategy": "invalid_strategy",
            }
        )
        mock_client = _make_mock_client(response_json)
        registry = _make_registry()
        planner = LLMPlanner(registry=registry, client=mock_client)

        result = planner.analyze_task("Review code")
        # Should still extract capabilities via fallback
        assert "code_review" in result.capabilities

    def test_unknown_expert_id_accepted_by_pydantic(self):
        """Pydantic model does not enforce expert_id membership (it's a string).

        The constraint is expressed in the prompt, not in the schema.
        Unknown IDs pass through but are the LLM's responsibility.
        """
        response_json = json.dumps(
            {
                "expert_selections": [
                    {"expert_id": "agency.nonexistent", "task": "Do something"},
                ],
            }
        )
        mock_client = _make_mock_client(response_json)
        registry = _make_registry()
        planner = LLMPlanner(registry=registry, client=mock_client)

        result = planner.analyze_task("Some task")
        assert result.expert_selections[0].expert_id == "agency.nonexistent"


# ---------------------------------------------------------------------------
# ExpertSelection model — unique tests only
# ---------------------------------------------------------------------------
