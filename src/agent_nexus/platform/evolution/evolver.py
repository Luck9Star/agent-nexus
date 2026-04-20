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

import logging
import uuid
from dataclasses import dataclass
import re
from enum import StrEnum

logger = logging.getLogger(__name__)

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
from agent_nexus.platform.evolution.health import build_health_suggestions
from agent_nexus.platform.evolution.thresholds import (
    SkillRates,
    evaluate_skill_health,
)


class EvolutionTrigger(StrEnum):
    """What initiated this evolution.

    These values match the trigger routing in EvolutionEngine.evolve().
    """

    POST_ANALYSIS = "post_analysis"
    TOOL_DEGRADATION = "tool_degradation"
    METRIC_CHECK = "metric_check"


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
        trigger: EvolutionTrigger = EvolutionTrigger.POST_ANALYSIS,
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
                trigger=EvolutionTrigger.POST_ANALYSIS,
                task_id=analysis.task_id,
            )
            results.append(result)
        return results

    def process_tool_degradation(
        self,
        tool_key: str,
        problem_description: str,
        affected_skill_ids: set[str] | None = None,
    ) -> list[EvolveResult]:
        """Fix skills that depend on a degraded tool.

        Args:
            tool_key: Identifier of the degraded tool.
            problem_description: Human-readable description of the degradation.
            affected_skill_ids: If provided, only evolve skills whose IDs are
                in this set.  When ``None`` (backward compat), all active
                skills are considered (with a warning).

        Anti-loop: tracks which skills have already been evolved for each
        degraded tool.  Skips already-addressed skills.
        """
        addressed = self._addressed.get(tool_key, set())
        active_skills = self._store.get_active_skills()

        if affected_skill_ids is not None:
            active_skills = [s for s in active_skills if s.id in affected_skill_ids]
        else:
            logger.warning("Evolving all skills for tool degradation (no filter)")

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
            # Only mark as addressed when evolution succeeds — failed
            # attempts should be eligible for retry on next degradation.
            if result.success:
                self._addressed.setdefault(tool_key, set()).add(skill.id)
                # Anti-loop guard is non-critical; cap total size to prevent
                # unbounded growth.  Reset entirely when the cap is exceeded.
                total = sum(len(s) for s in self._addressed.values())
                if total > 500:
                    self._addressed.clear()
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
                trigger=EvolutionTrigger.METRIC_CHECK,
            )
            results.append(result)

        return results

    def prune_recovered_tools(self, still_degraded_tool_keys: set[str]) -> None:
        """Remove addressed entries for tools no longer in the degraded set.

        Args:
            still_degraded_tool_keys: Tool keys that are STILL degraded.
                Any addressed entry NOT in this set is considered recovered
                and will be pruned.
        """
        recovered = [
            k for k in self._addressed if k not in still_degraded_tool_keys
        ]
        for k in recovered:
            del self._addressed[k]

    # ------------------------------------------------------------------
    # Evolution implementations
    # ------------------------------------------------------------------

    def _evolve_fix(
        self,
        suggestion: EvolutionSuggestion,
        _trigger: EvolutionTrigger,
        _task_id: str | None,
    ) -> EvolveResult:
        """In-place fix: same name, same directory, new version record."""
        if not suggestion.target_skill_ids:
            return EvolveResult(
                success=False, error="FIX requires exactly 1 parent"
            )

        if len(suggestion.target_skill_ids) != 1:
            return EvolveResult(
                success=False,
                error=f"FIX requires exactly 1 parent, got {len(suggestion.target_skill_ids)}",
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

        result = self._store.evolve_skill(new_record, [parent.id])
        if not result.success:
            return result
        return EvolveResult(success=True, new_record=new_record)

    def _evolve_derived(
        self,
        suggestion: EvolutionSuggestion,
        _trigger: EvolutionTrigger,
        _task_id: str | None,
    ) -> EvolveResult:
        """Create enhanced version in a new directory."""
        if not suggestion.target_skill_ids:
            return EvolveResult(
                success=False,
                error="DERIVED requires at least 1 parent",
            )

        # Load all parent skills in a single query (avoid N+1)
        found = self._store.get_skill_records_batch(suggestion.target_skill_ids)
        parents: list[SkillRecord] = []
        for pid in suggestion.target_skill_ids:
            if pid not in found:
                return EvolveResult(
                    success=False,
                    error=f"Parent skill not found: {pid}",
                )
            parents.append(found[pid])

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

        result = self._store.evolve_skill(
            new_record, [p.id for p in parents]
        )
        if not result.success:
            return result
        return EvolveResult(success=True, new_record=new_record)

    def _evolve_captured(
        self,
        suggestion: EvolutionSuggestion,
        _trigger: EvolutionTrigger,
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
        name_base = suggestion.direction.split(". ")[0].strip()
        if len(name_base) > 50:
            name_base = name_base[:50]
        name_base = name_base.lower().replace(" ", "-")
        # Clean up for use as name
        name_base = re.sub(r"[^a-z0-9\-]", "-", name_base)
        name_base = re.sub(r"-{2,}", "-", name_base).strip("-")
        if not name_base:
            name_base = f"captured_{uuid.uuid4().hex[:6]}"

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
                content_snapshot={"source_task_id": task_id or "unknown"},
            ),
            directory=directory,
            is_active=True,
            total_selections=0,
            total_applied=0,
            total_completions=0,
            total_fallbacks=0,
        )

        result = self._store.evolve_skill(new_record, [])
        if not result.success:
            return result
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

        FIX rules are deduplicated: only the highest-confidence FIX is kept
        (matching build_health_suggestions logic).

        Returns only the first (best) suggestion.  For the full list, use
        build_health_suggestions directly.
        """
        rates = SkillRates.from_record(record)
        if rates is None:
            return None

        eval_result = evaluate_skill_health(rates)
        suggestions = build_health_suggestions(
            skill_id=record.id,
            rates=rates,
            eval_result=eval_result,
        )
        return suggestions[0] if suggestions else None

    @property
    def store(self) -> EvolutionStore:
        """Access the underlying EvolutionStore."""
        return self._store
