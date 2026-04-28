"""LLMQualityGate — LLM-powered quality evaluation for agency artifacts.

Adds a semantic quality evaluation layer on top of the existing structural
:class:`QAGate`.  The LLM evaluates content relevance, depth, and completeness.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING

from .integrator import IntegratedArtifact
from .qa_gate import QAGate, QAGateInput, QAGateResult

if TYPE_CHECKING:
    from .llm_client import LLMClient

logger = logging.getLogger(__name__)

# Minimum score threshold for LLM QA pass
_PASS_THRESHOLD = 0.6


class LLMQualityGate:
    """LLM-powered quality evaluation replacing structural-only checks.

    Two-layer evaluation:
    1. **Structural** (always): Checks required sections exist and non-empty.
    2. **Semantic** (when LLM available): Evaluates content relevance, depth,
       and completeness against the original task.

    Falls back to structural-only when no LLM client is available.
    """

    _FALLBACK_COUNT = 0
    _fallback_lock = threading.Lock()

    def __init__(
        self,
        client: LLMClient | None = None,
        pass_threshold: float = _PASS_THRESHOLD,
        temperature: float | None = None,
    ) -> None:
        self._client = client
        self._pass_threshold = pass_threshold
        self._temperature = temperature

    @classmethod
    def fallback_count(cls) -> int:
        """Number of times any LLMQualityGate fell back to structural-only (monitoring)."""
        return cls._FALLBACK_COUNT

    def evaluate(
        self,
        integrated: IntegratedArtifact,
        task: str,
        required_sections: list[str] | None = None,
        task_type: str = "plan",
    ) -> QAGateResult:
        """Evaluate integrated output quality.

        Parameters
        ----------
        integrated:
            The integrated artifact from multiple experts.
        task:
            The original task description.
        required_sections:
            Sections that must be present (structural check).
        task_type:
            Task type for GitNexus gate check.

        Returns
        -------
        QAGateResult
            Pass/fail with detailed failures list.
        """
        # Layer 1: Structural check (always runs)
        sections = required_sections or []
        structural_input = QAGateInput(
            output={"sections": integrated.merged_sections},
            required_sections=sections,
            task_type=task_type,
        )
        structural_result = QAGate.run(structural_input)

        if not structural_result.passed:
            return structural_result

        # Layer 2: Semantic check (LLM)
        if self._client is None:
            logger.debug("LLMQualityGate: no LLM client, structural-only")
            with LLMQualityGate._fallback_lock:
                LLMQualityGate._FALLBACK_COUNT += 1
            return structural_result

        try:
            return self._llm_evaluate(integrated, task, structural_result)
        except Exception:
            logger.exception("LLMQualityGate: LLM call failed, structural-only")
            with LLMQualityGate._fallback_lock:
                LLMQualityGate._FALLBACK_COUNT += 1
            return structural_result

    def _llm_evaluate(
        self,
        integrated: IntegratedArtifact,
        task: str,
        structural_result: QAGateResult,
    ) -> QAGateResult:
        """Run LLM-based semantic evaluation."""
        sections_preview = "\n".join(
            f"  {k}: {str(v)[:200]}..."
            for k, v in list(integrated.merged_sections.items())[:10]
        )

        system_prompt = (
            "You are a quality assurance evaluator for expert analysis reports. "
            "Given the original task and the synthesized expert output, evaluate:\n"
            "1. Whether all aspects of the task are addressed\n"
            "2. Whether the depth of analysis is sufficient\n"
            "3. Whether the recommendations are actionable\n\n"
            "Respond with ONLY a JSON object:\n"
            "{\n"
            '  "passed": true/false,\n'
            '  "score": 0.0-1.0,\n'
            '  "issues": ["issue1", "issue2"],\n'
            '  "coverage": {\n'
            '    "task_addressed": true/false,\n'
            '    "depth_sufficient": true/false,\n'
            '    "recommendations_actionable": true/false\n'
            '  }\n'
            "}"
        )
        user_message = (
            f"Original task: {task}\n\n"
            f"Experts consulted: {', '.join(integrated.source_agents)}\n\n"
            f"Synthesized output:\n{sections_preview}"
        )

        response = self._client.call(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=self._temperature,
        )
        return self._parse_evaluation(response.text, structural_result)

    def _parse_evaluation(
        self,
        raw: str,
        structural_result: QAGateResult,
    ) -> QAGateResult:
        """Parse LLM evaluation response."""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("LLMQualityGate: failed to parse JSON, returning structural result")
            return structural_result

        score = data.get("score", 0.0)
        issues = data.get("issues", [])
        passed = data.get("passed", score >= self._pass_threshold)

        failures: list[str] = []
        if not passed:
            failures.append(
                f"LLM quality score: {score:.2f} (threshold: {self._pass_threshold})"
            )
            for issue in issues:
                failures.append(f"Quality issue: {issue}")

        # Merge with structural failures
        failures.extend(structural_result.failures)

        return QAGateResult(
            passed=passed and structural_result.passed,
            contract_result=structural_result.contract_result,
            gitnexus_result=structural_result.gitnexus_result,
            failures=failures,
        )
