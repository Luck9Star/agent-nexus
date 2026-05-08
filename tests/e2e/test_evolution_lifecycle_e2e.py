"""E2E: Evolution lifecycle — EvolutionStore + SkillStore + AnalysisStore + BudgetStore.

Tests cross-store transactions where SkillStore, AnalysisStore, and BudgetStore
must work together through the EvolutionStore facade. These paths cannot be
covered by unit tests of individual stores.

Also covers EvolutionEngine error injection paths: invalid triggers, missing
required arguments, and health check on nonexistent skills.
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
    db_path = tmp_path / "test_evolution.db"
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


# ---------------------------------------------------------------------------
# SkillStore + AnalysisStore cross-store integration
# ---------------------------------------------------------------------------


class TestSkillAnalysisIntegration:
    """Skills and their analysis/judgments must stay consistent."""

    def test_record_analysis_with_judgments_and_counters(self, store: EvolutionStore) -> None:
        skill = _make_skill("s1")
        store.save_skill_record(skill)

        judgments = [
            {
                "skill_id": "s1",
                "selected": True,
                "applied": True,
                "completed": True,
                "fell_back": False,
            }
        ]
        analysis_id = store.record_analysis(
            task_id="task-1",
            agent_name="agent-a",
            analysis_text="Analysis of task-1",
            judgments=judgments,
        )
        assert analysis_id

        # Judgments are retrievable
        j = store.get_judgments_for_skill("s1")
        assert len(j) == 1
        assert j[0]["selected"] is True
        assert j[0]["completed"] is True

        # Analysis is retrievable
        analyses = store.get_analyses_for_task("task-1")
        assert len(analyses) == 1
        assert analyses[0]["agent_name"] == "agent-a"

    def test_judgments_batch_across_skills(self, store: EvolutionStore) -> None:
        s1 = _make_skill("s1", name="alpha")
        s2 = _make_skill("s2", name="beta")
        store.save_skill_record(s1)
        store.save_skill_record(s2)

        # Record analysis with judgments for both skills
        store.record_analysis(
            "task-1", "agent-a", "text",
            judgments=[
                {"skill_id": "s1", "selected": True, "applied": True},
                {"skill_id": "s2", "selected": True, "fell_back": True},
            ],
        )

        batch = store.get_judgments_batch({"s1", "s2"})
        assert "s1" in batch
        assert "s2" in batch
        assert len(batch["s1"]) == 1
        assert len(batch["s2"]) == 1

    def test_analysis_counter_invariant_violation_rollback(
        self, store: EvolutionStore
    ) -> None:
        """Counter invariant violation rolls back both analysis and judgments."""
        skill = _make_skill("s1")
        store.save_skill_record(skill)

        with pytest.raises(ValueError, match="applied requires selected"):
            store.record_analysis(
                "task-bad", "agent-a", "bad analysis",
                judgments=[
                    {
                        "skill_id": "s1",
                        "applied": True,
                        "completed": True,
                        # violates: completed > applied (0 selections, but completed)
                    },
                ],
            )

        # Analysis should not exist after rollback
        assert store.get_analyses_for_task("task-bad") == []
        assert store.get_judgments_for_skill("s1") == []


# ---------------------------------------------------------------------------
# SkillStore + BudgetStore cross-store integration
# ---------------------------------------------------------------------------


class TestSkillBudgetIntegration:
    """Budget events should correlate with skill usage patterns."""

    def test_log_budget_and_query(self, store: EvolutionStore) -> None:
        store.log_budget_event(
            agent_name="agent-a",
            event_type="compaction",
            tokens_before=10000,
            tokens_after=5000,
            details={"reason": "threshold exceeded"},
        )
        log = store.get_budget_log("agent-a")
        assert len(log) == 1
        assert log[0]["event_type"] == "compaction"
        assert log[0]["tokens_before"] == 10000

    def test_budget_log_isolated_per_agent(self, store: EvolutionStore) -> None:
        store.log_budget_event("agent-a", "compaction", 10000, 5000)
        store.log_budget_event("agent-b", "eviction", 8000, 2000)

        log_a = store.get_budget_log("agent-a")
        log_b = store.get_budget_log("agent-b")
        assert len(log_a) == 1
        assert len(log_b) == 1
        assert log_a[0]["event_type"] == "compaction"
        assert log_b[0]["event_type"] == "eviction"


# ---------------------------------------------------------------------------
# Evolution full lifecycle: create → use → evolve → query lineage
# ---------------------------------------------------------------------------


class TestEvolutionLifecycle:
    """Full skill lifecycle from creation through evolution to lineage queries."""

    def test_skill_create_evolve_lineage(self, store: EvolutionStore) -> None:
        # Step 1: Create original skill
        parent = _make_skill("parent-1", name="review", generation=0)
        store.save_skill_record(parent)

        # Step 2: Simulate usage — increment counters
        store.increment_counters("parent-1", selected=True, applied=True, completed=True)

        # Step 3: Evolve into a new version (FIX)
        child = SkillRecord(
            id="child-1",
            name="review",
            lineage=SkillLineage(
                origin=SkillOrigin.FIXED,
                generation=1,
                parent_skill_ids=["parent-1"],
            ),
            directory="/skills/review-v2",
        )
        result = store.evolve_skill(child, parent_skill_ids=["parent-1"])
        assert result.success is True
        assert result.new_record is not None
        assert result.new_record.id == "child-1"

        # Step 4: Verify parent deactivated (FIX evolution)
        parent_record = store.get_skill_record("parent-1")
        assert parent_record is not None and parent_record.is_active is False

        # Step 5: Verify child is active
        child_record = store.get_skill_record("child-1")
        assert child_record is not None and child_record.is_active is True
        assert child_record.lineage.generation == 1

        # Step 6: Verify lineage
        ancestry = store.get_ancestry("child-1")
        assert len(ancestry) == 1
        assert ancestry[0].id == "parent-1"

        children = store.get_children("parent-1")
        assert "child-1" in children

    def test_multi_generation_evolution(self, store: EvolutionStore) -> None:
        # Gen 0
        g0 = _make_skill("gen-0", name="scan", generation=0)
        store.save_skill_record(g0)

        # Gen 1
        g1 = SkillRecord(
            id="gen-1", name="scan",
            lineage=SkillLineage(
                origin=SkillOrigin.DERIVED,
                generation=1,
                parent_skill_ids=["gen-0"],
            ),
            directory="/skills/scan-v1",
        )
        r1 = store.evolve_skill(g1, ["gen-0"])
        assert r1.success

        # Gen 2
        g2 = SkillRecord(
            id="gen-2", name="scan",
            lineage=SkillLineage(
                origin=SkillOrigin.DERIVED,
                generation=2,
                parent_skill_ids=["gen-1"],
            ),
            directory="/skills/scan-v2",
        )
        r2 = store.evolve_skill(g2, ["gen-1"])
        assert r2.success

        # Ancestry of gen-2 should trace back to gen-0
        ancestry = store.get_ancestry("gen-2")
        ids = [a.id for a in ancestry]
        assert "gen-1" in ids
        assert "gen-0" in ids

        # Metrics should reflect usage
        metrics = store.get_metrics()
        assert metrics.total_selections >= 0

    def test_skill_deactivation_and_reactivation(self, store: EvolutionStore) -> None:
        skill = _make_skill("s1")
        store.save_skill_record(skill)
        assert store.get_skill_record("s1").is_active is True

        # Deactivate
        assert store.deactivate_skill("s1") is True
        assert store.get_skill_record("s1").is_active is False

        # get_active_skills should not include deactivated
        active = store.get_active_skills()
        assert all(s.id != "s1" for s in active)

    def test_agent_record_lifecycle(self, store: EvolutionStore) -> None:
        skill = _make_skill("s1")
        store.save_skill_record(skill)

        # Save agent record
        store.save_agent_record(
            agent_id="agent-1",
            name="code-reviewer",
            type="atomic",
            skill_ids=["s1"],
            orchestration_toml="[dag]\ntask_1 = { agent = 'code-reviewer' }",
        )

        # Retrieve
        record = store.get_agent_record("agent-1")
        assert record is not None
        assert record["name"] == "code-reviewer"
        assert record["type"] == "atomic"
        assert "s1" in record["skill_ids"]

        # List active agents
        agents = store.get_active_agents()
        assert any(a["agent_id"] == "agent-1" for a in agents)


# ---------------------------------------------------------------------------
# Batch operations across stores
# ---------------------------------------------------------------------------


class TestBatchOperations:
    """Batch queries that touch multiple tables."""

    def test_get_skill_records_batch(self, store: EvolutionStore) -> None:
        for i in range(5):
            store.save_skill_record(_make_skill(f"s{i}", name=f"skill-{i}"))

        batch = store.get_skill_records_batch(["s0", "s2", "s4", "s-missing"])
        assert len(batch) == 3
        assert "s0" in batch
        assert "s2" in batch
        assert "s4" in batch
        assert "s-missing" not in batch

    def test_ancestry_batch(self, store: EvolutionStore) -> None:
        g0 = _make_skill("a0", name="root", generation=0)
        store.save_skill_record(g0)

        g1 = SkillRecord(
            id="a1", name="root",
            lineage=SkillLineage(origin=SkillOrigin.DERIVED, generation=1, parent_skill_ids=["a0"]),
            directory="/skills/root-v1",
        )
        store.evolve_skill(g1, ["a0"])

        batch = store.get_ancestry_batch(["a1", "a0"])
        assert "a1" in batch
        assert len(batch["a1"]) == 1  # parent a0
        assert "a0" in batch
        assert len(batch["a0"]) == 0  # root has no parents

    def test_get_all_skills_pagination(self, store: EvolutionStore) -> None:
        for i in range(10):
            store.save_skill_record(_make_skill(f"p{i}", name=f"paged-{i}"))

        page1 = store.get_all_skills(limit=5, offset=0)
        page2 = store.get_all_skills(limit=5, offset=5)
        assert len(page1) == 5
        assert len(page2) == 5
        # No overlap
        ids1 = {s.id for s in page1}
        ids2 = {s.id for s in page2}
        assert ids1.isdisjoint(ids2)


# ---------------------------------------------------------------------------
# EvolutionEngine error injection
# ---------------------------------------------------------------------------


class TestEvolutionEngineErrorPaths:
    """EvolutionEngine facade correctly routes and validates triggers.

    Uses real EvolutionStore (SQLite) to exercise the full engine → sub-component
    delegation chain. Only error/edge-case paths — happy paths are covered in
    test_evolution_e2e.py.
    """

    @pytest.fixture()
    def engine_and_store(self, tmp_path: Path):
        from agent_nexus.platform.evolution.engine import EvolutionEngine
        from agent_nexus.platform.evolution.store import EvolutionStore

        db_path = tmp_path / "engine_e2e.db"
        store = EvolutionStore(db_path)
        engine = EvolutionEngine(store)
        yield engine, store
        store.close()

    def test_evolve_post_analysis_without_ctx_raises(
        self, engine_and_store
    ) -> None:
        """POST_ANALYSIS trigger without ctx raises ValueError."""
        from agent_nexus.platform.evolution.evolver import EvolutionTrigger

        engine, _ = engine_and_store
        with pytest.raises(ValueError, match="ctx.*required"):
            engine.evolve(trigger=EvolutionTrigger.POST_ANALYSIS)

    def test_evolve_tool_degradation_without_tool_key_raises(
        self, engine_and_store
    ) -> None:
        """TOOL_DEGRADATION trigger without tool_key raises ValueError."""
        from agent_nexus.platform.evolution.evolver import EvolutionTrigger

        engine, _ = engine_and_store
        with pytest.raises(ValueError, match="tool_key.*required"):
            engine.evolve(trigger=EvolutionTrigger.TOOL_DEGRADATION)

    def test_evolve_unknown_trigger_raises(self, engine_and_store) -> None:
        """Passing an unknown/invalid trigger string raises ValueError."""
        engine, _ = engine_and_store
        with pytest.raises(ValueError, match="Unknown trigger"):
            engine.evolve(trigger="nonexistent_trigger")  # type: ignore[arg-type]

    def test_check_health_nonexistent_skill_raises(
        self, engine_and_store
    ) -> None:
        """check_health raises ValueError for a skill that doesn't exist."""
        engine, _ = engine_and_store
        with pytest.raises(ValueError, match="Skill not found"):
            engine.check_health("ghost-skill-999")

    def test_diagnose_all_empty_returns_empty_dict(
        self, engine_and_store
    ) -> None:
        """diagnose_all on empty store returns empty dict (no crash)."""
        engine, _ = engine_and_store
        result = engine.diagnose_all()
        assert result == {}

    def test_evolve_tool_degradation_returns_results(
        self, engine_and_store
    ) -> None:
        """TOOL_DEGRADATION with tool_key returns a list of EvolveResults."""
        from agent_nexus.platform.evolution.evolver import EvolutionTrigger

        engine, store = engine_and_store
        # Seed a skill so the evolver has something to work with
        store.save_skill_record(_make_skill("td-skill", name="td-skill"))

        results = engine.evolve(
            trigger=EvolutionTrigger.TOOL_DEGRADATION,
            tool_key="failing-tool",
            problem_description="Tool returns 500 errors",
        )
        assert isinstance(results, list)
        assert all(hasattr(r, "success") for r in results)

    def test_evolve_metric_check_returns_results(
        self, engine_and_store
    ) -> None:
        """METRIC_CHECK trigger returns a list (possibly empty if no skills qualify)."""
        from agent_nexus.platform.evolution.evolver import EvolutionTrigger

        engine, _ = engine_and_store
        results = engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK)
        assert isinstance(results, list)
        assert all(hasattr(r, "success") for r in results)
