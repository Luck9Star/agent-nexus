"""E2E: Evolution lifecycle — HealthChecker + SkillEvolver + AgentPromoter.

Tests the complete evolution pipeline using real components:
  - Metrics aggregation across skill creation, counter increment, deactivation
  - Health analysis with threshold-based suggestions
  - Skill evolution (FIX / DERIVED) with lineage tracking
  - Batch operations and children tracking

All components use real SQLite databases. No internal modules are mocked.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from agent_nexus.models.evolution import (
    EvolutionType,
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)
from agent_nexus.platform.evolution.evolver import SkillEvolver
from agent_nexus.platform.evolution.health import HealthChecker
from agent_nexus.platform.evolution.promotion import AgentPromoter, PromotionCandidate
from agent_nexus.platform.evolution.store import EvolutionStore
from agent_nexus.platform.evolution.thresholds import EvolutionSuggestion

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path: Path) -> Generator[EvolutionStore, None, None]:
    db_path = tmp_path / "evo_lifecycle.db"
    s = EvolutionStore(db_path)
    yield s
    s.close()


@pytest.fixture()
def health_checker(store: EvolutionStore) -> HealthChecker:
    return HealthChecker(store)


@pytest.fixture()
def evolver(store: EvolutionStore) -> SkillEvolver:
    return SkillEvolver(store)


@pytest.fixture()
def promoter(store: EvolutionStore, tmp_path: Path) -> AgentPromoter:
    return AgentPromoter(store, agents_root=tmp_path / "agents" / "atomic")


def _make_skill(
    skill_id: str = "skill-1",
    name: str = "test-skill",
    selections: int = 0,
    applied: int = 0,
    completions: int = 0,
    fallbacks: int = 0,
    directory: str = "skills/test",
    origin: SkillOrigin = SkillOrigin.IMPORTED,
    generation: int = 0,
    parent_ids: list[str] | None = None,
) -> SkillRecord:
    return SkillRecord(
        id=skill_id,
        name=name,
        lineage=SkillLineage(
            origin=origin,
            generation=generation,
            parent_skill_ids=parent_ids or [],
        ),
        directory=directory,
        total_selections=selections,
        total_applied=applied,
        total_completions=completions,
        total_fallbacks=fallbacks,
    )


# ---------------------------------------------------------------------------
# TestMetricsAggregationLifecycle
# ---------------------------------------------------------------------------


class TestMetricsAggregationLifecycle:
    """Metrics aggregation through create, increment, deactivate."""

    def test_metrics_after_skill_creation(self, store: EvolutionStore) -> None:
        """Create skills, verify metrics aggregate correctly."""
        s1 = _make_skill("s1", name="skill-a", selections=10, applied=8, completions=6, fallbacks=1)
        s2 = _make_skill(
            "s2", name="skill-b", selections=20, applied=15, completions=12, fallbacks=2
        )
        store.save_skill_record(s1)
        store.save_skill_record(s2)

        metrics = store.get_metrics()
        assert metrics.total_selections == 30
        assert metrics.total_applied == 23
        assert metrics.total_completions == 18
        assert metrics.total_fallbacks == 3

    def test_metrics_after_counter_increment(self, store: EvolutionStore) -> None:
        """Increment counters and verify metrics update."""
        skill = _make_skill("s1", selections=5, applied=4, completions=3, fallbacks=0)
        store.save_skill_record(skill)

        # Verify initial metrics
        metrics = store.get_metrics()
        assert metrics.total_selections == 5

        # Increment counters
        store.increment_counters("s1", selected=True, applied=True, completed=True)
        store.increment_counters("s1", selected=True, applied=True, fell_back=True)

        metrics = store.get_metrics()
        assert metrics.total_selections == 7
        assert metrics.total_applied == 6
        assert metrics.total_completions == 4
        assert metrics.total_fallbacks == 1

    def test_metrics_after_deactivation(self, store: EvolutionStore) -> None:
        """Deactivate a skill and verify it's excluded from metrics."""
        s1 = _make_skill("s1", name="skill-a", selections=10, applied=8, completions=6, fallbacks=1)
        s2 = _make_skill(
            "s2", name="skill-b", selections=20, applied=15, completions=12, fallbacks=2
        )
        store.save_skill_record(s1)
        store.save_skill_record(s2)

        # Deactivate s1
        store.deactivate_skill("s1")

        metrics = store.get_metrics()
        assert metrics.total_selections == 20  # Only s2 counts
        assert metrics.total_applied == 15
        assert metrics.total_completions == 12

    def test_metrics_empty_store(self, store: EvolutionStore) -> None:
        """Metrics on empty store returns all zeros."""
        metrics = store.get_metrics()
        assert metrics.total_selections == 0
        assert metrics.total_applied == 0
        assert metrics.total_completions == 0
        assert metrics.total_fallbacks == 0


