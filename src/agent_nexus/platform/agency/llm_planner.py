"""LLMPlanner — LLM-powered task decomposition for the agency pipeline.

Replaces keyword-based ``infer_capabilities()`` with semantic task analysis.
Falls back to keyword matching when LLM is unavailable.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .task_composer import infer_capabilities

if TYPE_CHECKING:
    from .llm_client import LLMClient
    from .registry import ExpertRegistry

logger = logging.getLogger(__name__)


@dataclass
class PlannerOutput:
    """Structured output from task decomposition."""

    capabilities: list[str] = field(default_factory=list)
    focus_hints: dict[str, str] = field(default_factory=dict)
    decomposition_strategy: str = "parallel"
    """Either ``"parallel"`` or ``"sequential"``."""

    @classmethod
    def from_json(cls, raw: str) -> PlannerOutput:
        """Parse LLM JSON response into PlannerOutput.

        Returns a default (empty) PlannerOutput on parse failure.
        """
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("LLMPlanner: failed to parse JSON response, returning empty output")
            return cls()

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

    @classmethod
    def fallback_count(cls) -> int:
        """Number of times any LLMPlanner fell back to keywords (monitoring)."""
        with cls._fallback_lock:
            return cls._fallback_count

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
            return self._keyword_fallback(task)

        try:
            return self._llm_analyze(task)
        except Exception:
            logger.exception("LLMPlanner: LLM call failed, falling back to keywords")
            with LLMPlanner._fallback_lock:
                LLMPlanner._fallback_count += 1
            return self._keyword_fallback(task)

    def _llm_analyze(self, task: str) -> PlannerOutput:
        """Perform LLM-based task analysis."""
        system_prompt = self._build_planning_prompt()
        response = self._client.call(
            system_prompt=system_prompt,
            user_message=task,
            temperature=self._temperature,
        )
        return PlannerOutput.from_json(response.text)

    def _build_planning_prompt(self) -> str:
        """Build system prompt with available expert info."""
        all_profiles = self._registry.search_by_capability([])
        if not all_profiles:
            all_profiles = [
                self._registry.get(pid)
                for pid in self._registry.list_all()
            ]
        all_profiles = [p for p in all_profiles if p is not None]

        all_caps: set[str] = set()
        expert_summary: list[str] = []
        for profile in all_profiles:
            caps = profile.get("capabilities", [])
            all_caps.update(caps)
            name = profile.get("name", profile.get("id", "unknown"))
            expert_summary.append(
                f"- {name}: {', '.join(caps)}"
            )

        return (
            "You are a task decomposition specialist. Given a user task and a pool of "
            "available experts, analyze the task and determine which capabilities are "
            "required.\n\n"
            f"Available capabilities: {', '.join(sorted(all_caps))}\n\n"
            f"Available experts:\n" + "\n".join(expert_summary) + "\n\n"
            "Respond with ONLY a JSON object (no markdown fences):\n"
            "{\n"
            '  "capabilities": ["cap1", "cap2"],\n'
            '  "focus_hints": {"expert-id": "specific focus area"},\n'
            '  "decomposition_strategy": "parallel" or "sequential"\n'
            "}\n\n"
            "The capabilities must come from the available capabilities list above. "
            "The focus_hints should guide each expert on what to focus on. "
            "Use \"parallel\" unless the task clearly requires sequential execution."
        )

    def _keyword_fallback(self, task: str) -> PlannerOutput:
        """Fall back to keyword-based capability inference."""
        capabilities = infer_capabilities(task)
        return PlannerOutput(
            capabilities=capabilities,
            decomposition_strategy="parallel",
        )
