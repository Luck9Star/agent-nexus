"""EvolutionMetrics and EvolutionDashboard -- evolution observability.

Provides aggregated metrics and a dashboard view over the evolution engine.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from agent_nexus.models.evolution import EvolutionType, SkillOrigin, SkillRecord
from agent_nexus.platform.evolution.health import HealthChecker
from agent_nexus.platform.evolution.store import EvolutionStore

logger = logging.getLogger(__name__)


@dataclass
class EvolutionSummary:
    """High-level evolution engine summary."""

    total_skills: int = 0
    active_skills: int = 0
    healthy_skills: int = 0
    unhealthy_skills: int = 0
    total_evolutions: int = 0
    fix_evolutions: int = 0
    derived_evolutions: int = 0
    captured_evolutions: int = 0
    avg_applied_rate: float = 0.0
    avg_effective_rate: float = 0.0


@dataclass
class LineageNode:
    """One node in a skill's lineage tree."""

    skill_id: str
    name: str
    generation: int
    origin: str
    children: list[LineageNode] = field(default_factory=list)


@dataclass
class HealthOverview:
    """Aggregate health overview."""

    total: int = 0
    healthy: int = 0
    unhealthy: int = 0
    fix_needed: int = 0
    derived_needed: int = 0
    unhealthy_skill_names: list[str] = field(default_factory=list)


class EvolutionDashboard:
    """Read-only dashboard for evolution engine state.

    Usage::

        dashboard = EvolutionDashboard(store)
        summary = dashboard.get_summary()
        health = dashboard.get_health_report()
        lineage = dashboard.get_skill_lineage(skill_id)
    """

    def __init__(self, store: EvolutionStore) -> None:
        self._store = store
        self._health_checker = HealthChecker(store)

    def get_summary(self) -> EvolutionSummary:
        """Get high-level evolution engine summary."""
        active = self._store.get_active_skills()
        all_skills = self._store.get_all_skills()

        origin_counts = Counter(s.lineage.origin for s in all_skills)

        # Compute average rates for active skills with data
        rates = []
        for s in active:
            if s.total_selections > 0:
                rates.append(
                    {
                        "applied": s.total_applied / s.total_selections,
                        "effective": s.total_completions / s.total_selections,
                    }
                )

        avg_applied = sum(r["applied"] for r in rates) / len(rates) if rates else 0.0
        avg_effective = sum(r["effective"] for r in rates) / len(rates) if rates else 0.0

        return EvolutionSummary(
            total_skills=len(all_skills),
            active_skills=len(active),
            healthy_skills=sum(1 for s in active if self._is_healthy(s)),
            unhealthy_skills=sum(1 for s in active if not self._is_healthy(s)),
            total_evolutions=sum(1 for s in all_skills if s.lineage.origin != SkillOrigin.IMPORTED),
            fix_evolutions=origin_counts.get(SkillOrigin.FIXED, 0),
            derived_evolutions=origin_counts.get(SkillOrigin.DERIVED, 0),
            captured_evolutions=origin_counts.get(SkillOrigin.CAPTURED, 0),
            avg_applied_rate=round(avg_applied, 3),
            avg_effective_rate=round(avg_effective, 3),
        )

    def get_health_report(self) -> HealthOverview:
        """Get aggregate health overview."""
        reports = self._health_checker.diagnose_all()
        total = len(reports)
        healthy = sum(1 for r in reports.values() if r.is_healthy)
        fix_needed = sum(
            1
            for r in reports.values()
            for s in r.suggestions
            if s.evolution_type == EvolutionType.FIX
        )
        derived_needed = sum(
            1
            for r in reports.values()
            for s in r.suggestions
            if s.evolution_type == EvolutionType.DERIVED
        )
        unhealthy_names = [r.skill_name for r in reports.values() if not r.is_healthy]

        return HealthOverview(
            total=total,
            healthy=healthy,
            unhealthy=total - healthy,
            fix_needed=fix_needed,
            derived_needed=derived_needed,
            unhealthy_skill_names=unhealthy_names,
        )

    def get_skill_lineage(self, skill_id: str) -> LineageNode | None:
        """Get the lineage tree for a skill."""
        record = self._store.get_skill_record(skill_id)
        if record is None:
            return None

        all_skills = self._store.get_all_skills()
        children = self._find_children(
            skill_id, all_skills=all_skills, visited={skill_id}, depth=0
        )
        return LineageNode(
            skill_id=record.id,
            name=record.name,
            generation=record.lineage.generation,
            origin=record.lineage.origin,
            children=children,
        )

    def _find_children(
        self,
        skill_id: str,
        *,
        all_skills: list[SkillRecord],
        visited: set[str],
        depth: int,
    ) -> list[LineageNode]:
        """Find skills that have skill_id as a parent.

        Parameters
        ----------
        skill_id:
            The parent skill to find children for.
        all_skills:
            Pre-fetched list of all skills (avoids repeated queries).
        visited:
            Set of already-visited skill IDs to prevent cycles.
        depth:
            Current recursion depth (max 10 to prevent unbounded recursion).
        """
        if skill_id in visited or depth > 10:
            return []
        visited.add(skill_id)
        children: list[LineageNode] = []
        for s in all_skills:
            if skill_id in s.lineage.parent_skill_ids:
                grandchildren = self._find_children(
                    s.id, all_skills=all_skills, visited=visited, depth=depth + 1
                )
                children.append(
                    LineageNode(
                        skill_id=s.id,
                        name=s.name,
                        generation=s.lineage.generation,
                        origin=s.lineage.origin,
                        children=grandchildren,
                    )
                )
        return children

    @staticmethod
    def _is_healthy(skill: SkillRecord) -> bool:
        """Quick health check without full diagnosis."""
        if skill.total_selections == 0:
            return True  # No data = can't be unhealthy
        fallback_rate = skill.total_fallbacks / skill.total_selections
        return fallback_rate <= 0.4