# ---------------------------------------------------------------------------
# TestHealthAnalysisLifecycle
# ---------------------------------------------------------------------------


class TestHealthAnalysisLifecycle:
    """HealthChecker evaluates skill health using real thresholds."""

    def test_healthy_skill_no_suggestions(self, health_checker: HealthChecker) -> None:
        """Skill with good metrics produces no suggestions."""
        # Good metrics: low fallback, high completion, high effective
        skill = _make_skill(
            "healthy",
            selections=100,
            applied=80,
            completions=70,
            fallbacks=5,
        )
        suggestions = health_checker.check_health(skill)
        assert suggestions == []

    def test_high_fallback_triggers_fix(self, health_checker: HealthChecker) -> None:
        """Skill with fallback_rate > 0.4 triggers FIX suggestion."""
        # fallback_rate = 45/100 = 0.45 > 0.4
        # applied=80, completions=35, fallbacks=45: 35+45=80 OK
        skill = _make_skill(
            "flaky",
            selections=100,
            applied=80,
            completions=35,
            fallbacks=45,
        )
        suggestions = health_checker.check_health(skill)

        assert len(suggestions) >= 1
        fix_suggestions = [s for s in suggestions if s.evolution_type == EvolutionType.FIX]
        assert len(fix_suggestions) >= 1
        assert "flaky" in fix_suggestions[0].target_skill_ids

    def test_low_completion_triggers_fix(self, health_checker: HealthChecker) -> None:
        """Skill with applied_rate > 0.4 and completion_rate < 0.35 triggers FIX."""
        # applied_rate = 50/100 = 0.5 > 0.4
        # completion_rate = 10/50 = 0.2 < 0.35
        skill = _make_skill(
            "stalled",
            selections=100,
            applied=50,
            completions=10,
            fallbacks=0,
        )
        suggestions = health_checker.check_health(skill)

        fix_suggestions = [s for s in suggestions if s.evolution_type == EvolutionType.FIX]
        assert len(fix_suggestions) >= 1

    def test_moderate_effective_triggers_derived(self, health_checker: HealthChecker) -> None:
        """Skill with effective_rate < 0.55 and applied_rate > 0.25 triggers DERIVED."""
        # applied_rate = 40/100 = 0.4 > 0.25
        # completion_rate = 30/40 = 0.75 (not < 0.35, so no FIX rule 2)
        # effective_rate = 30/100 = 0.3 < 0.55
        # fallback_rate = 0 (not > 0.4, so no FIX rule 1)
        # DERIVED rule: effective < 0.55 AND applied > 0.25 -> triggers
        skill = _make_skill(
            "mediocre",
            selections=100,
            applied=40,
            completions=30,
            fallbacks=0,
        )
        suggestions = health_checker.check_health(skill)

        derived = [s for s in suggestions if s.evolution_type == EvolutionType.DERIVED]
        assert len(derived) >= 1

    def test_moderate_effective_triggers_derived_valid(self, health_checker: HealthChecker) -> None:
        """Skill with effective_rate < 0.55 and applied_rate > 0.25 triggers DERIVED."""
        # applied_rate = 40/100 = 0.4
        # completion_rate = 30/40 = 0.75 (not < 0.35, so no FIX rule 2)
        # effective_rate = 30/100 = 0.3 < 0.55
        # fallback_rate = 0 (not > 0.4, so no FIX rule 1)
        # DERIVED rule: effective < 0.55 AND applied > 0.25 -> triggers
        skill = _make_skill(
            "mediocre",
            selections=100,
            applied=40,
            completions=30,
            fallbacks=0,
        )
        suggestions = health_checker.check_health(skill)

        derived = [s for s in suggestions if s.evolution_type == EvolutionType.DERIVED]
        assert len(derived) >= 1

    def test_zero_selections_no_suggestions(self, health_checker: HealthChecker) -> None:
        """Skill with zero selections has no rates to evaluate."""
        skill = _make_skill("new-skill", selections=0)
        suggestions = health_checker.check_health(skill)
        assert suggestions == []

    def test_get_health_summary_with_unhealthy(
        self, store: EvolutionStore, health_checker: HealthChecker
    ) -> None:
        """get_health_summary returns correct structure with unhealthy skills."""
        # Healthy skill
        store.save_skill_record(
            _make_skill(
                "good", name="good-skill", selections=100, applied=80, completions=70, fallbacks=5
            )
        )
        # Unhealthy skill (high fallback)
        # fallback_rate = 45/100 = 0.45 > 0.4
        # applied=80, completions=35, fallbacks=45: 35+45=80 OK
        store.save_skill_record(
            _make_skill(
                "bad", name="bad-skill", selections=100, applied=80, completions=35, fallbacks=45
            )
        )

        summary = health_checker.get_health_summary()
        assert "total_skills" in summary
        assert "healthy" in summary
        assert "unhealthy" in summary
        assert "fix_suggestions" in summary
        assert "derived_suggestions" in summary
        assert "unhealthy_skills" in summary

        assert summary["total_skills"] == 2
        assert summary["healthy"] >= 0
        assert summary["unhealthy"] >= 1
        assert "bad-skill" in summary["unhealthy_skills"]

    def test_diagnose_all_empty_store(self, health_checker: HealthChecker) -> None:
        """diagnose_all on empty store returns empty dict."""
        reports = health_checker.diagnose_all()
        assert reports == {}

    def test_get_unhealthy_filters_healthy(
        self, store: EvolutionStore, health_checker: HealthChecker
    ) -> None:
        """get_unhealthy only returns skills with suggestions."""
        # Healthy
        store.save_skill_record(
            _make_skill(
                "good", name="good-skill", selections=100, applied=80, completions=70, fallbacks=5
            )
        )
        # Unhealthy (high fallback)
        # fallback_rate = 45/100 = 0.45 > 0.4
        # applied=80, completions=10, fallbacks=45: 10+45=55 <= 80 OK
        store.save_skill_record(
            _make_skill(
                "sick", name="sick-skill", selections=100, applied=80, completions=10, fallbacks=45
            )
        )
        # Zero selections -> filtered out by get_unhealthy
        store.save_skill_record(_make_skill("unused", name="unused-skill", selections=0))

        unhealthy = health_checker.get_unhealthy()
        assert "sick" in unhealthy
        assert "good" not in unhealthy
        assert "unused" not in unhealthy


