"""Hybrid Reflector: rule-based (fast) + LLM (fine-grained) evaluation.

Determines whether a task result is sufficient or needs further iteration.
Rule layer runs first for cheap, deterministic checks; LLM layer provides
deeper quality assessment when rules have no opinion.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from .llm_client import LLMClient

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Reflection:
    """Result of evaluating whether a task result is sufficient."""

    sufficient: bool
    reason: str
    feedback: str = ""  # Improvement suggestions when insufficient
    next_queries: list[str] = field(default_factory=list)  # Next exploration directions


# ---------------------------------------------------------------------------
# Rule protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class ReflectionRule(Protocol):
    """Fast rule-based evaluation protocol."""

    def check(self, task: str, result: str, *, attempt: int = 0) -> Reflection | None:
        """Return None if rule has no opinion (pass to next layer)."""
        ...


# ---------------------------------------------------------------------------
# Built-in rules
# ---------------------------------------------------------------------------


class EmptyResultRule:
    """Catches empty or very short results."""

    def check(self, task: str, result: str, *, attempt: int = 0) -> Reflection | None:  # noqa: ARG002
        if not result or len(result.strip()) < 50:
            return Reflection(
                sufficient=False,
                reason="结果为空或过短",
                feedback="请提供更详细的内容，至少包含完整的分析和建议。",
            )
        return None


class MaxIterationRule:
    """Forces pass when max iterations reached."""

    def __init__(self, max_iterations: int) -> None:
        self._max = max_iterations

    def check(self, task: str, result: str, *, attempt: int = 0) -> Reflection | None:  # noqa: ARG002
        if attempt >= self._max:
            return Reflection(
                sufficient=True,
                reason=f"已达最大迭代次数 ({self._max})，强制通过",
            )
        return None


# ---------------------------------------------------------------------------
# LLM-based reflector
# ---------------------------------------------------------------------------


class LLMReflector:
    """LLM-driven fine-grained evaluation."""

    def __init__(self, client: LLMClient, max_iterations: int = 3) -> None:
        self._client = client
        self._max_iterations = max_iterations

    def evaluate(
        self,
        task: str,
        result: str,
        attempt: int,
        max_iterations: int | None = None,
    ) -> Reflection:
        """Use LLM to evaluate result sufficiency."""
        effective_max = max_iterations if max_iterations is not None else self._max_iterations
        system_prompt = (
            "You are a quality evaluator. Given a task and its result, determine "
            "if the result sufficiently addresses the task.\n\n"
            "Respond with ONLY a JSON object:\n"
            '{"sufficient": true/false, "reason": "...", '
            '"feedback": "improvement suggestions if insufficient", '
            '"next_queries": ["search direction 1", "direction 2"]}'
        )
        user_message = (
            f"Task: {task}\n\n"
            f"Result:\n{result}\n\n"
            f"Attempt: {attempt}/{effective_max}\n\n"
            "Is this result sufficient? Evaluate completeness, accuracy, and depth."
        )
        try:
            response = self._client.call(
                system_prompt=system_prompt,
                user_message=user_message,
                response_format="json",
            )
            return self._parse_reflection(response.text)
        except Exception:
            # LLM failure -> default to sufficient (don't block pipeline)
            return Reflection(sufficient=True, reason="LLM evaluation failed, defaulting to pass")

    def _parse_reflection(self, text: str) -> Reflection:
        """Parse LLM JSON response into Reflection."""
        try:
            # Strip markdown fences if present
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned.rsplit("```", 1)[0]
            data = json.loads(cleaned)
            return Reflection(
                sufficient=bool(data.get("sufficient", True)),
                reason=str(data.get("reason", "")),
                feedback=str(data.get("feedback", "")),
                next_queries=data.get("next_queries", []),
            )
        except (json.JSONDecodeError, KeyError):
            return Reflection(
                sufficient=True,
                reason="Failed to parse LLM reflection response",
            )


# ---------------------------------------------------------------------------
# Main Reflector (hybrid)
# ---------------------------------------------------------------------------


class Reflector:
    """Hybrid reflector: rules (fast) + LLM (fine-grained)."""

    def __init__(
        self,
        rules: list[ReflectionRule] | None = None,
        llm: LLMReflector | None = None,
        max_iterations: int = 3,
    ) -> None:
        self._rules = rules or []
        self._llm = llm
        self.max_iterations = max_iterations

    def evaluate(self, task: str, result: str, attempt: int = 1) -> Reflection:
        """Evaluate result sufficiency using rules first, then LLM."""
        # Rule layer: fast checks
        for rule in self._rules:
            try:
                verdict = rule.check(task, result, attempt=attempt)
                if verdict is not None:
                    return verdict
            except Exception:
                continue  # Rule failure -> skip, try next

        # LLM layer: fine-grained
        if self._llm:
            return self._llm.evaluate(task, result, attempt, self.max_iterations)

        # No rules failed, no LLM -> default pass
        return Reflection(sufficient=True, reason="No rules failed and no LLM configured")

    @classmethod
    def create_default(cls, client: LLMClient | None = None, max_iterations: int = 3) -> Reflector:
        """Factory: create with built-in rules + optional LLM."""
        rules: list[ReflectionRule] = [
            EmptyResultRule(),
            MaxIterationRule(max_iterations),
        ]
        llm = LLMReflector(client, max_iterations) if client else None
        return cls(rules=rules, llm=llm, max_iterations=max_iterations)
