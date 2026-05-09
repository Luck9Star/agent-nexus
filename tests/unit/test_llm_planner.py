"""Tests for LLMPlanner — LLM-powered task decomposition."""

import json
import threading
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from agent_nexus.platform.agency.llm_client import LLMResponse
from agent_nexus.platform.agency.llm_planner import (
    ExpertSelection,
    LLMPlanner,
    PlannerOutput,
    StructuredPlannerOutput,
)
from agent_nexus.platform.agency.registry import ExpertRegistry


def _make_registry():
    registry = ExpertRegistry()
    registry.add(
        "agency.code-reviewer",
        {
            "id": "agency.code-reviewer",
            "name": "Code Reviewer",
            "capabilities": ["code_review", "security_review"],
        },
        ["code_review", "security_review"],
    )
    registry.add(
        "agency.architect",
        {
            "id": "agency.architect",
            "name": "System Architect",
            "capabilities": ["system_design", "architecture_review"],
        },
        ["system_design", "architecture_review"],
    )
    return registry


def _llm_response(data: dict) -> MagicMock:
    mock = MagicMock()
    mock.call.return_value = LLMResponse(
        text=json.dumps(data),
        model="test-model",
        provider="test",
    )
    return mock


# --- Existing tests (kept for backward compat) ---


def test_planner_output_from_llm_response():
    raw = json.dumps(
        {
            "capabilities": ["code_review", "system_design"],
            "focus_hints": {
                "agency.code-reviewer": "Focus on security vulnerabilities",
                "agency.architect": "Focus on scalability concerns",
            },
            "decomposition_strategy": "parallel",
        }
    )
    output = PlannerOutput.from_json(raw)
    assert output.capabilities == ["code_review", "system_design"]
    assert output.decomposition_strategy == "parallel"
    assert "agency.code-reviewer" in output.focus_hints


def test_planner_output_from_invalid_json_falls_back():
    output = PlannerOutput.from_json("not json at all")
    assert output.capabilities == []
    assert output.decomposition_strategy == "parallel"


def test_llm_planner_analyze():
    mock_client = MagicMock()
    mock_client.call.return_value = LLMResponse(
        text=json.dumps(
            {
                "capabilities": ["code_review", "system_design"],
                "focus_hints": {"agency.code-reviewer": "security"},
                "decomposition_strategy": "parallel",
            }
        ),
        model="test-model",
        provider="test",
    )

    registry = _make_registry()
    planner = LLMPlanner(registry=registry, client=mock_client)
    result = planner.analyze_task("Review the payment system architecture for security issues")

    assert "code_review" in result.capabilities or "system_design" in result.capabilities
    mock_client.call.assert_called_once()
    call_args = mock_client.call.call_args
    assert (
        "code_review" in call_args.kwargs["system_prompt"]
        or "system_design" in call_args.kwargs["system_prompt"]
    )


def test_llm_planner_fallback_to_keywords():
    registry = _make_registry()
    planner = LLMPlanner(registry=registry, client=None)
    result = planner.analyze_task("Review the architecture design")
    assert isinstance(result, PlannerOutput)
    assert isinstance(result.capabilities, list)


# --- New tests ---