# ---------------------------------------------------------------------------
# TestEvolutionLifecycle
# ---------------------------------------------------------------------------


class TestEvolutionLifecycle:
    """Skill evolution (FIX / DERIVED) with lineage tracking."""

    def test_fix_evolution_deactivates_parent(
        self, store: EvolutionStore, evolver: SkillEvolver
    ) -> None:
        """FIX evolution deactivates parent skill."""
        parent = _make_skill("parent", name="review", selections=10)
        store.save_skill_record(parent)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.FIX,
            target_skill_ids=["parent"],
            direction="Fix broken skill",
        )
        result = evolver.evolve(suggestion)

        assert result.success
        assert result.new_record is not None

        # Parent should be deactivated
        parent_record = store.get_skill_record("parent")
        assert parent_record is not None
        assert parent_record.is_active is False

    def test_derived_evolution_preserves_parent(
        self, store: EvolutionStore, evolver: SkillEvolver
    ) -> None:
        """DERIVED evolution keeps parent active."""
        parent = _make_skill("parent", name="scan", selections=10)
        store.save_skill_record(parent)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.DERIVED,
            target_skill_ids=["parent"],
            direction="Enhance with better error handling",
        )
        result = evolver.evolve(suggestion)

        assert result.success
        assert result.new_record is not None

        # Parent should still be active
        parent_record = store.get_skill_record("parent")
        assert parent_record is not None
        assert parent_record.is_active is True

    def test_lineage_tracking(self, store: EvolutionStore, evolver: SkillEvolver) -> None:
        """Evolved skill has correct ancestry chain."""
        g0 = _make_skill("gen-0", name="analyze", generation=0)
        store.save_skill_record(g0)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.DERIVED,
            target_skill_ids=["gen-0"],
            direction="Improve analysis",
        )
        result = evolver.evolve(suggestion)
        assert result.success
        assert result.new_record is not None
        child_id = result.new_record.id

        # Verify ancestry
        ancestry = store.get_ancestry(child_id)
        assert len(ancestry) == 1
        assert ancestry[0].id == "gen-0"

    def test_evolved_skill_appears_in_active(
        self, store: EvolutionStore, evolver: SkillEvolver
    ) -> None:
        """Newly evolved skill is in active skills list."""
        parent = _make_skill("p1", name="tool-a", selections=5)
        store.save_skill_record(parent)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.DERIVED,
            target_skill_ids=["p1"],
            direction="Enhance tool-a",
        )
        result = evolver.evolve(suggestion)
        assert result.success

        assert result.new_record is not None
        active = store.get_active_skills()
        active_ids = {s.id for s in active}
        assert result.new_record.id in active_ids

    def test_fix_requires_single_parent(self, evolver: SkillEvolver) -> None:
        """FIX evolution requires exactly 1 parent skill."""
        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.FIX,
            target_skill_ids=["a", "b"],
            direction="Fix both",
        )
        result = evolver.evolve(suggestion)
        assert result.success is False
        assert "exactly 1 parent" in result.error

    def test_fix_nonexistent_parent_fails(self, evolver: SkillEvolver) -> None:
        """FIX with nonexistent parent returns error."""
        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.FIX,
            target_skill_ids=["ghost"],
            direction="Fix ghost",
        )
        result = evolver.evolve(suggestion)
        assert result.success is False
        assert "not found" in result.error

    def test_derived_requires_at_least_one_parent(self, evolver: SkillEvolver) -> None:
        """DERIVED evolution requires at least 1 parent."""
        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.DERIVED,
            target_skill_ids=[],
            direction="Derive from nothing",
        )
        result = evolver.evolve(suggestion)
        assert result.success is False
        assert "at least 1 parent" in result.error

    def test_multi_parent_derived_evolution(
        self, store: EvolutionStore, evolver: SkillEvolver
    ) -> None:
        """DERIVED with multiple parents creates merged skill."""
        p1 = _make_skill("p1", name="scan", selections=5, directory="skills/scan")
        p2 = _make_skill("p2", name="analyze", selections=5, directory="skills/analyze")
        store.save_skill_record(p1)
        store.save_skill_record(p2)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.DERIVED,
            target_skill_ids=["p1", "p2"],
            direction="Merge scan and analyze",
        )
        result = evolver.evolve(suggestion)
        assert result.success
        assert result.new_record is not None
        assert "merged" in result.new_record.name

        # Lineage should have both parents
        new_skill = result.new_record
        assert len(new_skill.lineage.parent_skill_ids) == 2


