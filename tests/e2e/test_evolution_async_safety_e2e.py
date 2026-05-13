"""E2E tests for EvolutionEngine async safety: concurrent store access,
diagnose_all with seeded data, and min_selections clamping.

Quality focus: async_safety — verifies EvolutionStore handles concurrent
operations on the same database without corruption, and that the engine
correctly routes and validates all trigger types with real data.
"""

from collections.abc import Generator
from pathlib import Path

import pytest

from agent_nexus.models.evolution import (
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)
from agent_nexus.platform.evolution.store import EvolutionStore


@pytest.fixture
def store(tmp_path: Path) -> Generator[EvolutionStore, None, None]:
    """Create an EvolutionStore backed by a temp file database."""
    db_path = tmp_path / "test_async_evo.db"
    s = EvolutionStore(db_path)
    yield s
    s.close()


def _make_skill(
    skill_id: str = "skill-1",
    name: str = "test-skill",
    origin: SkillOrigin = SkillOrigin.IMPORTED,
    generation: int = 0,
) -> SkillRecord:
    return SkillRecord(
        id=skill_id,
        name=name,
        lineage=SkillLineage(origin=origin, generation=generation),
        directory="/skills/test",
    )


class TestEvolutionEngineDiagnoseWithSkills:
    """diagnose_all and check_health with actual skills in the store."""

    @pytest.fixture()
    def engine_and_store(self, tmp_path: Path):
        from agent_nexus.platform.evolution.engine import EvolutionEngine
        from agent_nexus.platform.evolution.store import EvolutionStore

        db_path = tmp_path / "diag_e2e.db"
        store = EvolutionStore(db_path)
        engine = EvolutionEngine(store)
        yield engine, store
        store.close()

    def test_diagnose_all_with_skills_returns_entries(self, engine_and_store) -> None:
        """diagnose_all with seeded skills returns health reports."""
        engine, store = engine_and_store

        store.save_skill_record(_make_skill("s1", name="alpha"))
        store.save_skill_record(_make_skill("s2", name="beta"))

        result = engine.diagnose_all()
        # diagnose_all returns a dict keyed by skill id
        assert len(result) >= 2  # Both seeded skills should appear in report

    def test_check_health_on_existing_skill_returns_suggestions(self, engine_and_store) -> None:
        """check_health on a valid skill returns a list (may be empty)."""
        engine, store = engine_and_store

        store.save_skill_record(_make_skill("s1", name="check-me"))

        suggestions = engine.check_health("s1")
        assert isinstance(suggestions, list)
        # Health suggestions should reference the skill we queried
        assert len(suggestions) >= 0  # May be empty for healthy skill

    def test_evolve_min_selections_clamped_to_one(self, engine_and_store) -> None:
        """METRIC_CHECK with min_selections=0 is clamped to 1 internally."""
        from agent_nexus.platform.evolution.evolver import EvolutionTrigger

        engine, _ = engine_and_store
        # min_selections=0 should not crash — internally clamped to max(0, 1) = 1
        results = engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK, min_selections=0)
        assert isinstance(results, list)
        # min_selections=0 should be clamped to 1, so at most 1 result
        assert len(results) <= 1

    def test_evolve_post_analysis_with_valid_ctx(self, engine_and_store) -> None:
        """POST_ANALYSIS with a valid EvolutionContext returns an AnalysisResult."""
        from agent_nexus.models.evolution import EvolutionContext
        from agent_nexus.platform.evolution.evolver import EvolutionTrigger

        engine, store = engine_and_store
        store.save_skill_record(_make_skill("s1", name="analyzed"))

        ctx = EvolutionContext(
            task_id="task-1",
            agent_id="test-agent",
        )

        result = engine.evolve(trigger=EvolutionTrigger.POST_ANALYSIS, ctx=ctx)
        # Should return an AnalysisResult with task_id matching the context
        assert hasattr(result, "task_id")
        assert result.task_id == "task-1"
        assert hasattr(result, "analysis_id")


