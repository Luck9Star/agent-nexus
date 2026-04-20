"""EvolutionContextDescriber: tiered L0/L1/L2 context injection for evolution data.

Injects quality metrics, skill lineage, and health status into LLM context
at three token-budget tiers:
- L0 (summary): ~30 tokens, just key metrics
- L1 (details): ~100 tokens, per-skill health + top metrics
- L2 (full): ~300 tokens, lineage + judgment history + all skills

Reference: docs/04 Section 6.8
"""

from __future__ import annotations

import logging
from agent_nexus.models.evolution import SkillRecord
from agent_nexus.platform.evolution.health import HealthChecker, HealthReport
from agent_nexus.platform.evolution.store import EvolutionStore
from agent_nexus.platform.evolution.thresholds import SkillRates

logger = logging.getLogger(__name__)


class EvolutionContextDescriber:
    """Generate tiered evolution context descriptions for LLM injection.

    Usage::

        store = EvolutionStore(db_path)
        describer = EvolutionContextDescriber(store)

        # Every turn (~30 tokens)
        l0 = describer.l0_context()

        # First turn (~100 tokens)
        l1 = describer.l1_context()

        # On-demand full details (~300 tokens)
        l2 = describer.l2_context()
    """

    def __init__(
        self,
        store: EvolutionStore,
        agent_name: str | None = None,
    ) -> None:
        self._store = store
        self._agent_name = agent_name
        self._health = HealthChecker(store)

    # ------------------------------------------------------------------
    # L0: Summary (~30 tokens)
    # ------------------------------------------------------------------

    def l0_context(
        self,
        active_skills: list[SkillRecord] | None = None,
    ) -> str:
        """Generate L0 context block (~30 tokens).

        Summary: total active skills, overall health, evolution count.

        Args:
            active_skills: Pre-loaded active skills list.  If None, fetched
                from the store (allows callers to reuse a single query).

        Returns:
            Formatted context string, e.g.
            "Evolution: 5 active skills, 2 evolved, avg effective_rate 0.85"
        """
        active = active_skills if active_skills is not None else self._store.get_active_skills()
        total_active = len(active)
        if total_active == 0:
            return "[Evolution] No active skills"

        # Count evolved skills (origin is not IMPORTED)
        evolved = sum(
            1 for s in active if s.lineage.origin.value != "imported"
        )

        # Compute aggregate effective rate (scoped to agent if available)
        metrics = self._store.get_metrics(agent_name=self._agent_name)
        eff_rate = (
            metrics.total_completions / metrics.total_selections
            if metrics.total_selections > 0
            else 0.0
        )

        return (
            f"[Evolution] {total_active} active skills, "
            f"{evolved} evolved, "
            f"avg effective_rate {eff_rate:.2f}"
        )

    # ------------------------------------------------------------------
    # L1: Details (~100 tokens)
    # ------------------------------------------------------------------

    def l1_context(
        self,
        skill_ids: list[str] | None = None,
        active_skills: list[SkillRecord] | None = None,
    ) -> str:
        """Generate L1 context block (~100 tokens).

        Details: per-skill metrics and health for top skills.

        Args:
            skill_ids: Optional filter; if None, includes all active skills.
            active_skills: Pre-loaded active skills list.  If None, fetched
                from the store (allows callers to reuse a single query).

        Returns:
            Formatted markdown context string with a table of skills.
        """
        active = active_skills if active_skills is not None else self._store.get_active_skills()

        if skill_ids is not None:
            id_set = set(skill_ids)
            active = [s for s in active if s.id in id_set]

        if not active:
            return "[Evolution] No matching active skills"

        # Sort by total_selections descending (top skills first)
        active.sort(key=lambda s: s.total_selections, reverse=True)

        # Build health reports, passing the already-filtered list to
        # avoid diagnose_skills fetching get_active_skills() again.
        reports = self._health.diagnose_skills(skills=active)

        lines: list[str] = ["[Evolution Skill Metrics]"]
        lines.append(
            "| Skill | Selections | Eff. Rate | Health |"
        )
        lines.append(
            "|-------|-----------|-----------|--------|"
        )

        for skill in active:
            sel = skill.total_selections
            rates = SkillRates.from_record(skill)
            eff = rates.effective_rate if rates is not None else 0.0
            report = reports.get(skill.id)
            health_status = (
                "OK" if report and report.is_healthy else "WARN"
            )
            lines.append(
                f"| {skill.name} | {sel} | {eff:.2f} | {health_status} |"
            )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # L2: Full (~300 tokens)
    # ------------------------------------------------------------------

    def l2_context(
        self,
        skill_ids: list[str] | None = None,
        active_skills: list[SkillRecord] | None = None,
    ) -> str:
        """Generate L2 context block (~300 tokens).

        Full: lineage, judgments, health for all or specified skills.

        Args:
            skill_ids: Optional filter; if None, includes all active skills.
            active_skills: Pre-loaded active skills list.  If None, fetched
                from the store (allows callers to reuse a single query).

        Returns:
            Formatted markdown context string with lineage tree,
            health details, and evolution history.
        """
        active = active_skills if active_skills is not None else self._store.get_active_skills()

        if skill_ids is not None:
            id_set = set(skill_ids)
            active = [s for s in active if s.id in id_set]

        if not active:
            return "[Evolution] No matching active skills"

        # Sort by total_selections descending
        active.sort(key=lambda s: s.total_selections, reverse=True)

        # Build health reports, passing the already-filtered list to
        # avoid diagnose_skills fetching get_active_skills() again.
        reports = self._health.diagnose_skills(skills=active)

        parts: list[str] = []

        # Section 1: Lineage tree
        lineage_lines = self._build_lineage_tree(active)
        if lineage_lines:
            parts.append("[Evolution Lineage]\n" + "\n".join(lineage_lines))

        # Section 2: Detailed skill metrics
        detail_lines = self._build_detail_table(active, reports)
        parts.append("[Evolution Details]\n" + "\n".join(detail_lines))

        # Section 3: Health diagnostics
        health_lines = self._build_health_diagnostics(active, reports)
        if health_lines:
            parts.append(
                "[Evolution Health]\n" + "\n".join(health_lines)
            )

        # Section 4: Recent judgment history
        judgment_lines = self._build_judgment_history(active)
        if judgment_lines:
            parts.append(
                "[Evolution History]\n" + "\n".join(judgment_lines)
            )

        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_lineage_tree(
        self, skills: list[SkillRecord]
    ) -> list[str]:
        """Build a compact lineage tree representation."""
        lines: list[str] = []

        # Batch-load all ancestries in a single DB connection
        ancestry_map = self._store.get_ancestry_batch([s.id for s in skills])

        for skill in skills:
            lin = skill.lineage
            origin_tag = lin.origin.value
            gen = lin.generation

            # Build ancestry chain
            ancestors = ancestry_map.get(skill.id, [])
            if ancestors:
                chain = " -> ".join(
                    f"{a.name}(g{a.lineage.generation})"
                    for a in ancestors
                )
                lines.append(
                    f"{skill.name} (g{gen}, {origin_tag}): "
                    f"{chain}"
                )
            else:
                lines.append(
                    f"{skill.name} (g{gen}, {origin_tag})"
                )

        return lines

    def _build_detail_table(
        self,
        skills: list[SkillRecord],
        reports: dict[str, HealthReport],
    ) -> list[str]:
        """Build a detailed metrics table for L2 context."""
        lines: list[str] = [
            "| Skill | Version | Origin | Sel | Appl | Comp | Fb | Eff.Rate | Health |",
            "|-------|---------|--------|-----|------|------|----|----------|--------|",
        ]

        for skill in skills:
            sel = skill.total_selections
            rates = SkillRates.from_record(skill)
            eff = rates.effective_rate if rates is not None else 0.0
            report = reports.get(skill.id)
            health_status = (
                "OK" if report and report.is_healthy else "WARN"
            )
            lines.append(
                f"| {skill.name} | {skill.version} "
                f"| {skill.lineage.origin.value} "
                f"| {sel} | {skill.total_applied} "
                f"| {skill.total_completions} "
                f"| {skill.total_fallbacks} "
                f"| {eff:.2f} | {health_status} |"
            )

        return lines

    def _build_health_diagnostics(
        self,
        skills: list[SkillRecord],
        reports: dict[str, HealthReport],
    ) -> list[str]:
        """Build health diagnostic details for L2 context."""
        lines: list[str] = []

        for skill in skills:
            report = reports.get(skill.id)
            if report is None or report.is_healthy:
                continue

            lines.append(
                f"- {skill.name}: UNHEALTHY"
            )
            for suggestion in report.suggestions:
                targets = (
                    ", ".join(suggestion.target_skill_ids) or "(new)"
                )
                lines.append(
                    f"  [{suggestion.evolution_type.value}] "
                    f"{targets}: {suggestion.direction}"
                )

        return lines

    def _build_judgment_history(
        self, skills: list[SkillRecord]
    ) -> list[str]:
        """Build recent judgment history for L2 context."""
        lines: list[str] = []
        history_limit = 5  # Per skill, to keep within budget

        skill_ids = {s.id for s in skills}
        batch = self._store.get_judgments_batch(skill_ids, history_limit)

        for skill in skills:
            judgments = batch.get(skill.id, [])
            if not judgments:
                continue

            applied_count = sum(1 for j in judgments if j["applied"])
            completed_count = sum(1 for j in judgments if j["completed"])
            fell_back_count = sum(1 for j in judgments if j["fell_back"])

            lines.append(
                f"- {skill.name} (last {len(judgments)}): "
                f"applied={applied_count}, "
                f"completed={completed_count}, "
                f"fell_back={fell_back_count}"
            )

        return lines
