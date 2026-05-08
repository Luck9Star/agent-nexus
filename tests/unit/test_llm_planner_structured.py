"""Tests for N3 — Discriminated Union constrained Planner.

Covers:
- Structured JSON output correctly parsed via Pydantic
- Dynamic schema generation from expert profiles
- Fallback to free-text parsing on Pydantic ValidationError
- Fallback on JSON decode failure
- Multi-expert selection scenarios
- Unknown expert ID handling
- Default values for optional fields
- _build_planning_prompt includes correct expert descriptions and schema
"""

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
# StructuredPlannerOutput Pydantic model tests
# ---------------------------------------------------------------------------


class TestStructuredPlannerOutput:
    """Tests for the Pydantic StructuredPlannerOutput model."""

    def test_valid_structured_output(self):
        """Valid JSON parses correctly into StructuredPlannerOutput."""
        data = {
            "capabilities": ["code_review", "system_design"],
            "focus_hints": {"agency.code-reviewer": "security"},
            "decomposition_strategy": "parallel",
            "expert_selections": [
                {
                    "expert_id": "agency.code-reviewer",
                    "task": "Review code for security issues",
                    "parameters": {"depth": "deep"},
                }
            ],
        }
        result = StructuredPlannerOutput.model_validate(data)
        assert result.capabilities == ["code_review", "system_design"]
        assert result.decomposition_strategy == "parallel"
        assert len(result.expert_selections) == 1
        assert result.expert_selections[0].expert_id == "agency.code-reviewer"
        assert result.expert_selections[0].task == "Review code for security issues"
        assert result.expert_selections[0].parameters == {"depth": "deep"}

    def test_default_values(self):
        """Optional fields get correct defaults."""
        result = StructuredPlannerOutput.model_validate({})
        assert result.capabilities == []
        assert result.focus_hints == {}
        assert result.decomposition_strategy == "parallel"
        assert result.expert_selections == []

    def test_invalid_decomposition_strategy_rejected(self):
        """Invalid decomposition_strategy value raises ValidationError."""
        with pytest.raises(ValidationError):
            StructuredPlannerOutput.model_validate(
                {
                    "decomposition_strategy": "invalid",
                }
            )

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
# PlannerOutput dataclass + Pydantic integration tests
# ---------------------------------------------------------------------------


class TestPlannerOutputFromJson:
    """Tests for PlannerOutput.from_json() with Pydantic validation layer."""

    def test_structured_json_validates_via_pydantic(self):
        """Valid structured JSON goes through Pydantic and returns correctly."""
        raw = json.dumps(
            {
                "capabilities": ["code_review"],
                "focus_hints": {"agency.code-reviewer": "security"},
                "decomposition_strategy": "parallel",
                "expert_selections": [
                    {"expert_id": "agency.code-reviewer", "task": "review code"},
                ],
            }
        )
        output = PlannerOutput.from_json(raw)
        assert output.capabilities == ["code_review"]
        assert len(output.expert_selections) == 1
        assert output.expert_selections[0].expert_id == "agency.code-reviewer"

    def test_invalid_json_returns_empty(self):
        """Completely invalid JSON returns default PlannerOutput."""
        output = PlannerOutput.from_json("not json at all")
        assert output.capabilities == []
        assert output.decomposition_strategy == "parallel"
        assert output.expert_selections == []

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

    def test_empty_string_returns_default(self):
        """Empty string returns default PlannerOutput."""
        output = PlannerOutput.from_json("")
        assert output.capabilities == []

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
# LLMPlanner._build_planning_prompt tests
# ---------------------------------------------------------------------------


