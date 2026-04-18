"""Unit tests for EvolutionStore agent_records table and methods."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest  # noqa: F401 — needed for tmp_path fixture

from agent_nexus.models.evolution import SkillLineage, SkillOrigin, SkillRecord
from agent_nexus.platform.evolution.store import EvolutionStore


def _make_store(tmp_path: Path) -> EvolutionStore:
    return EvolutionStore(tmp_path / "test.db")


# ============================================================================
# save_agent_record + get_agent_record round-trip
# ============================================================================


class TestSaveAndGetAgentRecord:
    def test_save_and_get_round_trip(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record(
            agent_id="agent-1",
            name="feature-delivery",
            type="composite",
            skill_ids=["s1", "s2"],
            orchestration_toml="[pipeline]\nsteps = []",
        )
        record = store.get_agent_record("agent-1")
        assert record is not None
        assert record["agent_id"] == "agent-1"
        assert record["name"] == "feature-delivery"
        assert record["type"] == "composite"
        assert record["skill_ids"] == ["s1", "s2"]
        assert record["orchestration_toml"] == "[pipeline]\nsteps = []"
        assert record["is_active"] is True
        assert record["effective_rate"] == 0.0
        assert record["avg_steps"] is None
        assert record["avg_duration_ms"] is None
        assert record["created_at"] is not None
        assert record["updated_at"] is not None

    def test_save_minimal(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record(
            agent_id="a1",
            name="simple-agent",
            type="atomic",
            skill_ids=["s1"],
        )
        record = store.get_agent_record("a1")
        assert record is not None
        assert record["orchestration_toml"] is None
        assert record["skill_ids"] == ["s1"]

    def test_get_nonexistent_returns_none(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.get_agent_record("no-such-id") is None

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record(
            agent_id="a1", name="original", type="atomic", skill_ids=["s1"],
        )
        store.save_agent_record(
            agent_id="a1", name="updated", type="composite", skill_ids=["s1", "s2"],
        )
        record = store.get_agent_record("a1")
        assert record is not None
        assert record["name"] == "updated"
        assert record["type"] == "composite"
        assert record["skill_ids"] == ["s1", "s2"]

    def test_save_preserves_metrics_on_overwrite(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record(
            agent_id="a1", name="agent", type="atomic", skill_ids=["s1"],
        )
        store.update_agent_metrics("a1", 0.85, 5.0, 1200.0)
        store.save_agent_record(
            agent_id="a1", name="agent", type="atomic", skill_ids=["s1", "s2"],
        )
        record = store.get_agent_record("a1")
        assert record is not None
        assert record["effective_rate"] == 0.85
        assert record["avg_steps"] == 5.0
        assert record["avg_duration_ms"] == 1200.0
        assert record["skill_ids"] == ["s1", "s2"]

    def test_save_preserves_created_at(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record(
            agent_id="a1", name="agent", type="atomic", skill_ids=[],
        )
        original = store.get_agent_record("a1")
        assert original is not None
        original_created = original["created_at"]
        store.save_agent_record(
            agent_id="a1", name="agent-v2", type="atomic", skill_ids=[],
        )
        updated = store.get_agent_record("a1")
        assert updated is not None
        assert updated["created_at"] == original_created
        assert updated["name"] == "agent-v2"


# ============================================================================
# get_active_agents
# ============================================================================


class TestGetActiveAgents:
    def test_returns_only_active(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record("a1", "active", "atomic", ["s1"])
        store.save_agent_record("a2", "also-active", "atomic", ["s2"])
        store.save_agent_record("a3", "inactive", "composite", ["s3"])
        store.deactivate_agent("a3")
        active = store.get_active_agents()
        assert len(active) == 2
        assert {a["agent_id"] for a in active} == {"a1", "a2"}

    def test_empty_store(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.get_active_agents() == []

    def test_all_inactive(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record("a1", "x", "atomic", [])
        store.deactivate_agent("a1")
        assert store.get_active_agents() == []


# ============================================================================
# update_agent_metrics
# ============================================================================


class TestUpdateAgentMetrics:
    def test_update_metrics(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record("a1", "agent", "atomic", ["s1"])
        result = store.update_agent_metrics("a1", 0.92, 3.5, 850.0)
        assert result is True
        record = store.get_agent_record("a1")
        assert record is not None
        assert record["effective_rate"] == 0.92
        assert record["avg_steps"] == 3.5
        assert record["avg_duration_ms"] == 850.0

    def test_update_nonexistent_returns_false(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.update_agent_metrics("nope", 0.5, 1.0, 100.0) is False

    def test_update_preserves_other_fields(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record(
            agent_id="a1",
            name="my-agent",
            type="composite",
            skill_ids=["s1", "s2"],
            orchestration_toml="[pipeline]",
        )
        store.update_agent_metrics("a1", 0.75, 4.0, 2000.0)
        record = store.get_agent_record("a1")
        assert record is not None
        assert record["name"] == "my-agent"
        assert record["type"] == "composite"
        assert record["skill_ids"] == ["s1", "s2"]
        assert record["orchestration_toml"] == "[pipeline]"
        assert record["is_active"] is True


# ============================================================================
# deactivate_agent
# ============================================================================


class TestDeactivateAgent:
    def test_deactivate(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record("a1", "agent", "atomic", ["s1"])
        result = store.deactivate_agent("a1")
        assert result is True
        record = store.get_agent_record("a1")
        assert record is not None
        assert record["is_active"] is False

    def test_deactivate_nonexistent(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.deactivate_agent("nope") is False

    def test_deactivate_updates_timestamp(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record("a1", "agent", "atomic", [])
        original = store.get_agent_record("a1")
        assert original is not None
        store.deactivate_agent("a1")
        updated = store.get_agent_record("a1")
        assert updated is not None
        assert updated["updated_at"] != original["updated_at"]


# ============================================================================
# clear() cleans up agent_records
# ============================================================================


class TestClearAgentRecords:
    def test_clear_removes_agent_records(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_agent_record("a1", "agent", "atomic", ["s1"])
        store.save_agent_record("a2", "other", "composite", ["s2"])
        store.clear()
        assert store.get_agent_record("a1") is None
        assert store.get_agent_record("a2") is None
        assert store.get_active_agents() == []


# ============================================================================
# save_skill_record preserves counters on overwrite (from iter13)
# ============================================================================


class TestEvolutionStoreCounterPreservation:
    """Verify save_skill_record preserves counters on overwrite."""

    def _make_record(
        self,
        skill_id: str = "s1",
        name: str = "test-skill",
        total_selections: int = 0,
        total_applied: int = 0,
        total_completions: int = 0,
        total_fallbacks: int = 0,
    ) -> SkillRecord:
        return SkillRecord(
            id=skill_id,
            name=name,
            version="1.0.0",
            lineage=SkillLineage(
                origin=SkillOrigin.IMPORTED,
                generation=0,
            ),
            directory="skills/test",
            is_active=True,
            total_selections=total_selections,
            total_applied=total_applied,
            total_completions=total_completions,
            total_fallbacks=total_fallbacks,
            first_seen=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )

    def test_save_preserves_counters_when_zero(self, tmp_path: Path) -> None:
        """Saving a record with zero counters preserves existing counter values."""
        db_path = tmp_path / "test.db"
        store = EvolutionStore(db_path)

        record_v1 = self._make_record(
            skill_id="s1",
            total_selections=10,
            total_applied=8,
            total_completions=7,
            total_fallbacks=1,
        )
        store.save_skill_record(record_v1)

        record_v2 = self._make_record(
            skill_id="s1",
            total_selections=0,
            total_applied=0,
            total_completions=0,
            total_fallbacks=0,
        )
        store.save_skill_record(record_v2)

        loaded = store.get_skill_record("s1")
        assert loaded is not None
        assert loaded.total_selections == 10
        assert loaded.total_applied == 8
        assert loaded.total_completions == 7
        assert loaded.total_fallbacks == 1

    def test_save_updates_counters_when_nonzero(self, tmp_path: Path) -> None:
        """Saving a record with nonzero counters updates the counters."""
        db_path = tmp_path / "test.db"
        store = EvolutionStore(db_path)

        record_v1 = self._make_record(
            skill_id="s1",
            total_selections=10,
            total_applied=8,
        )
        store.save_skill_record(record_v1)

        record_v2 = self._make_record(
            skill_id="s1",
            total_selections=20,
            total_applied=15,
        )
        store.save_skill_record(record_v2)

        loaded = store.get_skill_record("s1")
        assert loaded is not None
        assert loaded.total_selections == 20
        assert loaded.total_applied == 15

    def test_save_new_record_inserts_normally(self, tmp_path: Path) -> None:
        """New records insert without any counter issues."""
        db_path = tmp_path / "test.db"
        store = EvolutionStore(db_path)

        record = self._make_record(skill_id="s-new")
        store.save_skill_record(record)

        loaded = store.get_skill_record("s-new")
        assert loaded is not None
        assert loaded.total_selections == 0
        assert loaded.total_applied == 0

    def test_evolve_skill_uses_upsert(self, tmp_path: Path) -> None:
        """evolve_skill uses the same upsert pattern (counter-safe)."""
        db_path = tmp_path / "test.db"
        store = EvolutionStore(db_path)

        parent = self._make_record(
            skill_id="parent-1",
            name="parent-skill",
            total_selections=100,
        )
        store.save_skill_record(parent)

        evolved = SkillRecord(
            id="evolved-1",
            name="parent-skill",
            version="1.0.0",
            lineage=SkillLineage(
                origin=SkillOrigin.FIXED,
                generation=1,
                parent_skill_ids=["parent-1"],
            ),
            directory="skills/test",
            is_active=True,
            total_selections=0,
            first_seen=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )

        store.evolve_skill(evolved, parent_skill_ids=["parent-1"])

        loaded_parent = store.get_skill_record("parent-1")
        assert loaded_parent is not None
        assert loaded_parent.is_active is False

        loaded_evolved = store.get_skill_record("evolved-1")
        assert loaded_evolved is not None
        assert loaded_evolved.is_active is True

    def test_partial_counter_update(self, tmp_path: Path) -> None:
        """Only nonzero counters are updated; zero counters keep existing values."""
        db_path = tmp_path / "test.db"
        store = EvolutionStore(db_path)

        record_v1 = self._make_record(
            skill_id="s1",
            total_selections=10,
            total_applied=8,
            total_completions=5,
            total_fallbacks=3,
        )
        store.save_skill_record(record_v1)

        record_v2 = self._make_record(
            skill_id="s1",
            total_selections=20,
            total_applied=0,
            total_completions=0,
            total_fallbacks=0,
        )
        store.save_skill_record(record_v2)

        loaded = store.get_skill_record("s1")
        assert loaded is not None
        assert loaded.total_selections == 20  # updated
        assert loaded.total_applied == 8  # preserved
        assert loaded.total_completions == 5  # preserved
        assert loaded.total_fallbacks == 3  # preserved


# ============================================================================
# _row_to_record handles malformed content_snapshot JSON (from iter14)
# ============================================================================


class TestMalformedSnapshot:
    """EvolutionStore._row_to_record must not crash on malformed snapshots."""

    def _make_store(self, tmp_path: Path) -> EvolutionStore:
        return EvolutionStore(tmp_path / "evo.db")

    def _save_raw(self, store: EvolutionStore, snapshot_json: str) -> str:
        """Insert a record with raw snapshot JSON for testing."""
        import json
        import uuid

        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with store._conn() as conn:
            conn.execute(
                "INSERT INTO skill_records "
                "(id, name, version, lineage_origin, lineage_generation, "
                "lineage_content_diff, lineage_content_snapshot, directory, "
                "is_active, total_selections, total_applied, "
                "total_completions, total_fallbacks, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    sid, "test-skill", "1.0.0", "imported", 0,
                    "", snapshot_json, "/tmp", 1,
                    0, 0, 0, 0, now, now,
                ),
            )
        return sid

    def test_null_snapshot(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        sid = self._save_raw(store, "null")
        record = store.get_skill_record(sid)
        assert record is not None
        assert record.lineage.content_snapshot is None

    def test_list_snapshot(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        sid = self._save_raw(store, '[1, 2, 3]')
        record = store.get_skill_record(sid)
        assert record is not None
        assert record.lineage.content_snapshot is None

    def test_string_snapshot(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        sid = self._save_raw(store, '"just a string"')
        record = store.get_skill_record(sid)
        assert record is not None
        assert record.lineage.content_snapshot is None

    def test_valid_dict_snapshot(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        sid = self._save_raw(store, '{"key": "value"}')
        record = store.get_skill_record(sid)
        assert record is not None
        assert record.lineage.content_snapshot == {"key": "value"}

    def test_invalid_json_snapshot(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        sid = self._save_raw(store, "not json at all {{{")
        record = store.get_skill_record(sid)
        assert record is not None
        assert record.lineage.content_snapshot is None

    def test_get_active_skills_with_malformed(self, tmp_path: Path) -> None:
        """All active skills should load even if one has a malformed snapshot."""
        store = self._make_store(tmp_path)
        self._save_raw(store, "null")
        self._save_raw(store, '{"ok": true}')
        self._save_raw(store, "[1,2]")
        skills = store.get_active_skills()
        assert len(skills) == 3
