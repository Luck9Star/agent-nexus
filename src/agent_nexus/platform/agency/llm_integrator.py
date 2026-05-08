"""LLMIntegrator — LLM-powered multi-expert artifact synthesis.

Replaces mechanical dict/list concatenation with semantic LLM synthesis.
Falls back to :class:`Integrator.merge()` when LLM is unavailable.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from .integrator import Artifact, ConflictItem, IntegratedArtifact, Integrator
from .json_parse import robust_json_parse
from .token_counter import StructuredPrompt, TokenCounter

if TYPE_CHECKING:
    from .llm_client import LLMClient

logger = logging.getLogger(__name__)

# Generous limit to accommodate large multi-expert synthesis prompts
# without truncating.  Most LLMs support 128K+ context; 120K leaves room
# for system prompts and response tokens.
_MAX_SYNTHESIS_TOKENS = 120_000


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

    def __init__(self, client: LLMClient | None = None, temperature: float | None = None) -> None:
        self._client = client
        self._temperature = temperature
        self._token_counter = TokenCounter()

    @classmethod
    def fallback_count(cls) -> int:
        """Number of times any LLMIntegrator fell back to rules (monitoring)."""
        with cls._fallback_lock:
            return cls._fallback_count

    @classmethod
    def reset_fallback_count(cls) -> None:
        """Reset the fallback counter (for test isolation)."""
        with cls._fallback_lock:
            cls._fallback_count = 0

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

        logger.info(
            "LLMIntegrator: synthesizing %d expert artifacts",
            len(artifacts),
        )

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
        assert self._client is not None  # guarded by synthesize
        system_prompt = self._build_synthesis_prompt(artifacts)
        user_message = (
            f"Original task: {task}\n\n"
            "Please synthesize the expert outputs above into a unified analysis."
        )

        response = self._client.call(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=self._temperature,
            response_format="json",
        )
        return self._parse_synthesis(response.text, artifacts)

    def _build_synthesis_prompt(self, artifacts: list[Artifact]) -> str:
        """Build system prompt with all expert outputs.

        Uses StructuredPrompt with priority-based section trimming to fit
        within ``_MAX_SYNTHESIS_TOKENS`` instead of per-expert char truncation.
        """
        prompt = StructuredPrompt()
        prompt.add(
            "角色定义",
            "You are a synthesis specialist. Multiple experts have analyzed a task "
            "and provided their findings. Your job is to:\n"
            "1. Combine their insights into a coherent summary\n"
            "2. Resolve any conflicting recommendations with reasoning\n"
            "3. Identify gaps or blind spots in the expert analyses\n"
            "4. Produce a unified set of recommendations",
            priority=1,
        )

        for art in artifacts:
            sections_str = "\n".join(f"  {k}: {v}" for k, v in art.sections.items())
            expert_block = f"Expert: {art.source_agent}\n{sections_str}"
            prompt.add(f"Expert output: {art.source_agent}", expert_block, priority=3)

        prompt.add(
            "输出格式",
            "Respond with ONLY a JSON object:\n"
            "{\n"
            '  "summary": "unified summary",\n'
            '  "recommendations": ["rec1", "rec2"],\n'
            '  "conflicts": [{"field": "...", "description": "...", "resolution": "..."}],\n'
            '  "gaps": ["gap1"],\n'
            '  "risks": ["risk1"]\n'
            "}",
            priority=2,
        )

        prompt.trim_to(_MAX_SYNTHESIS_TOKENS, self._token_counter)
        return prompt.render()

    def _parse_synthesis(
        self,
        raw: str,
        artifacts: list[Artifact],
    ) -> IntegratedArtifact:
        """Parse LLM synthesis response into IntegratedArtifact."""
        source_agents = [a.source_agent for a in artifacts]

        data = robust_json_parse(raw)
        if data is None:
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

        merged_sections["decision_summary"] = f"LLM-synthesized {len(artifacts)} expert outputs"

        conflicts = []
        for c in data.get("conflicts", []):
            conflicts.append(
                ConflictItem(
                    field=c.get("field", "unknown"),
                    description=c.get("description", ""),
                    agents=source_agents,
                )
            )

        result = IntegratedArtifact(
            source_agents=source_agents,
            merged_sections=merged_sections,
            conflicts=conflicts,
            risks=data.get("risks", []),
            open_questions=data.get("gaps", []),
        )
        logger.info(
            "LLMIntegrator: synthesis complete — %d sections, %d conflicts, %d risks",
            len(result.merged_sections),
            len(result.conflicts),
            len(result.risks),
        )
        return result