class TestPlannerOutputFromJson:
    """Edge cases for PlannerOutput.from_json()."""

    def test_valid_json_with_expert_selections(self):
        raw = json.dumps(
            {
                "capabilities": ["code_review"],
                "focus_hints": {},
                "decomposition_strategy": "sequential",
                "expert_selections": [
                    {
                        "expert_id": "agency.code-reviewer",
                        "task": "Review code",
                        "parameters": {"depth": "full"},
                    }
                ],
            }
        )
        output = PlannerOutput.from_json(raw)
        assert output.decomposition_strategy == "sequential"
        assert len(output.expert_selections) == 1
        assert output.expert_selections[0].expert_id == "agency.code-reviewer"
        assert output.expert_selections[0].parameters == {"depth": "full"}

    def test_partial_json_uses_manual_fallback(self):
        """JSON that fails Pydantic validation but is valid JSON uses manual parse."""
        raw = json.dumps(
            {
                "capabilities": ["code_review"],
                "decomposition_strategy": "parallel",
                # Missing focus_hints, extra fields, etc.
            }
        )
        output = PlannerOutput.from_json(raw)
        assert output.capabilities == ["code_review"]
        assert output.decomposition_strategy == "parallel"

    def test_empty_json_object(self):
        output = PlannerOutput.from_json("{}")
        assert output.capabilities == []
        assert output.decomposition_strategy == "parallel"
        assert output.focus_hints == {}

    def test_default_planner_output(self):
        output = PlannerOutput()
        assert output.capabilities == []
        assert output.focus_hints == {}
        assert output.decomposition_strategy == "parallel"
        assert output.expert_selections == []


class TestStructuredPlannerOutput:
    """Tests for Pydantic-validated StructuredPlannerOutput."""

    def test_valid_structured_output(self):
        data = {
            "capabilities": ["code_review"],
            "focus_hints": {"agency.code-reviewer": "security"},
            "decomposition_strategy": "parallel",
            "expert_selections": [
                {"expert_id": "agency.code-reviewer", "task": "Review"}
            ],
        }
        result = StructuredPlannerOutput.model_validate(data)
        assert result.capabilities == ["code_review"]
        assert result.decomposition_strategy == "parallel"
        assert len(result.expert_selections) == 1

    def test_invalid_decomposition_strategy_rejected(self):
        with pytest.raises(ValidationError):
            StructuredPlannerOutput.model_validate(
                {"decomposition_strategy": "invalid_strategy"}
            )

    def test_default_values(self):
        result = StructuredPlannerOutput.model_validate({})
        assert result.capabilities == []
        assert result.focus_hints == {}
        assert result.decomposition_strategy == "parallel"
        assert result.expert_selections == []


class TestExpertSelection:
    """Tests for ExpertSelection Pydantic model."""

    def test_basic_creation(self):
        sel = ExpertSelection(
            expert_id="agency.code-reviewer",
            task="Review payment module",
        )
        assert sel.expert_id == "agency.code-reviewer"
        assert sel.parameters == {}

    def test_with_parameters(self):
        sel = ExpertSelection(
            expert_id="agency.architect",
            task="Design API",
            parameters={"focus": "security"},
        )
        assert sel.parameters["focus"] == "security"


