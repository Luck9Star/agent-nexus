"""E2E: EvolutionEngine full lifecycle — analyze -> evolve -> validate -> promote.

Tests the COMPLETE engine-level lifecycle through the EvolutionEngine facade.
No sub-component mocking — real EvolutionStore (in-memory SQLite), real thresholds,
real lineage tracking.  The evolution module is fully deterministic (no LLM calls).

Covers:
  1. Full engine lifecycle (POST_ANALYSIS trigger with real EvolutionContext)
  2. Health-driven evolution cycle (high fallback -> FIX)
  3. Metric-driven evolution cycle (low completion -> evolution)
  4. Degradation -> recovery cycle (tool degradation + prune_recovered_tools)
  5. Promotion cycle (find candidates -> promote -> verify output files)
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from agent_nexus.models.evolution import (
    EvolutionContext,
    EvolutionType,
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)
from agent_nexus.platform.evolution.analyzer import AnalysisResult
from agent_nexus.platform.evolution.engine import EvolutionEngine
from agent_nexus.platform.evolution.evolver import EvolutionTrigger
from agent_nexus.platform.evolution.promotion import PromotionCandidate
from agent_nexus.platform.evolution.store import EvolutionStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store() -> Generator[EvolutionStore, None, None]:
    """In-memory EvolutionStore — no disk I/O."""
    s = EvolutionStore(Path(":memory:"))
    yield s
    s.close()


@pytest.fixture()
def engine(store: EvolutionStore) -> EvolutionEngine:
    """EvolutionEngine backed by an in-memory store."""
    return EvolutionEngine(store)


@pytest.fixture()
def engine_with_fs(store: EvolutionStore, tmp_path: Path) -> EvolutionEngine:
    """EvolutionEngine with a real filesystem agents_root for promotion tests."""
    return EvolutionEngine(store, agents_root=tmp_path / "agents" / "atomic")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    """Create a SkillRecord with sensible defaults."""
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


# ===================================================================
# 1. Full engine lifecycle — POST_ANALYSIS trigger
# ===================================================================


class TestEnginePostAnalysisLifecycle:
    """Exercise engine.evolve() with POST_ANALYSIS trigger end-to-end."""

    def test_post_analysis_with_healthy_skill(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """POST_ANALYSIS on healthy skill produces analysis with no suggestions."""
        store.save_skill_record(
            _make_skill("sk-healthy", selections=100, applied=90, completions=85, fallbacks=2)
        )
        ctx = EvolutionContext(
            agent_id="agent-1",
            task_id="task-001",
            task_description="Do something",
            task_completed=True,
            skill_ids_used=["sk-healthy"],
            skills_applied=["sk-healthy"],
            skills_fell_back=[],
        )

        result = engine.evolve(trigger=EvolutionTrigger.POST_ANALYSIS, ctx=ctx)
        assert isinstance(result, AnalysisResult)

        assert result.task_id == "task-001"
        assert result.agent_name == "agent-1"
        assert len(result.suggestions) == 0
        assert result.analysis_id != ""

    def test_post_analysis_with_unhealthy_skill_produces_fix(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """POST_ANALYSIS on high-fallback skill produces FIX suggestion + evolution."""
        # fallback_rate = 45/100 = 0.45 > 0.4
        # NOTE: record_analysis increments counters from judgments.
        # Judgment: selected=1, applied=1, completed=1, fell_back=1.
        # So after recording: sel=101, app=81, comp=36, fb=46.
        # Invariant: comp + fb = 82 <= app = 81? NO! 82 > 81.
        # Fix: start with comp + fb <= app accounting for +1/+1/+1/+1.
        # Start: sel=100, app=80, comp=35, fb=44 -> comp+fb=79 <= 80 OK.
        # After +1 each: sel=101, app=81, comp=36, fb=45 -> 36+45=81 <= 81 OK.
        # fallback_rate = 45/101 = 0.445 > 0.4 still triggers.
        store.save_skill_record(
            _make_skill(
                "sk-flaky",
                selections=100,
                applied=80,
                completions=35,
                fallbacks=44,
            )
        )
        ctx = EvolutionContext(
            agent_id="agent-1",
            task_id="task-002",
            task_completed=True,
            skill_ids_used=["sk-flaky"],
            skills_applied=["sk-flaky"],
            skills_fell_back=["sk-flaky"],
        )

        result = engine.evolve(trigger=EvolutionTrigger.POST_ANALYSIS, ctx=ctx)
        assert isinstance(result, AnalysisResult)

        assert result.task_id == "task-002"
        assert len(result.suggestions) >= 1
        fix_suggestions = [s for s in result.suggestions if s.evolution_type == EvolutionType.FIX]
        assert len(fix_suggestions) >= 1
        assert "sk-flaky" in fix_suggestions[0].target_skill_ids

        # Verify evolution happened: parent deactivated, new skill active
        parent = store.get_skill_record("sk-flaky")
        assert parent is not None
        assert parent.is_active is False

        active = store.get_active_skills()
        assert len(active) == 1
        assert active[0].lineage.origin == SkillOrigin.FIXED

    def test_post_analysis_captured_skill(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """POST_ANALYSIS on a successful task with NO skills used produces CAPTURED."""
        ctx = EvolutionContext(
            agent_id="agent-1",
            task_id="task-003",
            task_description="Novel task",
            task_completed=True,
            skill_ids_used=[],
            skills_applied=[],
            skills_fell_back=[],
        )

        result = engine.evolve(trigger=EvolutionTrigger.POST_ANALYSIS, ctx=ctx)
        assert isinstance(result, AnalysisResult)

        assert len(result.suggestions) == 1
        assert result.suggestions[0].evolution_type == EvolutionType.CAPTURED

        # Verify CAPTURED skill was created
        active = store.get_active_skills()
        assert len(active) == 1
        assert active[0].lineage.origin == SkillOrigin.CAPTURED

    def test_post_analysis_persists_analysis_record(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """POST_ANALYSIS stores the analysis record in the database."""
        store.save_skill_record(_make_skill("sk-1", selections=10, applied=9, completions=8))
        ctx = EvolutionContext(
            agent_id="agent-1",
            task_id="task-persist",
            task_completed=True,
            skill_ids_used=["sk-1"],
            skills_applied=["sk-1"],
        )

        result = engine.evolve(trigger=EvolutionTrigger.POST_ANALYSIS, ctx=ctx)
        assert isinstance(result, AnalysisResult)

        assert result.analysis_id != ""
        stored = store.get_analyses_for_task("task-persist")
        assert len(stored) == 1
        assert stored[0]["agent_name"] == "agent-1"

    def test_post_analysis_requires_ctx(self, engine: EvolutionEngine) -> None:
        """POST_ANALYSIS without ctx raises ValueError."""
        with pytest.raises(ValueError, match="ctx"):
            engine.evolve(trigger=EvolutionTrigger.POST_ANALYSIS)

    def test_check_health_after_post_analysis(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """After POST_ANALYSIS evolution, check_health on the new skill is clean."""
        # Unhealthy parent triggers FIX
        # Same counter care: comp + fb <= app before record_analysis adds +1/+1/+1/+1
        store.save_skill_record(
            _make_skill("sk-bad", selections=100, applied=80, completions=35, fallbacks=44)
        )
        ctx = EvolutionContext(
            agent_id="agent-1",
            task_id="task-004",
            task_completed=True,
            skill_ids_used=["sk-bad"],
            skills_applied=["sk-bad"],
            skills_fell_back=["sk-bad"],
        )
        engine.evolve(trigger=EvolutionTrigger.POST_ANALYSIS, ctx=ctx)

        # New skill starts with 0 selections -> no rates -> healthy
        active = store.get_active_skills()
        assert len(active) == 1
        suggestions = engine.check_health(active[0].id)
        assert suggestions == []

    def test_diagnose_all_after_post_analysis(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """diagnose_all after POST_ANALYSIS shows the new skill as healthy."""
        store.save_skill_record(
            _make_skill("sk-unhealthy", selections=100, applied=80, completions=35, fallbacks=44)
        )
        ctx = EvolutionContext(
            agent_id="agent-1",
            task_id="task-005",
            task_completed=True,
            skill_ids_used=["sk-unhealthy"],
            skills_applied=["sk-unhealthy"],
            skills_fell_back=["sk-unhealthy"],
        )
        engine.evolve(trigger=EvolutionTrigger.POST_ANALYSIS, ctx=ctx)

        reports = engine.diagnose_all()
        # Only the new FIXED skill is active (0 selections -> healthy)
        assert len(reports) == 1
        for report in reports.values():
            assert report.is_healthy


# ===================================================================
# 2. Health-driven evolution cycle
# ===================================================================


class TestHealthDrivenEvolutionCycle:
    """Seed high-fallback skill -> health check -> FIX evolution -> verify."""

    def test_high_fallback_triggers_fix_via_engine(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """High fallback rate -> check_health -> FIX suggestion -> evolve."""
        # fallback_rate = 45/100 = 0.45 > 0.4
        store.save_skill_record(
            _make_skill(
                "fall", name="fall-skill",
                selections=100, applied=80, completions=35, fallbacks=45,
            )
        )

        # Step 1: Health check
        suggestions = engine.check_health("fall")
        assert len(suggestions) >= 1
        fix = [s for s in suggestions if s.evolution_type == EvolutionType.FIX]
        assert len(fix) >= 1

        # Step 2: Evolve via metric check (which uses the same thresholds)
        evolve_result = engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK, min_selections=5)
        assert not isinstance(evolve_result, AnalysisResult)
        assert len(evolve_result) == 1
        assert evolve_result[0].success
        assert evolve_result[0].new_record is not None

        # Step 3: Parent deactivated
        parent = store.get_skill_record("fall")
        assert parent is not None
        assert parent.is_active is False

        # Step 4: Lineage tracked
        new_skill = evolve_result[0].new_record
        assert new_skill.lineage.origin == SkillOrigin.FIXED
        assert "fall" in new_skill.lineage.parent_skill_ids
        assert new_skill.lineage.generation == 1

    def test_low_completion_triggers_fix_via_engine(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """Low completion rate triggers FIX evolution through engine."""
        # applied_rate = 50/100 = 0.5 > 0.4
        # completion_rate = 10/50 = 0.2 < 0.35
        store.save_skill_record(
            _make_skill(
                "stall", name="stall-skill",
                selections=100, applied=50, completions=10, fallbacks=0,
            )
        )

        suggestions = engine.check_health("stall")
        fix = [s for s in suggestions if s.evolution_type == EvolutionType.FIX]
        assert len(fix) >= 1

        results = engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK)
        assert not isinstance(results, AnalysisResult)
        assert len(results) == 1
        assert results[0].success

    def test_moderate_effective_triggers_derived_via_engine(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """Moderate effective_rate triggers DERIVED evolution through engine."""
        # effective_rate = 30/100 = 0.3 < 0.55
        # applied_rate = 40/100 = 0.4 > 0.25
        # No FIX rules match (fallback=0, completion=30/40=0.75)
        store.save_skill_record(
            _make_skill(
                "moderate", name="mod-skill",
                selections=100, applied=40, completions=30, fallbacks=0,
            )
        )

        suggestions = engine.check_health("moderate")
        derived = [s for s in suggestions if s.evolution_type == EvolutionType.DERIVED]
        assert len(derived) >= 1

        results = engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK)
        assert not isinstance(results, AnalysisResult)
        assert len(results) == 1
        assert results[0].success
        assert results[0].new_record is not None
        # DERIVED does NOT deactivate parent
        parent = store.get_skill_record("moderate")
        assert parent is not None
        assert parent.is_active is True


# ===================================================================
# 3. Metric-driven evolution cycle
# ===================================================================


class TestMetricDrivenEvolutionCycle:
    """Seed low-completion skill -> metric check -> evolution -> counter tracking."""

    def test_metric_check_triggers_evolution(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """Metric check evolves unhealthy skills meeting min_selections."""
        # fallback_rate = 45/100 = 0.45 > 0.4
        store.save_skill_record(
            _make_skill("metric-sk", selections=100, applied=80, completions=35, fallbacks=45)
        )

        results = engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK, min_selections=10)
        assert not isinstance(results, AnalysisResult)
        assert len(results) == 1
        assert results[0].success

    def test_metric_check_skips_below_min_selections(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """Skills below min_selections are skipped even if unhealthy."""
        # Only 3 selections < min_selections=10
        store.save_skill_record(
            _make_skill("new-sk", selections=3, applied=2, completions=0, fallbacks=2)
        )

        results = engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK, min_selections=10)
        assert not isinstance(results, AnalysisResult)
        assert len(results) == 0

    def test_metric_check_healthy_skill_not_evolved(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """Healthy skill is not evolved by metric check."""
        store.save_skill_record(
            _make_skill("good-sk", selections=100, applied=90, completions=85, fallbacks=2)
        )

        results = engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK)
        assert not isinstance(results, AnalysisResult)
        assert len(results) == 0

    def test_counter_tracking_through_evolution(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """Newly evolved skill starts with zero counters, incremented counters tracked."""
        # Unhealthy skill
        store.save_skill_record(
            _make_skill("counter-sk", selections=100, applied=80, completions=35, fallbacks=45)
        )

        results = engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK)
        assert not isinstance(results, AnalysisResult)
        assert len(results) == 1
        new_skill = results[0].new_record
        assert new_skill is not None

        # New skill starts at zero
        assert new_skill.total_selections == 0
        assert new_skill.total_applied == 0
        assert new_skill.total_completions == 0
        assert new_skill.total_fallbacks == 0

        # Increment counters on the new skill
        store.increment_counters(new_skill.id, selected=True, applied=True, completed=True)
        store.increment_counters(new_skill.id, selected=True, applied=True, completed=True)
        store.increment_counters(new_skill.id, selected=True, applied=True, fell_back=True)

        reloaded = store.get_skill_record(new_skill.id)
        assert reloaded is not None
        assert reloaded.total_selections == 3
        assert reloaded.total_applied == 3
        assert reloaded.total_completions == 2
        assert reloaded.total_fallbacks == 1

        # Verify metrics aggregate correctly
        metrics = store.get_metrics()
        # Only the new skill is active (parent deactivated by FIX)
        assert metrics.total_selections == 3
        assert metrics.total_completions == 2


# ===================================================================
# 4. Degradation -> recovery cycle
# ===================================================================


class TestDegradationRecoveryCycle:
    """Tool degradation -> FIX evolution -> prune recovered -> re-evolve."""

    def test_tool_degradation_creates_fix(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """Reporting tool degradation creates FIX evolution for affected skills."""
        store.save_skill_record(
            _make_skill("deg-1", name="deg-skill", selections=10)
        )

        results = engine.evolve(
            trigger=EvolutionTrigger.TOOL_DEGRADATION,
            tool_key="failing-api",
            problem_description="API returns 500 errors",
            affected_skill_ids={"deg-1"},
        )
        assert not isinstance(results, AnalysisResult)

        assert len(results) == 1
        assert results[0].success
        assert results[0].new_record is not None
        assert results[0].new_record.lineage.origin == SkillOrigin.FIXED

        # Parent deactivated
        parent = store.get_skill_record("deg-1")
        assert parent is not None
        assert parent.is_active is False

    def test_tool_degradation_anti_loop(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """Same tool_key + skill_id is not evolved twice (anti-loop)."""
        store.save_skill_record(
            _make_skill("anti-1", name="anti-skill", selections=10)
        )

        # First degradation
        r1 = engine.evolve(
            trigger=EvolutionTrigger.TOOL_DEGRADATION,
            tool_key="broken-tool",
            problem_description="Tool is down",
            affected_skill_ids={"anti-1"},
        )
        assert not isinstance(r1, AnalysisResult)
        assert len(r1) == 1
        assert r1[0].success

        # The original skill is now deactivated and the new skill has a different ID
        # with 0 selections, so it won't be re-evolved by TOOL_DEGRADATION
        # (it's a different skill_id). But the _addressed set tracks anti-1.
        # A second call with the same affected_skill_ids should not re-evolve anti-1
        # (it's already inactive).
        r2 = engine.evolve(
            trigger=EvolutionTrigger.TOOL_DEGRADATION,
            tool_key="broken-tool",
            problem_description="Still down",
            affected_skill_ids={"anti-1"},
        )
        assert not isinstance(r2, AnalysisResult)
        # anti-1 is now inactive, so it's not in active_skills -> no evolution
        assert len(r2) == 0

    def test_degradation_requires_tool_key(self, engine: EvolutionEngine) -> None:
        """TOOL_DEGRADATION without tool_key raises ValueError."""
        with pytest.raises(ValueError, match="tool_key"):
            engine.evolve(trigger=EvolutionTrigger.TOOL_DEGRADATION)

    def test_prune_recovered_tools_allows_re_evolution(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """After pruning recovered tools, skills can be evolved again for that tool."""
        store.save_skill_record(
            _make_skill("recov-1", name="recov-skill", selections=10)
        )

        # Degrade tool-a and tool-b
        engine.evolve(
            trigger=EvolutionTrigger.TOOL_DEGRADATION,
            tool_key="tool-a",
            problem_description="A is broken",
            affected_skill_ids={"recov-1"},
        )
        engine.evolve(
            trigger=EvolutionTrigger.TOOL_DEGRADATION,
            tool_key="tool-b",
            problem_description="B is broken",
            affected_skill_ids={"recov-1"},
        )

        # Tool-b recovers, prune it
        engine.evolver.prune_recovered_tools({"tool-a"})

        # Verify tool-b is no longer in addressed set
        assert "tool-b" not in engine.evolver._addressed

        # Create a new skill for tool-b to evolve (the original is deactivated)
        store.save_skill_record(
            _make_skill("recov-2", name="recov-skill-2", selections=10)
        )
        r = engine.evolve(
            trigger=EvolutionTrigger.TOOL_DEGRADATION,
            tool_key="tool-b",
            problem_description="B relapsed",
            affected_skill_ids={"recov-2"},
        )
        assert not isinstance(r, AnalysisResult)
        assert len(r) == 1
        assert r[0].success

    def test_multiple_skills_degradation(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """Degradation affects multiple skills simultaneously."""
        store.save_skill_record(_make_skill("multi-1", name="multi-skill-1", selections=10))
        store.save_skill_record(_make_skill("multi-2", name="multi-skill-2", selections=10))
        store.save_skill_record(_make_skill("multi-3", name="multi-skill-3", selections=10))

        results = engine.evolve(
            trigger=EvolutionTrigger.TOOL_DEGRADATION,
            tool_key="shared-tool",
            problem_description="Shared tool is broken",
        )
        assert not isinstance(results, AnalysisResult)

        assert len(results) == 3
        assert all(r.success for r in results)

        # All parents deactivated
        for sid in ("multi-1", "multi-2", "multi-3"):
            record = store.get_skill_record(sid)
            assert record is not None
            assert record.is_active is False

        # 3 new active skills
        active = store.get_active_skills()
        assert len(active) == 3
        assert all(s.lineage.origin == SkillOrigin.FIXED for s in active)


# ===================================================================
# 5. Promotion cycle
# ===================================================================


class TestPromotionLifecycle:
    """Skill meeting promotion criteria -> find candidates -> promote -> verify files."""

    def test_find_no_candidates_empty_store(
        self, engine_with_fs: EvolutionEngine
    ) -> None:
        """No candidates in empty store."""
        candidates = engine_with_fs.promoter.find_candidates()
        assert candidates == []

    def test_find_candidates_qualified(
        self, engine_with_fs: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """Skill meeting all thresholds is found as candidate."""
        # effective_rate = 45/50 = 0.9 > 0.8, total_selections = 50 >= 50
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

        candidates = engine_with_fs.promoter.find_candidates()
        assert len(candidates) == 1
        assert candidates[0].skill_id == "star"
        assert candidates[0].effective_rate >= 0.8

    def test_promote_creates_agent_package(
        self, engine_with_fs: EvolutionEngine, store: EvolutionStore, tmp_path: Path
    ) -> None:
        """Full promote cycle creates all expected agent package files."""
        # Seed a promotable skill
        store.save_skill_record(
            _make_skill(
                "promote-me",
                name="promote-me",
                selections=60,
                applied=60,
                completions=55,
                fallbacks=0,
                directory="skills/promote-me",
            )
        )

        # Find candidates
        candidates = engine_with_fs.promoter.find_candidates()
        assert len(candidates) == 1

        # Promote
        result = engine_with_fs.promote_candidate(candidates[0])
        assert result.success
        assert result.agent_name == "promote-me"

        # Verify all files created
        agent_dir = Path(result.agent_directory)
        assert agent_dir.exists()
        assert (agent_dir / "agent-manifest.yaml").exists()
        assert (agent_dir / "pyproject.toml").exists()
        assert (agent_dir / "SKILL.md").exists()

        # Verify package directory (agent_name_to_package: "agent_" + name.replace("-", "_"))
        pkg_dir = agent_dir / "agent_promote_me"
        assert pkg_dir.exists()
        assert (pkg_dir / "__init__.py").exists()
        assert (pkg_dir / "agent.py").exists()
        assert (pkg_dir / "mcp_adapter.py").exists()

    def test_promote_via_engine_facade(
        self, engine_with_fs: EvolutionEngine, store: EvolutionStore, tmp_path: Path
    ) -> None:
        """Promotion through engine.promote_candidate() facade works."""
        candidate = PromotionCandidate(
            skill_id="facade-promo",
            skill_name="facade-promo",
            effective_rate=0.92,
            total_selections=100,
            directory="skills/facade",
            reason="Excellent metrics",
        )

        result = engine_with_fs.promote_candidate(candidate)
        assert result.success
        assert result.agent_name == "facade-promo"

        agent_dir = Path(result.agent_directory)
        assert (agent_dir / "agent-manifest.yaml").exists()
        assert (agent_dir / "pyproject.toml").exists()

    def test_skill_below_promotion_threshold_not_candidate(
        self, engine_with_fs: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """Skills not meeting thresholds are excluded from candidates."""
        # Below selections threshold
        store.save_skill_record(
            _make_skill("low-sel", selections=10, applied=9, completions=9, directory="skills/low")
        )
        # Below effective_rate threshold
        store.save_skill_record(
            _make_skill(
                "low-eff", selections=100,
                applied=80, completions=30, directory="skills/eff",
            )
        )

        candidates = engine_with_fs.promoter.find_candidates()
        assert len(candidates) == 0

    def test_skill_without_directory_not_candidate(
        self, engine_with_fs: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """Skill with empty directory is not a promotion candidate."""
        store.save_skill_record(
            _make_skill(
                "no-dir",
                selections=100,
                applied=100,
                completions=95,
                directory="",
            )
        )

        candidates = engine_with_fs.promoter.find_candidates()
        assert len(candidates) == 0


# ===================================================================
# Cross-lifecycle integration
# ===================================================================


class TestCrossLifecycleIntegration:
    """Full lifecycle spanning multiple engine operations."""

    def test_full_analyze_evolve_validate_promote_cycle(
        self, engine_with_fs: EvolutionEngine, store: EvolutionStore, tmp_path: Path
    ) -> None:
        """End-to-end: create -> analyze -> evolve -> increment -> promote.

        This test exercises the complete engine lifecycle:
          1. Seed a CAPTURED skill
          2. Run POST_ANALYSIS on a task using it
          3. Increment counters to build up metrics
          4. Run health check to verify healthy
          5. Increment counters to reach promotion threshold
          6. Find promotion candidates and promote
        """
        # Step 1: Seed a CAPTURED skill
        captured = _make_skill(
            "captured-1",
            name="auto-workflow",
            selections=0,
            origin=SkillOrigin.CAPTURED,
            directory="skills/auto-workflow",
        )
        store.save_skill_record(captured)

        # Step 2: POST_ANALYSIS — task uses the skill successfully
        ctx = EvolutionContext(
            agent_id="agent-1",
            task_id="task-integ",
            task_completed=True,
            skill_ids_used=["captured-1"],
            skills_applied=["captured-1"],
        )
        result = engine_with_fs.evolve(trigger=EvolutionTrigger.POST_ANALYSIS, ctx=ctx)
        assert isinstance(result, AnalysisResult)
        assert result.task_id == "task-integ"
        # Healthy skill -> no suggestions
        assert len(result.suggestions) == 0

        # Step 3: Simulate many successful uses to build metrics.
        # record_analysis already added +1 to selections/applied/completions
        # from the judgment, so we need 54 more to reach 55 total increments.
        for _ in range(54):
            store.increment_counters(
                "captured-1", selected=True, applied=True, completed=True
            )

        # Verify counters: 1 (from analysis) + 54 (manual) = 55
        skill = store.get_skill_record("captured-1")
        assert skill is not None
        assert skill.total_selections == 55
        assert skill.total_completions == 55

        # Step 4: Health check — should be healthy
        suggestions = engine_with_fs.check_health("captured-1")
        assert suggestions == []

        # Step 5: diagnose_all should show it as healthy
        reports = engine_with_fs.diagnose_all()
        assert "captured-1" in reports
        assert reports["captured-1"].is_healthy

        # Step 6: Find promotion candidates
        candidates = engine_with_fs.promoter.find_candidates()
        assert len(candidates) == 1
        assert candidates[0].skill_id == "captured-1"
        assert candidates[0].effective_rate == 1.0

        # Step 7: Promote
        promo_result = engine_with_fs.promote_candidate(candidates[0])
        assert promo_result.success
        assert promo_result.agent_name == "auto-workflow"

        agent_dir = Path(promo_result.agent_directory)
        assert (agent_dir / "agent-manifest.yaml").exists()
        assert (agent_dir / "pyproject.toml").exists()
        assert (agent_dir / "SKILL.md").exists()

    def test_degradation_then_metric_check_cycle(
        self, engine: EvolutionEngine, store: EvolutionStore
    ) -> None:
        """Tool degradation followed by metric check on the new skill."""
        # Seed skill
        store.save_skill_record(
            _make_skill(
                "cycle-sk", name="cycle-skill",
                selections=100, applied=80, completions=35, fallbacks=44,
            )
        )

        # Degrade
        r = engine.evolve(
            trigger=EvolutionTrigger.TOOL_DEGRADATION,
            tool_key="bad-api",
            problem_description="API broken",
            affected_skill_ids={"cycle-sk"},
        )
        assert not isinstance(r, AnalysisResult)
        assert len(r) == 1
        assert r[0].new_record is not None
        new_id = r[0].new_record.id

        # New skill has 0 selections -> metric check skips it
        r2 = engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK, min_selections=5)
        assert not isinstance(r2, AnalysisResult)
        assert len(r2) == 0

        # Build up selections (10 selections with 10 fallbacks -> fallback_rate=1.0)
        for _ in range(10):
            store.increment_counters(new_id, selected=True, applied=True, fell_back=True)

        # Now metric check should see it as unhealthy (fallback_rate = 1.0 > 0.4)
        r3 = engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK, min_selections=5)
        assert not isinstance(r3, AnalysisResult)
        assert len(r3) == 1
        assert r3[0].success

        # Grandchild created
        assert r3[0].new_record is not None
        assert r3[0].new_record.lineage.origin == SkillOrigin.FIXED
        # Lineage should trace back through the first evolution
        # ancestry is oldest-first: [cycle-sk (gen 0), new_id (gen 1)]
        ancestry = store.get_ancestry(r3[0].new_record.id)
        assert len(ancestry) == 2
        assert ancestry[0].id == "cycle-sk"  # Oldest ancestor
        assert ancestry[1].id == new_id      # Middle generation
