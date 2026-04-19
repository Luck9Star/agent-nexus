"""EvolutionEngine -- unified facade for the Self-Evolution Engine.

Orchestrates all evolution triggers through a single entry point.
Thin delegation layer: no additional business logic beyond routing.

Sub-components:
  - ExecutionAnalyzer   -- post-task analysis
  - SkillEvolver        -- FIX / DERIVED / CAPTURED evolution
  - HealthChecker       -- threshold-based diagnostics
  - CompactionGuard     -- context window protection
  - AgentPromoter       -- skill-to-agent promotion

Usage::

    store = EvolutionStore(db_path)
    engine = EvolutionEngine(store)

    # Trigger 1: post-task analysis -> evolve
    result = engine.evolve(trigger="post_analysis", ctx=evolution_ctx)

    # Trigger 2: tool degradation
    results = engine.evolve(trigger="tool_degradation", tool_key="api-x", ...)

    # Trigger 3: metric check
    results = engine.evolve(trigger="metric_check")

    # Convenience methods
    report = engine.check_health(skill_record)
    reports = engine.diagnose_all()
    promote_result = engine.promote_candidate(candidate)
    should = engine.should_compact(agent_ctx)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

from pathlib import Path
from typing import TYPE_CHECKING

from agent_nexus.platform.evolution.store import EvolutionStore
from agent_nexus.platform.evolution.analyzer import (
    AnalysisResult,
    EvolutionSuggestion,
    ExecutionAnalyzer,
)
from agent_nexus.platform.evolution.evolver import (
    EvolveResult,
    SkillEvolver,
)
from agent_nexus.platform.evolution.health import (
    HealthChecker,
    HealthReport,
)
from agent_nexus.platform.evolution.compaction import (
    AgentContext,
    CompactionGuard,
)
from agent_nexus.platform.evolution.promotion import (
    AgentPromoter,
    PromotionCandidate,
    PromotionResult,
)

if TYPE_CHECKING:
    from agent_nexus.models.evolution import EvolutionContext


class EvolutionEngine:
    """Unified facade for the Self-Evolution Engine.

    Creates all sub-components internally from a single EvolutionStore
    and delegates method calls to the appropriate component.

    Args:
        store: Central SQLite persistence layer.
        agent_id: Agent identifier for CompactionGuard (default "default").
        agents_root: Root directory for promoted agent packages.
    """

    def __init__(
        self,
        store: EvolutionStore,
        *,
        agent_id: str = "default",
        agents_root: Path | None = None,
    ) -> None:
        self._store = store
        self._agent_id = agent_id
        self._agents_root = agents_root

        # Create all sub-components
        self._analyzer = ExecutionAnalyzer(store)
        self._evolver = SkillEvolver(store)
        self._health_checker = HealthChecker(store)
        self._compaction_guard = CompactionGuard(store, agent_id)
        self._promoter = AgentPromoter(store, agents_root)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def store(self) -> EvolutionStore:
        """The underlying EvolutionStore."""
        return self._store

    @property
    def analyzer(self) -> ExecutionAnalyzer:
        """Post-task execution analyzer."""
        return self._analyzer

    @property
    def evolver(self) -> SkillEvolver:
        """Skill evolution executor (FIX / DERIVED / CAPTURED)."""
        return self._evolver

    @property
    def health_checker(self) -> HealthChecker:
        """Threshold-based health diagnostics."""
        return self._health_checker

    @property
    def compaction_guard(self) -> CompactionGuard:
        """Context window protection against compaction loops."""
        return self._compaction_guard

    @property
    def promoter(self) -> AgentPromoter:
        """Skill-to-agent promotion handler."""
        return self._promoter

    # ------------------------------------------------------------------
    # Unified evolve() entry point
    # ------------------------------------------------------------------

    def evolve(
        self,
        *,
        trigger: str,
        ctx: EvolutionContext | None = None,
        tool_key: str | None = None,
        problem_description: str | None = None,
        affected_skill_ids: set[str] | None = None,
        min_selections: int = 5,
    ) -> AnalysisResult | list[EvolveResult]:
        """Route to the appropriate evolution sub-component by trigger.

        Args:
            trigger: One of "post_analysis", "tool_degradation", "metric_check".
            ctx: EvolutionContext (required for "post_analysis").
            tool_key: Degraded tool identifier (required for "tool_degradation").
            problem_description: Tool problem description (for "tool_degradation").
            affected_skill_ids: Optional filter for affected skills.
            min_selections: Minimum selections for metric check evaluation.

        Returns:
            AnalysisResult for "post_analysis", list[EvolveResult] otherwise.

        Raises:
            ValueError: If trigger is unknown or required args are missing.
        """
        if trigger == "post_analysis":
            if ctx is None:
                raise ValueError(
                    "ctx (EvolutionContext) is required for trigger='post_analysis'"
                )
            analysis = self._analyzer.analyze_execution(ctx)
            self._evolver.process_analysis(analysis)
            return analysis

        elif trigger == "tool_degradation":
            if tool_key is None:
                raise ValueError(
                    "tool_key is required for trigger='tool_degradation'"
                )
            return self._evolver.process_tool_degradation(
                tool_key=tool_key,
                problem_description=problem_description or "",
                affected_skill_ids=affected_skill_ids,
            )

        elif trigger == "metric_check":
            return self._evolver.process_metric_check(
                min_selections=min_selections,
            )

        else:
            raise ValueError(
                f"Unknown trigger: {trigger!r}. "
                f"Expected one of: post_analysis, tool_degradation, metric_check"
            )

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def check_health(self, skill_id: str) -> list[EvolutionSuggestion]:
        """Check health of a single skill by ID.

        Args:
            skill_id: The skill to evaluate.

        Returns:
            List of evolution suggestions (empty if healthy).

        Raises:
            ValueError: If skill_id is not found.
        """
        record = self._store.get_skill_record(skill_id)
        if record is None:
            raise ValueError(f"Skill not found: {skill_id}")
        return self._health_checker.check_health(record)

    def diagnose_all(self) -> dict[str, HealthReport]:
        """Run health diagnostics on all active skills.

        Returns:
            Dict mapping skill_id -> HealthReport.
        """
        return self._health_checker.diagnose_all()

    def promote_candidate(
        self, candidate: PromotionCandidate
    ) -> PromotionResult:
        """Promote a skill candidate to a standalone agent.

        Args:
            candidate: The promotion candidate.

        Returns:
            PromotionResult with paths to generated files.
        """
        return self._promoter.promote(candidate)

    def should_compact(self, context: AgentContext) -> bool:
        """Check whether compaction should be triggered.

        Args:
            context: Current agent context.

        Returns:
            True if compaction should proceed.
        """
        return self._compaction_guard.should_compact(context)