# ---------------------------------------------------------------------------
# TestSkillRecordBatchLifecycle
# ---------------------------------------------------------------------------


class TestSkillRecordBatchLifecycle:
    """Batch operations across skill records."""

    def test_batch_load_after_multiple_saves(self, store: EvolutionStore) -> None:
        """Save 10 skills, batch load all."""
        ids = []
        for i in range(10):
            sid = f"batch-{i}"
            store.save_skill_record(_make_skill(sid, name=f"skill-{i}"))
            ids.append(sid)

        batch = store.get_skill_records_batch(ids)
        assert len(batch) == 10
        for sid in ids:
            assert sid in batch
            assert batch[sid].name.startswith("skill-")

    def test_batch_load_with_missing_ids(self, store: EvolutionStore) -> None:
        """Batch load gracefully handles missing IDs."""
        store.save_skill_record(_make_skill("exists-1", name="skill-e1"))
        store.save_skill_record(_make_skill("exists-2", name="skill-e2"))

        batch = store.get_skill_records_batch(["exists-1", "missing-1", "exists-2"])
        assert len(batch) == 2
        assert "exists-1" in batch
        assert "exists-2" in batch
        assert "missing-1" not in batch

    def test_children_tracking(self, store: EvolutionStore, evolver: SkillEvolver) -> None:
        """Evolve skill, verify get_children returns correct IDs."""
        parent = _make_skill("root", name="root-skill", selections=5)
        store.save_skill_record(parent)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.DERIVED,
            target_skill_ids=["root"],
            direction="Enhance root",
        )
        result = evolver.evolve(suggestion)
        assert result.success

        children = store.get_children("root")
        assert len(children) == 1
        assert result.new_record is not None
        assert result.new_record.id in children

    def test_multiple_children_tracking(self, store: EvolutionStore, evolver: SkillEvolver) -> None:
        """Evolve same parent twice, get_children returns both."""
        parent = _make_skill("multi-parent", name="base", selections=5)
        store.save_skill_record(parent)

        # First evolution
        r1 = evolver.evolve(
            EvolutionSuggestion(
                evolution_type=EvolutionType.DERIVED,
                target_skill_ids=["multi-parent"],
                direction="Enhancement 1",
            )
        )
        assert r1.success
        assert r1.new_record is not None

        # Deactivate first child so the second child can use the same name
        store.deactivate_skill(r1.new_record.id)

        # Second evolution (parent still active for DERIVED)
        r2 = evolver.evolve(
            EvolutionSuggestion(
                evolution_type=EvolutionType.DERIVED,
                target_skill_ids=["multi-parent"],
                direction="Enhancement 2",
            )
        )
        assert r2.success

        children = store.get_children("multi-parent")
        assert len(children) == 2
        assert r2.new_record is not None
        assert r1.new_record.id in children
        assert r2.new_record.id in children


