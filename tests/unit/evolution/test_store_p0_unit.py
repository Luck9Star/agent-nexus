"""P0 unit tests for EvolutionStore gaps + HealthChecker.get_health_summary.

Covers:
- EvolutionStore.get_metrics (aggregation, agent filter, inactive exclusion)
- EvolutionStore.deactivate_skill (activate/deactivate lifecycle)
- EvolutionStore.get_skill_records_batch (batch load, partial miss)
- EvolutionStore.get_children (lineage parent-child queries)
- HealthChecker.get_health_summary (aggregate diagnostics via mock store)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent_nexus.models.evolution import (
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)
from agent_nexus.platform.evolution.health import HealthChecker
from agent_nexus.platform.evolution.store import EvolutionStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> EvolutionStore:
    return EvolutionStore(tmp_path / "test.db")


def _skill(
    id: str = "sk-1",
    name: str = "test-skill",
    directory: str = "",
    selections: int = 0,
    applied: int = 0,
    completions: int = 0,
    fallbacks: int = 0,
    lineage: SkillLineage | None = None,
) -> SkillRecord:
    return SkillRecord(
        id=id,
        name=name,
        directory=directory,
        lineage=lineage or SkillLineage(),
        total_selections=selections,
        total_applied=applied,
        total_completions=completions,
        total_fallbacks=fallbacks,
    )


def _mock_store(skills: list[SkillRecord] | None = None) -> MagicMock:
    """Create a MagicMock that looks like an EvolutionStore for HealthChecker."""
    store = MagicMock(spec=EvolutionStore)
    store.get_active_skills.return_value = skills or []
    return store


# ===================================================================
# TestGetMetrics
# ===================================================================


@pytest.mark.unit
class TestGetMetrics:
    """EvolutionStore.get_metrics — aggregation over active skill_records."""

    def test_empty_db_returns_zero_metrics(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        metrics = store.get_metrics()
        assert metrics.total_selections == 0
        assert metrics.total_applied == 0
        assert metrics.total_completions == 0
        assert metrics.total_fallbacks == 0

    def test_single_skill_metrics(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_skill_record(
            _skill(
                id="sk-1",
                selections=100,
                applied=80,
                completions=60,
                fallbacks=20,
            )
        )
        metrics = store.get_metrics()
        assert metrics.total_selections == 100
        assert metrics.total_applied == 80
        assert metrics.total_completions == 60
        assert metrics.total_fallbacks == 20

    def test_multiple_skills_aggregate(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_skill_record(
            _skill(
                id="sk-1",
                selections=100,
                applied=80,
                completions=60,
                fallbacks=20,
            )
        )
        store.save_skill_record(
            _skill(
                id="sk-2",
                selections=50,
                applied=40,
                completions=30,
                fallbacks=10,
            )
        )
        store.save_skill_record(
            _skill(
                id="sk-3",
                selections=200,
                applied=150,
                completions=100,
                fallbacks=50,
            )
        )
        metrics = store.get_metrics()
        assert metrics.total_selections == 350
        assert metrics.total_applied == 270
        assert metrics.total_completions == 190
        assert metrics.total_fallbacks == 80

    def test_filters_by_agent_name(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_skill_record(
            _skill(
                id="sk-1",
                name="skill-a",
                directory="agents/agent-a/skills/s1",
                selections=100,
                applied=80,
                completions=60,
                fallbacks=20,
            )
        )
        store.save_skill_record(
            _skill(
                id="sk-2",
                name="skill-b",
                directory="agents/agent-b/skills/s2",
                selections=50,
                applied=40,
                completions=30,
                fallbacks=10,
            )
        )
        # Filter for agent-a only
        metrics_a = store.get_metrics(agent_name="agent-a")
        assert metrics_a.total_selections == 100
        assert metrics_a.total_applied == 80
        assert metrics_a.total_completions == 60
        assert metrics_a.total_fallbacks == 20

        # Filter for agent-b only
        metrics_b = store.get_metrics(agent_name="agent-b")
        assert metrics_b.total_selections == 50
        assert metrics_b.total_applied == 40
        assert metrics_b.total_completions == 30
        assert metrics_b.total_fallbacks == 10

    def test_inactive_skills_excluded(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_skill_record(
            _skill(
                id="sk-1",
                selections=100,
                applied=80,
                completions=60,
                fallbacks=20,
            )
        )
        store.save_skill_record(
            _skill(
                id="sk-2",
                selections=50,
                applied=40,
                completions=30,
                fallbacks=10,
            )
        )
        # Deactivate sk-1
        store.deactivate_skill("sk-1")
        metrics = store.get_metrics()
        # Only sk-2 remains active
        assert metrics.total_selections == 50
        assert metrics.total_applied == 40
        assert metrics.total_completions == 30
        assert metrics.total_fallbacks == 10

    def test_agent_name_with_special_chars(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        # Skill with % in agent name — LIKE must escape it
        store.save_skill_record(
            _skill(
                id="sk-1",
                name="pct-skill",
                directory="agents/agent%name/skills/s1",
                selections=100,
                applied=80,
                completions=60,
                fallbacks=20,
            )
        )
        # Skill with _ in agent name — LIKE must escape it
        store.save_skill_record(
            _skill(
                id="sk-2",
                name="us-skill",
                directory="agents/agent_name/skills/s2",
                selections=50,
                applied=40,
                completions=30,
                fallbacks=10,
            )
        )
        # Searching for literal "agent%name" should match only sk-1
        metrics_pct = store.get_metrics(agent_name="agent%name")
        assert metrics_pct.total_selections == 100

        # Searching for literal "agent_name" should match only sk-2
        metrics_us = store.get_metrics(agent_name="agent_name")
        assert metrics_us.total_selections == 50

    @pytest.mark.parametrize(
        "agent_name",
        [None, ""],
        ids=["none", "empty_string"],
    )
    def test_empty_agent_name_returns_all(
        self, tmp_path: Path, agent_name: str | None
    ) -> None:
        store = _make_store(tmp_path)
        store.save_skill_record(
            _skill(
                id="sk-1",
                directory="agents/a/skills/s1",
                selections=100,
                applied=80,
                completions=60,
                fallbacks=20,
            )
        )
        store.save_skill_record(
            _skill(
                id="sk-2",
                directory="agents/b/skills/s2",
                selections=50,
                applied=40,
                completions=30,
                fallbacks=10,
            )
        )
        metrics = store.get_metrics(agent_name=agent_name)
        assert metrics.total_selections == 150
        assert metrics.total_applied == 120


# ===================================================================
# TestDeactivateSkill
# ===================================================================


@pytest.mark.unit
class TestDeactivateSkill:
    """EvolutionStore.deactivate_skill — soft-delete via is_active flag."""

    def test_deactivate_existing_returns_true(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_skill_record(_skill(id="sk-1"))
        assert store.deactivate_skill("sk-1") is True

    def test_deactivate_nonexistent_returns_false(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.deactivate_skill("sk-nonexistent") is False

    def test_deactivate_already_inactive_idempotent(
        self, tmp_path: Path
    ) -> None:
        """Calling deactivate_skill on an already-inactive skill is idempotent.

        The SQL uses ``WHERE id = ?`` without ``AND is_active = 1``, so
        re-deactivating still returns True (rowcount = 1).  This is by
        design: the operation is a no-op that still acknowledges the row
        exists.
        """
        store = _make_store(tmp_path)
        store.save_skill_record(_skill(id="sk-1"))
        assert store.deactivate_skill("sk-1") is True
        # Idempotent — row still exists, UPDATE matches
        assert store.deactivate_skill("sk-1") is True
        # But the skill is definitely not in active list
        record = store.get_skill_record("sk-1")
        assert record is not None
        assert record.is_active is False

    def test_deactivate_removes_from_active_skills(
        self, tmp_path: Path
    ) -> None:
        store = _make_store(tmp_path)
        store.save_skill_record(_skill(id="sk-1", name="active-skill"))
        store.save_skill_record(_skill(id="sk-2", name="keep-skill"))

        assert len(store.get_active_skills()) == 2
        store.deactivate_skill("sk-1")
        active = store.get_active_skills()
        assert len(active) == 1
        assert active[0].id == "sk-2"


# ===================================================================
# TestGetSkillRecordsBatch
# ===================================================================


@pytest.mark.unit
class TestGetSkillRecordsBatch:
    """EvolutionStore.get_skill_records_batch — chunked IN query loading."""

    def test_empty_list_returns_empty_dict(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        result = store.get_skill_records_batch([])
        assert result == {}

    def test_single_id_found(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_skill_record(
            _skill(id="sk-1", name="found", selections=10, applied=5)
        )
        result = store.get_skill_records_batch(["sk-1"])
        assert "sk-1" in result
        assert result["sk-1"].name == "found"
        assert result["sk-1"].total_selections == 10

    def test_multiple_ids_found(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_skill_record(_skill(id="sk-1", name="first"))
        store.save_skill_record(_skill(id="sk-2", name="second"))
        store.save_skill_record(_skill(id="sk-3", name="third"))
        result = store.get_skill_records_batch(["sk-1", "sk-2", "sk-3"])
        assert len(result) == 3
        assert result["sk-1"].name == "first"
        assert result["sk-2"].name == "second"
        assert result["sk-3"].name == "third"

    def test_some_ids_missing(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_skill_record(_skill(id="sk-1", name="exists"))
        result = store.get_skill_records_batch(["sk-1", "sk-missing"])
        assert len(result) == 1
        assert "sk-1" in result
        assert "sk-missing" not in result

    def test_all_ids_missing_returns_empty(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        result = store.get_skill_records_batch(["sk-nope-1", "sk-nope-2"])
        assert result == {}


# ===================================================================
# TestGetChildren
# ===================================================================


@pytest.mark.unit
class TestGetChildren:
    """EvolutionStore.get_children — skill_lineage_parents queries."""

    def test_no_children_returns_empty(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_skill_record(_skill(id="sk-orphan", name="orphan"))
        assert store.get_children("sk-orphan") == []

    def test_single_child(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        parent = _skill(id="sk-parent", name="parent")
        store.save_skill_record(parent)

        # Evolve creates parent-child link
        child = _skill(
            id="sk-child",
            name="child",
            lineage=SkillLineage(
                origin=SkillOrigin.FIXED,
                parent_skill_ids=["sk-parent"],
            ),
        )
        result = store.evolve_skill(child, parent_skill_ids=["sk-parent"])
        assert result.success

        children = store.get_children("sk-parent")
        assert children == ["sk-child"]

    def test_multiple_children(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_skill_record(_skill(id="sk-parent", name="parent"))

        for i in range(3):
            child = _skill(
                id=f"sk-child-{i}",
                name=f"child-{i}",
                lineage=SkillLineage(
                    origin=SkillOrigin.DERIVED,
                    parent_skill_ids=["sk-parent"],
                ),
            )
            result = store.evolve_skill(
                child, parent_skill_ids=["sk-parent"]
            )
            assert result.success

        children = store.get_children("sk-parent")
        assert len(children) == 3
        assert set(children) == {"sk-child-0", "sk-child-1", "sk-child-2"}

    def test_evolved_skill_has_children(self, tmp_path: Path) -> None:
        """Full evolve lifecycle: parent -> evolved child -> verify link."""
        store = _make_store(tmp_path)
        parent = _skill(
            id="sk-original",
            name="original",
            selections=100,
            applied=50,
            completions=10,
            fallbacks=40,
        )
        store.save_skill_record(parent)

        evolved = _skill(
            id="sk-evolved",
            name="original-v2",
            lineage=SkillLineage(
                origin=SkillOrigin.FIXED,
                parent_skill_ids=["sk-original"],
            ),
        )
        result = store.evolve_skill(evolved, parent_skill_ids=["sk-original"])
        assert result.success

        children = store.get_children("sk-original")
        assert "sk-evolved" in children

        # Verify the parent is deactivated after FIX evolution
        parent_record = store.get_skill_record("sk-original")
        assert parent_record is not None
        assert parent_record.is_active is False


# ===================================================================
# TestGetHealthSummary
# ===================================================================


@pytest.mark.unit
class TestGetHealthSummary:
    """HealthChecker.get_health_summary — aggregate diagnostics via mock store."""

    def test_empty_skills_returns_zeros(self) -> None:
        store = _mock_store(skills=[])
        checker = HealthChecker(store)
        summary = checker.get_health_summary()
        assert summary["total_skills"] == 0
        assert summary["healthy"] == 0
        assert summary["unhealthy"] == 0
        assert summary["fix_suggestions"] == 0
        assert summary["derived_suggestions"] == 0
        assert summary["captured_suggestions"] == 0
        assert summary["unhealthy_skills"] == []

    def test_all_healthy_skills(self) -> None:
        store = _mock_store(
            skills=[
                _skill(
                    id="sk-1",
                    name="good-1",
                    selections=100,
                    applied=80,
                    completions=70,
                    fallbacks=10,
                ),
                _skill(
                    id="sk-2",
                    name="good-2",
                    selections=50,
                    applied=40,
                    completions=35,
                    fallbacks=5,
                ),
            ]
        )
        checker = HealthChecker(store)
        summary = checker.get_health_summary()
        assert summary["total_skills"] == 2
        assert summary["healthy"] == 2
        assert summary["unhealthy"] == 0
        assert summary["unhealthy_skills"] == []

    def test_mixed_healthy_unhealthy(self) -> None:
        """One healthy skill, one triggering FIX (high fallback), one
        triggering DERIVED (moderate effectiveness).

        Counter invariants: completions + fallbacks <= applied <= selections.
        FIX rule 1: fallback_rate = fallbacks / selections > 0.4
        DERIVED rule 3: effective_rate = completions / selections < 0.55
                        AND applied_rate = applied / selections > 0.25
        """
        store = _mock_store(
            skills=[
                # Healthy: fallback_rate=0.1, applied_rate=0.8, completion=0.75
                _skill(
                    id="sk-healthy",
                    name="healthy-skill",
                    selections=100,
                    applied=80,
                    completions=60,
                    fallbacks=10,
                ),
                # FIX: fallback_rate = 45/100 = 0.45 > 0.4
                # completions + fallbacks = 5 + 45 = 50 <= applied(50) OK
                _skill(
                    id="sk-broken",
                    name="broken-skill",
                    selections=100,
                    applied=50,
                    completions=5,
                    fallbacks=45,
                ),
                # DERIVED: effective_rate = 20/100 = 0.2 < 0.55
                #          applied_rate = 50/100 = 0.5 > 0.25
                # completions + fallbacks = 20 + 10 = 30 <= applied(50) OK
                _skill(
                    id="sk-moderate",
                    name="moderate-skill",
                    selections=100,
                    applied=50,
                    completions=20,
                    fallbacks=10,
                ),
            ]
        )
        checker = HealthChecker(store)
        summary = checker.get_health_summary()
        assert summary["total_skills"] == 3
        assert summary["healthy"] == 1
        assert summary["unhealthy"] == 2

    def test_unhealthy_skills_names_listed(self) -> None:
        """Verify unhealthy_skills lists the *names* of unhealthy skills.

        Counter invariants: completions + fallbacks <= applied <= selections.
        """
        store = _mock_store(
            skills=[
                # FIX: fallback_rate = 45/100 = 0.45 > 0.4
                _skill(
                    id="sk-1",
                    name="broken-a",
                    selections=100,
                    applied=50,
                    completions=5,
                    fallbacks=45,
                ),
                # Healthy
                _skill(
                    id="sk-2",
                    name="healthy-b",
                    selections=100,
                    applied=80,
                    completions=70,
                    fallbacks=10,
                ),
                # DERIVED: effective_rate = 20/100 = 0.2 < 0.55, applied=0.5 > 0.25
                _skill(
                    id="sk-3",
                    name="moderate-c",
                    selections=100,
                    applied=50,
                    completions=20,
                    fallbacks=10,
                ),
            ]
        )
        checker = HealthChecker(store)
        summary = checker.get_health_summary()
        assert "broken-a" in summary["unhealthy_skills"]
        assert "moderate-c" in summary["unhealthy_skills"]
        assert "healthy-b" not in summary["unhealthy_skills"]
        assert len(summary["unhealthy_skills"]) == 2

    def test_suggestion_type_counts(self) -> None:
        """Verify fix_suggestions and derived_suggestions are counted correctly.

        Counter invariants: completions + fallbacks <= applied <= selections.
        FIX rule 1: fallback_rate = fallbacks / selections > 0.4
        DERIVED rule 3: effective_rate = completions / selections < 0.55
                        AND applied_rate = applied / selections > 0.25
        """
        store = _mock_store(
            skills=[
                # FIX: fallback_rate = 45/100 = 0.45 > 0.4
                # completions + fallbacks = 5 + 45 = 50 <= applied(50) OK
                _skill(
                    id="sk-fix-1",
                    name="fix-skill-1",
                    selections=100,
                    applied=50,
                    completions=5,
                    fallbacks=45,
                ),
                # FIX: fallback_rate = 50/100 = 0.5 > 0.4
                # completions + fallbacks = 0 + 50 = 50 <= applied(50) OK
                _skill(
                    id="sk-fix-2",
                    name="fix-skill-2",
                    selections=100,
                    applied=50,
                    completions=0,
                    fallbacks=50,
                ),
                # DERIVED: effective_rate = 20/100 = 0.2 < 0.55, applied=0.5 > 0.25
                # completions + fallbacks = 20 + 10 = 30 <= applied(50) OK
                _skill(
                    id="sk-derived",
                    name="derived-skill",
                    selections=100,
                    applied=50,
                    completions=20,
                    fallbacks=10,
                ),
            ]
        )
        checker = HealthChecker(store)
        summary = checker.get_health_summary()
        assert summary["fix_suggestions"] == 2
        assert summary["derived_suggestions"] == 1
        assert summary["captured_suggestions"] == 0