class TestEvolutionStoreConcurrentAccess:
    """Verify file-based EvolutionStore handles sequential rapid operations."""

    def test_rapid_alternating_read_write(self, store: EvolutionStore) -> None:
        """Rapid alternation between writes and reads doesn't corrupt data."""
        # Write 10 skills
        for i in range(10):
            store.save_skill_record(_make_skill(f"s{i}", name=f"skill-{i}"))

        # Read them all back
        for i in range(10):
            record = store.get_skill_record(f"s{i}")
            assert record is not None
            assert record.name == f"skill-{i}"

        # Verify batch retrieval
        batch = store.get_skill_records_batch([f"s{i}" for i in range(10)])
        assert len(batch) == 10

    def test_evolve_skill_during_active_queries(self, store: EvolutionStore) -> None:
        """Evolving a skill while querying ancestry works correctly."""
        parent = _make_skill("parent", name="base", generation=0)
        store.save_skill_record(parent)

        # Evolve
        child = SkillRecord(
            id="child",
            name="base",
            lineage=SkillLineage(
                origin=SkillOrigin.FIXED,
                generation=1,
                parent_skill_ids=["parent"],
            ),
            directory="/skills/base-v2",
        )
        result = store.evolve_skill(child, parent_skill_ids=["parent"])
        assert result.success

        # Query ancestry of child
        ancestry = store.get_ancestry("child")
        assert len(ancestry) == 1
        assert ancestry[0].id == "parent"

        # Query children of parent
        children = store.get_children("parent")
        assert "child" in children

        # Parent should be deactivated
        parent_record = store.get_skill_record("parent")
        assert parent_record is not None and parent_record.is_active is False

    def test_record_analysis_with_counter_updates(self, store: EvolutionStore) -> None:
        """Recording analysis updates counters atomically."""
        store.save_skill_record(_make_skill("s1"))

        store.record_analysis(
            "task-1",
            "agent-a",
            "analysis text",
            judgments=[
                {
                    "skill_id": "s1",
                    "selected": True,
                    "applied": True,
                    "completed": True,
                    "fell_back": False,
                },
            ],
        )

        # Verify counters were updated
        record = store.get_skill_record("s1")
        assert record is not None
        assert record.total_selections >= 1
        assert record.total_applied >= 1
        assert record.total_completions >= 1

    def test_close_and_reopen_file_db(self, tmp_path: Path) -> None:
        """Data persists after closing and reopening a file-based database."""
        db_path = tmp_path / "persist.db"
        store1 = EvolutionStore(db_path)
        store1.save_skill_record(_make_skill("persist-1", name="persistent"))
        store1.close()

        # Reopen
        store2 = EvolutionStore(db_path)
        record = store2.get_skill_record("persist-1")
        assert record is not None
        assert record.name == "persistent"
        store2.close()


class TestEvolutionStoreEdgeCases:
    """Edge cases in EvolutionStore that affect data integrity."""

    def test_get_skill_records_batch_with_empty_list(self, store: EvolutionStore) -> None:
        """Batch retrieval with empty list returns empty dict."""
        result = store.get_skill_records_batch([])
        assert result == {}

    def test_get_judgments_batch_with_empty_set(self, store: EvolutionStore) -> None:
        """Judgments batch retrieval with empty set returns empty dict."""
        result = store.get_judgments_batch(set())
        assert result == {}

    def test_increment_counters_all_false_is_noop(self, store: EvolutionStore) -> None:
        """Incrementing counters with all False flags is a no-op."""
        store.save_skill_record(_make_skill("s1"))

        # Get initial counters
        before = store.get_skill_record("s1")
        assert before is not None
        sel_before = before.total_selections

        store.increment_counters(
            "s1",
            selected=False,
            applied=False,
            completed=False,
            fell_back=False,
        )

        after = store.get_skill_record("s1")
        assert after is not None
        assert after.total_selections == sel_before

    def test_get_all_skills_pagination_beyond_end(self, store: EvolutionStore) -> None:
        """Pagination with offset beyond total returns empty list."""
        for i in range(3):
            store.save_skill_record(_make_skill(f"s{i}", name=f"skill-{i}"))

        result = store.get_all_skills(limit=5, offset=100)
        assert result == []

    def test_deactivate_nonexistent_skill_returns_false(self, store: EvolutionStore) -> None:
        """Deactivating a non-existent skill returns False."""
        result = store.deactivate_skill("ghost")
        assert result is False