# ---------------------------------------------------------------------------
# TestEvolverToolDegradation
# ---------------------------------------------------------------------------


class TestEvolverToolDegradation:
    """Tool degradation anti-loop and evolution pipeline."""

    def test_tool_degradation_creates_fix(
        self, store: EvolutionStore, evolver: SkillEvolver
    ) -> None:
        """process_tool_degradation creates FIX evolution for affected skill."""
        store.save_skill_record(_make_skill("td-1", name="tool-user", selections=5))

        results = evolver.process_tool_degradation(
            tool_key="broken-api",
            problem_description="API returns 500",
        )
        assert len(results) == 1
        assert results[0].success
        assert results[0].new_record is not None

    def test_tool_degradation_anti_loop(self, store: EvolutionStore, evolver: SkillEvolver) -> None:
        """Same tool_key + skill_id combo is not evolved twice.

        The _addressed set tracks tool_key -> {skill_id}. After the first
        FIX, the original skill ID is marked as addressed. The child gets
        a new generated ID and IS evolved on the next call. This verifies
        the _addressed mechanism: the original skill_id is never re-evolved
        for the same tool_key.
        """
        store.save_skill_record(_make_skill("td-2", name="anti-loop-skill", selections=5))

        r1 = evolver.process_tool_degradation("bad-tool", "Broken")
        assert len(r1) == 1
        assert r1[0].new_record is not None
        original_id = r1[0].new_record.lineage.parent_skill_ids[0]
        assert original_id == "td-2"

        # The child (new ID) IS evolved on the next call, but the
        # _addressed set prevents the original "td-2" from being processed
        # again. Verify by checking that td-2 is in the addressed set.
        assert "td-2" in evolver._addressed.get("bad-tool", set())

    def test_prune_recovered_tools(self, store: EvolutionStore, evolver: SkillEvolver) -> None:
        """Pruning recovered tools allows re-evolution."""
        store.save_skill_record(_make_skill("td-3", name="recovery", selections=5))

        evolver.process_tool_degradation("tool-a", "Broken A")
        evolver.process_tool_degradation("tool-b", "Broken B")

        # Tool B recovers, tool A still degraded
        evolver.prune_recovered_tools({"tool-a"})

        # Tool B should now be eligible again
        results = evolver.process_tool_degradation("tool-b", "Relapsed")
        assert len(results) == 1


