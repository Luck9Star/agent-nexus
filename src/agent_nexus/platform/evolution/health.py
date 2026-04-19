"""HealthChecker -- threshold-based evolution trigger diagnostics.

Health diagnostic thresholds from docs/04:
  - fallback_rate > 0.4 -> FIX
  - applied_rate > 0.4 AND completion_rate < 0.35 -> FIX
  - effective_rate < 0.55 AND applied_rate > 0.25 -> DERIVED

Rule-engine pre-filter, LLM does final confirmation (in SkillEvolver).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from dataclasses import dataclass
from typing import Any

from agent_nexus.models.evolution import (
    EvolutionType,
    SkillRecord,
)
from agent_nexus.platform.evolution.store import EvolutionStore
from agent_nexus.platform.evolution.analyzer import EvolutionSuggestion
from agent_nexus.platform.evolution.thresholds import (
    _FALLBACK_THRESHOLD,
    _HIGH_APPLIED_FOR_FIX,
    _LOW_COMPLETION_THRESHOLD,
    _MODERATE_EFFECTIVE_THRESHOLD,
    _MIN_APPLIED_FOR_DERIVED,
)


@dataclass
class HealthReport:
    """Health diagnostic for a single skill."""

    skill_id: str
    skill_name: str
    is_healthy: bool
    suggestions: list[EvolutionSuggestion]
    metrics: dict[str, float]

    def summary(self) -> str:
        """Human-readable summary."""
        status = "HEALTHY" if self.is_healthy else "UNHEALTHY"
        parts = [f"[{status}] {self.skill_name} ({self.skill_id})"]
        for key, val in self.metrics.items():
            if key.endswith("_rate"):
                parts.append(f"  {key}: {val:.2%}")
            else:
                parts.append(f"  {key}: {val}")
        for s in self.suggestions:
            targets = ", ".join(s.target_skill_ids) or "(new)"
            parts.append(
                f"  -> [{s.evolution_type.value}] {targets}: {s.direction}"
            )
        return "\n".join(parts)


class HealthChecker:
    """Threshold-based health diagnostics for skill records.

    Provides:
      - check_health(skill): evaluate a single skill
      - diagnose_all(): evaluate all active skills
      - get_unhealthy(): return only unhealthy skills with suggestions

    Args:
        store: EvolutionStore for reading skill records.
    """

    def __init__(self, store: EvolutionStore) -> None:
        self._store = store

    def check_health(
        self, skill_record: SkillRecord
    ) -> list[EvolutionSuggestion]:
        """Evaluate health of a single skill and return suggestions.

        Applies the three threshold rules from docs/04:

        1. fallback_rate > 0.4 -> FIX
        2. applied_rate > 0.4 AND completion_rate < 0.35 -> FIX
        3. effective_rate < 0.55 AND applied_rate > 0.25 -> DERIVED

        Args:
            skill_record: The skill to evaluate.

        Returns:
            List of evolution suggestions (empty if healthy).
        """
        suggestions: list[EvolutionSuggestion] = []
        sel = skill_record.total_selections

        if sel == 0:
            return suggestions

        fallback_rate = skill_record.total_fallbacks / sel
        applied_rate = skill_record.total_applied / sel
        completion_rate = (
            skill_record.total_completions / skill_record.total_applied
            if skill_record.total_applied > 0
            else 0.0
        )
        effective_rate = skill_record.total_completions / sel

        # Track best FIX suggestion (deduplicate: keep highest confidence)
        best_fix: EvolutionSuggestion | None = None

        # Rule 1: High fallback rate
        if fallback_rate > _FALLBACK_THRESHOLD:
            fix1 = EvolutionSuggestion(
                evolution_type=EvolutionType.FIX,
                target_skill_ids=[skill_record.id],
                direction=(
                    f"High fallback rate ({fallback_rate:.0%}): "
                    f"skill is frequently selected but not applied, "
                    f"suggesting instructions are unclear or outdated"
                ),
                confidence=min(fallback_rate, 1.0),
            )
            best_fix = fix1

        # Rule 2: Applied often but rarely completes
        if (
            applied_rate > _HIGH_APPLIED_FOR_FIX
            and completion_rate < _LOW_COMPLETION_THRESHOLD
        ):
            fix2 = EvolutionSuggestion(
                evolution_type=EvolutionType.FIX,
                target_skill_ids=[skill_record.id],
                direction=(
                    f"Low completion rate ({completion_rate:.0%}) "
                    f"despite high applied rate ({applied_rate:.0%}): "
                    f"skill instructions may be incorrect or incomplete"
                ),
                confidence=min(
                    applied_rate * (1 - completion_rate), 1.0
                ),
            )
            # Keep the FIX with highest confidence
            if best_fix is None or fix2.confidence > best_fix.confidence:
                best_fix = fix2

        if best_fix is not None:
            suggestions.append(best_fix)

        # Rule 3: Moderate effectiveness -- only if no FIX was triggered
        # (DERIVED is lower priority than FIX for the same skill)
        if (
            best_fix is None
            and effective_rate < _MODERATE_EFFECTIVE_THRESHOLD
            and applied_rate > _MIN_APPLIED_FOR_DERIVED
        ):
            suggestions.append(EvolutionSuggestion(
                evolution_type=EvolutionType.DERIVED,
                target_skill_ids=[skill_record.id],
                direction=(
                    f"Moderate effectiveness ({effective_rate:.0%}): "
                    f"skill works sometimes but could be enhanced with "
                    f"better error handling or alternative approaches"
                ),
                confidence=min(1.0 - effective_rate, 1.0),
            ))

        return suggestions

    def diagnose_all(self) -> dict[str, HealthReport]:
        """Run health diagnostics on all active skills.

        Returns:
            Dict mapping skill_id -> HealthReport.
        """
        return self.diagnose_skills(skill_ids=None)

    def diagnose_skills(
        self, skill_ids: set[str] | None = None,
    ) -> dict[str, HealthReport]:
        """Run health diagnostics, optionally filtered to specific skill IDs.

        Args:
            skill_ids: If provided, only diagnose skills whose IDs are in
                this set.  If None, diagnose all active skills.

        Returns:
            Dict mapping skill_id -> HealthReport.
        """
        active_skills = self._store.get_active_skills()
        if skill_ids is not None:
            active_skills = [s for s in active_skills if s.id in skill_ids]
        reports: dict[str, HealthReport] = {}

        for skill in active_skills:
            suggestions = self.check_health(skill)
            sel = skill.total_selections

            metrics: dict[str, float] = {
                "total_selections": float(sel),
            }
            if sel > 0:
                metrics["applied_rate"] = skill.total_applied / sel
                metrics["completion_rate"] = (
                    skill.total_completions / skill.total_applied
                    if skill.total_applied > 0
                    else 0.0
                )
                metrics["effective_rate"] = skill.total_completions / sel
                metrics["fallback_rate"] = skill.total_fallbacks / sel
            else:
                metrics.update({
                    "applied_rate": 0.0,
                    "completion_rate": 0.0,
                    "effective_rate": 0.0,
                    "fallback_rate": 0.0,
                })

            reports[skill.id] = HealthReport(
                skill_id=skill.id,
                skill_name=skill.name,
                is_healthy=len(suggestions) == 0,
                suggestions=suggestions,
                metrics=metrics,
            )

        return reports

    def get_unhealthy(self) -> dict[str, HealthReport]:
        """Return only unhealthy skills (those with suggestions).

        Returns:
            Dict mapping skill_id -> HealthReport for unhealthy skills.
        """
        all_reports = self.diagnose_all()
        return {
            sid: report
            for sid, report in all_reports.items()
            if not report.is_healthy
        }

    def get_health_summary(self) -> dict[str, Any]:
        """Get a summary of overall skill health.

        Returns:
            Dict with counts and unhealthy skill names.
        """
        reports = self.diagnose_all()
        total = len(reports)
        healthy = sum(1 for r in reports.values() if r.is_healthy)
        unhealthy = total - healthy

        fix_count = 0
        derived_count = 0
        for r in reports.values():
            for s in r.suggestions:
                if s.evolution_type == EvolutionType.FIX:
                    fix_count += 1
                elif s.evolution_type == EvolutionType.DERIVED:
                    derived_count += 1

        return {
            "total_skills": total,
            "healthy": healthy,
            "unhealthy": unhealthy,
            "fix_suggestions": fix_count,
            "derived_suggestions": derived_count,
            "unhealthy_skills": [
                r.skill_name for r in reports.values()
                if not r.is_healthy
            ],
        }

    @property
    def store(self) -> EvolutionStore:
        """Access the underlying EvolutionStore."""
        return self._store
