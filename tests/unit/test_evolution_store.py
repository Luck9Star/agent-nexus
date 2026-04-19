"""Unit tests for EvolutionStore agent_records table and methods."""

from __future__ import annotations

import sqlite3
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

    def test_save_preserves_counters_on_conflict(self, tmp_path: Path) -> None:
        """Saving a record on conflict preserves DB counters (increment_counters is sole authority)."""
        db_path = tmp_path / "test.db"
        store = EvolutionStore(db_path)

        record_v1 = self._make_record(
            skill_id="s1",
            total_selections=10,
            total_applied=8,
        )
        store.save_skill_record(record_v1)

        # Bump counters via increment_counters (the authoritative path)
        for _ in range(5):
            store.increment_counters("s1", selected=True, applied=True)
        for _ in range(3):
            store.increment_counters("s1", selected=True)

        # Re-save with stale counters — must NOT overwrite the incremented values
        record_v2 = self._make_record(
            skill_id="s1",
            total_selections=20,
            total_applied=15,
        )
        store.save_skill_record(record_v2)

        loaded = store.get_skill_record("s1")
        assert loaded is not None
        assert loaded.total_selections == 18  # 10 + 5 (sel+appl) + 3 (sel only)
        assert loaded.total_applied == 13    # 8 + 5

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
        """save_skill_record preserves ALL counters on conflict; increment_counters is sole authority."""
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
        # All counters preserved from DB, not overwritten by record_v2
        assert loaded.total_selections == 10
        assert loaded.total_applied == 8
        assert loaded.total_completions == 5
        assert loaded.total_fallbacks == 3


# ============================================================================
# _row_to_record handles malformed content_snapshot JSON (from iter14)
# ============================================================================


class TestMalformedSnapshot:
    """EvolutionStore._row_to_record must not crash on malformed snapshots."""

    def _make_store(self, tmp_path: Path) -> EvolutionStore:
        return EvolutionStore(tmp_path / "evo.db")

    def _save_raw(self, store: EvolutionStore, snapshot_json: str) -> str:
        """Insert a record with raw snapshot JSON for testing."""
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


# ============================================================================
# record_analysis skips judgments with None/empty skill_id
# ============================================================================


