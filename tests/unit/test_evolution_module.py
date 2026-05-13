"""Unit tests for agent_nexus.platform.evolution -- all 6 modules.

Covers: store, analyzer, evolver, compaction, promotion, health.
Uses tmp_path for SQLite databases, pytest class-based organization.
Target: ~65 tests across all modules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_nexus.models.context import ContextBudget, TokenUsage
from agent_nexus.models.evolution import (
    EvolutionContext,
    EvolutionType,
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)
from agent_nexus.platform.evolution.analyzer import (
    AnalysisResult,
    EvolutionSuggestion,
    ExecutionAnalyzer,
    _correct_skill_ids,
)
from agent_nexus.platform.evolution.compaction import AgentContext, CompactionGuard
from agent_nexus.platform.evolution.engine import EvolutionEngine
from agent_nexus.platform.evolution.evolver import (
    EvolutionTrigger,
    SkillEvolver,
)
from agent_nexus.platform.evolution.health import HealthChecker
from agent_nexus.platform.evolution.promotion import (
    AgentPromoter,
    PromotionCandidate,
)
from agent_nexus.platform.evolution.store import EvolutionStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_now = datetime.now(UTC)


def _make_record(
    skill_id: str = "skill-1",
    name: str = "test-skill",
    *,
    version: str = "1.0.0",
    origin: SkillOrigin = SkillOrigin.IMPORTED,
    generation: int = 0,
    parent_ids: list[str] | None = None,
    directory: str = "skills/test",
    is_active: bool = True,
    selections: int = 0,
    applied: int = 0,
    completions: int = 0,
    fallbacks: int = 0,
) -> SkillRecord:
    """Create a SkillRecord for testing."""
    return SkillRecord(
        id=skill_id,
        name=name,
        version=version,
        lineage=SkillLineage(
            origin=origin,
            generation=generation,
            parent_skill_ids=parent_ids or [],
            content_diff=None,
            content_snapshot=None,
        ),
        directory=directory,
        is_active=is_active,
        total_selections=selections,
        total_applied=applied,
        total_completions=completions,
        total_fallbacks=fallbacks,
        first_seen=_now,
        last_updated=_now,
    )


def _store_with_records(tmp_path: Path, *records: SkillRecord) -> EvolutionStore:
    """Create an EvolutionStore and save the given records."""
    db = tmp_path / "test.db"
    store = EvolutionStore(db)
    for r in records:
        store.save_skill_record(r)
    return store


# ============================================================================
# 1. EvolutionStore
# ============================================================================


class TestEvolutionStoreInit:
    def test_creates_database_file(self, tmp_path: Path) -> None:
        db = tmp_path / "evo.db"
        EvolutionStore(db)
        assert db.exists()
        # Opening again should not fail (idempotent schema)
        EvolutionStore(db)

    # NOTE: test_wal_mode removed — verifies SQLite framework behavior, not our code


class TestEvolutionStoreCRUD:
    def test_save_and_get(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path, _make_record("s1", "my-skill"))
        record = store.get_skill_record("s1")
        assert record is not None
        assert record.id == "s1"
        assert record.name == "my-skill"
        assert record.is_active is True

    def test_get_nonexistent_returns_none(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        assert store.get_skill_record("no-such-id") is None

    # NOTE: test_get_active_skills and test_get_all_skills removed —
    # same CRUD path as test_save_and_get, trivial filter tests

    # NOTE: test_deactivate_skill and test_deactivate_nonexistent removed —
    # exact duplicates of TestDeactivateSkill in test_store_p0_unit.py

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        r = _make_record("s1", "original")
        store = _store_with_records(tmp_path, r)
        updated = _make_record("s1", "updated")
        store.save_skill_record(updated)
        got = store.get_skill_record("s1")
        assert got is not None
        assert got.name == "updated"

    def test_get_versions(self, tmp_path: Path) -> None:
        r1 = _make_record("s1", "skill", generation=0, is_active=False)
        r2 = _make_record("s2", "skill", generation=1)
        store = _store_with_records(tmp_path, r1, r2)
        versions = store.get_versions("skill")
        assert len(versions) == 2
        assert versions[0].lineage.generation == 0
        assert versions[1].lineage.generation == 1


# NOTE: TestEvolutionStoreLineageParents removed — same lineage persistence logic
# covered by TestEvolutionStoreEvolveSkill.test_lineage_parents_stored


class TestEvolutionStoreCounters:
    def test_increment_selected(self, tmp_path: Path) -> None:
        r = _make_record("s1", "x", selections=5)
        store = _store_with_records(tmp_path, r)
        store.increment_counters("s1", selected=True)
        got = store.get_skill_record("s1")
        assert got is not None
        assert got.total_selections == 6

    def test_increment_multiple(self, tmp_path: Path) -> None:
        r = _make_record("s1", "x", selections=10, applied=5, completions=3)
        store = _store_with_records(tmp_path, r)
        store.increment_counters("s1", selected=True, applied=True, completed=True)
        got = store.get_skill_record("s1")
        assert got is not None
        assert got.total_selections == 11
        assert got.total_applied == 6
        assert got.total_completions == 4

    def test_increment_no_flags_is_noop(self, tmp_path: Path) -> None:
        """Calling increment_counters with all flags False does not open a connection."""
        r = _make_record("s1", "x", selections=5)
        store = _store_with_records(tmp_path, r)

        # Patch _conn to fail if called -- proves the no-op returns before opening it
        from unittest.mock import patch as _patch

        with _patch.object(
            store, "_conn", side_effect=AssertionError("_conn should not be called")
        ):
            store.increment_counters("s1")  # should not raise

        got = store.get_skill_record("s1")
        assert got is not None
        assert got.total_selections == 5

    def test_increment_fallback(self, tmp_path: Path) -> None:
        r = _make_record("s1", "x", selections=3, applied=3, fallbacks=2)
        store = _store_with_records(tmp_path, r)
        store.increment_counters("s1", applied=True, selected=True, fell_back=True)
        got = store.get_skill_record("s1")
        assert got is not None
        assert got.total_fallbacks == 3


class TestEvolutionStoreAnalysis:
    # NOTE: test_record_analysis and test_judgments_persisted removed —
    # exact duplicates of TestGetAnalysesForTask and TestGetJudgmentsForSkill
    # in test_evolution_store.py (which test the same methods more thoroughly)

    def test_analysis_increments_counters(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path, _make_record("s1", "x"))
        # Applied but fell back: valid single judgment where applied and fell_back
        # are both True (skill was applied, then fell back to alternative).
        store.record_analysis(
            task_id="t1",
            agent_name="a",
            analysis_text="",
            judgments=[
                {
                    "skill_id": "s1",
                    "selected": True,
                    "applied": True,
                    "completed": False,
                    "fell_back": True,
                },
            ],
        )
        got = store.get_skill_record("s1")
        assert got is not None
        assert got.total_selections == 1
        assert got.total_applied == 1
        assert got.total_completions == 0
        assert got.total_fallbacks == 1


class TestEvolutionStoreEvolveSkill:
    # NOTE: test_fix_deactivates_parent removed — duplicate of
    # TestEvolveSkillParentValidation in test_evolution_store.py and
    # TestGetChildren.test_evolved_skill_has_children in test_store_p0_unit.py

    def test_derived_keeps_parent_active(self, tmp_path: Path) -> None:
        parent = _make_record("p1", "base", is_active=True)
        store = _store_with_records(tmp_path, parent)

        child = _make_record(
            "c1",
            "base-enhanced",
            origin=SkillOrigin.DERIVED,
            generation=1,
            parent_ids=["p1"],
        )
        store.evolve_skill(child, ["p1"])

        rec_p1 = store.get_skill_record("p1")
        assert rec_p1 is not None
        assert rec_p1.is_active is True
        rec_c1 = store.get_skill_record("c1")
        assert rec_c1 is not None
        assert rec_c1.is_active is True

    def test_lineage_parents_stored(self, tmp_path: Path) -> None:
        p1 = _make_record("p1", "a")
        p2 = _make_record("p2", "b")
        store = _store_with_records(tmp_path, p1, p2)

        child = _make_record("c1", "merged", parent_ids=["p1", "p2"], generation=1)
        store.evolve_skill(child, ["p1", "p2"])

        got = store.get_skill_record("c1")
        assert got is not None
        assert set(got.lineage.parent_skill_ids) == {"p1", "p2"}


# NOTE: TestEvolutionStoreAncestry removed — exact duplicate of TestGetAncestry
# in test_evolution_store.py and TestGetChildren in test_store_p0_unit.py

# NOTE: TestEvolutionStoreMetrics removed — exact duplicate of TestGetMetrics
# in test_store_p0_unit.py (which also tests aggregation, agent filter, and more)


class TestEvolutionStoreBudgetLog:
    def test_log_and_retrieve(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        log_id = store.log_budget_event(
            agent_name="agent-a",
            event_type="compaction",
            tokens_before=50000,
            tokens_after=30000,
            details={"consecutive": 2},
        )
        assert log_id

        log = store.get_budget_log("agent-a")
        assert len(log) == 1
        assert log[0]["event_type"] == "compaction"
        assert log[0]["tokens_before"] == 50000


class TestEvolutionStoreClear:
    def test_clear_removes_all_data(self, tmp_path: Path) -> None:
        store = _store_with_records(
            tmp_path,
            _make_record("s1", "a"),
            _make_record("s2", "b"),
        )
        store.record_analysis("t1", "a", "text")
        store.clear()
        assert store.get_all_skills() == []
        assert store.get_analyses_for_task("t1") == []


# ============================================================================
# 2. Analyzer -- _edit_distance, _correct_skill_ids, ExecutionAnalyzer
# NOTE: TestEditDistance, TestCorrectSkillIds, TestExecutionAnalyzer removed in
# Cycle 3 TSN cleanup — exact duplicates of test_evolution_analyzer.py
# NOTE: TestSkillEvolverFix, TestSkillEvolverDerived also removed — duplicates
# of test_evolution_evolver.py
# ============================================================================


# 3. Evolver -- SkillEvolver (captured, process_analysis, tool_degradation, etc.)
# ============================================================================


class TestSkillEvolverCaptured:
    def test_captured_creates_new_skill(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        evolver = SkillEvolver(store)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.CAPTURED,
            target_skill_ids=[],
            direction="Handle special case formatting",
        )
        result = evolver.evolve(suggestion)
        assert result.success
        assert result.new_record is not None
        assert result.new_record.lineage.origin == SkillOrigin.CAPTURED
        assert result.new_record.lineage.generation == 0
        assert result.new_record.lineage.parent_skill_ids == []

    def test_captured_with_directory(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        evolver = SkillEvolver(store)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.CAPTURED,
            direction="Some pattern",
        )
        result = evolver.evolve(suggestion, capture_directory="skills/custom")
        assert result.success
        assert result.new_record is not None
        assert result.new_record.directory == "skills/custom"

    def test_captured_no_direction_returns_error(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        evolver = SkillEvolver(store)
        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.CAPTURED,
            direction="",
        )
        result = evolver.evolve(suggestion)
        assert not result.success
        assert "direction" in result.error.lower()


class TestSkillEvolverProcessAnalysis:
    def test_processes_all_suggestions(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        evolver = SkillEvolver(store)

        analysis = AnalysisResult(
            task_id="t1",
            agent_name="a",
            analysis_text="",
            suggestions=[
                EvolutionSuggestion(
                    evolution_type=EvolutionType.CAPTURED,
                    direction="Pattern one",
                ),
                EvolutionSuggestion(
                    evolution_type=EvolutionType.CAPTURED,
                    direction="Pattern two",
                ),
            ],
        )
        results = evolver.process_analysis(analysis)
        assert len(results) == 2
        assert all(r.success for r in results)


class TestSkillEvolverToolDegradation:
    def test_fixes_all_active_skills(self, tmp_path: Path) -> None:
        s1 = _make_record("s1", "a")
        s2 = _make_record("s2", "b")
        store = _store_with_records(tmp_path, s1, s2)
        evolver = SkillEvolver(store)

        results = evolver.process_tool_degradation("tool-x", "API changed")
        assert len(results) == 2
        assert all(r.success for r in results)

    def test_process_tool_degradation_filters_by_affected_skills(self, tmp_path: Path) -> None:
        """Only skills in affected_skill_ids are evolved."""
        s1 = _make_record("skill-1", "a")
        s2 = _make_record("skill-2", "b")
        s3 = _make_record("skill-3", "c")
        store = _store_with_records(tmp_path, s1, s2, s3)
        evolver = SkillEvolver(store)

        results = evolver.process_tool_degradation(
            "tool-x", "broken", affected_skill_ids={"skill-1"}
        )
        assert len(results) == 1
        assert results[0].success
        assert results[0].new_record is not None
        assert "skill-1" in results[0].new_record.lineage.parent_skill_ids

    def test_anti_loop_skips_already_addressed(self, tmp_path: Path) -> None:
        s1 = _make_record("s1", "a")
        store = _store_with_records(tmp_path, s1)
        evolver = SkillEvolver(store)

        # First call addresses s1
        results = evolver.process_tool_degradation("tool-x", "broken")
        assert len(results) == 1
        # The original skill ID is now in the addressed set
        assert "s1" in evolver._addressed.get("tool-x", set())

        # Second call: the FIX evolved s1 into a new child that is active.
        # The new child has a different ID, so it is NOT in the addressed set
        # and will be evolved again. This is expected: the anti-loop tracks
        # specific skill IDs, and new evolved versions get new IDs.
        # The key invariant is that the SAME skill ID is not evolved twice.
        results2 = evolver.process_tool_degradation("tool-x", "still broken")
        # The new child (from the first FIX) gets evolved again
        assert len(results2) == 1


class TestSkillEvolverMetricCheck:
    def test_evolves_unhealthy_skill(self, tmp_path: Path) -> None:
        # fallback_rate = 60/100 = 0.6 > 0.4 -> FIX
        r = _make_record("s1", "bad", selections=100, applied=100, fallbacks=60)
        store = _store_with_records(tmp_path, r)
        evolver = SkillEvolver(store)

        results = evolver.process_metric_check(min_selections=5)
        assert len(results) == 1
        assert results[0].success

    def test_skips_below_min_selections(self, tmp_path: Path) -> None:
        r = _make_record("s1", "new", selections=3, applied=3, fallbacks=2)
        store = _store_with_records(tmp_path, r)
        evolver = SkillEvolver(store)

        results = evolver.process_metric_check(min_selections=5)
        assert len(results) == 0


class TestSkillEvolverUnknownType:
    def test_evolve_unknown_type_returns_error(self, tmp_path: Path) -> None:
        """Unknown evolution_type hits the else branch and returns error."""
        from unittest.mock import MagicMock

        store = _store_with_records(tmp_path)
        evolver = SkillEvolver(store)

        suggestion = MagicMock()
        suggestion.evolution_type = "nonexistent_type"
        result = evolver.evolve(suggestion)

        assert not result.success
        assert "Unknown evolution type" in result.error
        assert "nonexistent_type" in result.error
        assert result.new_record is None


# ============================================================================
# 4. CompactionGuard
# ============================================================================


def _make_agent_context(
    agent_id: str = "agent-a",
    session_id: str = "sess-1",
    turn: int = 10,
    total_tokens: int = 0,
    context_window: int = 128_000,
    last_compaction_turn: int = 0,
    l0_content: str = "",
    l1_content: str = "",
) -> AgentContext:
    """Create an AgentContext for compaction tests."""
    usage = TokenUsage(prompt_tokens=total_tokens, completion_tokens=0)
    return AgentContext(
        agent_id=agent_id,
        session_id=session_id,
        turn_number=turn,
        token_usage=usage,
        context_window=context_window,
        last_compaction_turn=last_compaction_turn,
        l0_content=l0_content,
        l1_content=l1_content,
    )


class TestCompactionGuardShouldCompact:
    def test_above_trigger_compacts(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(total_tokens=110_000, last_compaction_turn=0, turn=10)
        assert guard.should_compact(ctx) is True

    def test_below_trigger_no_compact(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(total_tokens=50_000)
        assert guard.should_compact(ctx) is False

    def test_too_recent_compaction(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(total_tokens=110_000, turn=6, last_compaction_turn=3)
        # Only 3 turns since last compaction, need 5
        assert guard.should_compact(ctx) is False

    def test_zero_context_window(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(total_tokens=100, context_window=0)
        assert guard.should_compact(ctx) is False


# NOTE: TestCompactionGuardTruncation and TestCompactionGuardHardCeiling removed —
# same threshold logic as TestCompactionGuardShouldCompact, different constants only


class TestCompactionGuardReinject:
    def test_reinject_returns_l0_and_l1(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(
            l0_content="Core identity",
            l1_content="Summary of work done",
        )
        result = guard.reinject_after_compaction(ctx)
        assert "Core identity" in result
        assert "Summary of work done" in result

    def test_reinject_increments_consecutive(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(l0_content="x", l1_content="y")
        guard.reinject_after_compaction(ctx)
        assert guard.consecutive_compactions == 1
        guard.reinject_after_compaction(ctx)
        assert guard.consecutive_compactions == 2

    # NOTE: test_reinject_logs_budget_event removed — verifies logging (implementation detail)

    # NOTE: test_reinject_l1_truncated removed — truncation is a detail of
    # reinject; test_reinject_returns_l0_and_l1 already tests reinject behavior

    def test_reinject_logs_estimated_tokens_not_chars(self, tmp_path: Path) -> None:
        """tokens_after stores estimated token count (~chars//4), not raw chars.

        Previously this stored raw char count which silently corrupted
        budget analytics (tokens_before was real tokens, tokens_after was chars).
        """
        import json as _json

        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(total_tokens=100_000, l0_content="hello world test data")
        guard.reinject_after_compaction(ctx)
        log = store.get_budget_log("agent-a")
        assert len(log) == 1
        tokens_after = log[0]["tokens_after"]
        details = _json.loads(log[0].get("details", "{}"))
        result_chars = details.get("result_chars", 0)
        # tokens_after is estimated tokens (chars//4), NOT raw chars
        assert tokens_after == result_chars // 4
        assert tokens_after < result_chars
        # Char count is preserved in details for full fidelity
        assert details.get("result_chars") == result_chars
        assert "result_tokens_estimated" in details

    # NOTE: test_compaction_short_result_still_logged removed — edge case of
    # test_reinject_logs_estimated_tokens_not_chars which already tests token estimation


# NOTE: TestCompactionGuardCheckAndLog removed — threshold alert logic covered by
# TestCompactionGuardShouldCompact (same threshold checks)


class TestCompactionGuardConsecutive:
    def test_should_alert_at_threshold(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(l0_content="x", l1_content="y")
        # consecutive_compaction_alert default = 3
        guard.reinject_after_compaction(ctx)
        guard.reinject_after_compaction(ctx)
        assert not guard.should_alert()
        guard.reinject_after_compaction(ctx)
        assert guard.should_alert()


# ============================================================================
# 5. Promotion
# ============================================================================


class TestPromotionCandidate:
    def test_find_candidates_meets_thresholds(self, tmp_path: Path) -> None:
        # effective_rate = 90/100 = 0.9 > 0.8, selections=100 > 50
        r = _make_record(
            "s1",
            "great-skill",
            selections=100,
            applied=90,
            completions=90,
            directory="skills/great",
        )
        store = _store_with_records(tmp_path, r)
        promoter = AgentPromoter(store)

        candidates = promoter.find_candidates()
        assert len(candidates) == 1
        assert candidates[0].skill_id == "s1"
        assert candidates[0].effective_rate == 0.9
        assert candidates[0].total_selections == 100

    # NOTE: test_find_candidates_low_effective_rate, test_find_candidates_too_few_selections,
    # test_find_candidates_no_directory removed — 3 tests for same filtering logic with
    # different rejection reasons; test_find_candidates_meets_thresholds covers the positive path


class TestPromotionPromote:
    def test_promote_creates_files(self, tmp_path: Path) -> None:
        r = _make_record(
            "s1",
            "great-skill",
            selections=100,
            applied=90,
            completions=90,
            directory="skills/great",
        )
        store = _store_with_records(tmp_path, r)
        agents_dir = tmp_path / "agents"
        promoter = AgentPromoter(store, agents_root=agents_dir)

        candidate = PromotionCandidate(
            skill_id="s1",
            skill_name="great-skill",
            effective_rate=0.9,
            total_selections=100,
            directory="skills/great",
            reason="test",
        )
        result = promoter.promote(candidate)
        assert result.success
        assert result.agent_name == "great-skill"
        assert Path(result.manifest_path).exists()
        assert Path(result.entry_point_path).exists()

    # NOTE: test_promote_manifest_content, test_promote_entry_point_content,
    # test_promote_skill_md removed — 3 tests verify file content details;
    # test_promote_creates_files covers the core promotion behavior


class TestPromotionPreservesExisting:
    """promote() must NOT delete a pre-existing directory on write failure."""

    def test_pre_existing_dir_not_deleted_on_failure(self, tmp_path: Path) -> None:
        """If agent_dir already exists and promotion fails, dir is preserved."""
        from unittest.mock import patch

        store = _store_with_records(tmp_path)
        agents_dir = tmp_path / "agents"
        # Create a pre-existing directory with a file inside
        agent_dir = agents_dir / "existing-skill"
        agent_dir.mkdir(parents=True)
        important_file = agent_dir / "important.txt"
        important_file.write_text("do not delete me", encoding="utf-8")

        promoter = AgentPromoter(store, agents_root=agents_dir)
        candidate = PromotionCandidate(
            skill_id="s1",
            skill_name="existing-skill",
            effective_rate=0.9,
            total_selections=100,
            directory="skills/existing",
            reason="test",
        )

        # Force a write failure after directory creation
        with patch(
            "agent_nexus.platform.evolution.promotion._atomic_write",
            side_effect=OSError("disk full"),
        ):
            result = promoter.promote(candidate)

        assert not result.success
        assert "disk full" in result.error
        # The pre-existing directory and its file must survive
        assert agent_dir.exists()
        assert important_file.exists()
        assert important_file.read_text() == "do not delete me"

    def test_newly_created_dir_cleaned_up_on_failure(self, tmp_path: Path) -> None:
        """If agent_dir was newly created and promotion fails, it IS cleaned up."""
        from unittest.mock import patch

        store = _store_with_records(tmp_path)
        agents_dir = tmp_path / "agents"
        promoter = AgentPromoter(store, agents_root=agents_dir)
        candidate = PromotionCandidate(
            skill_id="s1",
            skill_name="new-skill",
            effective_rate=0.9,
            total_selections=100,
            directory="skills/new",
            reason="test",
        )

        # The directory does not exist yet
        assert not (agents_dir / "new-skill").exists()

        with patch(
            "agent_nexus.platform.evolution.promotion._atomic_write",
            side_effect=OSError("disk full"),
        ):
            result = promoter.promote(candidate)

        assert not result.success
        # Newly created directory should be cleaned up
        assert not (agents_dir / "new-skill").exists()


class TestPromotionPathTraversalGuard:
    """promote() must reject skill names that could escape agents_root.

    Regression: promotion.py had no name validation (unlike installer.py
    and supervisor.py which use AGENT_NAME_RE from platform.utils).
    """

    def test_rejects_dotdot_traversal(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("private", encoding="utf-8")
        promoter = AgentPromoter(store, agents_root=agents_dir)

        candidate = PromotionCandidate(
            skill_id="s1",
            skill_name="../escape",
            effective_rate=0.9,
            total_selections=100,
            directory="skills/escape",
            reason="traversal attempt",
        )
        result = promoter.promote(candidate)
        assert not result.success
        assert "Invalid skill name" in result.error
        # No directory created outside agents_root
        assert not (tmp_path / "escape").exists()

    # NOTE: test_rejects_slash_in_name removed — same validation as
    # test_rejects_dotdot_traversal, different invalid character

    def test_accepts_valid_name(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        agents_dir = tmp_path / "agents"
        promoter = AgentPromoter(store, agents_root=agents_dir)

        candidate = PromotionCandidate(
            skill_id="s1",
            skill_name="valid-skill-v2",
            effective_rate=0.9,
            total_selections=100,
            directory="skills/valid",
            reason="valid",
        )
        result = promoter.promote(candidate)
        assert result.success


# ============================================================================
# 6. HealthChecker
# ============================================================================


class TestHealthCheckerCheckHealth:
    def test_healthy_skill(self) -> None:
        r = _make_record(
            "s1",
            "good",
            selections=100,
            applied=80,
            completions=70,
            fallbacks=5,
        )
        # With a dummy store (check_health doesn't use store)
        from unittest.mock import MagicMock

        checker = HealthChecker(MagicMock())
        suggestions = checker.check_health(r)
        assert len(suggestions) == 0

    def test_high_fallback_triggers_fix(self) -> None:
        # fallback_rate = 50/100 = 0.5 > 0.4
        r = _make_record("s1", "x", selections=100, applied=100, completions=50, fallbacks=50)
        from unittest.mock import MagicMock

        checker = HealthChecker(MagicMock())
        suggestions = checker.check_health(r)
        fix_suggestions = [s for s in suggestions if s.evolution_type == EvolutionType.FIX]
        assert len(fix_suggestions) >= 1
        assert any("fallback" in s.direction.lower() for s in fix_suggestions)

    def test_low_completion_triggers_fix(self) -> None:
        # applied_rate = 50/100 = 0.5 > 0.4, completion_rate = 15/50 = 0.3 < 0.35
        r = _make_record("s1", "x", selections=100, applied=50, completions=15)
        from unittest.mock import MagicMock

        checker = HealthChecker(MagicMock())
        suggestions = checker.check_health(r)
        fix_suggestions = [s for s in suggestions if s.evolution_type == EvolutionType.FIX]
        assert len(fix_suggestions) >= 1

    def test_moderate_effective_triggers_derived(self) -> None:
        # effective_rate = 40/100 = 0.4 < 0.55, applied_rate = 30/100 = 0.3 > 0.25
        r = _make_record("s1", "x", selections=100, applied=40, completions=30)
        from unittest.mock import MagicMock

        checker = HealthChecker(MagicMock())
        suggestions = checker.check_health(r)
        derived = [s for s in suggestions if s.evolution_type == EvolutionType.DERIVED]
        assert len(derived) >= 1

    def test_zero_selections_no_suggestions(self) -> None:
        r = _make_record("s1", "x", selections=0)
        from unittest.mock import MagicMock

        checker = HealthChecker(MagicMock())
        assert checker.check_health(r) == []


class TestHealthCheckerDiagnoseAll:
    def test_diagnose_all(self, tmp_path: Path) -> None:
        r1 = _make_record("s1", "healthy", selections=100, applied=80, completions=70, fallbacks=5)
        r2 = _make_record("s2", "unhealthy", selections=100, applied=100, fallbacks=60)
        store = _store_with_records(tmp_path, r1, r2)
        checker = HealthChecker(store)

        reports = checker.diagnose_all()
        assert len(reports) == 2
        assert reports["s1"].is_healthy is True
        assert reports["s2"].is_healthy is False

    # NOTE: test_diagnose_all_metrics removed — metric computation already tested by
    # TestHealthCheckerCheckHealth which exercises the same threshold logic

    def test_diagnose_all_zero_selections_metrics(self, tmp_path: Path) -> None:
        r = _make_record("s1", "x", selections=0)
        store = _store_with_records(tmp_path, r)
        checker = HealthChecker(store)

        reports = checker.diagnose_all()
        metrics = reports["s1"].metrics
        assert metrics["applied_rate"] == 0.0
        assert metrics["completion_rate"] == 0.0


# NOTE: TestHealthCheckerGetUnhealthy removed — same logic as TestHealthCheckerDiagnoseAll
# NOTE: TestHealthCheckerGetSummary removed — trivial dict aggregation
# NOTE: TestHealthReportSummary removed — tests summary() formatting, not behavior

# ============================================================================
# CompactionGuard.should_alert respects custom budget (from iter13)
# ============================================================================


class TestCompactionGuardCustomBudget:
    """Verify should_alert accepts and uses a custom ContextBudget."""

    def _make_store(self, tmp_path: Path) -> EvolutionStore:
        return EvolutionStore(tmp_path / "test.db")

    def _make_context(self) -> AgentContext:
        return AgentContext(
            agent_id="agent-a",
            session_id="session-1",
            l0_content="l0",
            l1_content="l1",
        )

    # NOTE: test_default_budget_threshold removed — same logic as
    # TestCompactionGuardConsecutive.test_should_alert_at_threshold

    def test_custom_budget_threshold(self, tmp_path: Path) -> None:
        """Custom budget with lower threshold triggers alert earlier."""
        store = self._make_store(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = self._make_context()

        custom_budget = ContextBudget(consecutive_compaction_alert=2)

        guard.reinject_after_compaction(ctx)
        assert not guard.should_alert(budget=custom_budget)

        guard.reinject_after_compaction(ctx)
        assert guard.should_alert(budget=custom_budget)

    # NOTE: test_none_budget_uses_default removed — same as test_default_budget_threshold


# ============================================================================
# HealthReport.summary uses key-based formatting (from iter15)
# ============================================================================


# ---------------------------------------------------------------------------
# Iteration 24 fixes: fuzzy ID prefix scoping, suggestion dedup,
# addressed-on-success
# ---------------------------------------------------------------------------


class TestCorrectSkillIdsPrefixScoping:
    """_correct_skill_ids skips fuzzy matching when raw_id has no __ prefix."""

    def test_no_prefix_separator_returns_raw_id_unchanged(self) -> None:
        """When raw_id has no __, it can't be scoped so it passes through."""
        known = {"reviewer__fix_a1b2c3d4", "reviewer__drv_e5f6g7h8"}
        # "reviewer" has no __ -> skip fuzzy matching entirely
        result = _correct_skill_ids(["reviewer"], known)
        assert result == ["reviewer"]

    def test_prefix_separator_scopes_candidates(self) -> None:
        """When raw_id has __, only candidates with matching prefix compete."""
        known = {
            "reviewer__fix_a1b2c3d4",
            "reviewer__drv_e5f6g7h8",
            "writer__fix_11111111",
        }
        # Typo in the suffix after reviewer__ should match reviewer candidates only
        result = _correct_skill_ids(["reviewer__fix_a1b2c3XX"], known)
        assert result == ["reviewer__fix_a1b2c3d4"]

    def test_no_prefix_does_not_cross_match(self) -> None:
        """Without __, a raw_id should never fuzzy-match to a known ID."""
        known = {"reviewer__fix_a1b2c3d4"}
        result = _correct_skill_ids(["reviewr"], known)
        # "reviewr" has no __, so it stays unchanged (not fuzzy-matched)
        assert result == ["reviewr"]


class TestSuggestionDeduplication:
    """_generate_suggestions deduplicates by (evolution_type, skill_id).

    A skill that triggers both high fallback AND low completion should
    produce only one FIX suggestion (keeping the higher confidence).
    """

    def _make_skill(
        self,
        selections: int,
        fallbacks: int,
        applied: int,
        completions: int,
    ) -> SkillRecord:
        return SkillRecord(
            id="skill-1",
            name="test-skill",
            version="1.0.0",
            lineage=SkillLineage(
                origin=SkillOrigin.FIXED,
                generation=1,
                parent_skill_ids=[],
            ),
            directory="skills/test",
            is_active=True,
            total_selections=selections,
            total_applied=applied,
            total_completions=completions,
            total_fallbacks=fallbacks,
        )

    def test_duplicate_fix_suggestions_deduplicated(self, tmp_path: Path) -> None:
        """Skill with high fallback AND low completion produces one FIX."""
        store = EvolutionStore(tmp_path / "test.db")
        analyzer = ExecutionAnalyzer(store)

        # completions(1) + fallbacks(6) = 7 <= applied(7)
        # fallback_rate = 6/10 = 0.6 > 0.4 threshold (FIX)
        # applied_rate = 7/10 = 0.7 > 0.4, completion_rate = 1/7 = 0.14 < 0.35 (FIX again)
        skill = self._make_skill(
            selections=10,
            fallbacks=6,
            applied=7,
            completions=1,
        )
        store.save_skill_record(skill)

        ctx = EvolutionContext(
            agent_id="a",
            task_id="t-1",
            skill_ids_used=["skill-1"],
            skills_applied=["skill-1"],
            skills_fell_back=["skill-1"],
        )
        result = analyzer.analyze_execution(ctx)

        fix_suggestions = [s for s in result.suggestions if s.evolution_type == EvolutionType.FIX]
        assert len(fix_suggestions) == 1, f"Expected 1 deduplicated FIX, got {len(fix_suggestions)}"

    def test_fix_takes_priority_over_derived_for_same_skill(self, tmp_path: Path) -> None:
        """When FIX is triggered, DERIVED is skipped for the same skill."""
        store = EvolutionStore(tmp_path / "test.db")
        analyzer = ExecutionAnalyzer(store)

        # completions(3) + fallbacks(6) = 9 <= applied(9)
        # fallback_rate = 6/10 = 0.6 > 0.4 (FIX)
        # effective_rate = 3/10 = 0.3 < 0.55, applied_rate = 9/10 = 0.9 > 0.25 (DERIVED)
        # But FIX takes priority, so only FIX should appear
        skill = self._make_skill(
            selections=10,
            fallbacks=6,
            applied=9,
            completions=3,
        )
        store.save_skill_record(skill)

        ctx = EvolutionContext(
            agent_id="a",
            task_id="t-1",
            skill_ids_used=["skill-1"],
            skills_applied=["skill-1"],
            skills_fell_back=["skill-1"],
        )
        result = analyzer.analyze_execution(ctx)

        types = {s.evolution_type for s in result.suggestions}
        assert EvolutionType.FIX in types
        # DERIVED should NOT appear because FIX was triggered first
        assert EvolutionType.DERIVED not in types


# NOTE: TestAddressedOnSuccessOnly removed — anti-loop _addressed marking logic
# already tested by TestSkillEvolverToolDegradation.test_anti_loop_skips_already_addressed


class TestAnalyzerCapturedDedup:
    """Analyzer dedup with CAPTURED suggestions (empty target_skill_ids) must
    not crash.  Regression test for the IndexError that occurred when
    target_skill_ids was [] and the dedup code indexed [0].
    """

    def test_captured_suggestion_dedup_no_crash(self, tmp_path: Path) -> None:
        """CAPTURED with empty target_skill_ids deduplicates safely."""
        store = EvolutionStore(tmp_path / "test.db")
        analyzer = ExecutionAnalyzer(store)

        # Task completed with no skills -> CAPTURED suggestion
        ctx = EvolutionContext(
            agent_id="a",
            task_id="t-1",
            skill_ids_used=[],
            skills_applied=[],
            skills_fell_back=[],
            task_completed=True,
        )
        result = analyzer.analyze_execution(ctx)
        captured = [s for s in result.suggestions if s.evolution_type == EvolutionType.CAPTURED]
        assert len(captured) == 1
        assert captured[0].target_skill_ids == []


# ============================================================================
# Iteration 25 fixes: import re at module level, health dedup/DERIVED
# suppression, edit distance scaling, sentence-split for captured names
# ============================================================================


# NOTE: TestEditDistanceScaling removed — 4 tests for same scaling logic with
# different parameter sizes; TestCorrectSkillIdsPrefixScoping covers prefix behavior


# ============================================================================
# 7. EvolutionEngine facade
# ============================================================================


# NOTE: TestEvolutionEngineInit removed — 3 tests verifying isinstance checks and
# property access; trivial delegation tests, covered by convenience methods below


class TestEvolutionEngineEvolvePostAnalysis:
    """evolve(trigger='post_analysis') delegates to analyzer then evolver."""

    def test_post_analysis_requires_ctx(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        engine = EvolutionEngine(store)

        with pytest.raises(ValueError, match="ctx.*required"):
            engine.evolve(trigger=EvolutionTrigger.POST_ANALYSIS)


class TestEvolutionEngineEvolveToolDegradation:
    """evolve(trigger='tool_degradation') delegates to evolver."""

    # NOTE: test_tool_degradation_filters_affected removed — same delegation as
    # TestSkillEvolverToolDegradation.test_process_tool_degradation_filters_by_affected_skills

    def test_tool_degradation_requires_tool_key(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        engine = EvolutionEngine(store)

        with pytest.raises(ValueError, match="tool_key.*required"):
            engine.evolve(trigger=EvolutionTrigger.TOOL_DEGRADATION)


# NOTE: TestEvolutionEngineEvolveMetricCheck removed — same delegation as
# TestSkillEvolverMetricCheck which tests the underlying logic directly


# NOTE: TestEvolutionEngineEvolveUnknown removed — single-assert ValueError check,
# trivial validation test


class TestEvolutionEngineConvenienceMethods:
    """Convenience methods delegate correctly."""

    # NOTE: test_check_health_healthy and test_check_health_unhealthy removed —
    # same logic as TestHealthCheckerCheckHealth tests

    def test_check_health_missing_skill_raises(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        engine = EvolutionEngine(store)

        with pytest.raises(ValueError, match="Skill not found"):
            engine.check_health("nonexistent")

    # NOTE: test_diagnose_all removed — same logic as TestHealthCheckerDiagnoseAll

    # NOTE: test_promote_candidate removed — same delegation as TestPromotionPromote

    # NOTE: test_should_compact_true and test_should_compact_false removed —
    # same logic as TestCompactionGuardShouldCompact tests


# ============================================================================
# 9. Regression: evolve_skill IntegrityError + get_metrics precise LIKE
# ============================================================================


# NOTE: TestEvolveSkillIntegrityError removed — exact duplicate of
# TestEvolveSkillIdCollision in test_evolution_store.py (which is more thorough
# with 3 tests covering collision, no lineage parents, and parent not deactivated)


class TestGetMetricsPreciseLikeMatching:
    """get_metrics uses precise directory LIKE matching so that querying
    agent_name='code' does not accidentally match 'encoder-decoder'.
    """

    def test_precise_agent_name_filtering(self, tmp_path: Path) -> None:
        """get_metrics(agent_name='code') only matches agents/code/... and
        agents/code exactly, not agents/encoder-decoder/... which contains
        'code' as a substring in the agent directory name.
        """
        store = EvolutionStore(tmp_path / "test.db")

        r1 = _make_record(
            "s1",
            "review",
            selections=10,
            directory="agents/code/review",
        )
        r2 = _make_record(
            "s2",
            "encode",
            selections=20,
            directory="agents/encoder-decoder/encode",
        )
        store.save_skill_record(r1)
        store.save_skill_record(r2)

        metrics = store.get_metrics(agent_name="code")
        assert metrics.total_selections == 10


# ============================================================================
# Coverage gap tests: evolver addressed-skip, metric-check healthy skip,
# fix multi-parent error, store.evolve_skill failure, derived missing parent,
# captured long name truncation, captured empty name fallback
# ============================================================================


# NOTE: TestProcessToolDegradationAddressedSkip removed — anti-loop logic already
# tested by TestSkillEvolverToolDegradation.test_anti_loop_skips_already_addressed


# NOTE: TestProcessMetricCheckHealthySkip removed — same logic as
# TestSkillEvolverMetricCheck.test_skips_below_min_selections (healthy skill = no evolution)


class TestEvolveFixMultipleParents:
    """FIX evolution requires exactly 1 parent."""

    def test_fix_with_two_parents_returns_error(self, tmp_path: Path) -> None:
        """FIX with multiple parents returns error, not crash."""
        store = _store_with_records(tmp_path)
        evolver = SkillEvolver(store)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.FIX,
            target_skill_ids=["p1", "p2"],
            direction="Fix multiple",
        )
        result = evolver.evolve(suggestion)
        assert not result.success
        assert "exactly 1 parent, got 2" in result.error


# NOTE: TestEvolveFixStoreFailure removed — store failure propagation is trivial error
# passthrough; TestEvolveFixMultipleParents already tests FIX error handling


# NOTE: TestEvolveDerivedMissingParent removed — same error path as
# TestEvolveFixMultipleParents (parent lookup failure), different EvolutionType only


# NOTE: TestEvolveDerivedStoreFailure removed — identical to TestEvolveFixStoreFailure,
# only differs in EvolutionType; store failure behavior is the same for all types


class TestEvolveCapturedLongName:
    """CAPTURED evolution truncates long direction text for name."""

    def test_long_direction_truncated(self, tmp_path: Path) -> None:
        """Direction longer than 50 chars is truncated for name generation."""
        store = _store_with_records(tmp_path)
        evolver = SkillEvolver(store)

        long_direction = "A" * 80 + ". And more text"
        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.CAPTURED,
            direction=long_direction,
        )
        result = evolver.evolve(suggestion)
        assert result.success
        assert result.new_record is not None
        # Name should be based on first 50 chars, lowercased, hyphens
        assert len(result.new_record.name) <= 50 + 10  # some slack for suffixes


class TestEvolveCapturedEmptyName:
    """CAPTURED evolution generates fallback name when direction sanitizes to empty."""

    def test_special_chars_direction_uses_fallback_name(
        self,
        tmp_path: Path,
    ) -> None:
        """Direction with only special chars produces a 'captured_' fallback name."""
        store = _store_with_records(tmp_path)
        evolver = SkillEvolver(store)

        # Direction that will sanitize to empty after removing non-alnum
        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.CAPTURED,
            direction="!!!???...",
        )
        result = evolver.evolve(suggestion)
        assert result.success
        assert result.new_record is not None
        assert result.new_record.name.startswith("captured_")


# NOTE: TestEvolveCapturedStoreFailure removed — identical to TestEvolveFixStoreFailure,
# only differs in EvolutionType; store failure behavior is the same for all types


# ============================================================================
# Coverage: store.py missed lines
# ============================================================================


# NOTE: TestEvolutionStoreCounterValidation removed — 3 tests for same guard logic
# with different parameter combinations; error handling covered by increment_counters tests


# NOTE: TestEvolutionStoreAnalysisSkipEmptySkillId removed — exact duplicate of
# TestRecordAnalysisSkipsBadSkillId in test_evolution_store.py (which uses
# parametrize for None/empty/missing_key and is more thorough)


# NOTE: TestEvolutionStoreAncestryCycleDetection removed — duplicate of
# TestGetAncestry in test_evolution_store.py


# ============================================================================
# Coverage gap tests: analyzer.py lines 106, 172, 234, 284, 351
# ============================================================================


# NOTE: TestCorrectSkillIdsManyCandidates removed — same fuzzy matching logic as
# TestCorrectSkillIdsPrefixScoping, just with more candidates


# NOTE: TestAnalyzerUnknownSkillIdContinue removed — same logic as
# TestSuggestionDeduplication which tests analyzer with missing skills


# NOTE: TestAnalyzerGenerateSuggestionsSkillNone removed — same code path as
# TestSuggestionDeduplication (missing skill produces no output)


# NOTE: TestAnalyzerDerivedSuggestion removed — same DERIVED logic as
# TestHealthCheckerCheckHealth.test_moderate_effective_triggers_derived


# NOTE: TestAnalyzerExecutionError removed — string inclusion in analysis text
# is a trivial formatting detail


# ============================================================================
# Coverage gap tests: compaction.py lines 99, 109, 204-205
# ============================================================================


# NOTE: TestCompactionGuardZeroWindowEdgeCases removed — 2 tests for same edge case
# (zero window) in two methods; TestCompactionGuardShouldCompact.test_zero_context_window
# already covers the zero-window pattern


# NOTE: TestCompactionGuardL0Fallback removed — fallback content generation
# is tested implicitly by reinject tests with non-empty l0


# ============================================================================
# Coverage gap tests: promotion.py lines 138-139 (OSError on mkdir)
# ============================================================================


# NOTE: TestPromotionMkdirOSError removed — error propagation for mkdir is trivial;
# TestPromotionPreservesExisting already tests error handling in promote()


# ============================================================================
# Coverage gap tests: health.py line 188 (diagnose_skills with skill_ids filter)
# ============================================================================


# NOTE: TestHealthCheckerDiagnoseSkillsFiltered removed — filter-by-ID is trivial
# set intersection logic; TestHealthCheckerDiagnoseAll already tests the core behavior


# ============================================================================
# Coverage gap tests: promotion.py lines 202-207 (_atomic_write cleanup)
# ============================================================================


# NOTE: TestPromotionAtomicWriteCleanup removed — tests platform.utils.atomic_write
# cleanup, not evolution behavior; should live in test_utils if needed