class TestBuildPlanningPrompt:
    """Tests for the dynamic schema and expert info injection."""

    def test_prompt_contains_expert_descriptions(self):
        """Prompt includes expert IDs and descriptions."""
        registry = _make_registry()
        planner = LLMPlanner(registry=registry, client=None)
        prompt = planner._build_planning_prompt()

        assert "agency.code-reviewer" in prompt
        assert "agency.architect" in prompt
        assert "Code Reviewer" in prompt
        assert "System Architect" in prompt

    def test_prompt_contains_capabilities(self):
        """Prompt includes available capabilities."""
        registry = _make_registry()
        planner = LLMPlanner(registry=registry, client=None)
        prompt = planner._build_planning_prompt()

        assert "code_review" in prompt
        assert "system_design" in prompt

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

    def test_prompt_with_explicit_profiles(self):
        """Prompt works with explicitly passed profiles."""
        registry = ExpertRegistry()
        profiles = [
            {
                "id": "agency.test-expert",
                "name": "Test Expert",
                "description": "A test expert",
                "capabilities": ["testing"],
            }
        ]
        planner = LLMPlanner(registry=registry, client=None)
        prompt = planner._build_planning_prompt(profiles)

        assert "agency.test-expert" in prompt
        assert "testing" in prompt

    def test_prompt_empty_registry(self):
        """Prompt with empty registry still renders (no crash)."""
        registry = ExpertRegistry()
        planner = LLMPlanner(registry=registry, client=None)
        prompt = planner._build_planning_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0


# ---------------------------------------------------------------------------
# LLMPlanner._llm_analyze integration tests
# ---------------------------------------------------------------------------


class TestLLMAnalyze:
    """Tests for _llm_analyze with structured output + fallback."""

    def setup_method(self):
        LLMPlanner.reset_fallback_count()

    def test_structured_response_parsed_via_pydantic(self):
        """Valid structured response is parsed through Pydantic."""
        response_json = json.dumps(
            {
                "capabilities": ["code_review"],
                "focus_hints": {"agency.code-reviewer": "security"},
                "decomposition_strategy": "parallel",
                "expert_selections": [
                    {"expert_id": "agency.code-reviewer", "task": "Review for security"},
                ],
            }
        )
        mock_client = _make_mock_client(response_json)
        registry = _make_registry()
        planner = LLMPlanner(registry=registry, client=mock_client)

        result = planner.analyze_task("Review code for security issues")
        assert "code_review" in result.capabilities
        assert len(result.expert_selections) == 1
        assert result.expert_selections[0].expert_id == "agency.code-reviewer"

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

    def test_fallback_on_invalid_json(self):
        """Non-JSON response falls back to from_json (returns default)."""
        mock_client = _make_mock_client("This is not JSON at all")
        registry = _make_registry()
        planner = LLMPlanner(registry=registry, client=mock_client)

        result = planner.analyze_task("Review code")
        # from_json will return a default PlannerOutput for invalid text
        assert result.capabilities is not None  # default empty list
        assert result.decomposition_strategy == "parallel"  # default strategy

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

    def test_no_client_uses_keyword_fallback(self):
        """Without LLMClient, falls back to keyword-based inference."""
        registry = _make_registry()
        planner = LLMPlanner(registry=registry, client=None)

        result = planner.analyze_task("Review the architecture design")
        assert isinstance(result, PlannerOutput)
        assert isinstance(result.capabilities, list)
        assert LLMPlanner.fallback_count() > 0


# ---------------------------------------------------------------------------
# ExpertSelection model tests
# ---------------------------------------------------------------------------


class TestExpertSelection:
    """Tests for the ExpertSelection Pydantic model."""

    def test_full_construction(self):
        """ExpertSelection accepts all fields."""
        sel = ExpertSelection(
            expert_id="agency.code-reviewer",
            task="Review the payment module",
            parameters={"language": "python", "depth": "deep"},
        )
        assert sel.expert_id == "agency.code-reviewer"
        assert sel.task == "Review the payment module"
        assert sel.parameters == {"language": "python", "depth": "deep"}

    def test_minimal_construction(self):
        """ExpertSelection with only required fields uses defaults."""
        sel = ExpertSelection(expert_id="test", task="do something")
        assert sel.parameters == {}

    def test_missing_required_field_raises(self):
        """Missing required fields raise ValidationError."""
        with pytest.raises(ValidationError):
            ExpertSelection(expert_id="test")  # missing task

    def test_schema_includes_descriptions(self):
        """Field descriptions are present in the JSON schema."""
        schema = ExpertSelection.model_json_schema()
        props = schema["properties"]
        assert "description" in props["expert_id"]
        assert "description" in props["task"]
