"""Unit tests for SkillStore — the skill_records CRUD layer.

SkillStore is typically created by EvolutionStore, which handles schema
initialization.  For these tests we create an in-memory SQLite database,
run the shared DDL, then instantiate SkillStore directly with a
conn_factory that reuses the same in-memory connection.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_nexus.models.evolution import (
    EvolutionMetrics,
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)
from agent_nexus.platform.evolution._shared import _SCHEMA_SQL
from agent_nexus.platform.evolution.skill_store import SkillStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc(*, days_ago: int = 0) -> datetime:
    ts = datetime.now(UTC)
    if days_ago:
        from datetime import timedelta

        ts = ts - timedelta(days=days_ago)
    return ts


def _make_record(
    *,
    id: str = "skill-1",
    name: str = "fill-template",
    origin: SkillOrigin = SkillOrigin.IMPORTED,
    generation: int = 0,
    parent_ids: list[str] | None = None,
    is_active: bool = True,
    directory: str = "",
    content_snapshot: dict[str, str] | None = None,
) -> SkillRecord:
    lineage = SkillLineage(
        origin=origin,
        generation=generation,
        parent_skill_ids=parent_ids or [],
        content_snapshot=content_snapshot,
    )
    return SkillRecord(
        id=id,
        name=name,
        lineage=lineage,
        is_active=is_active,
        directory=directory,
        first_seen=_utc(),
        last_updated=_utc(),
    )


def _init_schema(conn: sqlite3.Connection) -> None:
    """Execute the shared DDL to create skill tables."""
    conn.execute("PRAGMA foreign_keys=ON")
    for stmt in _SCHEMA_SQL.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


class _StoreFactory:
    """Creates a SkillStore backed by a single in-memory connection."""

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        _init_schema(self._conn)

    @contextmanager
    def conn(self, *, immediate: bool = False) -> Generator[sqlite3.Connection, None, None]:
        yield self._conn

    def make_store(self) -> SkillStore:
        return SkillStore(Path(":memory:"), conn_factory=self.conn)


@pytest.fixture()
def factory() -> _StoreFactory:
    return _StoreFactory()


@pytest.fixture()
def store(factory: _StoreFactory) -> SkillStore:
    return factory.make_store()


# ============================================================================
# save_skill_record + get_skill_record round-trip
# ============================================================================


class TestSaveAndGetSkillRecord:
    def test_save_and_get_round_trip(self, store: SkillStore) -> None:
        record = _make_record()
        store.save_skill_record(record)
        loaded = store.get_skill_record("skill-1")
        assert loaded is not None
        assert loaded.id == "skill-1"
        assert loaded.name == "fill-template"
        assert loaded.is_active is True
        assert loaded.lineage.origin == SkillOrigin.IMPORTED

    def test_get_nonexistent_returns_none(self, store: SkillStore) -> None:
        assert store.get_skill_record("no-such-id") is None

    def test_save_preserves_counters_on_conflict(self, store: SkillStore) -> None:
        original = _make_record(id="s1")
        store.save_skill_record(original)
        # increment counters via the store
        store.increment_counters("s1", selected=True, applied=True, completed=True)
        # save again with zero counters — counters should be preserved
        updated = _make_record(id="s1", name="renamed")
        store.save_skill_record(updated)
        loaded = store.get_skill_record("s1")
        assert loaded is not None
        assert loaded.name == "renamed"
        assert loaded.total_selections == 1
        assert loaded.total_applied == 1
        assert loaded.total_completions == 1

    def test_save_with_lineage_parents(self, store: SkillStore) -> None:
        parent = _make_record(id="p1", name="parent-skill")
        store.save_skill_record(parent)
        child = _make_record(
            id="c1",
            name="child-skill",
            origin=SkillOrigin.DERIVED,
            generation=1,
            parent_ids=["p1"],
        )
        store.save_skill_record(child)
        loaded = store.get_skill_record("c1")
        assert loaded is not None
        assert loaded.lineage.parent_skill_ids == ["p1"]

    def test_save_with_content_snapshot(self, store: SkillStore) -> None:
        record = _make_record(
            content_snapshot={"file1.py": "content1", "file2.py": "content2"},
        )
        store.save_skill_record(record)
        loaded = store.get_skill_record("skill-1")
        assert loaded is not None
        assert loaded.lineage.content_snapshot == {
            "file1.py": "content1",
            "file2.py": "content2",
        }


# ============================================================================
# get_active_skills / get_all_skills / deactivate
# ============================================================================


class TestListAndFilter:
    def _seed(self, store: SkillStore) -> None:
        for i in range(5):
            store.save_skill_record(
                _make_record(id=f"s{i}", name=f"skill-{i}", is_active=True),
            )
        store.save_skill_record(
            _make_record(id="inactive", name="inactive-skill", is_active=False),
        )

    def test_get_active_skills(self, store: SkillStore) -> None:
        self._seed(store)
        active = store.get_active_skills()
        assert len(active) == 5
        assert all(s.is_active for s in active)

    def test_get_all_skills_includes_inactive(self, store: SkillStore) -> None:
        self._seed(store)
        all_skills = store.get_all_skills()
        assert len(all_skills) == 6

    def test_get_active_skills_with_pagination(self, store: SkillStore) -> None:
        self._seed(store)
        page1 = store.get_active_skills(limit=3, offset=0)
        page2 = store.get_active_skills(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 2

    def test_deactivate_skill(self, store: SkillStore) -> None:
        store.save_skill_record(_make_record(id="s1"))
        assert store.deactivate_skill("s1") is True
        loaded = store.get_skill_record("s1")
        assert loaded is not None
        assert loaded.is_active is False

    def test_deactivate_nonexistent_returns_false(self, store: SkillStore) -> None:
        assert store.deactivate_skill("nope") is False


# ============================================================================
# get_versions / ancestry
# ============================================================================


class TestVersionsAndAncestry:
    def _seed(self, store: SkillStore) -> None:
        v1 = _make_record(id="s-v1", name="skill-a", generation=0)
        v2 = _make_record(
            id="s-v2",
            name="skill-a",
            origin=SkillOrigin.FIXED,
            generation=1,
            parent_ids=["s-v1"],
        )
        v3 = _make_record(
            id="s-v3",
            name="skill-a",
            origin=SkillOrigin.DERIVED,
            generation=2,
            parent_ids=["s-v2"],
        )
        for r in [v1, v2, v3]:
            store.save_skill_record(r)

    def test_get_versions_by_name(self, store: SkillStore) -> None:
        self._seed(store)
        versions = store.get_versions("skill-a")
        assert len(versions) == 3

    def test_get_ancestry(self, store: SkillStore) -> None:
        self._seed(store)
        ancestry = store.get_ancestry("s-v3")
        # Should trace back through s-v2 to s-v1
        ancestor_ids = [a.id for a in ancestry]
        assert "s-v2" in ancestor_ids
        assert "s-v1" in ancestor_ids

    def test_get_children_returns_ids(self, store: SkillStore) -> None:
        self._seed(store)
        children = store.get_children("s-v1")
        assert len(children) == 1
        assert children[0] == "s-v2"

    def test_get_ancestry_batch(self, store: SkillStore) -> None:
        self._seed(store)
        batch = store.get_ancestry_batch(["s-v3"])
        assert "s-v3" in batch


# ============================================================================
# increment_counters
# ============================================================================


class TestIncrementCounters:
    def test_increment_selected(self, store: SkillStore) -> None:
        store.save_skill_record(_make_record(id="s1"))
        store.increment_counters("s1", selected=True)
        loaded = store.get_skill_record("s1")
        assert loaded is not None
        assert loaded.total_selections == 1

    def test_increment_multiple_counters(self, store: SkillStore) -> None:
        store.save_skill_record(_make_record(id="s1"))
        store.increment_counters("s1", selected=True, applied=True, completed=True)
        loaded = store.get_skill_record("s1")
        assert loaded is not None
        assert loaded.total_selections == 1
        assert loaded.total_applied == 1
        assert loaded.total_completions == 1

    def test_increment_accumulates(self, store: SkillStore) -> None:
        store.save_skill_record(_make_record(id="s1"))
        store.increment_counters("s1", selected=True)
        store.increment_counters("s1", selected=True)
        store.increment_counters("s1", selected=True)
        loaded = store.get_skill_record("s1")
        assert loaded is not None
        assert loaded.total_selections == 3

    def test_increment_noop_when_no_flags(self, store: SkillStore) -> None:
        store.save_skill_record(_make_record(id="s1"))
        store.increment_counters("s1")
        loaded = store.get_skill_record("s1")
        assert loaded is not None
        assert loaded.total_selections == 0

    def test_applied_without_selected_raises(self, store: SkillStore) -> None:
        with pytest.raises(ValueError, match="applied requires selected"):
            store.increment_counters("s1", applied=True)

    def test_completed_without_applied_raises(self, store: SkillStore) -> None:
        with pytest.raises(ValueError, match="completed requires applied"):
            store.increment_counters("s1", selected=True, completed=True)

    def test_fell_back_without_selected_raises(self, store: SkillStore) -> None:
        with pytest.raises(ValueError, match="fell_back requires selected"):
            store.increment_counters("s1", fell_back=True)

    def test_increment_nonexistent_logs_warning(self, store: SkillStore) -> None:
        # Should not raise — just logs a warning
        store.increment_counters("ghost", selected=True)


# ============================================================================
# evolve_skill
# ============================================================================


class TestEvolveSkill:
    def test_evolve_derived(self, store: SkillStore) -> None:
        parent = _make_record(id="p1", name="base")
        store.save_skill_record(parent)
        child = _make_record(
            id="c1",
            name="derived",
            origin=SkillOrigin.DERIVED,
            generation=1,
        )
        result = store.evolve_skill(child, parent_skill_ids=["p1"])
        assert result.success is True
        assert result.new_record is not None
        assert result.new_record.id == "c1"

    def test_evolve_fix_deactivates_parents(self, store: SkillStore) -> None:
        parent = _make_record(id="p1", name="broken", is_active=True)
        store.save_skill_record(parent)
        fix = _make_record(
            id="fix1",
            name="fixed",
            origin=SkillOrigin.FIXED,
            generation=1,
        )
        result = store.evolve_skill(fix, parent_skill_ids=["p1"])
        assert result.success is True
        # Parent should be deactivated
        loaded_parent = store.get_skill_record("p1")
        assert loaded_parent is not None
        assert loaded_parent.is_active is False

    def test_evolve_fix_missing_parent_fails(self, store: SkillStore) -> None:
        fix = _make_record(
            id="fix1",
            name="fixed",
            origin=SkillOrigin.FIXED,
            generation=1,
        )
        result = store.evolve_skill(fix, parent_skill_ids=["nonexistent"])
        assert result.success is False
        assert "not found" in result.error

    def test_evolve_fix_duplicate_active_name_fails(self, store: SkillStore) -> None:
        existing = _make_record(id="e1", name="skill-x", is_active=True)
        other = _make_record(id="e2", name="skill-x", is_active=True)
        store.save_skill_record(existing)
        store.save_skill_record(other)
        fix = _make_record(
            id="fix1",
            name="skill-x",
            origin=SkillOrigin.FIXED,
            generation=1,
        )
        # e1 is deactivated, but e2 (same name) remains active → duplicate
        result = store.evolve_skill(fix, parent_skill_ids=["e1"])
        assert result.success is False
        assert "Duplicate active" in result.error

    def test_evolve_id_collision_returns_error(self, store: SkillStore) -> None:
        original = _make_record(id="s1", name="original")
        store.save_skill_record(original)
        # Try to evolve a new skill that happens to have the same ID
        # but with DIFFERENT parents (to bypass the FIX path's deactivation)
        dupe = _make_record(
            id="s1",
            name="dupe-name",
            origin=SkillOrigin.DERIVED,
            generation=1,
        )
        result = store.evolve_skill(dupe, parent_skill_ids=["s1"])
        # IntegrityError caught → returns error result
        assert result.success is False

    def test_evolve_no_parents_captured(self, store: SkillStore) -> None:
        captured = _make_record(
            id="cap1",
            name="captured-skill",
            origin=SkillOrigin.CAPTURED,
            generation=0,
        )
        result = store.evolve_skill(captured, parent_skill_ids=[])
        assert result.success is True


# ============================================================================
# get_metrics
# ============================================================================


class TestGetMetrics:
    def test_metrics_empty_db(self, store: SkillStore) -> None:
        metrics = store.get_metrics()
        assert metrics == EvolutionMetrics(
            total_selections=0,
            total_applied=0,
            total_completions=0,
            total_fallbacks=0,
        )

    def test_metrics_aggregates_active_only(self, store: SkillStore) -> None:
        r1 = _make_record(id="s1", name="skill-1", directory="agents/agent-a/skills")
        r2 = _make_record(id="s2", name="skill-2", directory="agents/agent-b/skills")
        store.save_skill_record(r1)
        store.save_skill_record(r2)
        store.increment_counters("s1", selected=True, applied=True)
        store.increment_counters("s2", selected=True, fell_back=True)
        metrics = store.get_metrics()
        assert metrics.total_selections == 2
        assert metrics.total_applied == 1
        assert metrics.total_fallbacks == 1

    def test_metrics_filter_by_agent(self, store: SkillStore) -> None:
        r1 = _make_record(id="s1", name="skill-1", directory="agents/agent-a/skills")
        r2 = _make_record(id="s2", name="skill-2", directory="agents/agent-b/skills")
        store.save_skill_record(r1)
        store.save_skill_record(r2)
        store.increment_counters("s1", selected=True)
        store.increment_counters("s2", selected=True)
        store.increment_counters("s2", selected=True)
        metrics = store.get_metrics(agent_name="agent-b")
        assert metrics.total_selections == 2


# ============================================================================
# get_skill_records_batch
# ============================================================================


class TestBatchOperations:
    def test_batch_get(self, store: SkillStore) -> None:
        for i in range(5):
            store.save_skill_record(_make_record(id=f"s{i}", name=f"skill-{i}"))
        results = store.get_skill_records_batch(["s1", "s3", "s5"])
        assert len(results) == 2
        assert set(results.keys()) == {"s1", "s3"}

    def test_batch_get_empty_list(self, store: SkillStore) -> None:
        results = store.get_skill_records_batch([])
        assert results == {}


# ============================================================================
# _row_to_record edge cases
# ============================================================================


class TestRowToRecordEdgeCases:
    def test_corrupted_snapshot_handled_gracefully(self, store: SkillStore) -> None:
        """A corrupted content_snapshot should not crash — it gets logged and dropped."""
        record = _make_record(
            content_snapshot={"file.py": "hello"},
        )
        store.save_skill_record(record)
        # Directly corrupt the snapshot in DB
        conn = store._conn()
        with conn as c:
            c.execute(
                "UPDATE skill_records SET lineage_content_snapshot = ? WHERE id = ?",
                ("not-json{{{" , "skill-1"),
            )
        loaded = store.get_skill_record("skill-1")
        assert loaded is not None
        # Snapshot should be None (gracefully degraded)
        assert loaded.lineage.content_snapshot is None or loaded.lineage.content_snapshot == {}

    def test_invalid_lineage_origin_defaults_to_captured(self, store: SkillStore) -> None:
        record = _make_record()
        store.save_skill_record(record)
        conn = store._conn()
        with conn as c:
            c.execute(
                "UPDATE skill_records SET lineage_origin = ? WHERE id = ?",
                ("invalid_origin", "skill-1"),
            )
        loaded = store.get_skill_record("skill-1")
        assert loaded is not None
        assert loaded.lineage.origin == SkillOrigin.CAPTURED
