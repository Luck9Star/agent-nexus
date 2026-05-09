"""LLMQualityGate — LLM-powered quality evaluation for agency artifacts.

Adds a semantic quality evaluation layer on top of the existing structural
:class:`QAGate`.  The LLM evaluates content relevance, depth, and completeness.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from .integrator import IntegratedArtifact
from .json_parse import robust_json_parse
from .qa_gate import QAGate, QAGateInput, QAGateResult
from .token_counter import StructuredPrompt, TokenCounter

if TYPE_CHECKING:
    from .llm_client import LLMClient

logger = logging.getLogger(__name__)

# Minimum score threshold for LLM QA pass
_PASS_THRESHOLD = 0.6
_MAX_EVAL_TOKENS = 50_000


class LLMQualityGate:
    """LLM-powered quality evaluation replacing structural-only checks.

    Two-layer evaluation:
    1. **Structural** (always): Checks required sections exist and non-empty.
    2. **Semantic** (when LLM available): Evaluates content relevance, depth,
       and completeness against the original task.

    Falls back to structural-only when no LLM client is available.
    """

    # Class-level: tracks fallbacks across ALL instances for monitoring/reset via tests.
    _fallback_count = 0
    _fallback_lock = threading.Lock()

    def __init__(
        self,
        client: LLMClient | None = None,
        pass_threshold: float = _PASS_THRESHOLD,
        structural_trust_floor: float = 0.5,
        temperature: float | None = None,
    ) -> None:
        self._client = client
        self._pass_threshold = pass_threshold
        self._structural_trust_floor = structural_trust_floor
        self._temperature = temperature
        self._token_counter = TokenCounter()

    @classmethod
    def fallback_count(cls) -> int:
        """Number of times any LLMQualityGate fell back to structural-only (monitoring)."""
        return cls._fallback_count

    @classmethod
    def reset_fallback_count(cls) -> None:
        """Reset the fallback counter (for test isolation)."""
        with cls._fallback_lock:
            cls._fallback_count = 0

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
            logger.info(
                "LLMQualityGate: evaluation result — passed=%s, failures=%d",
                structural_result.passed,
                len(structural_result.failures),
            )
            return structural_result

        # Layer 2: Semantic check (LLM)
        if self._client is None:
            logger.debug("LLMQualityGate: no LLM client, structural-only")
            with LLMQualityGate._fallback_lock:
                LLMQualityGate._fallback_count += 1
            return structural_result

        try:
            result = self._llm_evaluate(integrated, task, structural_result)
            logger.info(
                "LLMQualityGate: evaluation result — passed=%s, failures=%d",
                result.passed,
                len(result.failures),
            )
            return result
        except Exception:
            logger.exception("LLMQualityGate: LLM call failed, structural-only")
            with LLMQualityGate._fallback_lock:
                LLMQualityGate._fallback_count += 1
            return structural_result

    def _llm_evaluate(
        self,
        integrated: IntegratedArtifact,
        task: str,
        structural_result: QAGateResult,
    ) -> QAGateResult:
        """Run LLM-based semantic evaluation."""
        assert self._client is not None  # guarded by evaluate
        sections_preview = "\n".join(
            f"  {k}: {str(v)[:200]}..." for k, v in list(integrated.merged_sections.items())[:10]
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
            "  }\n"
            "}"
        )

        user_prompt = StructuredPrompt()
        user_prompt.add("原始任务", f"Original task: {task}", priority=1)
        user_prompt.add(
            "专家列表",
            f"Experts consulted: {', '.join(integrated.source_agents)}",
            priority=2,
        )
        user_prompt.add("综合输出", f"Synthesized output:\n{sections_preview}", priority=5)

        user_prompt.trim_to(_MAX_EVAL_TOKENS, self._token_counter)
        user_message = user_prompt.render()

        response = self._client.call(
            system_prompt=system_prompt,
            user_message=user_message,
            temperature=self._temperature,
            response_format="json",
        )
        return self._parse_evaluation(response.text, structural_result)

    def _parse_evaluation(
        self,
        raw: str,
        structural_result: QAGateResult,
    ) -> QAGateResult:
        """Parse LLM evaluation response."""
        data = robust_json_parse(raw)
        if data is None:
            logger.warning("LLMQualityGate: failed to parse JSON, returning structural result")
            return structural_result

        score = data.get("score", 0.0)
        issues = data.get("issues", [])
        passed = data.get("passed", score >= self._pass_threshold)

        # Structural trust override: if structural check passed and the LLM
        # score is within striking distance of the threshold (>= 0.5), trust
        # the structural result.  Prevents false negatives from weaker
        # evaluator models that under-score genuinely adequate content.
        # Scores < 0.5 still fail — the LLM clearly flagged bad content.
        trust_floor = self._structural_trust_floor
        if not passed and structural_result.passed and score >= trust_floor:
            logger.info(
                "LLMQualityGate: structural trust override — LLM score %.2f "
                "below threshold %.2f but structural check passed",
                score,
                self._pass_threshold,
            )
            passed = True

        failures: list[str] = []
        if not passed:
            failures.append(f"LLM quality score: {score:.2f} (threshold: {self._pass_threshold})")
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
