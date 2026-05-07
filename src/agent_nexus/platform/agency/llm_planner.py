"""LLMPlanner — LLM-powered task decomposition for the agency pipeline.

Replaces keyword-based ``infer_capabilities()`` with semantic task analysis.
Falls back to keyword matching when LLM is unavailable.

N3: Uses Pydantic-validated structured output with a discriminated-union
schema that constrains expert selection to known expert IDs.  Falls back to
free-text parsing when Pydantic validation or JSON parsing fails.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, ValidationError

from .json_parse import robust_json_parse
from .task_composer import infer_capabilities
from .token_counter import StructuredPrompt, TokenCounter

if TYPE_CHECKING:
    from .llm_client import LLMClient
    from .registry import ExpertRegistry

logger = logging.getLogger(__name__)

_MAX_PLANNING_TOKENS = 50_000


# ---------------------------------------------------------------------------
# Pydantic models for structured expert selection (N3)
# ---------------------------------------------------------------------------


class ExpertSelection(BaseModel):
    """A single expert selection with task assignment."""

    expert_id: str = Field(description="Expert profile ID from the known expert list")
    task: str = Field(description="Task description for this expert")
    parameters: dict = Field(default_factory=dict, description="Optional parameters for the expert")


class StructuredPlannerOutput(BaseModel):
    """Pydantic-validated structured output from task decomposition.

    Used by N3 to add schema-constrained validation on top of the existing
    PlannerOutput dataclass.  The LLM must respond with JSON that validates
    against this schema.
    """

    capabilities: list[str] = Field(
        default_factory=list, description="Required capabilities from the known set"
    )
    focus_hints: dict[str, str] = Field(
        default_factory=dict, description="Per-expert focus guidance"
    )
    decomposition_strategy: Literal["parallel", "sequential"] = Field(
        default="parallel", description="Execution strategy for the decomposition"
    )
    expert_selections: list[ExpertSelection] = Field(
        default_factory=list, description="Selected experts with task assignments"
    )


# ---------------------------------------------------------------------------
# Dataclass PlannerOutput (preserved for backward compatibility)
# ---------------------------------------------------------------------------


@dataclass
class PlannerOutput:
    """Structured output from task decomposition."""

    capabilities: list[str] = field(default_factory=list)
    focus_hints: dict[str, str] = field(default_factory=dict)
    decomposition_strategy: str = "parallel"
    """Either ``"parallel"`` or ``"sequential"``."""
    expert_selections: list[ExpertSelection] = field(default_factory=list)
    """N3: Structured expert selections with task assignments."""

    @classmethod
    def from_json(cls, raw: str) -> PlannerOutput:
        """Parse LLM JSON response into PlannerOutput.

        Uses Pydantic validation first (N3).  Falls back to manual parsing
        on ValidationError.  Returns a default (empty) PlannerOutput when
        no valid JSON is found.
        """
        data = robust_json_parse(raw)
        if data is None:
            logger.warning("LLMPlanner: failed to parse JSON response, returning empty output")
            return cls()

        # N3: Try Pydantic-validated parsing first
        try:
            validated = StructuredPlannerOutput.model_validate(data)
            return cls(
                capabilities=validated.capabilities,
                focus_hints=validated.focus_hints,
                decomposition_strategy=validated.decomposition_strategy,
                expert_selections=validated.expert_selections,
            )
        except ValidationError:
            logger.debug("LLMPlanner: Pydantic validation failed, falling back to manual parse")

        # Manual fallback
        return cls(
            capabilities=data.get("capabilities", []),
            focus_hints=data.get("focus_hints", {}),
            decomposition_strategy=data.get("decomposition_strategy", "parallel"),
        )


class LLMPlanner:
    """LLM-powered task decomposition replacing keyword-based inference.

    Uses an LLM to analyze user tasks and determine:
    1. Required capabilities (from the known capability set)
    2. Per-expert focus areas
    3. Decomposition strategy (parallel vs sequential)

    Falls back to keyword-based ``infer_capabilities()`` when no LLM client
    is available or the LLM call fails.
    """

    _fallback_count = 0
    _fallback_lock = threading.Lock()

    def __init__(
        self,
        registry: ExpertRegistry,
        client: LLMClient | None = None,
        temperature: float | None = None,
    ) -> None:
        self._registry = registry
        self._client = client
        self._temperature = temperature
        self._token_counter = TokenCounter()

    @classmethod
    def fallback_count(cls) -> int:
        """Number of times any LLMPlanner fell back to keywords (monitoring)."""
        with cls._fallback_lock:
            return cls._fallback_count

    @classmethod
    def reset_fallback_count(cls) -> None:
        """Reset the fallback counter (for test isolation)."""
        with cls._fallback_lock:
            cls._fallback_count = 0

    def analyze_task(self, task: str) -> PlannerOutput:
        """Analyze a task and return structured decomposition.

        Parameters
        ----------
        task:
            The user's task description.

        Returns
        -------
        PlannerOutput
            Capabilities, focus hints, and decomposition strategy.
        """
        if self._client is None:
            logger.debug("LLMPlanner: no LLM client, falling back to keywords")
            with LLMPlanner._fallback_lock:
                LLMPlanner._fallback_count += 1
            result = self._keyword_fallback(task)
            logger.info(
                "LLMPlanner: task analyzed — capabilities=%s, strategy=%s",
                result.capabilities,
                result.decomposition_strategy,
            )
            return result

        try:
            result = self._llm_analyze(task)
            logger.info(
                "LLMPlanner: task analyzed — capabilities=%s, strategy=%s",
                result.capabilities,
                result.decomposition_strategy,
            )
            return result
        except Exception:
            logger.exception("LLMPlanner: LLM call failed, falling back to keywords")
            with LLMPlanner._fallback_lock:
                LLMPlanner._fallback_count += 1
            return self._keyword_fallback(task)

    def _llm_analyze(self, task: str) -> PlannerOutput:
        """Perform LLM-based task analysis with Pydantic validation (N3)."""
        all_profiles = self._get_all_profiles()
        system_prompt = self._build_planning_prompt(all_profiles)
        response = self._client.call(
            system_prompt=system_prompt,
            user_message=task,
            temperature=self._temperature,
            response_format="json",
        )

        # N3: Try Pydantic-validated parsing first
        try:
            validated = StructuredPlannerOutput.model_validate_json(response.text)
            return PlannerOutput(
                capabilities=validated.capabilities,
                focus_hints=validated.focus_hints,
                decomposition_strategy=validated.decomposition_strategy,
                expert_selections=validated.expert_selections,
            )
        except (ValidationError, json.JSONDecodeError):
            logger.debug("LLMPlanner: structured parse failed, falling back to from_json()")

        # Fallback to robust JSON parse + manual extraction
        return PlannerOutput.from_json(response.text)

    def _get_all_profiles(self) -> list[dict]:
        """Retrieve all registered expert profiles."""
        all_profiles = self._registry.search_by_capability([])
        if not all_profiles:
            all_profiles = [self._registry.get(pid) for pid in self._registry.list_all()]
        return [p for p in all_profiles if p is not None]

    def _build_planning_prompt(self, expert_profiles: list[dict] | None = None) -> str:
        """Build system prompt with available expert info and JSON schema (N3).

        Parameters
        ----------
        expert_profiles:
            Optional pre-loaded profiles.  When ``None``, profiles are
            loaded from the registry.
        """
        all_profiles = expert_profiles if expert_profiles is not None else self._get_all_profiles()

        all_caps: set[str] = set()
        expert_summary: list[str] = []
        expert_ids: list[str] = []
        for profile in all_profiles:
            caps = profile.get("capabilities", [])
            all_caps.update(caps)
            name = profile.get("name", profile.get("id", "unknown"))
            pid = profile.get("id", "unknown")
            expert_ids.append(pid)
            desc = profile.get("description", "No description")
            expert_summary.append(f"- {pid} ({name}): {desc} — capabilities: {', '.join(caps)}")

        # N3: Include the Pydantic JSON schema for structured output
        schema = StructuredPlannerOutput.model_json_schema()

        prompt = StructuredPrompt()
        prompt.add(
            "角色定义",
            "You are a task decomposition specialist. Given a user task and a pool of "
            "available experts, analyze the task and determine which capabilities are "
            "required, and which experts should be selected.",
            priority=1,
        )
        prompt.add(
            "可用能力",
            f"Available capabilities: {', '.join(sorted(all_caps))}",
            priority=2,
        )
        prompt.add(
            "可用专家",
            "\n".join(expert_summary),
            priority=3,
        )
        prompt.add(
            "输出格式",
            "You MUST respond with ONLY a JSON object (no markdown fences) matching this schema:\n"
            f"{json.dumps(schema, indent=2)}\n\n"
            "Constraints:\n"
            f"- `expert_id` values must be one of: {expert_ids}\n"
            f"- `capabilities` values must come from: {', '.join(sorted(all_caps))}\n"
            '- `decomposition_strategy` must be "parallel" or "sequential"\n'
            'Use "parallel" unless the task clearly requires sequential execution.\n'
            "The `focus_hints` should guide each expert on what to focus on.\n"
            "The `expert_selections` list should contain the best experts for the task.",
            priority=2,
        )

        prompt.trim_to(_MAX_PLANNING_TOKENS, self._token_counter)
        return prompt.render()

    def _keyword_fallback(self, task: str) -> PlannerOutput:
        """Fall back to keyword-based capability inference."""
        capabilities = infer_capabilities(task)
        return PlannerOutput(
            capabilities=capabilities,
            decomposition_strategy="parallel",
        )
