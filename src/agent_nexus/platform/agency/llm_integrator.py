"""LLMIntegrator — LLM-powered multi-expert artifact synthesis.

Replaces mechanical dict/list concatenation with semantic LLM synthesis.
Falls back to :class:`Integrator.merge()` when LLM is unavailable.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING

from .integrator import Artifact, ConflictItem, IntegratedArtifact, Integrator

if TYPE_CHECKING:
    from .llm_client import LLMClient

logger = logging.getLogger(__name__)

_MAX_SYNTHESIS_PROMPT_CHARS = 50_000


class LLMIntegrator:
    """LLM-powered artifact synthesis replacing mechanical merge.

    Uses an LLM to:
    1. Understand semantic content of each expert's output
    2. Resolve conflicts with reasoning (not just severity comparison)
    3. Generate a coherent unified report

    Falls back to :class:`Integrator.merge()` when no LLM client is
    available or the LLM call fails.
    """

    _fallback_count = 0
    _fallback_lock = threading.Lock()

    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client

    @classmethod
    def fallback_count(cls) -> int:
        """Number of times any LLMIntegrator fell back to rules (monitoring)."""
        with cls._fallback_lock:
            return cls._fallback_count

    def synthesize(
        self,
        artifacts: list[Artifact],
        task: str,
    ) -> IntegratedArtifact:
        """Synthesize multi-expert artifacts into a unified output.

        Parameters
        ----------
        artifacts:
            List of expert artifacts to synthesize.
        task:
            The original task description (for context).

        Returns
        -------
        IntegratedArtifact
            Unified output with synthesized content.
        """
        if not artifacts:
            raise ValueError("Need at least one artifact to synthesize")

        # Single artifact: no synthesis needed, direct pass-through
        if len(artifacts) == 1:
            art = artifacts[0]
            return IntegratedArtifact(
                source_agents=[art.source_agent],
                merged_sections=art.sections,
            )

        if self._client is None:
            logger.debug("LLMIntegrator: no LLM client, falling back to rules")
            with LLMIntegrator._fallback_lock:
                LLMIntegrator._fallback_count += 1
            return Integrator.merge(artifacts)

        try:
            return self._llm_synthesize(artifacts, task)
        except Exception:
            logger.exception("LLMIntegrator: LLM call failed, falling back to rules")
            with LLMIntegrator._fallback_lock:
                LLMIntegrator._fallback_count += 1
            return Integrator.merge(artifacts)

    def _llm_synthesize(
        self,
        artifacts: list[Artifact],
        task: str,
    ) -> IntegratedArtifact:
        """Perform LLM-based synthesis."""
        system_prompt = self._build_synthesis_prompt(artifacts)
        user_message = (
            f"Original task: {task}\n\n"
            "Please synthesize the expert outputs above into a unified analysis."
        )

        response = self._client.call(
            system_prompt=system_prompt,
            user_message=user_message,
        )
        return self._parse_synthesis(response.text, artifacts)

    def _build_synthesis_prompt(self, artifacts: list[Artifact]) -> str:
        """Build system prompt with all expert outputs.

        Truncates individual expert sections if the total would exceed
        ``_MAX_SYNTHESIS_PROMPT_CHARS`` characters.
        """
        expert_outputs: list[str] = []
        per_expert_budget = _MAX_SYNTHESIS_PROMPT_CHARS // max(len(artifacts), 1)

        for art in artifacts:
            sections_str = "\n".join(
                f"  {k}: {v}" for k, v in art.sections.items()
            )
            expert_block = (
                f"Expert: {art.source_agent}\n{sections_str}"
            )
            if len(expert_block) > per_expert_budget:
                expert_block = expert_block[:per_expert_budget] + "\n[...truncated]"
                logger.warning(
                    "LLMIntegrator: truncated expert '%s' output to %d chars",
                    art.source_agent, per_expert_budget,
                )
            expert_outputs.append(expert_block)

        prompt_body = "\n\n".join(expert_outputs)

        return (
            "You are a synthesis specialist. Multiple experts have analyzed a task "
            "and provided their findings. Your job is to:\n"
            "1. Combine their insights into a coherent summary\n"
            "2. Resolve any conflicting recommendations with reasoning\n"
            "3. Identify gaps or blind spots in the expert analyses\n"
            "4. Produce a unified set of recommendations\n\n"
            "Expert outputs:\n\n"
            + prompt_body
            + "\n\nRespond with ONLY a JSON object:\n"
            "{\n"
            '  "summary": "unified summary",\n'
            '  "recommendations": ["rec1", "rec2"],\n'
            '  "conflicts": [{"field": "...", "description": "...", "resolution": "..."}],\n'
            '  "gaps": ["gap1"],\n'
            '  "risks": ["risk1"]\n'
            "}"
        )

    def _parse_synthesis(
        self,
        raw: str,
        artifacts: list[Artifact],
    ) -> IntegratedArtifact:
        """Parse LLM synthesis response into IntegratedArtifact."""
        source_agents = [a.source_agent for a in artifacts]

        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("LLMIntegrator: failed to parse JSON, using raw text")
            return IntegratedArtifact(
                source_agents=source_agents,
                merged_sections={"synthesis": raw},
            )

        merged_sections: dict[str, object] = {}
        if "summary" in data:
            merged_sections["summary"] = data["summary"]
        if "recommendations" in data:
            merged_sections["recommendations"] = data["recommendations"]

        # Preserve original expert sections as sub-keys
        for art in artifacts:
            prefix = art.source_agent.split(".")[-1]
            for key, value in art.sections.items():
                merged_sections[f"{prefix}.{key}"] = value

        merged_sections["decision_summary"] = (
            f"LLM-synthesized {len(artifacts)} expert outputs"
        )

        conflicts = []
        for c in data.get("conflicts", []):
            conflicts.append(ConflictItem(
                field=c.get("field", "unknown"),
                description=c.get("description", ""),
                agents=source_agents,
            ))

        return IntegratedArtifact(
            source_agents=source_agents,
            merged_sections=merged_sections,
            conflicts=conflicts,
            risks=data.get("risks", []),
            open_questions=data.get("gaps", []),
        )
