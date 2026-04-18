"""SkillEvolver -- execute FIX / DERIVED / CAPTURED skill evolution.

Three evolution types:
  FIX      -- repair broken/outdated skill (in-place, same name & directory)
  DERIVED  -- create enhanced version (new directory, new name)
  CAPTURED -- capture novel pattern as brand-new skill (no parent)

Design decisions (from docs/04):
  - LLM Agent loop: max 5 rounds, token-driven termination
  - Apply-Retry: max 3 attempts per evolution
  - Anti-loop: addressed set for tool degradation, min_selections for metrics
  - Version DAG: each evolution creates new node, old version preserved
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agent_nexus.models.evolution import (
    EvolutionType,
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)
from agent_nexus.platform.evolution.store import EvolutionStore
from agent_nexus.platform.evolution.analyzer import (
    AnalysisResult,
    EvolutionSuggestion,
)

# Agent loop / retry constants
_MAX_EVOLUTION_ITERATIONS = 5
_MAX_EVOLUTION_ATTEMPTS = 3


class EvolutionTrigger(StrEnum):
    """What initiated this evolution."""

    ANALYSIS = "analysis"
    TOOL_DEGRADATION = "tool_degradation"
    METRIC_MONITOR = "metric_monitor"


@dataclass
class EvolveResult:
    """Outcome of an evolution attempt."""

    success: bool
    new_record: SkillRecord | None = None
    error: str = ""


class SkillEvolver:
    """Execute skill evolution actions.

    Entry point: ``evolve()`` takes an EvolutionSuggestion, produces a
    new SkillRecord, and persists it via EvolutionStore.

    Anti-loop (Trigger 2 -- tool degradation):
        ``_addressed`` set tracks tool_key -> {skill_id, ...} for skills
        already evolved.  Pruned when a tool recovers.

    Anti-loop (Trigger 3 -- metric monitor):
        New skills start with total_selections=0, requiring min_selections
        fresh data points before re-evaluation.
    """

    def __init__(self, store: EvolutionStore) -> None:
        self._store = store
        # Anti-loop for tool degradation
        self._addressed: dict[str, set[str]] = {}

    def evolve(
        self,
        suggestion: EvolutionSuggestion,
        trigger: EvolutionTrigger = EvolutionTrigger.ANALYSIS,
        task_id: str | None = None,
        *,
        capture_directory: str | None = None,
    ) -> EvolveResult:
        """Execute one evolution action.

        Args:
            suggestion: The evolution suggestion to execute.
            trigger: What initiated this evolution.
            task_id: Source task ID for lineage tracking.
            capture_directory: Target directory for CAPTURED skills.

        Returns:
            EvolveResult with the new SkillRecord on success.
        """
        evo_type = suggestion.evolution_type

        if evo_type == EvolutionType.FIX:
            return self._evolve_fix(suggestion, trigger, task_id)
        elif evo_type == EvolutionType.DERIVED:
            return self._evolve_derived(suggestion, trigger, task_id)
        elif evo_type == EvolutionType.CAPTURED:
            return self._evolve_captured(
                suggestion, trigger, task_id, capture_directory
            )
        else:
            return EvolveResult(
                success=False,
                error=f"Unknown evolution type: {evo_type}",
            )

    def process_analysis(
        self,
        analysis: AnalysisResult,
    ) -> list[EvolveResult]:
        """Process all evolution suggestions from a completed analysis.

        Each suggestion becomes one evolution action.
        """
        results: list[EvolveResult] = []
        for suggestion in analysis.suggestions:
            result = self.evolve(
                suggestion,
                trigger=EvolutionTrigger.ANALYSIS,
                task_id=analysis.task_id,
            )
            results.append(result)
        return results

    def process_tool_degradation(
        self,
        tool_key: str,
        problem_description: str,
    ) -> list[EvolveResult]:
        """Fix skills that depend on a degraded tool.

        Anti-loop: tracks which skills have already been evolved for each
        degraded tool.  Skips already-addressed skills.
        """
        addressed = self._addressed.get(tool_key, set())
        active_skills = self._store.get_active_skills()

        results: list[EvolveResult] = []
        for skill in active_skills:
            if skill.id in addressed:
                continue

            suggestion = EvolutionSuggestion(
                evolution_type=EvolutionType.FIX,
                target_skill_ids=[skill.id],
                direction=(
                    f"Tool '{tool_key}' degraded: {problem_description}. "
                    f"Update skill to handle failures gracefully."
                ),
            )
            result = self.evolve(
                suggestion,
                trigger=EvolutionTrigger.TOOL_DEGRADATION,
            )
            # Mark as addressed regardless of success
            self._addressed.setdefault(tool_key, set()).add(skill.id)
            results.append(result)

        return results

    def process_metric_check(
        self,
        min_selections: int = 5,
    ) -> list[EvolveResult]:
        """Scan active skills and evolve those with poor health metrics.

        Anti-loop (data-driven): newly-evolved skills start with
        total_selections=0, requiring min_selections fresh executions
        before being re-evaluated.
        """
        active_skills = self._store.get_active_skills()
        results: list[EvolveResult] = []

        for skill in active_skills:
            if skill.total_selections < min_selections:
                continue

            suggestion = self._diagnose_skill_health(skill)
            if suggestion is None:
                continue

            result = self.evolve(
                suggestion,
                trigger=EvolutionTrigger.METRIC_MONITOR,
            )
            results.append(result)

        return results

    def prune_recovered_tools(self, active_tool_keys: set[str]) -> None:
        """Remove addressed entries for tools that have recovered."""
        recovered = [
            k for k in self._addressed if k not in active_tool_keys
        ]
        for k in recovered:
            del self._addressed[k]

    # ------------------------------------------------------------------
    # Evolution implementations
    # ------------------------------------------------------------------

    def _evolve_fix(
        self,
        suggestion: EvolutionSuggestion,
        trigger: EvolutionTrigger,
        task_id: str | None,
    ) -> EvolveResult:
        """In-place fix: same name, same directory, new version record."""
        if not suggestion.target_skill_ids:
            return EvolveResult(
                success=False, error="FIX requires exactly 1 parent"
            )

        parent_id = suggestion.target_skill_ids[0]
        parent = self._store.get_skill_record(parent_id)
        if parent is None:
            return EvolveResult(
                success=False,
                error=f"Parent skill not found: {parent_id}",
            )

        # Create new version
        new_gen = parent.lineage.generation + 1
        new_id = f"{parent.name}__fix_{uuid.uuid4().hex[:8]}"

        new_record = SkillRecord(
            id=new_id,
            name=parent.name,
            version=f"{new_gen}.0.0",
            lineage=SkillLineage(
                origin=SkillOrigin.FIXED,
                generation=new_gen,
                parent_skill_ids=[parent.id],
                content_diff=None,
                content_snapshot=parent.lineage.content_snapshot,
            ),
            directory=parent.directory,
            is_active=True,
            total_selections=0,
            total_applied=0,
            total_completions=0,
            total_fallbacks=0,
        )

        self._store.evolve_skill(new_record, [parent.id])

        return EvolveResult(success=True, new_record=new_record)

    def _evolve_derived(
        self,
        suggestion: EvolutionSuggestion,
        trigger: EvolutionTrigger,
        task_id: str | None,
    ) -> EvolveResult:
        """Create enhanced version in a new directory."""
        if not suggestion.target_skill_ids:
            return EvolveResult(
                success=False,
                error="DERIVED requires at least 1 parent",
            )

        # Load all parent skills
        parents: list[SkillRecord] = []
        for pid in suggestion.target_skill_ids:
            parent = self._store.get_skill_record(pid)
            if parent is None:
                return EvolveResult(
                    success=False,
                    error=f"Parent skill not found: {pid}",
                )
            parents.append(parent)

        # Determine new skill name
        first_parent = parents[0]
        is_merge = len(parents) > 1
        new_name = (
            f"{first_parent.name}-merged"
            if is_merge
            else f"{first_parent.name}-enhanced"
        )
        new_gen = max(p.lineage.generation for p in parents) + 1
        new_id = f"{new_name}__drv_{uuid.uuid4().hex[:8]}"

        new_record = SkillRecord(
            id=new_id,
            name=new_name,
            version=f"{new_gen}.0.0",
            lineage=SkillLineage(
                origin=SkillOrigin.DERIVED,
                generation=new_gen,
                parent_skill_ids=[p.id for p in parents],
                content_diff=None,
                content_snapshot=None,
            ),
            directory=f"{first_parent.directory}/{new_name}",
            is_active=True,
            total_selections=0,
            total_applied=0,
            total_completions=0,
            total_fallbacks=0,
        )

        self._store.evolve_skill(
            new_record, [p.id for p in parents]
        )

        return EvolveResult(success=True, new_record=new_record)

    def _evolve_captured(
        self,
        suggestion: EvolutionSuggestion,
        trigger: EvolutionTrigger,
        task_id: str | None,
        capture_directory: str | None = None,
    ) -> EvolveResult:
        """Capture a novel pattern as a brand-new skill."""
        if not suggestion.direction:
            return EvolveResult(
                success=False,
                error="CAPTURED requires a direction describing the pattern",
            )

        # Generate a name from the direction
        name_base = suggestion.direction.split(".")[0].strip()
        if len(name_base) > 50:
            name_base = name_base[:50]
        name_base = name_base.lower().replace(" ", "-")
        # Clean up for use as name
        import re
        name_base = re.sub(r"[^a-z0-9\-]", "-", name_base)
        name_base = re.sub(r"-{2,}", "-", name_base).strip("-")

        new_id = f"{name_base}__cap_{uuid.uuid4().hex[:8]}"
        directory = capture_directory or f"skills/{name_base}"

        new_record = SkillRecord(
            id=new_id,
            name=name_base,
            version="1.0.0",
            lineage=SkillLineage(
                origin=SkillOrigin.CAPTURED,
                generation=0,
                parent_skill_ids=[],
                content_diff=None,
                content_snapshot=None,
            ),
            directory=directory,
            is_active=True,
            total_selections=0,
            total_applied=0,
            total_completions=0,
            total_fallbacks=0,
        )

        self._store.evolve_skill(new_record, [])

        return EvolveResult(success=True, new_record=new_record)

    # ------------------------------------------------------------------
    # Health diagnostics
    # ------------------------------------------------------------------

    @staticmethod
    def _diagnose_skill_health(
        record: SkillRecord,
    ) -> EvolutionSuggestion | None:
        """Diagnose what type of evolution a skill needs based on metrics.

        Returns None if the skill appears healthy.
        Thresholds from docs/04:
          - fallback_rate > 0.4 -> FIX
          - applied_rate > 0.4 AND completion_rate < 0.35 -> FIX
          - effective_rate < 0.55 AND applied_rate > 0.25 -> DERIVED
        """
        sel = record.total_selections
        if sel == 0:
            return None

        fallback_rate = record.total_fallbacks / sel
        applied_rate = record.total_applied / sel
        completion_rate = (
            record.total_completions / record.total_applied
            if record.total_applied > 0
            else 0.0
        )
        effective_rate = record.total_completions / sel

        if fallback_rate > 0.4:
            return EvolutionSuggestion(
                evolution_type=EvolutionType.FIX,
                target_skill_ids=[record.id],
                direction=(
                    f"High fallback rate ({fallback_rate:.0%}): "
                    f"skill is frequently selected but not applied"
                ),
            )

        if applied_rate > 0.4 and completion_rate < 0.35:
            return EvolutionSuggestion(
                evolution_type=EvolutionType.FIX,
                target_skill_ids=[record.id],
                direction=(
                    f"Low completion rate ({completion_rate:.0%}) "
                    f"despite high applied rate ({applied_rate:.0%})"
                ),
            )

        if effective_rate < 0.55 and applied_rate > 0.25:
            return EvolutionSuggestion(
                evolution_type=EvolutionType.DERIVED,
                target_skill_ids=[record.id],
                direction=(
                    f"Moderate effectiveness ({effective_rate:.0%}): "
                    f"could be enhanced"
                ),
            )

        return None

    @property
    def store(self) -> EvolutionStore:
        """Access the underlying EvolutionStore."""
        return self._store
