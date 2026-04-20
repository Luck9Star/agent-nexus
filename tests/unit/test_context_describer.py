"""Unit tests for agent_nexus.platform.evolution.context_describer.

Tests L0/L1/L2 tiered context generation for EvolutionContextDescriber.
Uses unittest.mock to mock EvolutionStore and HealthChecker.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent_nexus.models.evolution import (
    EvolutionMetrics,
    EvolutionType,
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)
from agent_nexus.platform.evolution.analyzer import EvolutionSuggestion
from agent_nexus.platform.evolution.context_describer import (
    EvolutionContextDescriber,
)
from agent_nexus.platform.evolution.health import HealthChecker, HealthReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_now = datetime.now(timezone.utc)


def _make_record(
    skill_id: str = "skill-1",
    name: str = "test-skill",
    *,
    version: str = "1.0.0",
    origin: SkillOrigin = SkillOrigin.IMPORTED,
    generation: int = 0,
    parent_ids: list[str] | None = None,
    directory: str = "skills/test",
    is_active: bool = True,
    selections: int = 0,
    applied: int = 0,
    completions: int = 0,
    fallbacks: int = 0,
) -> SkillRecord:
    """Create a SkillRecord for testing."""
    return SkillRecord(
        id=skill_id,
        name=name,
        version=version,
        lineage=SkillLineage(
            origin=origin,
            generation=generation,
            parent_skill_ids=parent_ids or [],
            content_diff=None,
            content_snapshot=None,
        ),
        directory=directory,
        is_active=is_active,
        total_selections=selections,
        total_applied=applied,
        total_completions=completions,
        total_fallbacks=fallbacks,
        first_seen=_now,
        last_updated=_now,
    )


def _make_health_report(
    skill_id: str,
    skill_name: str,
    is_healthy: bool,
    suggestions: list[EvolutionSuggestion] | None = None,
    selections: int = 0,
    applied: int = 0,
    completions: int = 0,
    fallbacks: int = 0,
) -> HealthReport:
    """Create a HealthReport for testing."""
    sel = selections
    metrics: dict[str, float] = {
        "total_selections": float(sel),
    }
    if sel > 0:
        metrics["applied_rate"] = applied / sel
        metrics["completion_rate"] = (
            completions / applied if applied > 0 else 0.0
        )
        metrics["effective_rate"] = completions / sel
        metrics["fallback_rate"] = fallbacks / sel

    return HealthReport(
        skill_id=skill_id,
        skill_name=skill_name,
        is_healthy=is_healthy,
        suggestions=suggestions or [],
        metrics=metrics,
    )


def _mock_store(
    active_skills: list[SkillRecord],
    metrics: EvolutionMetrics,
    ancestry_map: dict[str, list[SkillRecord]] | None = None,
    judgments_map: dict[str, list[dict]] | None = None,
) -> MagicMock:
    """Create a partially-mocked EvolutionStore.

    Mocks:
      - get_active_skills() -> active_skills
      - get_metrics() -> metrics
      - get_ancestry(skill_id) -> ancestry_map.get(skill_id, [])
      - get_judgments_for_skill(skill_id, limit=N) -> judgments_map.get(skill_id, [])
    """
    store = MagicMock()
    store.get_active_skills.return_value = active_skills
    store.get_metrics.return_value = metrics

    # get_ancestry needs careful handling since it takes skill_id
    def mock_ancestry(skill_id: str, max_depth: int = 10) -> list[SkillRecord]:
        return ancestry_map.get(skill_id, []) if ancestry_map else []

    store.get_ancestry.side_effect = mock_ancestry

    # get_ancestry_batch(skill_ids, max_depth) — batch fetch used by
    # _build_lineage_tree
    def mock_ancestry_batch(
        skill_ids: list[str], max_depth: int = 10,
    ) -> dict[str, list[SkillRecord]]:
        if not ancestry_map:
            return {}
        return {sid: ancestry_map.get(sid, []) for sid in skill_ids}

    store.get_ancestry_batch.side_effect = mock_ancestry_batch

    # get_judgments_for_skill(skill_id, limit=N) — legacy per-skill fetch
    def mock_judgments(skill_id: str, limit: int = 50) -> list[dict]:
        return judgments_map.get(skill_id, []) if judgments_map else []

    store.get_judgments_for_skill.side_effect = mock_judgments

    # get_judgments_batch(skill_ids, limit_per_skill) — batch fetch
    def mock_judgments_batch(
        skill_ids: set[str], limit_per_skill: int = 50,
    ) -> dict[str, list[dict]]:
        if not judgments_map:
            return {}
        return {sid: judgments_map.get(sid, []) for sid in skill_ids}

    store.get_judgments_batch.side_effect = mock_judgments_batch

    return store


def _mock_health_checker(reports: dict[str, HealthReport]) -> MagicMock:
    """Create a partially-mocked HealthChecker that returns fixed reports."""
    checker = MagicMock(spec=HealthChecker)
    checker.diagnose_skills.return_value = reports
    return checker


# ============================================================================
# TestL0Context
# ============================================================================


class TestL0Context:
    def test_no_active_skills(self) -> None:
        """L0 with no active skills returns the empty-state message."""
        store = _mock_store(
            active_skills=[],
            metrics=EvolutionMetrics(),
        )
        describer = EvolutionContextDescriber(store)
        result = describer.l0_context()
        assert result == "[Evolution] No active skills"

    def test_with_skills(self) -> None:
        """L0 with active skills returns summary with count, evolved count, eff rate."""
        r1 = _make_record(
            "s1", "alpha",
            origin=SkillOrigin.IMPORTED,
            selections=100,
            applied=80,
            completions=70,
        )
        r2 = _make_record(
            "s2", "beta",
            origin=SkillOrigin.FIXED,
            selections=50,
            applied=40,
            completions=30,
        )
        store = _mock_store(
            active_skills=[r1, r2],
            metrics=EvolutionMetrics(
                total_selections=150,
                total_applied=120,
                total_completions=100,
                total_fallbacks=5,
            ),
        )
        describer = EvolutionContextDescriber(store)
        result = describer.l0_context()

        assert "2 active skills" in result
        # Only s2 is evolved (origin != imported)
        assert "1 evolved" in result
        # effective_rate = 100 / 150 = 0.667
        assert "0.67" in result

    def test_all_imported(self) -> None:
        """L0 when all skills are imported shows 0 evolved."""
        r1 = _make_record("s1", "a", origin=SkillOrigin.IMPORTED)
        r2 = _make_record("s2", "b", origin=SkillOrigin.IMPORTED)
        store = _mock_store(
            active_skills=[r1, r2],
            metrics=EvolutionMetrics(
                total_selections=10,
                total_applied=8,
                total_completions=5,
                total_fallbacks=0,
            ),
        )
        describer = EvolutionContextDescriber(store)
        result = describer.l0_context()

        assert "2 active skills" in result
        assert "0 evolved" in result

    def test_zero_selections_effective_rate_zero(self) -> None:
        """L0 handles zero selections without division error."""
        r = _make_record("s1", "zero", selections=0)
        store = _mock_store(
            active_skills=[r],
            metrics=EvolutionMetrics(
                total_selections=0,
                total_applied=0,
                total_completions=0,
                total_fallbacks=0,
            ),
        )
        describer = EvolutionContextDescriber(store)
        result = describer.l0_context()

        assert "1 active skills" in result
        assert "0 evolved" in result
        # Should not crash; effective_rate is 0
        assert "0.00" in result


# ============================================================================
# TestL1Context
# ============================================================================


class TestL1Context:
    def test_no_skills(self) -> None:
        """L1 with no active skills returns the empty-state message."""
        store = _mock_store(
            active_skills=[],
            metrics=EvolutionMetrics(),
        )
        describer = EvolutionContextDescriber(store)
        result = describer.l1_context()
        assert result == "[Evolution] No matching active skills"

    def test_with_skills_table(self) -> None:
        """L1 returns a markdown table with per-skill metrics."""
        r1 = _make_record(
            "s1", "alpha",
            selections=100,
            applied=80,
            completions=70,
            fallbacks=5,
        )
        r2 = _make_record(
            "s2", "beta",
            selections=200,
            applied=150,
            completions=100,
            fallbacks=30,
        )
        report1 = _make_health_report(
            "s1", "alpha", is_healthy=True,
            selections=100, applied=80, completions=70, fallbacks=5,
        )
        report2 = _make_health_report(
            "s2", "beta", is_healthy=False,
            selections=200, applied=150, completions=100, fallbacks=30,
            suggestions=[
                EvolutionSuggestion(
                    evolution_type=EvolutionType.FIX,
                    target_skill_ids=["s2"],
                    direction="High fallback rate",
                ),
            ],
        )
        store = _mock_store(
            active_skills=[r1, r2],
            metrics=EvolutionMetrics(),
        )
        # Patch HealthChecker so diagnose_skills returns our fixed reports
        with patch.object(
            HealthChecker, "diagnose_skills",
            return_value={"s1": report1, "s2": report2},
        ):
            describer = EvolutionContextDescriber(store)
            result = describer.l1_context()

        # Header check
        assert "[Evolution Skill Metrics]" in result
        # Table header
        assert "| Skill | Selections | Eff. Rate | Health |" in result
        # Both skills present
        assert "alpha" in result
        assert "beta" in result
        # s1 is healthy -> OK; s2 is unhealthy -> WARN
        assert "OK" in result
        assert "WARN" in result
        # effective rates
        # s1: 70/100 = 0.70
        assert "0.70" in result
        # s2: 100/200 = 0.50
        assert "0.50" in result

    def test_skill_ids_filter(self) -> None:
        """L1 only includes skills matching the provided skill_ids filter."""
        r1 = _make_record("s1", "alpha", selections=100)
        r2 = _make_record("s2", "beta", selections=200)
        r3 = _make_record("s3", "gamma", selections=300)
        healthy = _make_health_report("s1", "alpha", is_healthy=True)
        store = _mock_store(
            active_skills=[r1, r2, r3],
            metrics=EvolutionMetrics(),
        )
        with patch.object(
            HealthChecker, "diagnose_skills",
            return_value={"s1": healthy},
        ):
            describer = EvolutionContextDescriber(store)
            result = describer.l1_context(skill_ids=["s1", "s3"])

        assert "alpha" in result
        assert "gamma" in result
        assert "beta" not in result

    def test_sorted_by_selections_desc(self) -> None:
        """L1 sorts skills by total_selections descending."""
        r_low = _make_record("s1", "low", selections=10, is_active=True)
        r_high = _make_record("s2", "high", selections=500, is_active=True)
        r_mid = _make_record("s3", "mid", selections=100, is_active=True)

        def mock_diagnose_skills(skill_ids=None, *, skills=None) -> dict[str, HealthReport]:
            return {
                "s1": _make_health_report("s1", "low", is_healthy=True),
                "s2": _make_health_report("s2", "high", is_healthy=True),
                "s3": _make_health_report("s3", "mid", is_healthy=True),
            }

        store = _mock_store(
            active_skills=[r_low, r_high, r_mid],
            metrics=EvolutionMetrics(),
        )
        with patch.object(HealthChecker, "diagnose_skills", side_effect=mock_diagnose_skills):
            describer = EvolutionContextDescriber(store)
            result = describer.l1_context()

        lines = result.split("\n")
        # Find row indices (skip header rows)
        skill_lines = [l for l in lines if l.startswith("| ") and "Skill |" not in l]
        names = [l.split("|")[1].strip() for l in skill_lines]
        # high (500) > mid (100) > low (10)
        assert names == ["high", "mid", "low"]


# ============================================================================
# TestL2Context
# ============================================================================


class TestL2Context:
    def test_no_skills(self) -> None:
        """L2 with no active skills returns the empty-state message."""
        store = _mock_store(
            active_skills=[],
            metrics=EvolutionMetrics(),
        )
        describer = EvolutionContextDescriber(store)
        result = describer.l2_context()
        assert result == "[Evolution] No matching active skills"

    def test_full_output(self) -> None:
        """L2 returns all four sections: lineage, details, health, history."""
        r = _make_record(
            "s1", "my-skill",
            origin=SkillOrigin.FIXED,
            generation=1,
            parent_ids=["p1"],
            selections=100,
            applied=80,
            completions=70,
            fallbacks=10,
        )
        parent = _make_record("p1", "my-skill", generation=0)
        unhealthy_report = _make_health_report(
            "s1", "my-skill",
            is_healthy=False,
            selections=100, applied=80, completions=70, fallbacks=10,
            suggestions=[
                EvolutionSuggestion(
                    evolution_type=EvolutionType.FIX,
                    target_skill_ids=["s1"],
                    direction="High fallback rate",
                ),
            ],
        )
        judgments = [
            {
                "id": "j1",
                "analysis_id": "a1",
                "skill_id": "s1",
                "selected": True,
                "applied": True,
                "completed": True,
                "fell_back": False,
            },
            {
                "id": "j2",
                "analysis_id": "a2",
                "skill_id": "s1",
                "selected": True,
                "applied": True,
                "completed": False,
                "fell_back": True,
            },
        ]
        store = _mock_store(
            active_skills=[r],
            metrics=EvolutionMetrics(),
            ancestry_map={"s1": [parent]},
            judgments_map={"s1": judgments},
        )
        with patch.object(
            HealthChecker, "diagnose_skills",
            return_value={"s1": unhealthy_report},
        ):
            describer = EvolutionContextDescriber(store)
            result = describer.l2_context()

        # Section 1: Lineage
        assert "[Evolution Lineage]" in result
        assert "my-skill" in result
        # parent chain: g0 -> g1
        assert "parent(g0)" in result or "my-skill (g1" in result

        # Section 2: Details
        assert "[Evolution Details]" in result
        assert "my-skill" in result
        assert "fixed" in result  # origin

        # Section 3: Health diagnostics (unhealthy -> included)
        assert "[Evolution Health]" in result
        assert "UNHEALTHY" in result
        assert "fix" in result.lower()

        # Section 4: Judgment history
        assert "[Evolution History]" in result
        assert "applied=2" in result
        assert "completed=1" in result
        assert "fell_back=1" in result

    def test_skill_ids_filter(self) -> None:
        """L2 only includes skills matching the provided skill_ids filter."""
        r1 = _make_record("s1", "alpha", selections=100)
        r2 = _make_record("s2", "beta", selections=200)
        r3 = _make_record("s3", "gamma", selections=300)

        def mock_diagnose_skills(skill_ids=None, *, skills=None) -> dict[str, HealthReport]:
            return {
                "s1": _make_health_report("s1", "alpha", is_healthy=True),
                "s2": _make_health_report("s2", "beta", is_healthy=True),
                "s3": _make_health_report("s3", "gamma", is_healthy=True),
            }

        store = _mock_store(
            active_skills=[r1, r2, r3],
            metrics=EvolutionMetrics(),
            judgments_map={"s1": [], "s2": [], "s3": []},
        )
        with patch.object(HealthChecker, "diagnose_skills", side_effect=mock_diagnose_skills):
            describer = EvolutionContextDescriber(store)
            result = describer.l2_context(skill_ids=["s1", "s3"])

        assert "alpha" in result
        assert "gamma" in result
        assert "beta" not in result

    def test_healthy_skill_no_health_section(self) -> None:
        """L2 healthy skills do not generate health warnings or health section."""
        r = _make_record(
            "s1", "healthy-skill",
            selections=100,
            applied=80,
            completions=70,
            fallbacks=5,
        )
        healthy_report = _make_health_report(
            "s1", "healthy-skill",
            is_healthy=True,
            selections=100, applied=80, completions=70, fallbacks=5,
        )
        store = _mock_store(
            active_skills=[r],
            metrics=EvolutionMetrics(),
            judgments_map={"s1": []},
        )
        with patch.object(
            HealthChecker, "diagnose_skills",
            return_value={"s1": healthy_report},
        ):
            describer = EvolutionContextDescriber(store)
            result = describer.l2_context()

        # Should NOT contain health section since all skills are healthy
        assert "[Evolution Health]" not in result
        # But should still contain other sections
        assert "[Evolution Lineage]" in result
        assert "[Evolution Details]" in result

    def test_mixed_health_contains_health_section(self) -> None:
        """L2 with at least one unhealthy skill includes the health section."""
        r_healthy = _make_record(
            "s1", "good",
            selections=100, applied=80, completions=70, fallbacks=5,
        )
        r_unhealthy = _make_record(
            "s2", "bad",
            selections=100, applied=80, completions=20, fallbacks=5,
        )
        healthy_report = _make_health_report(
            "s1", "good", is_healthy=True,
            selections=100, applied=80, completions=70, fallbacks=5,
        )
        unhealthy_report = _make_health_report(
            "s2", "bad", is_healthy=False,
            selections=100, applied=80, completions=20, fallbacks=5,
            suggestions=[
                EvolutionSuggestion(
                    evolution_type=EvolutionType.FIX,
                    target_skill_ids=["s2"],
                    direction="Low completion rate",
                ),
            ],
        )
        store = _mock_store(
            active_skills=[r_healthy, r_unhealthy],
            metrics=EvolutionMetrics(),
            judgments_map={"s1": [], "s2": []},
        )
        with patch.object(
            HealthChecker, "diagnose_skills",
            return_value={"s1": healthy_report, "s2": unhealthy_report},
        ):
            describer = EvolutionContextDescriber(store)
            result = describer.l2_context()

        assert "[Evolution Health]" in result
        assert "bad" in result
        assert "UNHEALTHY" in result

    def test_no_judgments_no_history_section(self) -> None:
        """L2 without judgments omits the history section."""
        r = _make_record("s1", "new-skill", selections=0)
        healthy_report = _make_health_report(
            "s1", "new-skill", is_healthy=True,
        )
        store = _mock_store(
            active_skills=[r],
            metrics=EvolutionMetrics(),
            judgments_map={"s1": []},
        )
        with patch.object(
            HealthChecker, "diagnose_skills",
            return_value={"s1": healthy_report},
        ):
            describer = EvolutionContextDescriber(store)
            result = describer.l2_context()

        # Lineage and details should still be present
        assert "[Evolution Lineage]" in result
        assert "[Evolution Details]" in result
        # Health section should not appear (all healthy)
        assert "[Evolution Health]" not in result
        # History section should not appear (no judgments)
        assert "[Evolution History]" not in result

    def test_lineage_tree_no_ancestors(self) -> None:
        """L2 skill with no ancestors shows only its own info."""
        r = _make_record(
            "s1", "root-skill",
            origin=SkillOrigin.CAPTURED,
            generation=0,
        )
        healthy_report = _make_health_report("s1", "root-skill", is_healthy=True)
        store = _mock_store(
            active_skills=[r],
            metrics=EvolutionMetrics(),
            ancestry_map={"s1": []},  # No ancestors
            judgments_map={"s1": []},
        )
        with patch.object(
            HealthChecker, "diagnose_skills",
            return_value={"s1": healthy_report},
        ):
            describer = EvolutionContextDescriber(store)
            result = describer.l2_context()

        assert "[Evolution Lineage]" in result
        # Should show the skill itself with g0
        assert "root-skill (g0, captured)" in result

    def test_detail_table_columns(self) -> None:
        """L2 detail table contains expected columns."""
        r = _make_record(
            "s1", "my-skill",
            version="2.1.0",
            origin=SkillOrigin.DERIVED,
            generation=3,
            selections=50,
            applied=40,
            completions=30,
            fallbacks=5,
        )
        healthy_report = _make_health_report(
            "s1", "my-skill", is_healthy=True,
            selections=50, applied=40, completions=30, fallbacks=5,
        )
        store = _mock_store(
            active_skills=[r],
            metrics=EvolutionMetrics(),
            judgments_map={"s1": []},
        )
        with patch.object(
            HealthChecker, "diagnose_skills",
            return_value={"s1": healthy_report},
        ):
            describer = EvolutionContextDescriber(store)
            result = describer.l2_context()

        assert "[Evolution Details]" in result
        # Version
        assert "2.1.0" in result
        # Origin
        assert "derived" in result
        # Counters
        assert "50" in result  # selections
        assert "40" in result  # applied
        assert "30" in result  # completions
        assert "5" in result   # fallbacks
        # Effective rate 30/50 = 0.60
        assert "0.60" in result

    def test_skill_with_no_ancestors(self) -> None:
        """_build_lineage_tree else-branch: skill with empty ancestry list."""
        r = _make_record(
            "s-noanc", "orphan-skill",
            origin=SkillOrigin.IMPORTED,
            generation=1,
            parent_ids=[],
        )
        store = _mock_store(
            active_skills=[r],
            metrics=EvolutionMetrics(),
            ancestry_map={"s-noanc": []},
        )
        with patch(
            "agent_nexus.platform.evolution.context_describer.HealthChecker"
        ) as hc_cls:
            hc_cls.return_value = _mock_health_checker(
                {"s-noanc": _make_health_report("s-noanc", "orphan-skill", True)}
            )
            describer = EvolutionContextDescriber(store)
            result = describer.l2_context()

        # No ancestry chain — just the name with generation and origin
        assert "orphan-skill (g1, imported)" in result
