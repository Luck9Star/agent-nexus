"""E2E: EvolutionExperimenter SQLite persistence across simulated restarts.

TRUE E2E tests verifying that experiment state survives closing and
reopening the EvolutionStore with the same on-disk SQLite database.

Test sections:
  1. Data survival: experiment + outcomes persist across store reopen
  2. list_active: running experiments survive restart
  3. evaluate: recommendation works with persisted data after reopen
  4. rollback: parent reactivation works after restart
  5. Multiple experiments: mixed statuses survive restart
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_nexus.models.evolution import (
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)
from agent_nexus.platform.evolution.experimenter import (
    EvolutionExperimenter,
    ExperimentStatus,
)
from agent_nexus.platform.evolution.store import EvolutionStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill(
    name: str,
    origin: SkillOrigin = SkillOrigin.IMPORTED,
    parent_ids: list[str] | None = None,
    selections: int = 0,
    applied: int = 0,
    completions: int = 0,
    fallbacks: int = 0,
) -> SkillRecord:
    """Create a SkillRecord with SkillLineage (mirrors unit test helper)."""
    return SkillRecord(
        id=f"{name}__v1",
        name=name,
        lineage=SkillLineage(
            origin=origin,
            generation=1 if origin != SkillOrigin.IMPORTED else 0,
            parent_skill_ids=parent_ids or [],
        ),
        total_selections=selections,
        total_applied=applied,
        total_completions=completions,
        total_fallbacks=fallbacks,
    )


def _register_skill(store: EvolutionStore, skill: SkillRecord) -> SkillRecord:
    """Persist a skill record and return it."""
    store.save_skill_record(skill)
    return skill


def _open_store(db_path: Path) -> EvolutionStore:
    """Open EvolutionStore backed by a real file (NOT :memory:)."""
    return EvolutionStore(db_path)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    """Provide a temporary file-based SQLite database path."""
    return tmp_path / "evolution_test.db"


# ===========================================================================
# 1. Experiment + outcomes survive store reopen
# ===========================================================================


class TestDataSurvival:
    """get_experiment returns full data after closing and reopening store."""

    def test_experiment_survives_restart(self, db_path: Path) -> None:
        """Create experiment, record outcomes, close store, reopen, verify."""
        # --- Phase 1: create and populate ---
        store = _open_store(db_path)
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved, min_samples=5)

        # Record some outcomes
        for _ in range(3):
            ex.record_outcome(exp.experiment_id, parent.id, success=True)
        for _ in range(2):
            ex.record_outcome(exp.experiment_id, parent.id, success=False)
        for _ in range(4):
            ex.record_outcome(exp.experiment_id, evolved.id, success=True)
        for _ in range(1):
            ex.record_outcome(exp.experiment_id, evolved.id, success=False)

        # Sanity check before close
        before = ex.get_experiment(exp.experiment_id)
        assert before is not None
        assert before.parent_total == 5
        assert before.parent_successes == 3
        assert before.evolved_total == 5
        assert before.evolved_successes == 4

        experiment_id = exp.experiment_id
        store.close()

        # --- Phase 2: reopen and verify ---
        store2 = _open_store(db_path)
        ex2 = EvolutionExperimenter(store2)

        after = ex2.get_experiment(experiment_id)
        assert after is not None
        assert after.experiment_id == experiment_id
        assert after.parent_skill_id == parent.id
        assert after.evolved_skill_id == evolved.id
        assert after.status == ExperimentStatus.RUNNING
        assert after.parent_total == 5
        assert after.parent_successes == 3
        assert after.evolved_total == 5
        assert after.evolved_successes == 4
        assert after.min_samples == 5

        store2.close()


# ===========================================================================
# 2. list_active returns running experiments after restart
# ===========================================================================


class TestListActivePersistence:
    """list_active returns the correct running experiments after reopen."""

    def test_list_active_after_restart(self, db_path: Path) -> None:
        """Create two experiments, rollback one, reopen, verify list_active."""
        store = _open_store(db_path)
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp1 = ex.create_experiment(parent, evolved)
        exp2 = ex.create_experiment(parent, evolved)

        assert len(ex.list_active()) == 2

        ex.rollback(exp1.experiment_id)
        assert len(ex.list_active()) == 1

        store.close()

        # Reopen
        store2 = _open_store(db_path)
        ex2 = EvolutionExperimenter(store2)

        active = ex2.list_active()
        assert len(active) == 1
        assert active[0].experiment_id == exp2.experiment_id
        assert active[0].status == ExperimentStatus.RUNNING

        # Verify the rolled-back experiment is NOT in active list
        rolled_back = ex2.get_experiment(exp1.experiment_id)
        assert rolled_back is not None
        assert rolled_back.status == ExperimentStatus.REVERTED

        store2.close()


# ===========================================================================
# 3. evaluate works after restart with persisted data
# ===========================================================================


class TestEvaluateAfterRestart:
    """evaluate returns correct recommendation using persisted data."""

    def test_evaluate_promote_after_restart(self, db_path: Path) -> None:
        """Record enough outcomes, close, reopen, evaluate recommends promote."""
        store = _open_store(db_path)
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved, min_samples=5)

        # Parent: 3/10 success rate
        for _ in range(3):
            ex.record_outcome(exp.experiment_id, parent.id, success=True)
        for _ in range(7):
            ex.record_outcome(exp.experiment_id, parent.id, success=False)

        # Evolved: 8/10 success rate
        for _ in range(8):
            ex.record_outcome(exp.experiment_id, evolved.id, success=True)
        for _ in range(2):
            ex.record_outcome(exp.experiment_id, evolved.id, success=False)

        experiment_id = exp.experiment_id
        store.close()

        # Reopen and evaluate
        store2 = _open_store(db_path)
        ex2 = EvolutionExperimenter(store2)

        result = ex2.evaluate(experiment_id)
        assert result.evolved_performance > result.parent_performance
        assert result.recommendation == "promote"
        assert result.samples_remaining == 0

        store2.close()

    def test_evaluate_revert_after_restart(self, db_path: Path) -> None:
        """Record enough outcomes, close, reopen, evaluate recommends revert."""
        store = _open_store(db_path)
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved, min_samples=5)

        # Parent: 9/10 success rate
        for _ in range(9):
            ex.record_outcome(exp.experiment_id, parent.id, success=True)
        for _ in range(1):
            ex.record_outcome(exp.experiment_id, parent.id, success=False)

        # Evolved: 2/10 success rate
        for _ in range(2):
            ex.record_outcome(exp.experiment_id, evolved.id, success=True)
        for _ in range(8):
            ex.record_outcome(exp.experiment_id, evolved.id, success=False)

        experiment_id = exp.experiment_id
        store.close()

        # Reopen and evaluate
        store2 = _open_store(db_path)
        ex2 = EvolutionExperimenter(store2)

        result = ex2.evaluate(experiment_id)
        assert result.parent_performance > result.evolved_performance
        assert result.recommendation == "revert"

        store2.close()

    def test_evaluate_continue_after_restart(self, db_path: Path) -> None:
        """Insufficient samples persisted, close, reopen, evaluate says continue."""
        store = _open_store(db_path)
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved, min_samples=30)

        # Only 2 samples each
        ex.record_outcome(exp.experiment_id, parent.id, success=True)
        ex.record_outcome(exp.experiment_id, evolved.id, success=True)

        experiment_id = exp.experiment_id
        store.close()

        # Reopen and evaluate
        store2 = _open_store(db_path)
        ex2 = EvolutionExperimenter(store2)

        result = ex2.evaluate(experiment_id)
        assert result.recommendation == "continue"
        assert result.samples_remaining > 0

        store2.close()


# ===========================================================================
# 4. rollback works after restart
# ===========================================================================


class TestRollbackAfterRestart:
    """rollback deactivates evolved and reactivates parent after reopen."""

    def test_rollback_after_restart(self, db_path: Path) -> None:
        """Create experiment, close, reopen, rollback succeeds."""
        store = _open_store(db_path)
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved)

        experiment_id = exp.experiment_id
        store.close()

        # Reopen and rollback
        store2 = _open_store(db_path)
        ex2 = EvolutionExperimenter(store2)

        returned_parent = ex2.rollback(experiment_id)
        assert returned_parent.id == parent.id

        # Verify experiment status changed to REVERTED
        updated_exp = ex2.get_experiment(experiment_id)
        assert updated_exp is not None
        assert updated_exp.status == ExperimentStatus.REVERTED

        # Verify evolved skill is deactivated
        evolved_record = store2.get_skill_record(evolved.id)
        assert evolved_record is not None
        assert evolved_record.is_active is False

        # Verify parent skill is active
        parent_record = store2.get_skill_record(parent.id)
        assert parent_record is not None
        assert parent_record.is_active is True

        store2.close()

    def test_rollback_with_outcomes_after_restart(self, db_path: Path) -> None:
        """Record outcomes, close, reopen, rollback preserves outcome data."""
        store = _open_store(db_path)
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved, min_samples=5)

        # Record outcomes before restart
        for _ in range(5):
            ex.record_outcome(exp.experiment_id, parent.id, success=True)
        for _ in range(5):
            ex.record_outcome(exp.experiment_id, evolved.id, success=False)

        experiment_id = exp.experiment_id
        store.close()

        # Reopen, rollback, verify outcome data survived
        store2 = _open_store(db_path)
        ex2 = EvolutionExperimenter(store2)

        ex2.rollback(experiment_id)

        reverted_exp = ex2.get_experiment(experiment_id)
        assert reverted_exp is not None
        assert reverted_exp.status == ExperimentStatus.REVERTED
        # Outcome counters should be preserved
        assert reverted_exp.parent_successes == 5
        assert reverted_exp.parent_total == 5
        assert reverted_exp.evolved_successes == 0
        assert reverted_exp.evolved_total == 5

        store2.close()


# ===========================================================================
# 5. Multiple experiments with mixed statuses survive restart
# ===========================================================================


class TestMultipleExperimentsPersistence:
    """Multiple experiments with mixed statuses all survive restart."""

    def test_mixed_statuses_after_restart(self, db_path: Path) -> None:
        """Create 3 experiments, rollback 1, close, reopen, verify all."""
        store = _open_store(db_path)
        parent = _register_skill(store, _make_skill("parent"))
        evolved_a = _register_skill(store, _make_skill("evolved_a", SkillOrigin.FIXED))
        evolved_b = _register_skill(store, _make_skill("evolved_b", SkillOrigin.FIXED))
        evolved_c = _register_skill(store, _make_skill("evolved_c", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp_a = ex.create_experiment(parent, evolved_a, min_samples=5)
        exp_b = ex.create_experiment(parent, evolved_b, min_samples=5)
        exp_c = ex.create_experiment(parent, evolved_c, min_samples=5)

        # Record outcomes for all three
        for _ in range(3):
            ex.record_outcome(exp_a.experiment_id, parent.id, success=True)
            ex.record_outcome(exp_a.experiment_id, evolved_a.id, success=True)

        for _ in range(2):
            ex.record_outcome(exp_b.experiment_id, parent.id, success=False)
            ex.record_outcome(exp_b.experiment_id, evolved_b.id, success=True)

        for _ in range(4):
            ex.record_outcome(exp_c.experiment_id, parent.id, success=True)
            ex.record_outcome(exp_c.experiment_id, evolved_c.id, success=False)

        # Rollback experiment C
        ex.rollback(exp_c.experiment_id)

        ids = {
            "a": exp_a.experiment_id,
            "b": exp_b.experiment_id,
            "c": exp_c.experiment_id,
        }
        store.close()

        # Reopen
        store2 = _open_store(db_path)
        ex2 = EvolutionExperimenter(store2)

        # Verify A: still running with outcomes
        exp_a_loaded = ex2.get_experiment(ids["a"])
        assert exp_a_loaded is not None
        assert exp_a_loaded.status == ExperimentStatus.RUNNING
        assert exp_a_loaded.parent_total == 3
        assert exp_a_loaded.evolved_total == 3

        # Verify B: still running with outcomes
        exp_b_loaded = ex2.get_experiment(ids["b"])
        assert exp_b_loaded is not None
        assert exp_b_loaded.status == ExperimentStatus.RUNNING
        assert exp_b_loaded.parent_total == 2
        assert exp_b_loaded.evolved_total == 2

        # Verify C: reverted
        exp_c_loaded = ex2.get_experiment(ids["c"])
        assert exp_c_loaded is not None
        assert exp_c_loaded.status == ExperimentStatus.REVERTED
        assert exp_c_loaded.parent_total == 4
        assert exp_c_loaded.evolved_total == 4

        # Verify list_active returns only A and B
        active = ex2.list_active()
        assert len(active) == 2
        active_ids = {e.experiment_id for e in active}
        assert ids["a"] in active_ids
        assert ids["b"] in active_ids
        assert ids["c"] not in active_ids

        # Evaluate A: evolved was perfect, parent had same rate -> check
        result_a = ex2.evaluate(ids["a"])
        assert result_a.recommendation == "continue"  # Not enough samples (6 < 10)

        store2.close()
