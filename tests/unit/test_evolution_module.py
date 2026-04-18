"""Unit tests for agent_nexus.platform.evolution -- all 6 modules.

Covers: store, analyzer, evolver, compaction, promotion, health.
Uses tmp_path for SQLite databases, pytest class-based organization.
Target: ~65 tests across all modules.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_nexus.models.evolution import (
    EvolutionContext,
    EvolutionMetrics,
    EvolutionType,
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)
from agent_nexus.models.context import ContextBudget, TokenUsage
from agent_nexus.platform.evolution.store import EvolutionStore
from agent_nexus.platform.evolution.analyzer import (
    AnalysisResult,
    EvolutionSuggestion,
    ExecutionAnalyzer,
    _correct_skill_ids,
    _edit_distance,
)
from agent_nexus.platform.evolution.evolver import (
    EvolveResult,
    EvolutionTrigger,
    SkillEvolver,
)
from agent_nexus.platform.evolution.compaction import AgentContext, CompactionGuard
from agent_nexus.platform.evolution.promotion import (
    AgentPromoter,
    PromotionCandidate,
    PromotionResult,
)
from agent_nexus.platform.evolution.health import HealthChecker, HealthReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_now = datetime.now(timezone.utc)


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
        store = EvolutionStore(db)
        assert db.exists()
        # Opening again should not fail (idempotent schema)
        EvolutionStore(db)

    def test_wal_mode(self, tmp_path: Path) -> None:
        db = tmp_path / "evo.db"
        EvolutionStore(db)
        import sqlite3

        conn = sqlite3.connect(str(db))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"


class TestEvolutionStoreCRUD:
    def test_save_and_get(self, tmp_path: Path) -> None:
        store = _store_with_records(
            tmp_path, _make_record("s1", "my-skill")
        )
        record = store.get_skill_record("s1")
        assert record is not None
        assert record.id == "s1"
        assert record.name == "my-skill"
        assert record.is_active is True

    def test_get_nonexistent_returns_none(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        assert store.get_skill_record("no-such-id") is None

    def test_get_active_skills(self, tmp_path: Path) -> None:
        r1 = _make_record("s1", "active-skill", is_active=True)
        r2 = _make_record("s2", "inactive-skill", is_active=False)
        store = _store_with_records(tmp_path, r1, r2)
        active = store.get_active_skills()
        assert len(active) == 1
        assert active[0].id == "s1"

    def test_get_all_skills(self, tmp_path: Path) -> None:
        r1 = _make_record("s1", "a", is_active=True)
        r2 = _make_record("s2", "b", is_active=False)
        store = _store_with_records(tmp_path, r1, r2)
        all_skills = store.get_all_skills()
        assert len(all_skills) == 2

    def test_deactivate_skill(self, tmp_path: Path) -> None:
        r = _make_record("s1", "x", is_active=True)
        store = _store_with_records(tmp_path, r)
        assert store.deactivate_skill("s1") is True
        assert store.get_skill_record("s1") is not None
        assert store.get_skill_record("s1").is_active is False

    def test_deactivate_nonexistent(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        assert store.deactivate_skill("nope") is False

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        r = _make_record("s1", "original")
        store = _store_with_records(tmp_path, r)
        updated = _make_record("s1", "updated")
        store.save_skill_record(updated)
        got = store.get_skill_record("s1")
        assert got.name == "updated"

    def test_get_versions(self, tmp_path: Path) -> None:
        r1 = _make_record("s1", "skill", generation=0)
        r2 = _make_record("s2", "skill", generation=1)
        store = _store_with_records(tmp_path, r1, r2)
        versions = store.get_versions("skill")
        assert len(versions) == 2
        assert versions[0].lineage.generation == 0
        assert versions[1].lineage.generation == 1


class TestEvolutionStoreLineageParents:
    def test_parents_persisted(self, tmp_path: Path) -> None:
        p1 = _make_record("p1", "parent-a")
        p2 = _make_record("p2", "parent-b")
        r = _make_record(
            "s1", "child", parent_ids=["p1", "p2"], generation=1
        )
        store = _store_with_records(tmp_path, p1, p2, r)
        got = store.get_skill_record("s1")
        assert set(got.lineage.parent_skill_ids) == {"p1", "p2"}

    def test_parents_updated_on_resave(self, tmp_path: Path) -> None:
        pa = _make_record("pa", "parent-a")
        pb = _make_record("pb", "parent-b")
        pc = _make_record("pc", "parent-c")
        r = _make_record("s1", "child", parent_ids=["pa"])
        store = _store_with_records(tmp_path, pa, pb, pc, r)
        r2 = _make_record("s1", "child", parent_ids=["pb", "pc"])
        store.save_skill_record(r2)
        got = store.get_skill_record("s1")
        assert set(got.lineage.parent_skill_ids) == {"pb", "pc"}


class TestEvolutionStoreCounters:
    def test_increment_selected(self, tmp_path: Path) -> None:
        r = _make_record("s1", "x", selections=5)
        store = _store_with_records(tmp_path, r)
        store.increment_counters("s1", selected=True)
        got = store.get_skill_record("s1")
        assert got.total_selections == 6

    def test_increment_multiple(self, tmp_path: Path) -> None:
        r = _make_record("s1", "x", selections=10, applied=5, completions=3)
        store = _store_with_records(tmp_path, r)
        store.increment_counters(
            "s1", selected=True, applied=True, completed=True
        )
        got = store.get_skill_record("s1")
        assert got.total_selections == 11
        assert got.total_applied == 6
        assert got.total_completions == 4

    def test_increment_no_flags_is_noop(self, tmp_path: Path) -> None:
        r = _make_record("s1", "x", selections=5)
        store = _store_with_records(tmp_path, r)
        store.increment_counters("s1")
        got = store.get_skill_record("s1")
        assert got.total_selections == 5

    def test_increment_fallback(self, tmp_path: Path) -> None:
        r = _make_record("s1", "x", fallbacks=2)
        store = _store_with_records(tmp_path, r)
        store.increment_counters("s1", fell_back=True)
        assert store.get_skill_record("s1").total_fallbacks == 3


class TestEvolutionStoreAnalysis:
    def test_record_analysis(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path, _make_record("s1", "x"))
        analysis_id = store.record_analysis(
            task_id="t1",
            agent_name="agent-a",
            analysis_text="looks good",
            evolution_suggestions=[{"type": "fix", "target": "s1"}],
            judgments=[
                {"skill_id": "s1", "selected": True, "applied": True,
                 "completed": False, "fell_back": False},
            ],
        )
        assert analysis_id
        analyses = store.get_analyses_for_task("t1")
        assert len(analyses) == 1
        assert analyses[0]["agent_name"] == "agent-a"
        assert analyses[0]["analysis"] == "looks good"

    def test_judgments_persisted(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path, _make_record("s1", "x"))
        store.record_analysis(
            task_id="t1",
            agent_name="a",
            analysis_text="text",
            judgments=[
                {"skill_id": "s1", "selected": True, "applied": True,
                 "completed": True, "fell_back": False},
            ],
        )
        judgments = store.get_judgments_for_skill("s1")
        assert len(judgments) == 1
        assert judgments[0]["selected"] is True
        assert judgments[0]["completed"] is True

    def test_analysis_increments_counters(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path, _make_record("s1", "x"))
        store.record_analysis(
            task_id="t1",
            agent_name="a",
            analysis_text="",
            judgments=[
                {"skill_id": "s1", "selected": True, "applied": True,
                 "completed": True, "fell_back": True},
            ],
        )
        got = store.get_skill_record("s1")
        assert got.total_selections == 1
        assert got.total_applied == 1
        assert got.total_completions == 1
        assert got.total_fallbacks == 1


class TestEvolutionStoreEvolveSkill:
    def test_fix_deactivates_parent(self, tmp_path: Path) -> None:
        parent = _make_record("p1", "buggy", is_active=True)
        store = _store_with_records(tmp_path, parent)

        child = _make_record(
            "c1",
            "buggy",
            origin=SkillOrigin.FIXED,
            generation=1,
            parent_ids=["p1"],
        )
        store.evolve_skill(child, ["p1"])

        assert store.get_skill_record("p1").is_active is False
        assert store.get_skill_record("c1").is_active is True

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

        assert store.get_skill_record("p1").is_active is True
        assert store.get_skill_record("c1").is_active is True

    def test_lineage_parents_stored(self, tmp_path: Path) -> None:
        p1 = _make_record("p1", "a")
        p2 = _make_record("p2", "b")
        store = _store_with_records(tmp_path, p1, p2)

        child = _make_record(
            "c1", "merged", parent_ids=["p1", "p2"], generation=1
        )
        store.evolve_skill(child, ["p1", "p2"])

        got = store.get_skill_record("c1")
        assert set(got.lineage.parent_skill_ids) == {"p1", "p2"}


class TestEvolutionStoreAncestry:
    def test_get_ancestry_linear(self, tmp_path: Path) -> None:
        g0 = _make_record("g0", "skill", generation=0)
        g1 = _make_record("g1", "skill", generation=1, parent_ids=["g0"])
        g2 = _make_record("g2", "skill", generation=2, parent_ids=["g1"])
        store = _store_with_records(tmp_path, g0, g1, g2)

        ancestors = store.get_ancestry("g2")
        assert len(ancestors) == 2
        assert ancestors[0].id == "g0"
        assert ancestors[1].id == "g1"

    def test_get_children(self, tmp_path: Path) -> None:
        p = _make_record("p1", "parent")
        c1 = _make_record("c1", "child", parent_ids=["p1"])
        c2 = _make_record("c2", "child2", parent_ids=["p1"])
        store = _store_with_records(tmp_path, p, c1, c2)

        children = store.get_children("p1")
        assert set(children) == {"c1", "c2"}


class TestEvolutionStoreMetrics:
    def test_get_metrics_all(self, tmp_path: Path) -> None:
        r1 = _make_record("s1", "a", selections=10, applied=5, completions=3, fallbacks=1)
        r2 = _make_record("s2", "b", selections=20, applied=15, completions=10, fallbacks=2)
        store = _store_with_records(tmp_path, r1, r2)

        metrics = store.get_metrics()
        assert metrics.total_selections == 30
        assert metrics.total_applied == 20
        assert metrics.total_completions == 13
        assert metrics.total_fallbacks == 3

    def test_get_metrics_by_agent_name(self, tmp_path: Path) -> None:
        r1 = _make_record("s1", "a", selections=10, directory="agents/myagent")
        r2 = _make_record("s2", "b", selections=20, directory="agents/other")
        store = _store_with_records(tmp_path, r1, r2)

        metrics = store.get_metrics("myagent")
        assert metrics.total_selections == 10


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
# ============================================================================


class TestEditDistance:
    def test_identical_strings(self) -> None:
        assert _edit_distance("hello", "hello") == 0

    def test_empty_strings(self) -> None:
        assert _edit_distance("", "") == 0

    def test_one_empty(self) -> None:
        assert _edit_distance("abc", "") == 3
        assert _edit_distance("", "abc") == 3

    def test_substitution(self) -> None:
        assert _edit_distance("cat", "bat") == 1

    def test_insertion(self) -> None:
        assert _edit_distance("ac", "abc") == 1

    def test_deletion(self) -> None:
        assert _edit_distance("abc", "ac") == 1

    def test_complete_mismatch(self) -> None:
        assert _edit_distance("abc", "xyz") == 3

    def test_longer_strings(self) -> None:
        assert _edit_distance("kitten", "sitting") == 3


class TestCorrectSkillIds:
    def test_known_ids_unchanged(self) -> None:
        known = {"skill-a__v1", "skill-b__v2"}
        assert _correct_skill_ids(["skill-a__v1"], known) == ["skill-a__v1"]

    def test_fuzzy_match_close_id(self) -> None:
        known = {"agent-a__review_code"}
        # One character off
        result = _correct_skill_ids(["agent-a__review_codx"], known)
        assert result == ["agent-a__review_code"]

    def test_too_far_returns_original(self) -> None:
        known = {"agent-a__review_code"}
        result = _correct_skill_ids(["agent-a__something_else"], known)
        assert result == ["agent-a__something_else"]

    def test_empty_known_returns_input(self) -> None:
        assert _correct_skill_ids(["a", "b"], set()) == ["a", "b"]

    def test_no_prefix_match_returns_original(self) -> None:
        known = {"agent-a__foo"}
        result = _correct_skill_ids(["agent-b__fop"], known)
        # Different prefix, no candidates
        assert result == ["agent-b__fop"]

    def test_ambiguous_returns_original(self) -> None:
        known = {"x__abc", "x__abd"}
        result = _correct_skill_ids(["x__abe"], known)
        # Both abc and abd are distance 1 from abe -- ambiguous
        assert result == ["x__abe"]


class TestExecutionAnalyzer:
    def test_analyze_with_high_fallback(self, tmp_path: Path) -> None:
        # fallback_rate = 50/100 = 0.5 > 0.4 -> FIX
        r = _make_record("s1", "buggy", selections=100, fallbacks=50)
        store = _store_with_records(tmp_path, r)
        analyzer = ExecutionAnalyzer(store)

        ctx = EvolutionContext(
            agent_id="agent-a",
            task_id="t1",
            task_completed=False,
            skill_ids_used=["s1"],
        )
        result = analyzer.analyze_execution(ctx)
        assert result.task_id == "t1"
        assert result.agent_name == "agent-a"
        fix_suggestions = [
            s for s in result.suggestions
            if s.evolution_type == EvolutionType.FIX
        ]
        assert len(fix_suggestions) >= 1
        assert "s1" in fix_suggestions[0].target_skill_ids

    def test_analyze_captured_on_success_no_skills(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        analyzer = ExecutionAnalyzer(store)

        ctx = EvolutionContext(
            agent_id="agent-a",
            task_id="t2",
            task_completed=True,
            skill_ids_used=[],
        )
        result = analyzer.analyze_execution(ctx)
        captured = [
            s for s in result.suggestions
            if s.evolution_type == EvolutionType.CAPTURED
        ]
        assert len(captured) == 1

    def test_analyze_healthy_skill_no_suggestion(self, tmp_path: Path) -> None:
        r = _make_record("s1", "good", selections=100, applied=80, completions=70, fallbacks=5)
        store = _store_with_records(tmp_path, r)
        analyzer = ExecutionAnalyzer(store)

        ctx = EvolutionContext(
            agent_id="agent-a",
            task_id="t3",
            task_completed=True,
            skill_ids_used=["s1"],
        )
        result = analyzer.analyze_execution(ctx)
        assert len(result.suggestions) == 0

    def test_analyze_builds_analysis_text(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path, _make_record("s1", "x"))
        analyzer = ExecutionAnalyzer(store)
        ctx = EvolutionContext(
            agent_id="agent-a",
            task_id="t4",
            task_description="Do something",
            task_completed=True,
            skill_ids_used=["s1"],
        )
        result = analyzer.analyze_execution(ctx)
        assert "t4" in result.analysis_text
        assert "agent-a" in result.analysis_text

    def test_analyze_persists_analysis(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path, _make_record("s1", "x"))
        analyzer = ExecutionAnalyzer(store)
        ctx = EvolutionContext(
            agent_id="agent-a",
            task_id="t5",
            task_completed=True,
            skill_ids_used=["s1"],
        )
        result = analyzer.analyze_execution(ctx)
        assert result.analysis_id
        analyses = store.get_analyses_for_task("t5")
        assert len(analyses) == 1

    def test_analyze_zero_selections_skipped(self, tmp_path: Path) -> None:
        r = _make_record("s1", "unused", selections=0)
        store = _store_with_records(tmp_path, r)
        analyzer = ExecutionAnalyzer(store)
        ctx = EvolutionContext(
            agent_id="agent-a",
            task_id="t6",
            task_completed=True,
            skill_ids_used=["s1"],
        )
        result = analyzer.analyze_execution(ctx)
        # No suggestions for a skill with zero selections
        assert len(result.suggestions) == 0


# ============================================================================
# 3. SkillEvolver
# ============================================================================


class TestSkillEvolverFix:
    def test_fix_creates_new_version(self, tmp_path: Path) -> None:
        parent = _make_record("p1", "my-skill", directory="skills/my-skill")
        store = _store_with_records(tmp_path, parent)
        evolver = SkillEvolver(store)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.FIX,
            target_skill_ids=["p1"],
            direction="High fallback rate",
        )
        result = evolver.evolve(suggestion)
        assert result.success
        assert result.new_record is not None
        assert "fix_" in result.new_record.id
        assert result.new_record.name == "my-skill"
        assert result.new_record.lineage.origin == SkillOrigin.FIXED
        assert result.new_record.lineage.generation == 1

    def test_fix_deactivates_parent(self, tmp_path: Path) -> None:
        parent = _make_record("p1", "my-skill", is_active=True)
        store = _store_with_records(tmp_path, parent)
        evolver = SkillEvolver(store)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.FIX,
            target_skill_ids=["p1"],
            direction="Fix needed",
        )
        evolver.evolve(suggestion)
        assert store.get_skill_record("p1").is_active is False

    def test_fix_no_parent_returns_error(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        evolver = SkillEvolver(store)
        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.FIX,
            target_skill_ids=[],
        )
        result = evolver.evolve(suggestion)
        assert not result.success
        assert "requires exactly 1 parent" in result.error

    def test_fix_missing_parent_returns_error(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        evolver = SkillEvolver(store)
        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.FIX,
            target_skill_ids=["nonexistent"],
        )
        result = evolver.evolve(suggestion)
        assert not result.success
        assert "not found" in result.error


class TestSkillEvolverDerived:
    def test_derived_creates_enhanced(self, tmp_path: Path) -> None:
        parent = _make_record("p1", "base-skill", directory="skills/base")
        store = _store_with_records(tmp_path, parent)
        evolver = SkillEvolver(store)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.DERIVED,
            target_skill_ids=["p1"],
            direction="Moderate effectiveness",
        )
        result = evolver.evolve(suggestion)
        assert result.success
        assert "enhanced" in result.new_record.name
        assert result.new_record.lineage.origin == SkillOrigin.DERIVED

    def test_derived_keeps_parent_active(self, tmp_path: Path) -> None:
        parent = _make_record("p1", "base", is_active=True)
        store = _store_with_records(tmp_path, parent)
        evolver = SkillEvolver(store)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.DERIVED,
            target_skill_ids=["p1"],
        )
        evolver.evolve(suggestion)
        assert store.get_skill_record("p1").is_active is True

    def test_derived_merge_two_parents(self, tmp_path: Path) -> None:
        p1 = _make_record("p1", "skill-a", generation=0)
        p2 = _make_record("p2", "skill-b", generation=1)
        store = _store_with_records(tmp_path, p1, p2)
        evolver = SkillEvolver(store)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.DERIVED,
            target_skill_ids=["p1", "p2"],
        )
        result = evolver.evolve(suggestion)
        assert result.success
        assert "merged" in result.new_record.name
        assert result.new_record.lineage.generation == 2

    def test_derived_no_parent_returns_error(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        evolver = SkillEvolver(store)
        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.DERIVED,
            target_skill_ids=[],
        )
        result = evolver.evolve(suggestion)
        assert not result.success


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
        result = evolver.evolve(
            suggestion, capture_directory="skills/custom"
        )
        assert result.success
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
        r = _make_record("s1", "bad", selections=100, fallbacks=60)
        store = _store_with_records(tmp_path, r)
        evolver = SkillEvolver(store)

        results = evolver.process_metric_check(min_selections=5)
        assert len(results) == 1
        assert results[0].success

    def test_skips_below_min_selections(self, tmp_path: Path) -> None:
        r = _make_record("s1", "new", selections=3, fallbacks=2)
        store = _store_with_records(tmp_path, r)
        evolver = SkillEvolver(store)

        results = evolver.process_metric_check(min_selections=5)
        assert len(results) == 0


class TestSkillEvolverPruneRecoveredTools:
    def test_prune_removes_recovered(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        evolver = SkillEvolver(store)
        evolver._addressed = {"tool-a": {"s1"}, "tool-b": {"s2"}}
        evolver.prune_recovered_tools({"tool-a"})
        assert "tool-a" in evolver._addressed
        assert "tool-b" not in evolver._addressed


class TestSkillEvolverDiagnose:
    def test_high_fallback_suggests_fix(self) -> None:
        r = _make_record("s1", "x", selections=100, fallbacks=50)
        suggestion = SkillEvolver._diagnose_skill_health(r)
        assert suggestion is not None
        assert suggestion.evolution_type == EvolutionType.FIX

    def test_low_completion_suggests_fix(self) -> None:
        # applied_rate = 50/100 = 0.5 > 0.4, completion_rate = 15/50 = 0.3 < 0.35
        r = _make_record("s1", "x", selections=100, applied=50, completions=15)
        suggestion = SkillEvolver._diagnose_skill_health(r)
        assert suggestion is not None
        assert suggestion.evolution_type == EvolutionType.FIX

    def test_moderate_effective_suggests_derived(self) -> None:
        # effective_rate = 40/100 = 0.4 < 0.55, applied_rate = 30/100 = 0.3 > 0.25
        r = _make_record("s1", "x", selections=100, applied=30, completions=40)
        suggestion = SkillEvolver._diagnose_skill_health(r)
        assert suggestion is not None
        assert suggestion.evolution_type == EvolutionType.DERIVED

    def test_healthy_returns_none(self) -> None:
        r = _make_record("s1", "x", selections=100, applied=80, completions=70, fallbacks=5)
        assert SkillEvolver._diagnose_skill_health(r) is None

    def test_zero_selections_returns_none(self) -> None:
        r = _make_record("s1", "x", selections=0)
        assert SkillEvolver._diagnose_skill_health(r) is None


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
    usage = TokenUsage(total_tokens=total_tokens)
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
        ctx = _make_agent_context(
            total_tokens=110_000, turn=6, last_compaction_turn=3
        )
        # Only 3 turns since last compaction, need 5
        assert guard.should_compact(ctx) is False

    def test_zero_context_window(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(total_tokens=100, context_window=0)
        assert guard.should_compact(ctx) is False


class TestCompactionGuardTruncation:
    def test_needs_truncation_at_90(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(total_tokens=116_000)
        assert guard.needs_truncation(ctx) is True

    def test_no_truncation_below_90(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(total_tokens=100_000)
        assert guard.needs_truncation(ctx) is False


class TestCompactionGuardHardCeiling:
    def test_needs_hard_ceiling_at_95(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(total_tokens=123_000)
        assert guard.needs_hard_ceiling(ctx) is True

    def test_no_hard_ceiling_below_95(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(total_tokens=110_000)
        assert guard.needs_hard_ceiling(ctx) is False


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

    def test_reinject_logs_budget_event(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(
            total_tokens=100_000, l0_content="x", l1_content="y"
        )
        guard.reinject_after_compaction(ctx)
        log = store.get_budget_log("agent-a")
        assert len(log) == 1
        assert log[0]["event_type"] == "compaction"

    def test_reinject_l1_truncated(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        long_l1 = "x" * 5000
        ctx = _make_agent_context(l0_content="id", l1_content=long_l1)
        result = guard.reinject_after_compaction(ctx)
        budget = ContextBudget()
        # l1_max is 3000, so l1 should be truncated
        assert len(result) < len("id\n" + long_l1)


class TestCompactionGuardCheckAndLog:
    def test_returns_alert_level(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(total_tokens=123_000)
        alert = guard.check_and_log(ctx)
        assert alert == "hard_ceiling"

    def test_returns_none_when_healthy(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(total_tokens=50_000)
        assert guard.check_and_log(ctx) is None

    def test_logs_event(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(total_tokens=50_000)
        guard.check_and_log(ctx)
        log = store.get_budget_log("agent-a")
        assert len(log) == 1
        assert log[0]["event_type"] == "budget_check"


class TestCompactionGuardConsecutive:
    def test_reset_clears_counter(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(l0_content="x", l1_content="y")
        guard.reinject_after_compaction(ctx)
        guard.reinject_after_compaction(ctx)
        assert guard.consecutive_compactions == 2
        guard.reset_consecutive_count()
        assert guard.consecutive_compactions == 0

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
            "s1", "great-skill",
            selections=100, completions=90,
            directory="skills/great",
        )
        store = _store_with_records(tmp_path, r)
        promoter = AgentPromoter(store)

        candidates = promoter.find_candidates()
        assert len(candidates) == 1
        assert candidates[0].skill_id == "s1"
        assert candidates[0].effective_rate == 0.9
        assert candidates[0].total_selections == 100

    def test_find_candidates_low_effective_rate(self, tmp_path: Path) -> None:
        # effective_rate = 40/100 = 0.4 < 0.8
        r = _make_record(
            "s1", "mediocre",
            selections=100, completions=40,
            directory="skills/med",
        )
        store = _store_with_records(tmp_path, r)
        promoter = AgentPromoter(store)
        assert promoter.find_candidates() == []

    def test_find_candidates_too_few_selections(self, tmp_path: Path) -> None:
        r = _make_record(
            "s1", "promising",
            selections=30, completions=28,
            directory="skills/prom",
        )
        store = _store_with_records(tmp_path, r)
        promoter = AgentPromoter(store)
        assert promoter.find_candidates() == []

    def test_find_candidates_no_directory(self, tmp_path: Path) -> None:
        r = _make_record(
            "s1", "naked-skill",
            selections=100, completions=90,
            directory="",
        )
        store = _store_with_records(tmp_path, r)
        promoter = AgentPromoter(store)
        assert promoter.find_candidates() == []


class TestPromotionPromote:
    def test_promote_creates_files(self, tmp_path: Path) -> None:
        r = _make_record(
            "s1", "great-skill",
            selections=100, completions=90,
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

    def test_promote_manifest_content(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        agents_dir = tmp_path / "agents"
        promoter = AgentPromoter(store, agents_root=agents_dir)

        candidate = PromotionCandidate(
            skill_id="s1",
            skill_name="test-skill",
            effective_rate=0.85,
            total_selections=60,
            directory="skills/test",
            reason="test",
        )
        result = promoter.promote(candidate)
        manifest = Path(result.manifest_path).read_text()
        assert 'name = "test-skill"' in manifest
        assert 'type = "atomic"' in manifest
        assert "from_skill" in manifest

    def test_promote_entry_point_content(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        agents_dir = tmp_path / "agents"
        promoter = AgentPromoter(store, agents_root=agents_dir)

        candidate = PromotionCandidate(
            skill_id="s1",
            skill_name="my-skill",
            effective_rate=0.9,
            total_selections=100,
            directory="skills/my",
            reason="test",
        )
        result = promoter.promote(candidate)
        entry = Path(result.entry_point_path).read_text()
        assert "async def run" in entry
        assert "my-skill" in entry

    def test_promote_skill_md(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        agents_dir = tmp_path / "agents"
        promoter = AgentPromoter(store, agents_root=agents_dir)

        candidate = PromotionCandidate(
            skill_id="s1",
            skill_name="promoted",
            effective_rate=0.88,
            total_selections=70,
            directory="skills/promoted",
            reason="test",
        )
        result = promoter.promote(candidate)
        skill_path = Path(result.agent_directory) / "SKILL.md"
        assert skill_path.exists()
        content = skill_path.read_text()
        assert "promoted" in content


# ============================================================================
# 6. HealthChecker
# ============================================================================


class TestHealthCheckerCheckHealth:
    def test_healthy_skill(self) -> None:
        r = _make_record(
            "s1", "good",
            selections=100, applied=80, completions=70, fallbacks=5,
        )
        # With a dummy store (check_health doesn't use store)
        from unittest.mock import MagicMock

        checker = HealthChecker(MagicMock())
        suggestions = checker.check_health(r)
        assert len(suggestions) == 0

    def test_high_fallback_triggers_fix(self) -> None:
        # fallback_rate = 50/100 = 0.5 > 0.4
        r = _make_record("s1", "x", selections=100, fallbacks=50)
        from unittest.mock import MagicMock

        checker = HealthChecker(MagicMock())
        suggestions = checker.check_health(r)
        fix_suggestions = [
            s for s in suggestions if s.evolution_type == EvolutionType.FIX
        ]
        assert len(fix_suggestions) >= 1
        assert any("fallback" in s.direction.lower() for s in fix_suggestions)

    def test_low_completion_triggers_fix(self) -> None:
        # applied_rate = 50/100 = 0.5 > 0.4, completion_rate = 15/50 = 0.3 < 0.35
        r = _make_record("s1", "x", selections=100, applied=50, completions=15)
        from unittest.mock import MagicMock

        checker = HealthChecker(MagicMock())
        suggestions = checker.check_health(r)
        fix_suggestions = [
            s for s in suggestions if s.evolution_type == EvolutionType.FIX
        ]
        assert len(fix_suggestions) >= 1

    def test_moderate_effective_triggers_derived(self) -> None:
        # effective_rate = 40/100 = 0.4 < 0.55, applied_rate = 30/100 = 0.3 > 0.25
        r = _make_record("s1", "x", selections=100, applied=30, completions=40)
        from unittest.mock import MagicMock

        checker = HealthChecker(MagicMock())
        suggestions = checker.check_health(r)
        derived = [
            s for s in suggestions if s.evolution_type == EvolutionType.DERIVED
        ]
        assert len(derived) >= 1

    def test_zero_selections_no_suggestions(self) -> None:
        r = _make_record("s1", "x", selections=0)
        from unittest.mock import MagicMock

        checker = HealthChecker(MagicMock())
        assert checker.check_health(r) == []


class TestHealthCheckerDiagnoseAll:
    def test_diagnose_all(self, tmp_path: Path) -> None:
        r1 = _make_record("s1", "healthy", selections=100, applied=80, completions=70, fallbacks=5)
        r2 = _make_record("s2", "unhealthy", selections=100, fallbacks=60)
        store = _store_with_records(tmp_path, r1, r2)
        checker = HealthChecker(store)

        reports = checker.diagnose_all()
        assert len(reports) == 2
        assert reports["s1"].is_healthy is True
        assert reports["s2"].is_healthy is False

    def test_diagnose_all_metrics(self, tmp_path: Path) -> None:
        r = _make_record("s1", "x", selections=100, applied=50, completions=30, fallbacks=10)
        store = _store_with_records(tmp_path, r)
        checker = HealthChecker(store)

        reports = checker.diagnose_all()
        metrics = reports["s1"].metrics
        assert metrics["applied_rate"] == 0.5
        assert metrics["effective_rate"] == 0.3
        assert metrics["fallback_rate"] == 0.1

    def test_diagnose_all_zero_selections_metrics(self, tmp_path: Path) -> None:
        r = _make_record("s1", "x", selections=0)
        store = _store_with_records(tmp_path, r)
        checker = HealthChecker(store)

        reports = checker.diagnose_all()
        metrics = reports["s1"].metrics
        assert metrics["applied_rate"] == 0.0
        assert metrics["completion_rate"] == 0.0


class TestHealthCheckerGetUnhealthy:
    def test_filters_to_unhealthy(self, tmp_path: Path) -> None:
        r1 = _make_record("s1", "healthy", selections=100, applied=80, completions=70, fallbacks=5)
        r2 = _make_record("s2", "unhealthy", selections=100, fallbacks=60)
        store = _store_with_records(tmp_path, r1, r2)
        checker = HealthChecker(store)

        unhealthy = checker.get_unhealthy()
        assert len(unhealthy) == 1
        assert "s2" in unhealthy
        assert "s1" not in unhealthy


class TestHealthCheckerGetSummary:
    def test_summary_counts(self, tmp_path: Path) -> None:
        r1 = _make_record("s1", "healthy", selections=100, applied=80, completions=70, fallbacks=5)
        r2 = _make_record("s2", "bad", selections=100, fallbacks=60)
        r3 = _make_record("s3", "moderate", selections=100, applied=30, completions=40)
        store = _store_with_records(tmp_path, r1, r2, r3)
        checker = HealthChecker(store)

        summary = checker.get_health_summary()
        assert summary["total_skills"] == 3
        assert summary["healthy"] == 1
        assert summary["unhealthy"] == 2
        assert "bad" in summary["unhealthy_skills"]
        assert "moderate" in summary["unhealthy_skills"]

    def test_summary_empty_store(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        checker = HealthChecker(store)
        summary = checker.get_health_summary()
        assert summary["total_skills"] == 0
        assert summary["healthy"] == 0
        assert summary["unhealthy"] == 0


class TestHealthReportSummary:
    def test_healthy_summary(self) -> None:
        report = HealthReport(
            skill_id="s1",
            skill_name="test",
            is_healthy=True,
            suggestions=[],
            metrics={"effective_rate": 0.9},
        )
        text = report.summary()
        assert "[HEALTHY]" in text
        assert "test" in text

    def test_unhealthy_summary_with_suggestions(self) -> None:
        report = HealthReport(
            skill_id="s2",
            skill_name="bad-skill",
            is_healthy=False,
            suggestions=[
                EvolutionSuggestion(
                    evolution_type=EvolutionType.FIX,
                    target_skill_ids=["s2"],
                    direction="High fallback rate",
                ),
            ],
            metrics={"fallback_rate": 0.6},
        )
        text = report.summary()
        assert "[UNHEALTHY]" in text
        assert "bad-skill" in text
        assert "fix" in text.lower()
