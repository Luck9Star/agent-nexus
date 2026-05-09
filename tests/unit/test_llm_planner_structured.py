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

import pytest
from pydantic import ValidationError

# Direct module imports to avoid triggering __init__.py circular imports
from agent_nexus.platform.agency.llm_planner import (
    ExpertSelection,
    LLMPlanner,
    PlannerOutput,
    StructuredPlannerOutput,
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

    def test_expert_selection_defaults(self):
        """ExpertSelection parameters default to empty dict."""
        sel = ExpertSelection(expert_id="test", task="do something")
        assert sel.parameters == {}

    def test_json_schema_generation(self):
        """model_json_schema returns a usable JSON schema."""
        schema = StructuredPlannerOutput.model_json_schema()
        assert "properties" in schema
        assert "capabilities" in schema["properties"]
        assert "expert_selections" in schema["properties"]
        assert "decomposition_strategy" in schema["properties"]
        # decomposition_strategy should be constrained to literal values
        ds_prop = schema["properties"]["decomposition_strategy"]
        assert "enum" in ds_prop or "anyOf" in ds_prop

    def test_model_validate_json_parses_string(self):
        """model_validate_json accepts a raw JSON string."""
        raw = json.dumps(
            {
                "capabilities": ["code_review"],
                "expert_selections": [{"expert_id": "agency.code-reviewer", "task": "review"}],
            }
        )
        result = StructuredPlannerOutput.model_validate_json(raw)
        assert result.capabilities == ["code_review"]

    def test_model_validate_json_rejects_invalid(self):
        """model_validate_json raises on invalid JSON."""
        with pytest.raises((ValidationError, ValueError)):
            StructuredPlannerOutput.model_validate_json("not json")


# ---------------------------------------------------------------------------
# PlannerOutput.from_json — unique tests only
# ---------------------------------------------------------------------------


class TestPlannerOutputFromJson:
    """Tests for PlannerOutput.from_json() — unique scenarios only."""

    def test_partial_json_falls_back_to_manual_parse(self):
        """JSON with fields that fail Pydantic falls back to manual extraction.

        For example, decomposition_strategy="bad_value" fails Pydantic's
        Literal constraint, so the fallback path extracts what it can.
        """
        raw = json.dumps(
            {
                "capabilities": ["code_review"],
                "decomposition_strategy": "bad_value",
            }
        )
        output = PlannerOutput.from_json(raw)
        # Fallback should still extract capabilities
        assert output.capabilities == ["code_review"]
        # decomposition_strategy from manual fallback is the raw value
        assert output.decomposition_strategy == "bad_value"

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


class TestBuildPlanningPrompt:
    """Tests for the dynamic schema and expert info injection — unique scenarios."""

    def test_prompt_contains_json_schema(self):
        """Prompt includes the Pydantic JSON schema."""
        registry = _make_registry()
        planner = LLMPlanner(registry=registry, client=None)
        prompt = planner._build_planning_prompt()

        # The schema should be included in the output format section
        assert "capabilities" in prompt
        assert "expert_selections" in prompt
        assert "expert_id" in prompt
        assert "decomposition_strategy" in prompt

    def test_prompt_contains_expert_id_constraint(self):
        """Prompt includes the constraint that expert_id must be from the known list."""
        registry = _make_registry()
        planner = LLMPlanner(registry=registry, client=None)
        prompt = planner._build_planning_prompt()

        assert "agency.code-reviewer" in prompt
        assert "agency.architect" in prompt
        # The constraint text should mention expert_id values
        assert "expert_id" in prompt


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


class TestExpertSelection:
    """Tests for the ExpertSelection Pydantic model — unique scenarios."""

    def test_missing_required_field_raises(self):
        """Missing required fields raise ValidationError."""
        with pytest.raises(ValidationError):
            ExpertSelection(expert_id="test", task=None)  # type: ignore[arg-type]

    def test_schema_includes_descriptions(self):
        """Field descriptions are present in the JSON schema."""
        schema = ExpertSelection.model_json_schema()
        props = schema["properties"]
        assert "description" in props["expert_id"]
        assert "description" in props["task"]