class TestRecordAnalysisSkipsBadSkillId:
    """Lines 400-403: judgments with missing skill_id are silently skipped."""

    def _seed_skill(self, store: EvolutionStore, skill_id: str) -> None:
        store.save_skill_record(
            SkillRecord(
                id=skill_id,
                name="test-skill",
                version="1.0.0",
                lineage=SkillLineage(origin=SkillOrigin.IMPORTED, generation=0),
                directory="skills/test",
                is_active=True,
                first_seen=datetime.now(timezone.utc),
                last_updated=datetime.now(timezone.utc),
            )
        )

    def test_none_skill_id_skipped(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        self._seed_skill(store, "s-valid")

        analysis_id = store.record_analysis(  # noqa: FURB118  # pyright: ignore[reportUnusedVariable]
            task_id="t1",
            agent_name="tester",
            analysis_text="check",
            judgments=[
                {"skill_id": None, "selected": True},
                {"skill_id": "s-valid", "selected": True},
            ],
        )

        # Only the valid judgment should appear under this analysis
        analyses = store.get_analyses_for_task("t1")
        assert len(analyses) == 1
        assert len(analyses[0]["judgments"]) == 1
        assert analyses[0]["judgments"][0]["skill_id"] == "s-valid"

    def test_empty_string_skill_id_skipped(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        self._seed_skill(store, "s-valid")

        store.record_analysis(
            task_id="t2",
            agent_name="tester",
            analysis_text="check",
            judgments=[
                {"skill_id": "", "selected": True},
                {"skill_id": "s-valid", "selected": True, "applied": True},
            ],
        )

        analyses = store.get_analyses_for_task("t2")
        assert len(analyses[0]["judgments"]) == 1
        assert analyses[0]["judgments"][0]["skill_id"] == "s-valid"

    def test_missing_skill_id_key_skipped(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        self._seed_skill(store, "s-valid")

        store.record_analysis(
            task_id="t3",
            agent_name="tester",
            analysis_text="check",
            judgments=[
                {"selected": True},  # no skill_id key at all
                {"skill_id": "s-valid", "selected": True},
            ],
        )

        analyses = store.get_analyses_for_task("t3")
        assert len(analyses[0]["judgments"]) == 1

    def test_all_invalid_skill_ids_no_judgments(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        analysis_id = store.record_analysis(  # pyright: ignore[reportUnusedVariable]
            task_id="t4",
            agent_name="tester",
            analysis_text="check",
            judgments=[
                {"skill_id": None},
                {"skill_id": ""},
                {"selected": True},
            ],
        )

        analyses = store.get_analyses_for_task("t4")
        assert len(analyses[0]["judgments"]) == 0


class TestSkillJudgmentFKEnforcement:
    """Regression: skill_judgments.skill_id FK constraint prevents orphans."""

    def test_nonexistent_skill_id_rejected(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        with pytest.raises(sqlite3.IntegrityError):
            store.record_analysis(
                task_id="t-fk",
                agent_name="tester",
                analysis_text="check",
                judgments=[
                    {"skill_id": "ghost-skill", "selected": True},
                ],
            )

        # The entire transaction should have rolled back — no analysis either
        analyses = store.get_analyses_for_task("t-fk")
        assert len(analyses) == 0

    def test_mixed_valid_and_ghost_rejected(self, tmp_path: Path) -> None:
        """Even one ghost skill_id in a batch rolls back the entire analysis."""
        store = _make_store(tmp_path)
        # Seed a valid skill
        store.save_skill_record(
            SkillRecord(
                id="s-real",
                name="real",
                version="1.0.0",
                lineage=SkillLineage(origin=SkillOrigin.IMPORTED, generation=0),
                directory="skills/real",
                is_active=True,
                first_seen=datetime.now(timezone.utc),
                last_updated=datetime.now(timezone.utc),
            )
        )

        with pytest.raises(sqlite3.IntegrityError):
            store.record_analysis(
                task_id="t-mix",
                agent_name="tester",
                analysis_text="check",
                judgments=[
                    {"skill_id": "s-real", "selected": True},
                    {"skill_id": "ghost", "applied": True},
                ],
            )

        # Nothing should be persisted — atomic rollback
        analyses = store.get_analyses_for_task("t-mix")
        assert len(analyses) == 0


# ============================================================================
# evolve_skill with ID collision returns EvolveResult(success=False)
# ============================================================================


class TestEvolveSkillIdCollision:
    """Lines 610-617: IntegrityError on duplicate ID triggers rollback."""

    def _make_record(
        self,
        skill_id: str,
        name: str = "test-skill",
        origin: SkillOrigin = SkillOrigin.IMPORTED,
        generation: int = 0,
    ) -> SkillRecord:
        return SkillRecord(
            id=skill_id,
            name=name,
            version="1.0.0",
            lineage=SkillLineage(origin=origin, generation=generation),
            directory="skills/test",
            is_active=True,
            first_seen=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )

    def test_collision_returns_failure(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        # Insert a skill with a known ID
        existing = self._make_record(skill_id="collision-id", name="original")
        store.save_skill_record(existing)

        # Try to evolve into the same ID
        evolved = self._make_record(
            skill_id="collision-id",
            name="evolved",
            origin=SkillOrigin.FIXED,
            generation=1,
        )
        result = store.evolve_skill(evolved, parent_skill_ids=["collision-id"])

        assert result.success is False
        assert "collision" in result.error
        assert result.new_record is None

    def test_collision_no_lineage_parents_created(self, tmp_path: Path) -> None:
        """When insert fails, no lineage parents should be created."""
        store = _make_store(tmp_path)

        parent = self._make_record(skill_id="parent-1", name="parent")
        store.save_skill_record(parent)

        # Insert another skill to use as a different parent reference
        other = self._make_record(skill_id="other-1", name="other")
        store.save_skill_record(other)

        evolved = self._make_record(
            skill_id="parent-1",  # collision
            name="parent",
            origin=SkillOrigin.DERIVED,
            generation=1,
        )
        result = store.evolve_skill(evolved, parent_skill_ids=["other-1"])
        assert result.success is False

        # No lineage edges should exist for the colliding ID
        children = store.get_children("other-1")
        assert children == []

    def test_collision_parent_not_deactivated(self, tmp_path: Path) -> None:
        """FIX evolution: parent must stay active when new record INSERT fails.

        Regression: evolve_skill used to commit the parent deactivation
        (is_active=0) before the new-record INSERT failed with IntegrityError.
        The _conn context manager then committed the partial state, leaving
        the parent permanently deactivated with no replacement.
        """
        store = _make_store(tmp_path)

        parent = self._make_record(
            skill_id="parent-1", name="parent", origin=SkillOrigin.IMPORTED
        )
        store.save_skill_record(parent)
        assert store.get_skill_record("parent-1").is_active is True

        # Evolve with FIXED origin (triggers parent deactivation)
        # but use colliding ID so INSERT fails
        evolved = self._make_record(
            skill_id="parent-1",  # same ID → collision
            name="parent",
            origin=SkillOrigin.FIXED,
            generation=1,
        )
        result = store.evolve_skill(evolved, parent_skill_ids=["parent-1"])
        assert result.success is False

        # Parent must still be active — deactivation was rolled back
        parent_record = store.get_skill_record("parent-1")
        assert parent_record is not None
        assert parent_record.is_active is True


# ============================================================================
# get_analyses_for_task
# ============================================================================


class TestGetAnalysesForTask:
    """Lines 450-461: returns analyses with judgments joined correctly."""

    def _seed_skill(self, store: EvolutionStore, skill_id: str) -> None:
        store.save_skill_record(
            SkillRecord(
                id=skill_id,
                name="test-skill",
                version="1.0.0",
                lineage=SkillLineage(origin=SkillOrigin.IMPORTED, generation=0),
                directory="skills/test",
                is_active=True,
                first_seen=datetime.now(timezone.utc),
                last_updated=datetime.now(timezone.utc),
            )
        )

    def test_empty_result(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        result = store.get_analyses_for_task("nonexistent-task")
        assert result == []

    def test_single_analysis_with_judgments(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        self._seed_skill(store, "s1")
        self._seed_skill(store, "s2")

        store.record_analysis(
            task_id="t1",
            agent_name="agent-a",
            analysis_text="looks good",
            evolution_suggestions=[{"action": "fix", "skill": "s1"}],
            judgments=[
                {"skill_id": "s1", "selected": True, "applied": True},
                {"skill_id": "s2", "selected": True, "applied": False},
            ],
        )

        analyses = store.get_analyses_for_task("t1")
        assert len(analyses) == 1
        a = analyses[0]
        assert a["task_id"] == "t1"
        assert a["agent_name"] == "agent-a"
        assert a["analysis"] == "looks good"
        assert a["evolution_suggestions"] == [{"action": "fix", "skill": "s1"}]
        assert len(a["judgments"]) == 2
        assert a["judgments"][0]["selected"] is True
        assert a["judgments"][1]["applied"] is False

    def test_multiple_analyses_same_task(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        store.record_analysis(
            task_id="t1", agent_name="a1", analysis_text="first pass",
        )
        store.record_analysis(
            task_id="t1", agent_name="a2", analysis_text="second pass",
        )

        analyses = store.get_analyses_for_task("t1")
        assert len(analyses) == 2
        texts = {a["analysis"] for a in analyses}
        assert texts == {"first pass", "second pass"}


# ============================================================================
# get_judgments_for_skill
# ============================================================================


class TestGetJudgmentsForSkill:
    """Lines 463-485: limit, ordering (most recent first), boolean conversion."""

    def _seed_skill(self, store: EvolutionStore, skill_id: str) -> None:
        store.save_skill_record(
            SkillRecord(
                id=skill_id,
                name="test-skill",
                version="1.0.0",
                lineage=SkillLineage(origin=SkillOrigin.IMPORTED, generation=0),
                directory="skills/test",
                is_active=True,
                first_seen=datetime.now(timezone.utc),
                last_updated=datetime.now(timezone.utc),
            )
        )

    def test_empty_result(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        result = store.get_judgments_for_skill("no-such-skill")
        assert result == []

    def test_returns_judgments_for_correct_skill(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        self._seed_skill(store, "s1")
        self._seed_skill(store, "s2")

        store.record_analysis(
            task_id="t1",
            agent_name="tester",
            analysis_text="check",
            judgments=[
                {"skill_id": "s1", "selected": True},
                {"skill_id": "s2", "selected": True},
            ],
        )

        judgments = store.get_judgments_for_skill("s1")
        assert len(judgments) == 1
        assert judgments[0]["skill_id"] == "s1"

    def test_limit_parameter(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        self._seed_skill(store, "s1")

        # Create 5 judgments via 5 analyses
        for i in range(5):
            store.record_analysis(
                task_id=f"t{i}",
                agent_name="tester",
                analysis_text=f"analysis {i}",
                judgments=[{"skill_id": "s1", "selected": True}],
            )

        judgments = store.get_judgments_for_skill("s1", limit=3)
        assert len(judgments) == 3

    def test_boolean_conversion(self, tmp_path: Path) -> None:
        """SQLite stores booleans as 0/1; output must be Python bools."""
        store = _make_store(tmp_path)
        self._seed_skill(store, "s1")

        store.record_analysis(
            task_id="t1",
            agent_name="tester",
            analysis_text="check",
            judgments=[
                {
                    "skill_id": "s1",
                    "selected": True,
                    "applied": True,
                    "completed": False,
                    "fell_back": False,
                },
            ],
        )

        j = store.get_judgments_for_skill("s1")
        assert len(j) == 1
        assert j[0]["selected"] is True
        assert j[0]["applied"] is True
        assert j[0]["completed"] is False
        assert j[0]["fell_back"] is False

    def test_ordering_most_recent_first(self, tmp_path: Path) -> None:
        """Judgments should be returned in reverse insertion order (rowid DESC)."""
        store = _make_store(tmp_path)
        self._seed_skill(store, "s1")

        # Record two analyses with judgments for the same skill
        store.record_analysis(
            task_id="t1",
            agent_name="tester",
            analysis_text="first",
            judgments=[{"skill_id": "s1", "selected": True}],
        )
        store.record_analysis(
            task_id="t2",
            agent_name="tester",
            analysis_text="second",
            judgments=[{"skill_id": "s1", "selected": True}],
        )

        judgments = store.get_judgments_for_skill("s1")
        assert len(judgments) == 2
        # The most recent judgment (from t2) should be first
        first_analysis_id = judgments[0]["analysis_id"]
        second_analysis_id = judgments[1]["analysis_id"]
        assert first_analysis_id != second_analysis_id


# ============================================================================
# get_ancestry with max_depth and cycle protection
# ============================================================================


class TestGetAncestry:
    """Lines 633-673: max_depth limiting and cycle protection."""

    def _make_record(self, skill_id: str, generation: int) -> SkillRecord:
        return SkillRecord(
            id=skill_id,
            name=f"skill-{skill_id}",
            version="1.0.0",
            lineage=SkillLineage(origin=SkillOrigin.IMPORTED, generation=generation),
            directory="skills/test",
            is_active=True,
            first_seen=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )

    def test_linear_ancestry(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        grandparent = self._make_record("gp", generation=0)
        parent = SkillRecord(
            id="p",
            name="skill-p",
            version="1.0.0",
            lineage=SkillLineage(
                origin=SkillOrigin.FIXED,
                generation=1,
                parent_skill_ids=["gp"],
            ),
            directory="skills/test",
            is_active=True,
            first_seen=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )
        child = SkillRecord(
            id="c",
            name="skill-c",
            version="1.0.0",
            lineage=SkillLineage(
                origin=SkillOrigin.FIXED,
                generation=2,
                parent_skill_ids=["p"],
            ),
            directory="skills/test",
            is_active=True,
            first_seen=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )

        store.save_skill_record(grandparent)
        store.evolve_skill(parent, parent_skill_ids=["gp"])
        store.evolve_skill(child, parent_skill_ids=["p"])

        ancestors = store.get_ancestry("c", max_depth=10)
        assert len(ancestors) == 2
        # Sorted by generation ascending (oldest first)
        assert ancestors[0].id == "gp"
        assert ancestors[1].id == "p"

    def test_max_depth_limits_traversal(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        # Build a chain: s0 <- s1 <- s2 <- s3 (s3 is the leaf)
        prev_id = "s0"
        store.save_skill_record(self._make_record("s0", generation=0))

        for i in range(1, 4):
            rec = SkillRecord(
                id=f"s{i}",
                name=f"skill-s{i}",
                version="1.0.0",
                lineage=SkillLineage(
                    origin=SkillOrigin.FIXED,
                    generation=i,
                    parent_skill_ids=[prev_id],
                ),
                directory="skills/test",
                is_active=True,
                first_seen=datetime.now(timezone.utc),
                last_updated=datetime.now(timezone.utc),
            )
            store.evolve_skill(rec, parent_skill_ids=[prev_id])
            prev_id = f"s{i}"

        # With max_depth=1, we should only get the immediate parent (s2)
        ancestors = store.get_ancestry("s3", max_depth=1)
        assert len(ancestors) == 1
        assert ancestors[0].id == "s2"

        # With max_depth=2, we should get s2 and s1
        ancestors = store.get_ancestry("s3", max_depth=2)
        assert len(ancestors) == 2
        assert ancestors[0].id == "s1"
        assert ancestors[1].id == "s2"

    def test_cycle_does_not_infinite_loop(self, tmp_path: Path) -> None:
        """A cycle in lineage must not cause infinite traversal."""
        store = _make_store(tmp_path)

        # Insert two skills
        rec_a = self._make_record("a", generation=0)
        rec_b = self._make_record("b", generation=1)
        store.save_skill_record(rec_a)
        store.save_skill_record(rec_b)

        # Manually create a cycle: a -> b, b -> a in lineage parents
        with store._conn(immediate=True) as conn:
            conn.execute(
                "INSERT INTO skill_lineage_parents (skill_id, parent_id) "
                "VALUES ('a', 'b')"
            )
            conn.execute(
                "INSERT INTO skill_lineage_parents (skill_id, parent_id) "
                "VALUES ('b', 'a')"
            )

        # Should terminate and return exactly the two records
        ancestors = store.get_ancestry("a", max_depth=100)
        ids = {anc.id for anc in ancestors}
        assert "b" in ids
        # Should not hang or return duplicates
        assert len(ancestors) == len(set(a.id for a in ancestors))

    def test_no_ancestry_returns_empty(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_skill_record(self._make_record("orphan", generation=0))

        ancestors = store.get_ancestry("orphan")
        assert ancestors == []

    def test_nonexistent_skill_returns_empty(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        ancestors = store.get_ancestry("no-such-id")
        assert ancestors == []


# ============================================================================
# _row_to_record handles invalid lineage_origin (defaults to CAPTURED)
# ============================================================================


class TestInvalidLineageOrigin:
    """EvolutionStore._row_to_record defaults invalid origin to CAPTURED."""

    def _make_store(self, tmp_path: Path) -> EvolutionStore:
        return EvolutionStore(tmp_path / "evo.db")

    def _save_with_origin(self, store: EvolutionStore, origin: str) -> str:
        """Insert a record with a custom lineage_origin value."""
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
                    sid, "test-skill", "1.0.0", origin, 0,
                    "", "{}", "/tmp", 1,
                    0, 0, 0, 0, now, now,
                ),
            )
        return sid

    def test_invalid_origin_defaults_to_captured(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        sid = self._save_with_origin(store, "totally_invalid_value")
        record = store.get_skill_record(sid)
        assert record is not None
        assert record.lineage.origin == SkillOrigin.CAPTURED

    def test_valid_origin_preserved(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        sid = self._save_with_origin(store, "fixed")
        record = store.get_skill_record(sid)
        assert record is not None
        assert record.lineage.origin == SkillOrigin.FIXED

    def test_empty_origin_defaults_to_captured(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        sid = self._save_with_origin(store, "")
        record = store.get_skill_record(sid)
        assert record is not None
        assert record.lineage.origin == SkillOrigin.CAPTURED


# ============================================================================
# EvolutionStore.close() — resource lifecycle regression
# ============================================================================


class TestEvolutionStoreClose:
    """EvolutionStore.close() is a no-op for file-based DBs but must exist."""

    def test_close_is_noop_for_file_db(self, tmp_path: Path) -> None:
        store = EvolutionStore(tmp_path / "evo.db")
        store.close()  # should not raise

    def test_close_idempotent(self, tmp_path: Path) -> None:
        store = EvolutionStore(tmp_path / "evo.db")
        store.close()
        store.close()  # second call is fine


class TestJudgmentsBatchEvenLimit:
    """get_judgments_batch must return up to limit_per_skill rows per skill.

    Regression: a global LIMIT could starve low-frequency skills when a
    high-frequency skill dominated the result set.  The ROW_NUMBER()
    window function ensures each skill gets its own quota.
    """

    def _seed_judgments(
        self, store: EvolutionStore, skill_id: str, count: int
    ) -> None:
        """Insert *count* judgment rows for *skill_id*."""
        import uuid
        from datetime import datetime, timezone

        with store._conn(immediate=True) as conn:
            # Insert a parent analysis row (FK requirement)
            analysis_id = f"ana-{skill_id}"
            conn.execute(
                "INSERT OR IGNORE INTO execution_analyses "
                "(id, task_id, agent_name, analysis, created_at) "
                "VALUES (?, ?, ?, '', ?)",
                (
                    analysis_id,
                    "test-task",
                    "test-agent",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            for i in range(count):
                conn.execute(
                    "INSERT INTO skill_judgments "
                    "(id, analysis_id, skill_id, selected, applied, completed, fell_back) "
                    "VALUES (?, ?, ?, 1, 1, 1, 0)",
                    (str(uuid.uuid4()), analysis_id, skill_id),
                )

    def test_even_distribution_with_unbalanced_data(
        self, tmp_path: Path
    ) -> None:
        """High-frequency skill doesn't crowd out low-frequency skill."""
        store = EvolutionStore(tmp_path / "evo.db")
        store.save_skill_record(SkillRecord(
            id="hi", name="hi", version="1.0",
            lineage=SkillLineage(origin=SkillOrigin.IMPORTED, generation=0),
        ))
        store.save_skill_record(SkillRecord(
            id="lo", name="lo", version="1.0",
            lineage=SkillLineage(origin=SkillOrigin.IMPORTED, generation=0),
        ))

        self._seed_judgments(store, "hi", 90)
        self._seed_judgments(store, "lo", 10)

        batch = store.get_judgments_batch({"hi", "lo"}, limit_per_skill=50)
        assert len(batch["hi"]) == 50  # capped at limit
        assert len(batch["lo"]) == 10  # all available rows

    def test_respects_limit_per_skill(self, tmp_path: Path) -> None:
        """Each skill is independently capped at limit_per_skill."""
        store = EvolutionStore(tmp_path / "evo.db")
        store.save_skill_record(SkillRecord(
            id="s1", name="s1", version="1.0",
            lineage=SkillLineage(origin=SkillOrigin.IMPORTED, generation=0),
        ))
        store.save_skill_record(SkillRecord(
            id="s2", name="s2", version="1.0",
            lineage=SkillLineage(origin=SkillOrigin.IMPORTED, generation=0),
        ))

        self._seed_judgments(store, "s1", 100)
        self._seed_judgments(store, "s2", 100)

        batch = store.get_judgments_batch({"s1", "s2"}, limit_per_skill=5)
        assert len(batch["s1"]) == 5
        assert len(batch["s2"]) == 5


# iter104 regression: get_ancestry_batch + get_judgments_batch empty input


class TestGetAncestryBatch:
    """get_ancestry_batch: batch BFS lineage traversal for multiple skills."""

    def _make_record(self, skill_id: str, generation: int) -> SkillRecord:
        return SkillRecord(
            id=skill_id,
            name=f"skill-{skill_id}",
            version="1.0.0",
            lineage=SkillLineage(origin=SkillOrigin.IMPORTED, generation=generation),
            directory="skills/test",
            is_active=True,
            first_seen=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )

    def test_empty_input_returns_empty(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.get_ancestry_batch([]) == {}

    def test_single_skill_linear_lineage(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_skill_record(self._make_record("gp", 0))
        parent = SkillRecord(
            id="p",
            name="skill-p",
            version="1.0.0",
            lineage=SkillLineage(
                origin=SkillOrigin.FIXED, generation=1, parent_skill_ids=["gp"],
            ),
            directory="skills/test",
            is_active=True,
            first_seen=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )
        child = SkillRecord(
            id="c",
            name="skill-c",
            version="1.0.0",
            lineage=SkillLineage(
                origin=SkillOrigin.FIXED, generation=2, parent_skill_ids=["p"],
            ),
            directory="skills/test",
            is_active=True,
            first_seen=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )
        store.evolve_skill(parent, parent_skill_ids=["gp"])
        store.evolve_skill(child, parent_skill_ids=["p"])

        result = store.get_ancestry_batch(["c"])
        assert "c" in result
        assert len(result["c"]) == 2
        assert result["c"][0].id == "gp"
        assert result["c"][1].id == "p"

    def test_multiple_skills_shared_ancestor(self, tmp_path: Path) -> None:
        """Two skills sharing a common grandparent get correct ancestry."""
        store = _make_store(tmp_path)
        store.save_skill_record(self._make_record("root", 0))

        for i, child_id in enumerate("ab", start=1):
            rec = SkillRecord(
                id=child_id,
                name=f"skill-{child_id}",
                version="1.0.0",
                lineage=SkillLineage(
                    origin=SkillOrigin.FIXED,
                    generation=i,
                    parent_skill_ids=["root"],
                ),
                directory="skills/test",
                is_active=True,
                first_seen=datetime.now(timezone.utc),
                last_updated=datetime.now(timezone.utc),
            )
            store.evolve_skill(rec, parent_skill_ids=["root"])

        result = store.get_ancestry_batch(["a", "b"])
        assert len(result["a"]) == 1
        assert result["a"][0].id == "root"
        assert len(result["b"]) == 1
        assert result["b"][0].id == "root"

    def test_orphan_skill_returns_empty_ancestry(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_skill_record(self._make_record("orphan", 0))
        result = store.get_ancestry_batch(["orphan"])
        assert result == {"orphan": []}

    def test_nonexistent_skill_returns_empty_ancestry(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        result = store.get_ancestry_batch(["ghost"])
        assert result == {"ghost": []}

    def test_max_depth_limits_traversal(self, tmp_path: Path) -> None:
        """max_depth stops BFS at the configured depth."""
        store = _make_store(tmp_path)
        prev_id = "s0"
        store.save_skill_record(self._make_record("s0", 0))
        for i in range(1, 4):
            rec = SkillRecord(
                id=f"s{i}",
                name=f"skill-s{i}",
                version="1.0.0",
                lineage=SkillLineage(
                    origin=SkillOrigin.FIXED,
                    generation=i,
                    parent_skill_ids=[prev_id],
                ),
                directory="skills/test",
                is_active=True,
                first_seen=datetime.now(timezone.utc),
                last_updated=datetime.now(timezone.utc),
            )
            store.evolve_skill(rec, parent_skill_ids=[prev_id])
            prev_id = f"s{i}"

        result = store.get_ancestry_batch(["s3"], max_depth=1)
        assert len(result["s3"]) == 1
        assert result["s3"][0].id == "s2"

    def test_cycle_protection(self, tmp_path: Path) -> None:
        """Cycles in lineage do not cause infinite traversal."""
        store = _make_store(tmp_path)
        store.save_skill_record(self._make_record("a", 0))
        store.save_skill_record(self._make_record("b", 1))
        with store._conn(immediate=True) as conn:
            conn.execute(
                "INSERT INTO skill_lineage_parents (skill_id, parent_id) "
                "VALUES ('a', 'b')"
            )
            conn.execute(
                "INSERT INTO skill_lineage_parents (skill_id, parent_id) "
                "VALUES ('b', 'a')"
            )

        result = store.get_ancestry_batch(["a"], max_depth=100)
        ids = {r.id for r in result["a"]}
        assert "b" in ids
        assert len(result["a"]) == len(set(r.id for r in result["a"]))


class TestGetJudgmentsBatchEmptyInput:
    """get_judgments_batch with empty skill_ids returns empty dict."""

    def test_empty_set_returns_empty(self, tmp_path: Path) -> None:
        store = EvolutionStore(tmp_path / "evo.db")
        assert store.get_judgments_batch(set()) == {}


class TestConnResourceCleanup:
    """iter108 regression: PRAGMA/BEGIN failures must not leak connections.

    Before the fix, PRAGMA foreign_keys=ON and BEGIN IMMEDIATE were executed
    *before* the try/finally block in ``_conn()``.  If either raised, the
    ``finally: conn.close()`` never ran, leaking the SQLite connection.
    """

    @staticmethod
    def _make_failing_connect(fail_on: str, error_msg: str):
        """Return (patcher, close_counter) for tracking conn.close() calls.

        *fail_on* is a substring matched against the SQL string — any
        ``execute()`` call whose SQL contains it raises OperationalError.
        """
        import unittest.mock as mock

        close_counter: list[int] = [0]
        _real_connect = sqlite3.connect

        class _Proxy:
            """Lightweight proxy around sqlite3.Connection."""

            def __init__(self, conn):
                self._conn = conn

            def execute(self, sql, *a, **kw):
                if fail_on in sql:
                    raise sqlite3.OperationalError(error_msg)
                return self._conn.execute(sql, *a, **kw)

            def commit(self):
                return self._conn.commit()

            def rollback(self):
                return self._conn.rollback()

            def close(self):
                close_counter[0] += 1
                return self._conn.close()

            def __getattr__(self, name):
                return getattr(self._conn, name)

        def _connect(*args, **kwargs):
            return _Proxy(_real_connect(*args, **kwargs))

        return mock.patch.object(sqlite3, "connect", _connect), close_counter

    def test_pragma_failure_closes_connection(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        patcher, closes = self._make_failing_connect("PRAGMA", "forced pragma fail")
        with patcher:
            with pytest.raises(sqlite3.OperationalError, match="forced pragma"):
                with store._conn() as _:
                    pass

        assert closes[0] == 1, "Connection leaked: conn.close() never called after PRAGMA failure"

    def test_begin_immediate_failure_closes_connection(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)

        patcher, closes = self._make_failing_connect(
            "BEGIN", "database is locked"
        )
        with patcher:
            with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                with store._conn(immediate=True) as _:
                    pass

        assert closes[0] == 1, "Connection leaked after BEGIN IMMEDIATE failure"


class TestBatchRowResilience:
    """iter109 regression: single corrupt row must not kill entire list.

    get_active_skills / get_all_skills / get_versions used list comprehensions.
    If ``_row_to_record`` threw on one bad row (e.g. malformed datetime),
    the entire batch was lost.  Now ``_rows_to_records`` skips and logs.
    """

    @staticmethod
    def _make_record(
        skill_id: str,
        generation: int = 0,
        origin: SkillOrigin = SkillOrigin.CAPTURED,
    ) -> SkillRecord:
        return SkillRecord(
            id=skill_id,
            name=f"skill-{skill_id}",
            version="1.0",
            lineage=SkillLineage(origin=origin, generation=generation),
            directory="",
        )

    def test_corrupt_row_skipped_in_get_active_skills(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        # Insert 2 good records
        store.save_skill_record(self._make_record("good-1"))
        store.save_skill_record(self._make_record("good-2"))

        # Corrupt one row's updated_at field directly in DB
        with store._conn(immediate=True) as conn:
            conn.execute(
                "UPDATE skill_records SET updated_at = 'NOT-A-DATE' WHERE id = 'good-1'"
            )

        # get_active_skills should return 1 good record, skip 1 corrupt
        skills = store.get_active_skills()
        assert len(skills) == 1
        assert skills[0].id == "good-2"

    def test_corrupt_row_skipped_in_get_all_skills(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_skill_record(self._make_record("a"))
        store.save_skill_record(self._make_record("b"))

        with store._conn(immediate=True) as conn:
            conn.execute(
                "UPDATE skill_records SET created_at = '' WHERE id = 'a'"
            )

        skills = store.get_all_skills()
        assert len(skills) == 1
        assert skills[0].id == "b"

    def test_corrupt_row_skipped_in_get_versions(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        # Both records share the same name so get_versions returns both
        rec1 = SkillRecord(
            id="v1", name="my-skill", version="1.0",
            lineage=SkillLineage(origin=SkillOrigin.CAPTURED, generation=0),
            directory="",
        )
        rec2 = SkillRecord(
            id="v2", name="my-skill", version="1.1",
            lineage=SkillLineage(origin=SkillOrigin.CAPTURED, generation=1),
            directory="",
        )
        store.save_skill_record(rec1)
        store.save_skill_record(rec2)

        with store._conn(immediate=True) as conn:
            conn.execute(
                "UPDATE skill_records SET updated_at = 'bogus' WHERE id = 'v1'"
            )

        versions = store.get_versions("my-skill")
        assert len(versions) == 1
        assert versions[0].id == "v2"

    def test_all_rows_corrupt_returns_empty(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        store.save_skill_record(self._make_record("x"))

        with store._conn(immediate=True) as conn:
            conn.execute(
                "UPDATE skill_records SET created_at = 'garbage', updated_at = 'garbage' "
                "WHERE id = 'x'"
            )

        skills = store.get_active_skills()
        assert skills == []


# ============================================================================
# iter110e regression: record_analysis counter invariant validation
# ============================================================================


class TestRecordAnalysisCounterInvariants:
    """record_analysis must enforce the same counter invariants as increment_counters.

    applied requires selected, completed requires applied, fell_back requires selected.
    """

    @staticmethod
    def _seed_skill(store: EvolutionStore, skill_id: str) -> None:
        store.save_skill_record(
            SkillRecord(
                id=skill_id,
                name="test-skill",
                version="1.0.0",
                lineage=SkillLineage(origin=SkillOrigin.IMPORTED, generation=0),
                directory="skills/test",
                is_active=True,
                first_seen=datetime.now(timezone.utc),
                last_updated=datetime.now(timezone.utc),
            )
        )

    def test_fell_back_without_selected_raises(self, tmp_path: Path) -> None:
        """fell_back without selected raises; fell_back without applied is valid (selected suffices)."""
        store = _make_store(tmp_path)
        self._seed_skill(store, "s1")
        # fell_back WITHOUT selected is invalid
        with pytest.raises(ValueError, match="fell_back requires selected"):
            store.record_analysis(
                task_id="t1",
                agent_name="tester",
                analysis_text="test",
                judgments=[{
                    "skill_id": "s1",
                    "selected": False,
                    "applied": False,
                    "fell_back": True,
                }],
            )
        # fell_back WITH selected but WITHOUT applied is VALID (tried, failed, fell back)
        store.record_analysis(
            task_id="t2",
            agent_name="tester",
            analysis_text="test",
            judgments=[{
                "skill_id": "s1",
                "selected": True,
                "applied": False,
                "fell_back": True,
            }],
        )
        judgments = store.get_judgments_for_skill("s1")
        assert len(judgments) == 1

    def test_applied_without_selected_raises(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        self._seed_skill(store, "s1")
        with pytest.raises(ValueError, match="applied requires selected"):
            store.record_analysis(
                task_id="t1",
                agent_name="tester",
                analysis_text="test",
                judgments=[{
                    "skill_id": "s1",
                    "selected": False,
                    "applied": True,
                }],
            )

    def test_valid_counters_succeed(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        self._seed_skill(store, "s1")
        # selected + applied + completed + fell_back is valid
        store.record_analysis(
            task_id="t1",
            agent_name="tester",
            analysis_text="test",
            judgments=[{
                "skill_id": "s1",
                "selected": True,
                "applied": True,
                "completed": True,
                "fell_back": True,
            }],
        )
        judgments = store.get_judgments_for_skill("s1")
        assert len(judgments) == 1


# ============================================================================
# iter110 regression: negative limit bypasses SQL LIMIT
# ============================================================================


class TestNegativeLimitClamped:
    """Negative/zero limit must be clamped to 1, not passed to SQLite.

    SQLite treats ``LIMIT -1`` as "no limit", silently returning all rows.
    """

    @staticmethod
    def _seed_skill(store: EvolutionStore, skill_id: str) -> None:
        store.save_skill_record(
            SkillRecord(
                id=skill_id,
                name="test-skill",
                version="1.0.0",
                lineage=SkillLineage(origin=SkillOrigin.IMPORTED, generation=0),
                directory="skills/test",
                is_active=True,
                first_seen=datetime.now(timezone.utc),
                last_updated=datetime.now(timezone.utc),
            )
        )

    def test_get_judgments_for_skill_negative_limit(self, tmp_path: Path) -> None:
        """limit=-1 must return at most 1 row, not all rows."""
        store = _make_store(tmp_path)
        self._seed_skill(store, "s1")
        for i in range(5):
            store.record_analysis(
                task_id=f"t{i}",
                agent_name="tester",
                analysis_text=f"analysis {i}",
                judgments=[{"skill_id": "s1", "selected": True}],
            )
        # Without fix, limit=-1 would return all 5 rows
        result = store.get_judgments_for_skill("s1", limit=-1)
        assert len(result) == 1

    def test_get_judgments_for_skill_zero_limit(self, tmp_path: Path) -> None:
        """limit=0 must return at most 1 row, not 0 rows."""
        store = _make_store(tmp_path)
        self._seed_skill(store, "s1")
        store.record_analysis(
            task_id="t1",
            agent_name="tester",
            analysis_text="analysis",
            judgments=[{"skill_id": "s1", "selected": True}],
        )
        result = store.get_judgments_for_skill("s1", limit=0)
        assert len(result) == 1

    def test_get_budget_log_negative_limit(self, tmp_path: Path) -> None:
        """limit=-1 in get_budget_log must return at most 1 row."""
        store = _make_store(tmp_path)
        for i in range(5):
            store.log_budget_event(
                agent_name="test-agent",
                event_type="compaction",
                tokens_before=1000,
                tokens_after=500,
                details={"note": "test"},
            )
        result = store.get_budget_log("test-agent", limit=-1)
        assert len(result) == 1

    def test_get_budget_log_zero_limit(self, tmp_path: Path) -> None:
        """limit=0 in get_budget_log must return at most 1 row."""
        store = _make_store(tmp_path)
        store.log_budget_event(
            agent_name="test-agent",
            event_type="compaction",
            tokens_before=1000,
            tokens_after=500,
            details={"note": "test"},
        )
        result = store.get_budget_log("test-agent", limit=0)
        assert len(result) == 1


# ============================================================================
# iter111 regression: get_judgments_batch limit_per_skill clamp
# ============================================================================


class TestJudgmentsBatchLimitClamp:
    """get_judgments_batch limit_per_skill must clamp negative/zero to 1.

    Mirrors TestNegativeLimitClamped but for the batch method.
    """

    @staticmethod
    def _seed_skill(store: EvolutionStore, skill_id: str) -> None:
        store.save_skill_record(
            SkillRecord(
                id=skill_id,
                name="test-skill",
                version="1.0.0",
                lineage=SkillLineage(origin=SkillOrigin.IMPORTED, generation=0),
                directory="skills/test",
                is_active=True,
                first_seen=datetime.now(timezone.utc),
                last_updated=datetime.now(timezone.utc),
            )
        )

    @staticmethod
    def _seed_judgments(store: EvolutionStore, skill_id: str, count: int) -> None:
        import uuid

        for i in range(count):
            store.record_analysis(
                task_id=f"t-{skill_id}-{i}",
                agent_name="tester",
                analysis_text=f"analysis {i}",
                judgments=[{"skill_id": skill_id, "selected": True}],
            )

    def test_negative_limit_clamped_to_one(self, tmp_path: Path) -> None:
        """limit_per_skill=-1 must return at most 1 row per skill."""
        store = _make_store(tmp_path)
        self._seed_skill(store, "s1")
        self._seed_judgments(store, "s1", 5)
        batch = store.get_judgments_batch({"s1"}, limit_per_skill=-1)
        assert len(batch["s1"]) == 1

    def test_zero_limit_clamped_to_one(self, tmp_path: Path) -> None:
        """limit_per_skill=0 must return at most 1 row per skill."""
        store = _make_store(tmp_path)
        self._seed_skill(store, "s1")
        self._seed_judgments(store, "s1", 3)
        batch = store.get_judgments_batch({"s1"}, limit_per_skill=0)
        assert len(batch["s1"]) == 1
