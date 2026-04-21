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

import logging

logger = logging.getLogger(__name__)

from dataclasses import dataclass, field
from typing import Any

from agent_nexus.models.evolution import (
    EvolutionContext,
    EvolutionType,
    SkillRecord,
)
from agent_nexus.platform.evolution.health import build_health_suggestions
from agent_nexus.platform.evolution.store import EvolutionStore
from agent_nexus.platform.evolution.thresholds import (
    EvolutionSuggestion,  # re-exported for backward compatibility
    SkillRates,
    evaluate_skill_health,
)


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
    same name prefix (before ``__``) and within a scaled edit distance
    threshold based on suffix length (1-3).
    """
    if not known_ids:
        return ids

    corrected: list[str] = []
    for raw_id in ids:
        if raw_id in known_ids:
            corrected.append(raw_id)
            continue

        if "__" not in raw_id:
            # No prefix separator — can't narrow candidates safely, skip
            corrected.append(raw_id)
            continue

        prefix = raw_id.split("__")[0]
        pfx = prefix + "__"
        candidates = [k for k in known_ids if k.startswith(pfx)]

        # Scale max_dist with suffix length to avoid loose matches on short IDs
        # e.g. "x__ab" matching "x__wxyz" at distance 4 is clearly wrong.
        _suffix = raw_id[len(pfx):]
        _suffix_len = len(_suffix)
        if len(candidates) > 20:
            max_dist = 2
        elif _suffix_len <= 4:
            max_dist = 1
        elif _suffix_len <= 8:
            max_dist = 2
        else:
            max_dist = 3
        best: str | None = None
        best_dist = max_dist + 1
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
        applied_set = set(ctx.skills_applied)
        fell_back_set = set(ctx.skills_fell_back)
        judgments: list[dict[str, Any]] = []
        for skill_id in corrected_ids:
            skill = skills_by_id.get(skill_id)
            if skill is None:
                continue

            # Determine judgment based on per-skill outcome
            applied = skill_id in applied_set
            fell_back = skill_id in fell_back_set
            completed = applied and ctx.task_completed

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
            rates = SkillRates.from_record(skill)
            if rates is None:
                continue

            eval_result = evaluate_skill_health(rates)
            skill_suggestions = build_health_suggestions(
                skill_id=skill_id,
                rates=rates,
                eval_result=eval_result,
            )
            suggestions.extend(skill_suggestions)

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

        # Deduplicate: same (evolution_type, skill_id) can trigger from
        # multiple thresholds — keep the one with highest confidence.
        seen: dict[tuple[EvolutionType, str], EvolutionSuggestion] = {}
        for s in suggestions:
            dedup_id = s.target_skill_ids[0] if s.target_skill_ids else ""
            key = (s.evolution_type, dedup_id)
            if key not in seen or s.confidence > seen[key].confidence:
                seen[key] = s
        return list(seen.values())

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