# ---------------------------------------------------------------------------
# TestEvolverMetricCheck
# ---------------------------------------------------------------------------


class TestEvolverMetricCheck:
    """Metric-based evolution trigger pipeline."""

    def test_metric_check_healthy_skill_no_evolution(
        self, store: EvolutionStore, evolver: SkillEvolver
    ) -> None:
        """Healthy skill with good metrics is not evolved by metric check."""
        store.save_skill_record(
            _make_skill("healthy", selections=10, applied=9, completions=8, fallbacks=0)
        )
        results = evolver.process_metric_check(min_selections=5)
        assert len(results) == 0

    def test_metric_check_unhealthy_skill_triggers_evolution(
        self, store: EvolutionStore, evolver: SkillEvolver
    ) -> None:
        """Unhealthy skill triggers evolution via metric check."""
        # High fallback: 45/100 = 0.45 > 0.4
        # applied=80, completions=35, fallbacks=45: 35+45=80 OK
        store.save_skill_record(
            _make_skill("unhealthy", selections=100, applied=80, completions=35, fallbacks=45)
        )
        results = evolver.process_metric_check(min_selections=5)
        assert len(results) == 1
        assert results[0].success

    def test_metric_check_below_min_selections(
        self, store: EvolutionStore, evolver: SkillEvolver
    ) -> None:
        """Skill below min_selections threshold is skipped."""
        # selections=3 < min_selections=5, so skipped regardless of rates
        store.save_skill_record(
            _make_skill("new", selections=3, applied=2, completions=1, fallbacks=1)
        )
        results = evolver.process_metric_check(min_selections=5)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# TestAgentPromoterLifecycle
# ---------------------------------------------------------------------------


class TestAgentPromoterLifecycle:
    """AgentPromoter candidate finding and promotion pipeline."""

    def test_find_candidates_empty_store(self, promoter: AgentPromoter) -> None:
        """find_candidates on empty store returns empty list."""
        assert promoter.find_candidates() == []

    def test_find_candidates_below_threshold(
        self, store: EvolutionStore, promoter: AgentPromoter
    ) -> None:
        """Skills below thresholds are not candidates."""
        # Below total_selections threshold (50)
        store.save_skill_record(
            _make_skill(
                "low-sel",
                name="low-sel-skill",
                selections=10,
                applied=9,
                completions=9,
                fallbacks=0,
            )
        )
        # High selections but low effective_rate
        store.save_skill_record(
            _make_skill(
                "low-eff",
                name="low-eff-skill",
                selections=100,
                applied=80,
                completions=30,
                fallbacks=10,
            )
        )
        candidates = promoter.find_candidates()
        assert len(candidates) == 0

    def test_find_candidates_qualified(
        self, store: EvolutionStore, promoter: AgentPromoter
    ) -> None:
        """Skill meeting all thresholds is a candidate."""
        # effective_rate = 45/50 = 0.9 > 0.8
        # total_selections = 50 >= 50
        store.save_skill_record(
            _make_skill(
                "star",
                name="star-skill",
                selections=50,
                applied=50,
                completions=45,
                fallbacks=0,
                directory="skills/star",
            )
        )
        candidates = promoter.find_candidates()
        assert len(candidates) == 1
        assert candidates[0].skill_id == "star"
        assert candidates[0].effective_rate >= 0.8

    def test_promote_creates_files(self, store: EvolutionStore, promoter: AgentPromoter) -> None:
        """Promotion generates agent package files."""
        candidate = PromotionCandidate(
            skill_id="promotable",
            skill_name="promotable-skill",
            effective_rate=0.9,
            total_selections=100,
            directory="skills/promotable",
            reason="High performance",
        )
        result = promoter.promote(candidate)
        assert result.success
        assert result.agent_name == "promotable-skill"

        # Verify files were created
        from pathlib import Path

        agent_dir = Path(result.agent_directory)
        assert agent_dir.exists()
        assert (agent_dir / "agent-manifest.yaml").exists()
        assert (agent_dir / "pyproject.toml").exists()
        assert (agent_dir / "SKILL.md").exists()
