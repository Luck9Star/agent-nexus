"""Tests for Step 16: Evolution observability, A/B testing, and configuration."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent_nexus.models.evolution import (
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)
from agent_nexus.platform.evolution.evolution_config import EvolutionConfig
from agent_nexus.platform.evolution.experimenter import (
    EvolutionExperimenter,
    ExperimentStatus,
)
from agent_nexus.platform.evolution.metrics import EvolutionDashboard
from agent_nexus.platform.evolution.store import EvolutionStore


@pytest.fixture
def store() -> EvolutionStore:
    s = EvolutionStore(Path(":memory:"))
    return s


def _make_skill(
    name: str,
    origin: SkillOrigin = SkillOrigin.IMPORTED,
    parent_ids: list[str] | None = None,
    selections: int = 0,
    applied: int = 0,
    completions: int = 0,
    fallbacks: int = 0,
) -> SkillRecord:
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
    store.save_skill_record(skill)
    return skill


# ---------------------------------------------------------------------------
# EvolutionConfig
# ---------------------------------------------------------------------------


class TestEvolutionConfig:
    def test_defaults(self) -> None:
        config = EvolutionConfig()
        assert config.enabled is True
        assert config.auto_promote is False
        assert config.max_evolution_per_day == 10
        assert config.llm_model == "anthropic:claude-sonnet-4-20250514"
        assert config.llm_temperature == 0.3
        assert config.llm_max_tokens == 4096

    def test_load_missing_file(self) -> None:
        config = EvolutionConfig.load(Path("/nonexistent/evolution.toml"))
        assert config.enabled is True  # defaults

    def test_load_valid_toml(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
            f.write("""
[evolution]
enabled = false
auto_promote = true
max_evolution_per_day = 5

[evolution.thresholds]
fix_fallback_rate = 0.3

[evolution.llm]
model = "deepseek:deepseek-chat"
temperature = 0.5