class TestLLMPlannerAnalyze:
    """Additional tests for LLMPlanner.analyze_task()."""

    def setup_method(self):
        LLMPlanner.reset_fallback_count()

    def test_llm_exception_falls_back_to_keywords(self):
        mock_client = MagicMock()
        mock_client.call.side_effect = RuntimeError("Connection refused")

        planner = LLMPlanner(registry=_make_registry(), client=mock_client)
        result = planner.analyze_task("Review the code")

        assert isinstance(result, PlannerOutput)
        assert LLMPlanner.fallback_count() == 1

    def test_llm_returns_structured_with_expert_selections(self):
        """N3 path: LLM returns Pydantic-compatible structured output."""
        mock_client = _llm_response(
            {
                "capabilities": ["code_review", "security_review"],
                "focus_hints": {"agency.code-reviewer": "Focus on SQL injection"},
                "decomposition_strategy": "parallel",
                "expert_selections": [
                    {
                        "expert_id": "agency.code-reviewer",
                        "task": "Security review of payment system",
                        "parameters": {},
                    }
                ],
            }
        )

        planner = LLMPlanner(registry=_make_registry(), client=mock_client)
        result = planner.analyze_task("Review payment security")

        assert "code_review" in result.capabilities
        assert len(result.expert_selections) == 1
        assert result.expert_selections[0].expert_id == "agency.code-reviewer"

    def test_llm_returns_malformed_json_falls_back(self):
        """LLM returns invalid JSON → from_json returns empty defaults."""
        mock_client = MagicMock()
        mock_client.call.return_value = LLMResponse(
            text="not json at all",
            model="test",
            provider="test",
        )

        planner = LLMPlanner(registry=_make_registry(), client=mock_client)
        result = planner.analyze_task("some task")

        assert isinstance(result, PlannerOutput)
        assert result.capabilities == []

    def test_temperature_forwarded(self):
        mock_client = _llm_response(
            {"capabilities": [], "decomposition_strategy": "parallel"}
        )

        planner = LLMPlanner(
            registry=_make_registry(), client=mock_client, temperature=0.1
        )
        planner.analyze_task("task")

        call_kwargs = mock_client.call.call_args.kwargs
        assert call_kwargs["temperature"] == 0.1

    def test_no_client_increments_fallback_counter(self):
        planner = LLMPlanner(registry=_make_registry(), client=None)
        planner.analyze_task("review code")

        assert LLMPlanner.fallback_count() == 1

    def test_prompt_contains_expert_info(self):
        mock_client = _llm_response(
            {"capabilities": [], "decomposition_strategy": "parallel"}
        )

        planner = LLMPlanner(registry=_make_registry(), client=mock_client)
        planner.analyze_task("review")

        call_kwargs = mock_client.call.call_args.kwargs
        prompt = call_kwargs["system_prompt"]
        assert "code_review" in prompt
        assert "system_design" in prompt
        assert "agency.code-reviewer" in prompt


class TestGetAllProfiles:
    """Tests for _get_all_profiles."""

    def test_returns_profiles_from_registry(self):
        registry = _make_registry()
        planner = LLMPlanner(registry=registry)
        profiles = planner._get_all_profiles()

        assert len(profiles) == 2
        ids = [p["id"] for p in profiles]
        assert "agency.code-reviewer" in ids
        assert "agency.architect" in ids

    def test_empty_registry_returns_empty_list(self):
        registry = ExpertRegistry()
        planner = LLMPlanner(registry=registry)
        profiles = planner._get_all_profiles()

        assert profiles == []


class TestBuildPlanningPrompt:
    """Tests for _build_planning_prompt."""

    def test_includes_schema_section(self):
        planner = LLMPlanner(registry=_make_registry())
        prompt = planner._build_planning_prompt()

        assert "capabilities" in prompt
        assert "decomposition_strategy" in prompt

    def test_uses_provided_profiles(self):
        planner = LLMPlanner(registry=_make_registry())
        profiles = [{"id": "agency.custom", "name": "Custom", "capabilities": ["x"]}]
        prompt = planner._build_planning_prompt(expert_profiles=profiles)

        assert "agency.custom" in prompt

    def test_empty_profiles_still_produces_prompt(self):
        planner = LLMPlanner(registry=_make_registry())
        prompt = planner._build_planning_prompt(expert_profiles=[])

        assert isinstance(prompt, str)
        assert len(prompt) > 0


class TestPlannerFallbackCounter:
    """Tests for thread-safe fallback counter."""

    def setup_method(self):
        LLMPlanner.reset_fallback_count()

    def test_reset_clears_counter(self):
        LLMPlanner(registry=_make_registry(), client=None).analyze_task("task")
        assert LLMPlanner.fallback_count() > 0

        LLMPlanner.reset_fallback_count()
        assert LLMPlanner.fallback_count() == 0

    def test_concurrent_fallbacks_thread_safe(self):
        barrier = threading.Barrier(4)
        results = []

        def fall_back():
            barrier.wait()
            planner = LLMPlanner(registry=_make_registry(), client=None)
            planner.analyze_task("review code")
            results.append(True)

        threads = [threading.Thread(target=fall_back) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 4
        assert LLMPlanner.fallback_count() == 4
