"""ExecutionAnalyzer -- post-task analysis and skill quality evaluation.

Responsibilities:
  1. After each task execution, evaluate which skills were selected/applied.
  2. Generate evolution suggestions based on quality metrics.
  3. Provide fuzzy matching for LLM-hallucinated skill IDs.

Integration:
  Called after task completion to produce analysis + judgments,
  persisted via EvolutionStore.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_nexus.models.evolution import (
    EvolutionContext,
    EvolutionType,
    SkillRecord,
)
from agent_nexus.platform.evolution.store import EvolutionStore


@dataclass
class EvolutionSuggestion:
    """One evolution action suggested by the analyzer."""

    evolution_type: EvolutionType
    target_skill_ids: list[str] = field(default_factory=list)
    direction: str = ""
    confidence: float = 0.0


@dataclass
class AnalysisResult:
    """Result of analyzing a task execution."""

    task_id: str
    agent_name: str
    analysis_text: str
    suggestions: list[EvolutionSuggestion] = field(default_factory=list)
    judgments: list[dict[str, Any]] = field(default_factory=list)
    analysis_id: str = ""


def _edit_distance(a: str, b: str) -> int:
    """Levenshtein edit distance (compact DP, O(min(m,n)) space)."""
    if len(a) < len(b):
        a, b = b, a
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            curr[j] = min(
                prev[j] + 1,
                curr[j - 1] + 1,
                prev[j - 1] + (0 if ca == cb else 1),
            )
        prev = curr
    return prev[-1]


def _correct_skill_ids(
    ids: list[str],
    known_ids: set[str],
) -> list[str]:
    """Best-effort correction of LLM-hallucinated skill IDs.

    For each ID not in known_ids, find the closest known ID sharing the
    same name prefix (before ``__``) and within edit distance <= 3.
    """
    if not known_ids:
        return ids

    corrected: list[str] = []
    for raw_id in ids:
        if raw_id in known_ids:
            corrected.append(raw_id)
            continue

        prefix = raw_id.split("__")[0] if "__" in raw_id else ""

        candidates = [
            k for k in known_ids
            if prefix and k.split("__")[0] == prefix
        ]

        max_dist = 2 if len(candidates) > 20 else 4
        best: str | None = None
        best_dist = max_dist
        ambiguous = False

        for cand in candidates:
            d = _edit_distance(raw_id, cand)
            if d < best_dist:
                best, best_dist, ambiguous = cand, d, False
            elif d == best_dist and cand != best:
                ambiguous = True

        if best is not None and not ambiguous:
            corrected.append(best)
        else:
            corrected.append(raw_id)

    return corrected


class ExecutionAnalyzer:
    """Analyzes task execution results and tracks skill quality.

    Args:
        store: Persistence layer for skill records and analyses.
    """

    def __init__(self, store: EvolutionStore) -> None:
        self._store = store

    def analyze_execution(self, ctx: EvolutionContext) -> AnalysisResult:
        """Analyze a completed task execution and produce evolution suggestions.

        Evaluates which skills were selected/applied/completed, computes
        quality metrics, and generates evolution suggestions based on
        health thresholds.

        Args:
            ctx: Evolution context with task and skill usage info.

        Returns:
            AnalysisResult with suggestions and judgments.
        """
        # Gather known skill IDs for fuzzy matching
        active_skills = self._store.get_active_skills()
        known_ids = {s.id for s in active_skills}
        skills_by_id = {s.id: s for s in active_skills}

        # Correct any hallucinated skill IDs from the context
        corrected_ids = _correct_skill_ids(
            ctx.skill_ids_used, known_ids
        )

        # Build judgments for each skill referenced in the task
        judgments: list[dict[str, Any]] = []
        for skill_id in corrected_ids:
            skill = skills_by_id.get(skill_id)
            if skill is None:
                continue

            # Determine judgment based on task outcome
            applied = ctx.task_completed
            completed = ctx.task_completed and applied
            fell_back = not applied and not ctx.task_completed

            judgments.append({
                "skill_id": skill_id,
                "selected": True,
                "applied": applied,
                "completed": completed,
                "fell_back": fell_back,
            })

        # Generate evolution suggestions
        suggestions = self._generate_suggestions(
            corrected_ids, skills_by_id, ctx
        )

        # Build analysis text
        analysis_text = self._build_analysis_text(ctx, judgments, suggestions)

        # Persist the analysis
        analysis_id = self._store.record_analysis(
            task_id=ctx.task_id,
            agent_name=ctx.agent_id,
            analysis_text=analysis_text,
            evolution_suggestions=[
                {
                    "type": s.evolution_type.value,
                    "target_skill_ids": s.target_skill_ids,
                    "direction": s.direction,
                    "confidence": s.confidence,
                }
                for s in suggestions
            ],
            judgments=judgments,
        )

        return AnalysisResult(
            task_id=ctx.task_id,
            agent_name=ctx.agent_id,
            analysis_text=analysis_text,
            suggestions=suggestions,
            judgments=judgments,
            analysis_id=analysis_id,
        )

    def _generate_suggestions(
        self,
        skill_ids: list[str],
        skills_by_id: dict[str, SkillRecord],
        ctx: EvolutionContext,
    ) -> list[EvolutionSuggestion]:
        """Generate evolution suggestions based on health metrics."""
        suggestions: list[EvolutionSuggestion] = []

        for skill_id in skill_ids:
            skill = skills_by_id.get(skill_id)
            if skill is None:
                continue

            # Compute rates
            sel = skill.total_selections
            if sel == 0:
                continue

            fallback_rate = skill.total_fallbacks / sel
            applied_rate = skill.total_applied / sel
            completion_rate = (
                skill.total_completions / skill.total_applied
                if skill.total_applied > 0
                else 0.0
            )
            effective_rate = skill.total_completions / sel

            # Threshold checks from docs/04
            if fallback_rate > 0.4:
                suggestions.append(EvolutionSuggestion(
                    evolution_type=EvolutionType.FIX,
                    target_skill_ids=[skill_id],
                    direction=(
                        f"High fallback rate ({fallback_rate:.0%}): "
                        f"skill is frequently selected but not applied"
                    ),
                    confidence=min(fallback_rate, 1.0),
                ))

            if applied_rate > 0.4 and completion_rate < 0.35:
                suggestions.append(EvolutionSuggestion(
                    evolution_type=EvolutionType.FIX,
                    target_skill_ids=[skill_id],
                    direction=(
                        f"Low completion rate ({completion_rate:.0%}) "
                        f"despite high applied rate ({applied_rate:.0%})"
                    ),
                    confidence=min(applied_rate * (1 - completion_rate), 1.0),
                ))

            if effective_rate < 0.55 and applied_rate > 0.25:
                suggestions.append(EvolutionSuggestion(
                    evolution_type=EvolutionType.DERIVED,
                    target_skill_ids=[skill_id],
                    direction=(
                        f"Moderate effectiveness ({effective_rate:.0%}): "
                        f"could be enhanced with better error handling"
                    ),
                    confidence=min(1.0 - effective_rate, 1.0),
                ))

        # CAPTURED suggestion if task succeeded with no skills involved
        if ctx.task_completed and not skill_ids:
            suggestions.append(EvolutionSuggestion(
                evolution_type=EvolutionType.CAPTURED,
                target_skill_ids=[],
                direction=(
                    f"Successful task with no skills used — "
                    f"pattern may be worth capturing"
                ),
                confidence=0.6,
            ))

        return suggestions

    def _build_analysis_text(
        self,
        ctx: EvolutionContext,
        judgments: list[dict[str, Any]],
        suggestions: list[EvolutionSuggestion],
    ) -> str:
        """Build a human-readable analysis text."""
        parts: list[str] = []

        parts.append(f"Task: {ctx.task_id}")
        parts.append(f"Agent: {ctx.agent_id}")
        parts.append(f"Completed: {ctx.task_completed}")

        if ctx.task_description:
            parts.append(f"Description: {ctx.task_description}")

        if judgments:
            parts.append("\nSkill Judgments:")
            for j in judgments:
                parts.append(
                    f"  - {j['skill_id']}: "
                    f"applied={j['applied']}, completed={j['completed']}, "
                    f"fell_back={j['fell_back']}"
                )

        if suggestions:
            parts.append("\nEvolution Suggestions:")
            for s in suggestions:
                targets = ", ".join(s.target_skill_ids) or "(new)"
                parts.append(
                    f"  - [{s.evolution_type.value}] {targets}: "
                    f"{s.direction}"
                )

        if ctx.execution_error:
            parts.append(f"\nExecution Error: {ctx.execution_error}")

        return "\n".join(parts)

    @property
    def store(self) -> EvolutionStore:
        """Access the underlying EvolutionStore."""
        return self._store