[evolution.experiment]
min_samples = 50
""")
            f.flush()
            config = EvolutionConfig.load(Path(f.name))

        assert config.enabled is False
        assert config.auto_promote is True
        assert config.max_evolution_per_day == 5
        assert config.thresholds["fix_fallback_rate"] == 0.3
        assert config.llm_model == "deepseek:deepseek-chat"
        assert config.llm_temperature == 0.5
        assert config.experiment_min_samples == 50


# ---------------------------------------------------------------------------
# EvolutionExperimenter
# ---------------------------------------------------------------------------


class TestExperimenter:
    def test_create_experiment(self, store: EvolutionStore) -> None:
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved)

        assert exp.parent_skill_id == parent.id
        assert exp.evolved_skill_id == evolved.id
        assert exp.status == ExperimentStatus.RUNNING
        assert exp.min_samples == 30

    def test_assign_returns_valid_skill(self, store: EvolutionStore) -> None:
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved)

        assigned = ex.assign(exp)
        assert assigned.id in (parent.id, evolved.id)

    def test_assign_raises_when_skill_deleted(self, store: EvolutionStore) -> None:
        """assign() raises KeyError if the skill was deleted after experiment creation."""
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved)

        # Delete both skills so assign can't find either
        with store._conn() as conn:
            conn.execute("DELETE FROM skill_records WHERE id IN (?, ?)", (parent.id, evolved.id))

        with pytest.raises(KeyError, match="Skill not found"):
            ex.assign(exp)

    def test_record_outcome_parent(self, store: EvolutionStore) -> None:
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved)

        ex.record_outcome(exp.experiment_id, parent.id, success=True)
        ex.record_outcome(exp.experiment_id, parent.id, success=False)

        updated = ex.get_experiment(exp.experiment_id)
        assert updated is not None
        assert updated.parent_total == 2
        assert updated.parent_successes == 1

    def test_record_outcome_evolved(self, store: EvolutionStore) -> None:
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved)

        ex.record_outcome(exp.experiment_id, evolved.id, success=True)

        updated = ex.get_experiment(exp.experiment_id)
        assert updated is not None
        assert updated.evolved_total == 1
        assert updated.evolved_successes == 1

    def test_record_outcome_invalid_skill_raises(self, store: EvolutionStore) -> None:
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved)

        with pytest.raises(ValueError, match="not part of experiment"):
            ex.record_outcome(exp.experiment_id, "unknown_skill", True)

    def test_evaluate_continue_when_insufficient_samples(self, store: EvolutionStore) -> None:
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved, min_samples=30)

        ex.record_outcome(exp.experiment_id, parent.id, True)
        ex.record_outcome(exp.experiment_id, evolved.id, True)

        result = ex.evaluate(exp.experiment_id)
        assert result.recommendation == "continue"
        assert result.samples_remaining > 0

    def test_evaluate_promote_when_better(self, store: EvolutionStore) -> None:
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved, min_samples=5)

        # Parent: 3/10 success
        for _ in range(3):
            ex.record_outcome(exp.experiment_id, parent.id, True)
        for _ in range(7):
            ex.record_outcome(exp.experiment_id, parent.id, False)

        # Evolved: 8/10 success
        for _ in range(8):
            ex.record_outcome(exp.experiment_id, evolved.id, True)
        for _ in range(2):
            ex.record_outcome(exp.experiment_id, evolved.id, False)

        result = ex.evaluate(exp.experiment_id)
        assert result.evolved_performance > result.parent_performance
        assert result.recommendation == "promote"

    def test_evaluate_revert_when_worse(self, store: EvolutionStore) -> None:
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved, min_samples=5)

        # Parent: 8/10 success
        for _ in range(8):
            ex.record_outcome(exp.experiment_id, parent.id, True)
        for _ in range(2):
            ex.record_outcome(exp.experiment_id, parent.id, False)

        # Evolved: 2/10 success
        for _ in range(2):
            ex.record_outcome(exp.experiment_id, evolved.id, True)
        for _ in range(8):
            ex.record_outcome(exp.experiment_id, evolved.id, False)

        result = ex.evaluate(exp.experiment_id)
        assert result.recommendation == "revert"

    def test_rollback(self, store: EvolutionStore) -> None:
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved)

        returned_parent = ex.rollback(exp.experiment_id)
        assert returned_parent.id == parent.id

        updated_exp = ex.get_experiment(exp.experiment_id)
        assert updated_exp is not None
        assert updated_exp.status == ExperimentStatus.REVERTED

    def test_rollback_raises_when_parent_deleted(self, store: EvolutionStore) -> None:
        """rollback() raises KeyError if parent skill was deleted from store."""
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved)

        # Delete parent from store entirely
        with store._conn() as conn:
            conn.execute("DELETE FROM skill_records WHERE id = ?", (parent.id,))

        with pytest.raises(KeyError, match="Parent skill not found"):
            ex.rollback(exp.experiment_id)

    def test_rollback_preserves_status_on_failure(self, store: EvolutionStore) -> None:
        """rollback() must NOT persist REVERTED status when parent is missing."""
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved)

        # Delete parent so rollback fails
        with store._conn() as conn:
            conn.execute("DELETE FROM skill_records WHERE id = ?", (parent.id,))

        with pytest.raises(KeyError):
            ex.rollback(exp.experiment_id)

        # Experiment must still be RUNNING, not REVERTED
        after = ex.get_experiment(exp.experiment_id)
        assert after is not None
        assert after.status == ExperimentStatus.RUNNING

    def test_list_active_experiments(self, store: EvolutionStore) -> None:
        parent = _register_skill(store, _make_skill("parent"))
        evolved = _register_skill(store, _make_skill("evolved", SkillOrigin.FIXED))

        ex = EvolutionExperimenter(store)
        exp1 = ex.create_experiment(parent, evolved)
        ex.create_experiment(parent, evolved)

        active = ex.list_active()
        assert len(active) == 2

        ex.rollback(exp1.experiment_id)
        active = ex.list_active()
        assert len(active) == 1

    def test_unknown_experiment_raises(self, store: EvolutionStore) -> None:
        ex = EvolutionExperimenter(store)
        with pytest.raises(KeyError, match="Experiment not found"):
            ex.evaluate("nonexistent")


# ---------------------------------------------------------------------------
# EvolutionDashboard
# ---------------------------------------------------------------------------


class TestDashboard:
    def test_summary_empty_store(self, store: EvolutionStore) -> None:
        dashboard = EvolutionDashboard(store)
        summary = dashboard.get_summary()
        assert summary.total_skills == 0
        assert summary.active_skills == 0

    def test_summary_with_skills(self, store: EvolutionStore) -> None:
        _register_skill(store, _make_skill("skill-a", selections=10, applied=5, completions=3))
        _register_skill(store, _make_skill("skill-b", selections=20, applied=15, completions=12))

        dashboard = EvolutionDashboard(store)
        summary = dashboard.get_summary()
        assert summary.active_skills == 2
        assert summary.avg_applied_rate > 0

    def test_health_report_healthy(self, store: EvolutionStore) -> None:
        # Skill with low fallback = healthy
        _register_skill(
            store,
            _make_skill("healthy-skill", selections=10, applied=8, completions=6, fallbacks=1),
        )

        dashboard = EvolutionDashboard(store)
        report = dashboard.get_health_report()
        assert report.total >= 1

    def test_health_report_unhealthy(self, store: EvolutionStore) -> None:
        # High fallback rate = unhealthy
        _register_skill(
            store,
            _make_skill("bad-skill", selections=10, applied=8, completions=3, fallbacks=5),
        )

        dashboard = EvolutionDashboard(store)
        report = dashboard.get_health_report()
        assert report.unhealthy >= 1

    def test_lineage_not_found(self, store: EvolutionStore) -> None:
        dashboard = EvolutionDashboard(store)
        result = dashboard.get_skill_lineage("nonexistent")
        assert result is None

    def test_lineage_found(self, store: EvolutionStore) -> None:
        skill = _register_skill(store, _make_skill("root-skill"))
        dashboard = EvolutionDashboard(store)
        lineage = dashboard.get_skill_lineage(skill.id)
        assert lineage is not None
        assert lineage.name == "root-skill"

    def test_lineage_with_children(self, store: EvolutionStore) -> None:
        """Verify children are found (regression: visited set pre-seeding bug)."""
        parent = _register_skill(store, _make_skill("parent"))
        _register_skill(
            store,
            _make_skill("child", parent_ids=[parent.id]),
        )

        dashboard = EvolutionDashboard(store)
        lineage = dashboard.get_skill_lineage(parent.id)

        assert lineage is not None
        assert len(lineage.children) == 1
        assert lineage.children[0].name == "child"
