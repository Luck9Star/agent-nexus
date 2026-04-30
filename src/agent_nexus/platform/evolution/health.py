"""HealthChecker -- threshold-based evolution trigger diagnostics.

Health diagnostic thresholds from docs/04:
  - fallback_rate > 0.4 -> FIX
  - applied_rate > 0.4 AND completion_rate < 0.35 -> FIX
  - effective_rate < 0.55 AND applied_rate > 0.25 -> DERIVED

Rule-engine pre-filter, LLM does final confirmation (in SkillEvolver).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agent_nexus.models.evolution import (
    EvolutionType,
    SkillRecord,
)
from agent_nexus.platform.evolution.store import EvolutionStore
from agent_nexus.platform.evolution.thresholds import (
    RULE_HIGH_FALLBACK,
    RULE_LOW_COMPLETION,
    RULE_MODERATE_EFFECTIVE,
    EvolutionSuggestion,
    HealthEvaluation,
    SkillRates,
    evaluate_skill_health,
)

logger = logging.getLogger(__name__)


def build_health_suggestions(
    skill_id: str,
    rates: SkillRates,
    eval_result: HealthEvaluation,
) -> list[EvolutionSuggestion]:
    """Build evolution suggestions from health evaluation results.

    Applies the three threshold rules from docs/04 Section 6 and returns
    a list of suggestions.  FIX rules are deduplicated: only the
    highest-confidence FIX is kept.  DERIVED is included independently.

    This is the single implementation of the 3-rule threshold-to-suggestion
    logic, shared by HealthChecker, ExecutionAnalyzer, and SkillEvolver.

    Args:
        skill_id: The skill to generate suggestions for.
        rates: Pre-computed SkillRates.
        eval_result: Result of evaluate_skill_health(rates).

    Returns:
        List of EvolutionSuggestion (empty if healthy).
    """
    suggestions: list[EvolutionSuggestion] = []

    # Track best FIX suggestion (deduplicate: keep highest confidence)
    best_fix: EvolutionSuggestion | None = None

    # Rule 1: High fallback rate
    if RULE_HIGH_FALLBACK in eval_result.rules:
        fix1 = EvolutionSuggestion(
            evolution_type=EvolutionType.FIX,
            target_skill_ids=[skill_id],
            direction=(
                f"High fallback rate ({rates.fallback_rate:.0%}): "
                f"skill is frequently selected but not applied, "
                f"suggesting instructions are unclear or outdated"
            ),
            confidence=min(rates.fallback_rate, 1.0),
        )
        best_fix = fix1

    # Rule 2: Applied often but rarely completes
    if RULE_LOW_COMPLETION in eval_result.rules:
        fix2 = EvolutionSuggestion(
            evolution_type=EvolutionType.FIX,
            target_skill_ids=[skill_id],
            direction=(
                f"Low completion rate ({rates.completion_rate:.0%}) "
                f"despite high applied rate ({rates.applied_rate:.0%}): "
                f"skill instructions may be incorrect or incomplete"
            ),
            confidence=min(rates.applied_rate * (1 - rates.completion_rate), 1.0),
        )
        # Keep the FIX with highest confidence
        if best_fix is None or fix2.confidence > best_fix.confidence:
            best_fix = fix2

    if best_fix is not None:
        suggestions.append(best_fix)

    # Rule 3: Moderate effectiveness
    if RULE_MODERATE_EFFECTIVE in eval_result.rules:
        suggestions.append(
            EvolutionSuggestion(
                evolution_type=EvolutionType.DERIVED,
                target_skill_ids=[skill_id],
                direction=(
                    f"Moderate effectiveness ({rates.effective_rate:.0%}): "
                    f"skill works sometimes but could be enhanced with "
                    f"better error handling or alternative approaches"
                ),
                confidence=min(1.0 - rates.effective_rate, 1.0),
            )
        )

    return suggestions


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
            parts.append(f"  -> [{s.evolution_type.value}] {targets}: {s.direction}")
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
        self,
        skill_record: SkillRecord,
        rates: SkillRates | None = None,
    ) -> list[EvolutionSuggestion]:
        """Evaluate health of a single skill and return suggestions.

        Applies the three threshold rules from docs/04:

        1. fallback_rate > 0.4 -> FIX
        2. applied_rate > 0.4 AND completion_rate < 0.35 -> FIX
        3. effective_rate < 0.55 AND applied_rate > 0.25 -> DERIVED

        Args:
            skill_record: The skill to evaluate.
            rates: Pre-computed SkillRates to avoid double computation.
                If None, computed from skill_record.

        Returns:
            List of evolution suggestions (empty if healthy).
        """
        rates = rates or SkillRates.from_record(skill_record)
        if rates is None:
            return []

        eval_result = evaluate_skill_health(rates)
        return build_health_suggestions(
            skill_id=skill_record.id,
            rates=rates,
            eval_result=eval_result,
        )

    def diagnose_all(self) -> dict[str, HealthReport]:
        """Run health diagnostics on all active skills.

        Returns:
            Dict mapping skill_id -> HealthReport.
        """
        return self.diagnose_skills(skill_ids=None)

    def diagnose_skills(
        self,
        skill_ids: set[str] | None = None,
        skills: list[SkillRecord] | None = None,
    ) -> dict[str, HealthReport]:
        """Run health diagnostics, optionally filtered to specific skill IDs.

        Args:
            skill_ids: If provided, only diagnose skills whose IDs are in
                this set.  If None, diagnose all active skills.
            skills: Pre-loaded skill list.  If provided, used directly
                instead of fetching from the store (avoids redundant query).

        Returns:
            Dict mapping skill_id -> HealthReport.
        """
        if skills is not None:
            active_skills = skills
            if skill_ids is not None:
                active_skills = [s for s in active_skills if s.id in skill_ids]
        else:
            active_skills = self._store.get_active_skills()
            if skill_ids is not None:
                active_skills = [s for s in active_skills if s.id in skill_ids]
        reports: dict[str, HealthReport] = {}

        for skill in active_skills:
            # Compute rates once and pass into check_health to avoid
            # redundant SkillRates.from_record() call.
            rates = SkillRates.from_record(skill)
            suggestions = self.check_health(skill, rates=rates)

            metrics: dict[str, float] = {
                "total_selections": float(skill.total_selections),
            }
            if rates is not None:
                metrics["applied_rate"] = rates.applied_rate
                metrics["completion_rate"] = rates.completion_rate
                metrics["effective_rate"] = rates.effective_rate
                metrics["fallback_rate"] = rates.fallback_rate
            else:
                metrics.update(
                    {
                        "applied_rate": 0.0,
                        "completion_rate": 0.0,
                        "effective_rate": 0.0,
                        "fallback_rate": 0.0,
                    }
                )

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

        Pre-filters skills with 0 total_selections before running full
        diagnosis, since skills never selected cannot be unhealthy.
        """
        active_skills = self._store.get_active_skills()
        candidates = [s for s in active_skills if s.total_selections > 0]
        if not candidates:
            return {}
        reports = self.diagnose_skills(skills=candidates)
        return {sid: report for sid, report in reports.items() if not report.is_healthy}

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
        captured_count = 0
        for r in reports.values():
            for s in r.suggestions:
                if s.evolution_type == EvolutionType.FIX:
                    fix_count += 1
                elif s.evolution_type == EvolutionType.DERIVED:
                    derived_count += 1
                elif s.evolution_type == EvolutionType.CAPTURED:
                    captured_count += 1

        return {
            "total_skills": total,
            "healthy": healthy,
            "unhealthy": unhealthy,
            "fix_suggestions": fix_count,
            "derived_suggestions": derived_count,
            "captured_suggestions": captured_count,
            "unhealthy_skills": [r.skill_name for r in reports.values() if not r.is_healthy],
        }

    @property
    def store(self) -> EvolutionStore:
        """Access the underlying EvolutionStore."""
        return self._store
