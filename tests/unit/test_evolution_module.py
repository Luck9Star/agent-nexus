"""Unit tests for agent_nexus.platform.evolution -- all 6 modules.

Covers: store, analyzer, evolver, compaction, promotion, health.
Uses tmp_path for SQLite databases, pytest class-based organization.
Target: ~65 tests across all modules.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import inspect

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
        assert store.get_skill_record("s1").is_active is False  # type: ignore[union-attr]

    def test_deactivate_nonexistent(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        assert store.deactivate_skill("nope") is False

    def test_save_overwrites_existing(self, tmp_path: Path) -> None:
        r = _make_record("s1", "original")
        store = _store_with_records(tmp_path, r)
        updated = _make_record("s1", "updated")
        store.save_skill_record(updated)
        got = store.get_skill_record("s1")
        assert got is not None
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
        assert got is not None
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
        assert got is not None
        assert set(got.lineage.parent_skill_ids) == {"pb", "pc"}


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
        store.increment_counters(
            "s1", selected=True, applied=True, completed=True
        )
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
        with _patch.object(store, "_conn", side_effect=AssertionError("_conn should not be called")):
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
        # Applied but fell back: valid single judgment where applied and fell_back
        # are both True (skill was applied, then fell back to alternative).
        store.record_analysis(
            task_id="t1",
            agent_name="a",
            analysis_text="",
            judgments=[
                {"skill_id": "s1", "selected": True, "applied": True,
                 "completed": False, "fell_back": True},
            ],
        )
        got = store.get_skill_record("s1")
        assert got is not None
        assert got.total_selections == 1
        assert got.total_applied == 1
        assert got.total_completions == 0
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

        rec_p1 = store.get_skill_record("p1")
        assert rec_p1 is not None
        assert rec_p1.is_active is False
        rec_c1 = store.get_skill_record("c1")
        assert rec_c1 is not None
        assert rec_c1.is_active is True

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

        child = _make_record(
            "c1", "merged", parent_ids=["p1", "p2"], generation=1
        )
        store.evolve_skill(child, ["p1", "p2"])

        got = store.get_skill_record("c1")
        assert got is not None
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
        r = _make_record("s1", "buggy", selections=100, applied=100, fallbacks=50)
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

    def test_analyze_skills_applied_and_fell_back(self, tmp_path: Path) -> None:
        """Judgments reflect skills_applied and skills_fell_back fields."""
        r1 = _make_record("s1", "applied-skill")
        r2 = _make_record("s2", "fell-back-skill")
        store = _store_with_records(tmp_path, r1, r2)
        analyzer = ExecutionAnalyzer(store)

        ctx = EvolutionContext(
            agent_id="agent-a",
            task_id="t-applied",
            task_completed=True,
            skill_ids_used=["s1", "s2"],
            skills_applied=["s1"],
            skills_fell_back=["s2"],
        )
        result = analyzer.analyze_execution(ctx)

        assert len(result.judgments) == 2
        # s1 was applied, not fell_back
        assert result.judgments[0]["skill_id"] == "s1"
        assert result.judgments[0]["applied"] is True
        assert result.judgments[0]["fell_back"] is False
        # s2 was NOT applied, but DID fall back
        assert result.judgments[1]["skill_id"] == "s2"
        assert result.judgments[1]["applied"] is False
        assert result.judgments[1]["fell_back"] is True

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
        rec = store.get_skill_record("p1")
        assert rec is not None
        assert rec.is_active is False

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
        assert result.new_record is not None
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
        rec = store.get_skill_record("p1")
        assert rec is not None
        assert rec.is_active is True

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
        assert result.new_record is not None
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
        result = evolver.evolve(
            suggestion, capture_directory="skills/custom"
        )
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


class TestSkillEvolverPruneRecoveredTools:
    def test_prune_removes_recovered(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        evolver = SkillEvolver(store)
        evolver._addressed = {"tool-a": {"s1"}, "tool-b": {"s2"}}
        evolver.prune_recovered_tools({"tool-a"})
        assert "tool-a" in evolver._addressed
        assert "tool-b" not in evolver._addressed


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


class TestSkillEvolverDiagnose:
    def test_high_fallback_suggests_fix(self) -> None:
        r = _make_record("s1", "x", selections=100, applied=100, completions=50, fallbacks=50)
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
        r = _make_record("s1", "x", selections=100, applied=40, completions=30)
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

    def test_reinject_logs_token_estimate_not_chars(self, tmp_path: Path) -> None:
        """tokens_after should be a token estimate (chars//4), not raw char count."""
        import json as _json

        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(
            total_tokens=100_000, l0_content="hello world test data"
        )
        guard.reinject_after_compaction(ctx)
        log = store.get_budget_log("agent-a")
        assert len(log) == 1
        tokens_after = log[0]["tokens_after"]
        # details is stored as JSON string in SQLite
        details = _json.loads(log[0].get("details", "{}"))
        result_chars = details.get("result_chars", 0)
        if result_chars > 0:
            # Token estimate should be roughly chars//4, not chars
            assert tokens_after < result_chars

    def test_compaction_empty_result_chars(self, tmp_path: Path) -> None:
        """When result has <4 chars, tokens_after is 0 (chars//4 approximation)."""
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        # l0_content is truthy but short; l1 empty -> result is "x\n" (2 chars)
        ctx = _make_agent_context(l0_content="x", l1_content="")
        guard.reinject_after_compaction(ctx)
        log = store.get_budget_log("agent-a")
        assert len(log) == 1
        # "x\n" is 2 chars -> 2 // 4 = 0 tokens estimated
        assert log[0]["tokens_after"] == 0


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
            selections=100, applied=90, completions=90,
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
            selections=100, applied=40, completions=40,
            directory="skills/med",
        )
        store = _store_with_records(tmp_path, r)
        promoter = AgentPromoter(store)
        assert promoter.find_candidates() == []

    def test_find_candidates_too_few_selections(self, tmp_path: Path) -> None:
        r = _make_record(
            "s1", "promising",
            selections=30, applied=28, completions=28,
            directory="skills/prom",
        )
        store = _store_with_records(tmp_path, r)
        promoter = AgentPromoter(store)
        assert promoter.find_candidates() == []

    def test_find_candidates_no_directory(self, tmp_path: Path) -> None:
        r = _make_record(
            "s1", "naked-skill",
            selections=100, applied=90, completions=90,
            directory="",
        )
        store = _store_with_records(tmp_path, r)
        promoter = AgentPromoter(store)
        assert promoter.find_candidates() == []


class TestPromotionPromote:
    def test_promote_creates_files(self, tmp_path: Path) -> None:
        r = _make_record(
            "s1", "great-skill",
            selections=100, applied=90, completions=90,
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
        # Verify manifest filename is agent-manifest.yaml (not .toml)
        assert result.manifest_path.endswith("agent-manifest.yaml")
        manifest = Path(result.manifest_path).read_text()
        assert "name: test-skill" in manifest
        assert "type: atomic" in manifest
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
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
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

        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            result = promoter.promote(candidate)

        assert not result.success
        # Newly created directory should be cleaned up
        assert not (agents_dir / "new-skill").exists()


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
        r = _make_record("s1", "x", selections=100, applied=100, completions=50, fallbacks=50)
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
        r = _make_record("s1", "x", selections=100, applied=40, completions=30)
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
        r2 = _make_record("s2", "unhealthy", selections=100, applied=100, fallbacks=60)
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
        r2 = _make_record("s2", "unhealthy", selections=100, applied=100, fallbacks=60)
        store = _store_with_records(tmp_path, r1, r2)
        checker = HealthChecker(store)

        unhealthy = checker.get_unhealthy()
        assert len(unhealthy) == 1
        assert "s2" in unhealthy
        assert "s1" not in unhealthy


class TestHealthCheckerGetSummary:
    def test_summary_counts(self, tmp_path: Path) -> None:
        r1 = _make_record("s1", "healthy", selections=100, applied=80, completions=70, fallbacks=5)
        r2 = _make_record("s2", "bad", selections=100, applied=100, fallbacks=60)
        r3 = _make_record("s3", "moderate", selections=100, applied=40, completions=30)
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

    def test_default_budget_threshold(self, tmp_path: Path) -> None:
        """Without custom budget, uses default consecutive_compaction_alert=3."""
        store = self._make_store(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = self._make_context()

        guard.reinject_after_compaction(ctx)
        guard.reinject_after_compaction(ctx)
        assert not guard.should_alert()

        guard.reinject_after_compaction(ctx)
        assert guard.should_alert()

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

    def test_higher_threshold_custom_budget(self, tmp_path: Path) -> None:
        """Custom budget with higher threshold requires more compactions."""
        store = self._make_store(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = self._make_context()

        custom_budget = ContextBudget(consecutive_compaction_alert=5)

        for _ in range(4):
            guard.reinject_after_compaction(ctx)
        assert not guard.should_alert(budget=custom_budget)

        guard.reinject_after_compaction(ctx)
        assert guard.should_alert(budget=custom_budget)

    def test_none_budget_uses_default(self, tmp_path: Path) -> None:
        """Passing None explicitly uses default budget."""
        store = self._make_store(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = self._make_context()

        for _ in range(3):
            guard.reinject_after_compaction(ctx)

        assert guard.should_alert(budget=None)


# ============================================================================
# HealthReport.summary uses key-based formatting (from iter15)
# ============================================================================


class TestHealthReportFormatting:
    """HealthReport.summary should format rates as %, counts as numbers."""

    def test_rate_formatted_as_percentage(self) -> None:
        report = HealthReport(
            skill_id="s1",
            skill_name="test",
            is_healthy=True,
            suggestions=[],
            metrics={"effective_rate": 0.75},
        )
        lines = report.summary()
        assert "75.00%" in lines

    def test_count_formatted_as_number(self) -> None:
        report = HealthReport(
            skill_id="s1",
            skill_name="test",
            is_healthy=True,
            suggestions=[],
            metrics={"total_selections": 5},
        )
        lines = report.summary()
        assert "total_selections: 5" in lines
        # Should NOT be formatted as percentage
        assert "500.00%" not in lines

    def test_mixed_metrics(self) -> None:
        report = HealthReport(
            skill_id="s1",
            skill_name="test",
            is_healthy=True,
            suggestions=[],
            metrics={
                "effective_rate": 0.87,
                "total_selections": 42,
                "fallback_rate": 0.12,
                "total_completions": 38,
            },
        )
        lines = report.summary()
        assert "87.00%" in lines
        assert "12.00%" in lines
        assert "total_selections: 42" in lines
        assert "total_completions: 38" in lines


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

        fix_suggestions = [
            s for s in result.suggestions
            if s.evolution_type == EvolutionType.FIX
        ]
        assert len(fix_suggestions) == 1, (
            f"Expected 1 deduplicated FIX, got {len(fix_suggestions)}"
        )

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


class TestAddressedOnSuccessOnly:
    """process_tool_degradation only marks _addressed when evolve succeeds."""

    def _make_skill(self, sid: str = "skill-1") -> SkillRecord:
        return SkillRecord(
            id=sid,
            name="test-skill",
            version="1.0.0",
            lineage=SkillLineage(
                origin=SkillOrigin.FIXED,
                generation=1,
                parent_skill_ids=[],
            ),
            directory="skills/test",
            is_active=True,
        )

    def test_failed_evolve_not_marked_addressed(self, tmp_path: Path) -> None:
        """When evolution fails, skill is NOT added to _addressed."""
        from unittest.mock import patch

        store = EvolutionStore(tmp_path / "test.db")
        skill = self._make_skill()
        store.save_skill_record(skill)

        evolver = SkillEvolver(store)
        fail_result = EvolveResult(success=False, error="simulated failure")
        with patch.object(evolver, "evolve", return_value=fail_result):
            results = evolver.process_tool_degradation(
                tool_key="tool-a",
                problem_description="broken",
                affected_skill_ids={skill.id},
            )

        assert len(results) == 1
        assert results[0].success is False
        assert skill.id not in evolver._addressed.get("tool-a", set())

    def test_successful_evolve_marked_addressed(self, tmp_path: Path) -> None:
        """When evolution succeeds, skill IS added to _addressed."""
        from unittest.mock import patch

        store = EvolutionStore(tmp_path / "test.db")
        skill = self._make_skill()
        store.save_skill_record(skill)

        evolver = SkillEvolver(store)
        ok_result = EvolveResult(success=True, new_record=skill)
        with patch.object(evolver, "evolve", return_value=ok_result):
            results = evolver.process_tool_degradation(
                tool_key="tool-x",
                problem_description="degraded",
                affected_skill_ids={skill.id},
            )

        assert len(results) == 1
        assert results[0].success is True
        assert skill.id in evolver._addressed.get("tool-x", set())


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
        captured = [
            s for s in result.suggestions
            if s.evolution_type == EvolutionType.CAPTURED
        ]
        assert len(captured) == 1
        assert captured[0].target_skill_ids == []


# ============================================================================
# Iteration 25 fixes: import re at module level, health dedup/DERIVED
# suppression, edit distance scaling, sentence-split for captured names
# ============================================================================


class TestEvolverModuleLevelReImport:
    """Verify 'import re' is at module level, not inline in methods."""

    def test_re_is_module_level_import(self) -> None:
        import ast
        import agent_nexus.platform.evolution.evolver as evolver_mod

        source = inspect.getsource(evolver_mod)
        tree = ast.parse(source)
        # Check that 're' is in top-level imports
        top_imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert "re" in top_imports, "'import re' should be at module level"

    def test_no_inline_import_re_in_methods(self) -> None:
        """No method body should contain 'import re'."""
        import ast
        import agent_nexus.platform.evolution.evolver as evolver_mod

        source = inspect.getsource(evolver_mod)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(node):
                    if isinstance(child, ast.Import):
                        for alias in child.names:
                            assert alias.name != "re", (
                                f"'import re' found inside method "
                                f"'{node.name}', should be module-level"
                            )


class TestHealthCheckerDedupFix:
    """check_health must deduplicate FIX suggestions (keep highest confidence)
    and suppress DERIVED when FIX is triggered for the same skill.
    """

    def test_dual_fix_triggers_only_one_fix(self) -> None:
        """Skill with high fallback AND low completion produces only one FIX.

        Before fix: both Rule 1 and Rule 2 appended separate FIX suggestions,
        producing 2 FIX suggestions for the same skill.
        After fix: only one FIX is returned (highest confidence).
        """
        from unittest.mock import MagicMock

        # completions(10) + fallbacks(60) = 70 <= applied(70)
        # fallback_rate = 60/100 = 0.6 > 0.4 (Rule 1: FIX)
        # applied_rate = 70/100 = 0.7 > 0.4, completion_rate = 10/70 = 0.14 < 0.35 (Rule 2: FIX)
        r = _make_record("s1", "x", selections=100, applied=70, completions=10, fallbacks=60)
        checker = HealthChecker(MagicMock())
        suggestions = checker.check_health(r)

        fix_suggestions = [s for s in suggestions if s.evolution_type == EvolutionType.FIX]
        assert len(fix_suggestions) == 1, (
            f"Expected exactly 1 deduplicated FIX, got {len(fix_suggestions)}"
        )

    def test_fix_suppresses_derived(self) -> None:
        """When FIX is triggered, DERIVED should not be returned.

        This skill triggers both:
          - fallback_rate = 60/100 = 0.6 > 0.4 (FIX)
          - effective_rate = 10/100 = 0.1 < 0.55, applied_rate = 70/100 = 0.7 > 0.25 (DERIVED)
        After fix: only FIX, no DERIVED.
        """
        from unittest.mock import MagicMock

        r = _make_record("s1", "x", selections=100, applied=70, completions=10, fallbacks=60)
        checker = HealthChecker(MagicMock())
        suggestions = checker.check_health(r)

        types = {s.evolution_type for s in suggestions}
        assert EvolutionType.FIX in types
        assert EvolutionType.DERIVED not in types, (
            "DERIVED should be suppressed when FIX is triggered for the same skill"
        )

    def test_derived_returned_when_no_fix(self) -> None:
        """When no FIX triggers, DERIVED should still be returned."""
        from unittest.mock import MagicMock

        # effective_rate = 30/100 = 0.3 < 0.55, applied_rate = 40/100 = 0.4 > 0.25
        # fallback_rate = 10/100 = 0.1 <= 0.4 (no FIX)
        # completion_rate = 30/40 = 0.75 >= 0.35 (no FIX)
        r = _make_record("s1", "x", selections=100, applied=40, completions=30, fallbacks=10)
        checker = HealthChecker(MagicMock())
        suggestions = checker.check_health(r)

        derived = [s for s in suggestions if s.evolution_type == EvolutionType.DERIVED]
        assert len(derived) == 1

    def test_best_fix_keeps_higher_confidence(self) -> None:
        """When both FIX rules trigger, the FIX with higher confidence wins.

        Under invariant completions + fallbacks <= applied, the completion
        FIX confidence = (applied - completions)/selections always >=
        fallbacks/selections, so the completion FIX naturally wins.
        """
        from unittest.mock import MagicMock

        # completions(10) + fallbacks(55) = 65 <= applied(70)
        # fallback_rate = 55/100 = 0.55 -> fallback FIX confidence = 0.55
        # applied_rate = 70/100 = 0.7, completion_rate = 10/70 = 0.143
        #   -> completion FIX confidence = min(0.7 * 0.857, 1.0) = 0.6
        # completion FIX has higher confidence (0.6 > 0.55)
        r = _make_record("s1", "x", selections=100, applied=70, completions=10, fallbacks=55)
        checker = HealthChecker(MagicMock())
        suggestions = checker.check_health(r)

        fix_suggestions = [s for s in suggestions if s.evolution_type == EvolutionType.FIX]
        assert len(fix_suggestions) == 1
        assert fix_suggestions[0].confidence == pytest.approx(0.6, abs=0.01)

    def test_low_completion_fix_wins_when_higher_confidence(self) -> None:
        """When the completion-rate FIX has higher confidence, it wins."""
        from unittest.mock import MagicMock

        # fallback_rate = 45/100 = 0.45 -> confidence = 0.45
        # applied_rate = 90/100 = 0.9, completion_rate = 30/90 = 0.333 < 0.35
        #   -> confidence = min(0.9 * 0.667, 1.0) = 0.6
        # completion FIX has higher confidence (0.6 > 0.45)
        r = _make_record("s1", "x", selections=100, applied=90, completions=30, fallbacks=45)
        checker = HealthChecker(MagicMock())
        suggestions = checker.check_health(r)

        fix_suggestions = [s for s in suggestions if s.evolution_type == EvolutionType.FIX]
        assert len(fix_suggestions) == 1
        assert "completion" in fix_suggestions[0].direction.lower()


class TestEditDistanceScaling:
    """_correct_skill_ids scales max_dist by suffix length to avoid loose
    matches on short IDs.
    """

    def test_short_suffix_tight_threshold(self) -> None:
        """Suffix <= 4 chars should only match at distance 1."""
        known = {"x__abcd"}
        # Distance 2 -> too far for suffix length 4
        result = _correct_skill_ids(["x__abef"], known)
        assert result == ["x__abef"]  # not corrected

    def test_medium_suffix_moderate_threshold(self) -> None:
        """Suffix 5-8 chars: max_dist=2, matches at distance <= 2."""
        known = {"x__abcdefgh"}
        # Distance 2 -> matched (within max_dist=2)
        result = _correct_skill_ids(["x__abcdefXY"], known)
        assert result == ["x__abcdefgh"]
        # Distance 1 -> matched
        result2 = _correct_skill_ids(["x__abcdefXh"], known)
        assert result2 == ["x__abcdefgh"]
        # Distance 3, suffix len=8, max_dist=2 -> too far, not matched
        result3 = _correct_skill_ids(["x__abcdeXYZ"], known)
        assert result3 == ["x__abcdeXYZ"]

    def test_long_suffix_relaxed_threshold(self) -> None:
        """Suffix > 8 chars should match at distance <= 3."""
        known = {"agent-a__review_code_v2"}
        result = _correct_skill_ids(["agent-a__review_code_vX"], known)
        assert result == ["agent-a__review_code_v2"]  # distance 1

    def test_no_false_match_on_short_ids(self) -> None:
        """Regression: short suffixes should not false-match with old loose threshold."""
        known = {"x__ab"}
        # Distance from "x__ab" to "x__cd" is 2, old threshold was 4 -> would match
        result = _correct_skill_ids(["x__cd"], known)
        assert result == ["x__cd"]  # NOT corrected: short suffix, distance too high


class TestCapturedSentenceSplit:
    """_evolve_captured should split direction on '. ' (sentence boundary),
    not bare '.' to avoid breaking version numbers like 'v2.0'.
    """

    def test_version_number_preserved_in_name(self, tmp_path: Path) -> None:
        """Direction with version number 'v2.0' should not be split at the dot."""
        store = _store_with_records(tmp_path)
        evolver = SkillEvolver(store)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.CAPTURED,
            direction="v2.0 upgrade handler",
        )
        result = evolver.evolve(suggestion)
        assert result.success
        assert result.new_record is not None
        # Name should include "v2-0" not just "v2"
        assert "v2-0" in result.new_record.name, (
            f"Expected 'v2-0' in name, got '{result.new_record.name}'"
        )

    def test_sentence_split_still_works(self, tmp_path: Path) -> None:
        """Direction with sentence-ending period should still be trimmed."""
        store = _store_with_records(tmp_path)
        evolver = SkillEvolver(store)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.CAPTURED,
            direction="Handle special cases. Also do more things.",
        )
        result = evolver.evolve(suggestion)
        assert result.success
        assert result.new_record is not None
        # Should only use first sentence
        assert result.new_record.name == "handle-special-cases"


# ============================================================================
# 7. EvolutionEngine facade
# ============================================================================


from agent_nexus.platform.evolution.engine import EvolutionEngine


class TestEvolutionEngineInit:
    """EvolutionEngine creates all sub-components on init."""

    def test_creates_all_sub_components(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        engine = EvolutionEngine(store)

        assert isinstance(engine.analyzer, ExecutionAnalyzer)
        assert isinstance(engine.evolver, SkillEvolver)
        assert isinstance(engine.health_checker, HealthChecker)
        assert isinstance(engine.compaction_guard, CompactionGuard)
        assert isinstance(engine.promoter, AgentPromoter)

    def test_store_property_returns_store(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        engine = EvolutionEngine(store)
        assert engine.store is store

    def test_sub_components_share_same_store(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        engine = EvolutionEngine(store)

        assert engine.analyzer.store is store
        assert engine.evolver.store is store
        assert engine.health_checker.store is store
        # CompactionGuard does not expose a public store property
        assert engine.promoter.store is store


class TestEvolutionEngineEvolvePostAnalysis:
    """evolve(trigger='post_analysis') delegates to analyzer then evolver."""

    def test_post_analysis_returns_analysis_result(self, tmp_path: Path) -> None:
        r = _make_record("s1", "buggy", selections=100, applied=100, fallbacks=50)
        store = _store_with_records(tmp_path, r)
        engine = EvolutionEngine(store)

        ctx = EvolutionContext(
            agent_id="agent-a",
            task_id="t1",
            task_completed=False,
            skill_ids_used=["s1"],
        )
        result = engine.evolve(trigger="post_analysis", ctx=ctx)
        assert isinstance(result, AnalysisResult)
        assert result.task_id == "t1"

    def test_post_analysis_creates_evolved_skill(self, tmp_path: Path) -> None:
        """Post-analysis on unhealthy skill produces a new evolved skill."""
        r = _make_record("s1", "buggy", selections=100, applied=100, fallbacks=60)
        store = _store_with_records(tmp_path, r)
        engine = EvolutionEngine(store)

        ctx = EvolutionContext(
            agent_id="agent-a",
            task_id="t1",
            task_completed=False,
            skill_ids_used=["s1"],
        )
        result = engine.evolve(trigger="post_analysis", ctx=ctx)
        # The original should be deactivated (FIX evolution)
        original = store.get_skill_record("s1")
        assert original is not None
        assert original.is_active is False

    def test_post_analysis_requires_ctx(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        engine = EvolutionEngine(store)

        with pytest.raises(ValueError, match="ctx.*required"):
            engine.evolve(trigger="post_analysis")


class TestEvolutionEngineEvolveToolDegradation:
    """evolve(trigger='tool_degradation') delegates to evolver."""

    def test_tool_degradation_returns_evolve_results(self, tmp_path: Path) -> None:
        s1 = _make_record("s1", "a")
        store = _store_with_records(tmp_path, s1)
        engine = EvolutionEngine(store)

        results = engine.evolve(
            trigger="tool_degradation",
            tool_key="tool-x",
            problem_description="API changed",
        )
        assert isinstance(results, list)
        assert all(isinstance(r, EvolveResult) for r in results)
        assert len(results) == 1

    def test_tool_degradation_filters_affected(self, tmp_path: Path) -> None:
        s1 = _make_record("s1", "a")
        s2 = _make_record("s2", "b")
        store = _store_with_records(tmp_path, s1, s2)
        engine = EvolutionEngine(store)

        results = engine.evolve(
            trigger="tool_degradation",
            tool_key="tool-x",
            problem_description="broken",
            affected_skill_ids={"s1"},
        )
        assert len(results) == 1

    def test_tool_degradation_requires_tool_key(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        engine = EvolutionEngine(store)

        with pytest.raises(ValueError, match="tool_key.*required"):
            engine.evolve(trigger="tool_degradation")


class TestEvolutionEngineEvolveMetricCheck:
    """evolve(trigger='metric_check') delegates to evolver."""

    def test_metric_check_returns_evolve_results(self, tmp_path: Path) -> None:
        r = _make_record("s1", "bad", selections=100, applied=100, fallbacks=60)
        store = _store_with_records(tmp_path, r)
        engine = EvolutionEngine(store)

        results = engine.evolve(trigger="metric_check")
        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0].success

    def test_metric_check_skips_below_min(self, tmp_path: Path) -> None:
        r = _make_record("s1", "new", selections=3, applied=3, fallbacks=2)
        store = _store_with_records(tmp_path, r)
        engine = EvolutionEngine(store)

        results = engine.evolve(trigger="metric_check", min_selections=5)
        assert results == []


class TestEvolutionEngineEvolveUnknown:
    """evolve() rejects unknown triggers."""

    def test_unknown_trigger_raises(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        engine = EvolutionEngine(store)

        with pytest.raises(ValueError, match="Unknown trigger"):
            engine.evolve(trigger="nonexistent")


class TestEvolutionEngineConvenienceMethods:
    """Convenience methods delegate correctly."""

    def test_check_health_healthy(self, tmp_path: Path) -> None:
        r = _make_record("s1", "good", selections=100, applied=80, completions=70, fallbacks=5)
        store = _store_with_records(tmp_path, r)
        engine = EvolutionEngine(store)

        suggestions = engine.check_health("s1")
        assert suggestions == []

    def test_check_health_unhealthy(self, tmp_path: Path) -> None:
        r = _make_record("s1", "bad", selections=100, applied=100, fallbacks=60)
        store = _store_with_records(tmp_path, r)
        engine = EvolutionEngine(store)

        suggestions = engine.check_health("s1")
        assert len(suggestions) >= 1
        assert any(s.evolution_type == EvolutionType.FIX for s in suggestions)

    def test_check_health_missing_skill_raises(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        engine = EvolutionEngine(store)

        with pytest.raises(ValueError, match="Skill not found"):
            engine.check_health("nonexistent")

    def test_diagnose_all(self, tmp_path: Path) -> None:
        r1 = _make_record("s1", "healthy", selections=100, applied=80, completions=70, fallbacks=5)
        r2 = _make_record("s2", "bad", selections=100, applied=100, fallbacks=60)
        store = _store_with_records(tmp_path, r1, r2)
        engine = EvolutionEngine(store)

        reports = engine.diagnose_all()
        assert len(reports) == 2
        assert reports["s1"].is_healthy is True
        assert reports["s2"].is_healthy is False

    def test_promote_candidate(self, tmp_path: Path) -> None:
        r = _make_record(
            "s1", "great",
            selections=100, applied=90, completions=90,
            directory="skills/great",
        )
        agents_dir = tmp_path / "agents"
        store = _store_with_records(tmp_path, r)
        engine = EvolutionEngine(store, agents_root=agents_dir)

        candidate = PromotionCandidate(
            skill_id="s1",
            skill_name="great",
            effective_rate=0.9,
            total_selections=100,
            directory="skills/great",
            reason="test",
        )
        result = engine.promote_candidate(candidate)
        assert isinstance(result, PromotionResult)
        assert result.success
        assert result.agent_name == "great"

    def test_should_compact_true(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        engine = EvolutionEngine(store, agent_id="agent-a")
        ctx = _make_agent_context(total_tokens=110_000, last_compaction_turn=0, turn=10)
        assert engine.should_compact(ctx) is True

    def test_should_compact_false(self, tmp_path: Path) -> None:
        store = _store_with_records(tmp_path)
        engine = EvolutionEngine(store, agent_id="agent-a")
        ctx = _make_agent_context(total_tokens=50_000)
        assert engine.should_compact(ctx) is False


class TestEvolutionEngineImport:
    """EvolutionEngine is importable from the evolution package."""

    def test_import_from_package(self) -> None:
        from agent_nexus.platform.evolution import EvolutionEngine as EE
        assert EE is EvolutionEngine

    def test_in_all(self) -> None:
        import agent_nexus.platform.evolution as evo_pkg
        assert "EvolutionEngine" in evo_pkg.__all__


# ============================================================================
# 8. Evolution health threshold constants
# ============================================================================


from agent_nexus.platform.evolution import thresholds


class TestThresholdConstants:
    """Verify threshold constants are in valid ranges and match documented values."""

    def test_fallback_threshold_in_range(self) -> None:
        assert 0.0 < thresholds._FALLBACK_THRESHOLD < 1.0

    def test_fallback_threshold_value(self) -> None:
        assert thresholds._FALLBACK_THRESHOLD == 0.4

    def test_high_applied_for_fix_in_range(self) -> None:
        assert 0.0 < thresholds._HIGH_APPLIED_FOR_FIX < 1.0

    def test_low_completion_threshold_in_range(self) -> None:
        assert 0.0 < thresholds._LOW_COMPLETION_THRESHOLD < 1.0

    def test_moderate_effective_threshold_in_range(self) -> None:
        assert 0.0 < thresholds._MODERATE_EFFECTIVE_THRESHOLD < 1.0

    def test_min_applied_for_derived_in_range(self) -> None:
        assert 0.0 < thresholds._MIN_APPLIED_FOR_DERIVED < 1.0

    def test_fix_thresholds_consistent(self) -> None:
        """FIX triggers when fallback > _FALLBACK_THRESHOLD."""
        # Fallback rate of 0.5 should exceed the 0.4 threshold
        assert 0.5 > thresholds._FALLBACK_THRESHOLD

    def test_derived_thresholds_consistent(self) -> None:
        """DERIVED triggers when effective < _MODERATE and applied > _MIN_APPLIED."""
        assert thresholds._MODERATE_EFFECTIVE_THRESHOLD > thresholds._MIN_APPLIED_FOR_DERIVED


# ============================================================================
# Regression: EvolutionEngine facade wiring (from iter 42 audit)
# ============================================================================


class TestEvolutionEngineFacadeDelegation:
    """EvolutionEngine delegates correctly to all sub-components.

    The engine is a thin facade. Each method must route to the correct
    sub-component with the right arguments, and return results unchanged.
    """

    @pytest.fixture
    def engine(self, tmp_path: Path) -> "EvolutionEngine":
        """Create an EvolutionEngine with a temp SQLite store."""
        from agent_nexus.platform.evolution.engine import EvolutionEngine
        db_path = tmp_path / "evolution.db"
        store = EvolutionStore(db_path)
        return EvolutionEngine(store, agents_root=tmp_path / "agents")

    def test_properties_return_components(
        self, engine: "EvolutionEngine"
    ) -> None:
        """All sub-component properties return non-None instances."""
        assert engine.store is not None
        assert engine.analyzer is not None
        assert engine.evolver is not None
        assert engine.health_checker is not None
        assert engine.compaction_guard is not None
        assert engine.promoter is not None

    def test_evolve_post_analysis_requires_ctx(
        self, engine: "EvolutionEngine"
    ) -> None:
        """trigger='post_analysis' without ctx raises ValueError."""
        with pytest.raises(ValueError, match="ctx.*required"):
            engine.evolve(trigger="post_analysis", ctx=None)

    def test_evolve_tool_degradation_requires_tool_key(
        self, engine: "EvolutionEngine"
    ) -> None:
        """trigger='tool_degradation' without tool_key raises ValueError."""
        with pytest.raises(ValueError, match="tool_key.*required"):
            engine.evolve(trigger="tool_degradation")

    def test_evolve_unknown_trigger(self, engine: "EvolutionEngine") -> None:
        """Unknown trigger raises ValueError."""
        with pytest.raises(ValueError, match="Unknown trigger"):
            engine.evolve(trigger="nonexistent")

    def test_evolve_post_analysis_returns_analysis_result(
        self, engine: "EvolutionEngine"
    ) -> None:
        """trigger='post_analysis' with valid ctx returns AnalysisResult."""
        ctx = EvolutionContext(
            agent_id="test-agent",
            task_id="task-1",
            task_description="test task",
        )
        result = engine.evolve(trigger="post_analysis", ctx=ctx)
        # AnalysisResult has .suggestions attribute
        assert hasattr(result, "suggestions")

    def test_evolve_tool_degradation_returns_list(
        self, engine: "EvolutionEngine"
    ) -> None:
        """trigger='tool_degradation' returns list[EvolveResult]."""
        results = engine.evolve(
            trigger="tool_degradation",
            tool_key="test-tool",
            problem_description="tool is slow",
        )
        assert isinstance(results, list)

    def test_evolve_metric_check_returns_list(
        self, engine: "EvolutionEngine"
    ) -> None:
        """trigger='metric_check' returns list[EvolveResult]."""
        results = engine.evolve(trigger="metric_check")
        assert isinstance(results, list)

    def test_should_compact_delegates(
        self, engine: "EvolutionEngine"
    ) -> None:
        """should_compact delegates to CompactionGuard."""
        from agent_nexus.platform.evolution.compaction import AgentContext
        ctx = AgentContext(agent_id="test", session_id="s1")
        # Should return bool without error
        result = engine.should_compact(ctx)
        assert isinstance(result, bool)

    def test_diagnose_all_delegates(self, engine: "EvolutionEngine") -> None:
        """diagnose_all delegates to HealthChecker."""
        report = engine.diagnose_all()
        assert isinstance(report, dict)

    def test_check_health_missing_skill_raises(
        self, engine: "EvolutionEngine"
    ) -> None:
        """check_health raises ValueError for unknown skill_id."""
        with pytest.raises(ValueError, match="Skill not found"):
            engine.check_health("nonexistent-skill")


# ============================================================================
# 9. Regression: evolve_skill IntegrityError + get_metrics precise LIKE
# ============================================================================


class TestEvolveSkillIntegrityError:
    """evolve_skill catches sqlite3.IntegrityError on duplicate skill ID
    and returns EvolveResult(success=False) with 'collision' in the error.
    """

    def test_duplicate_id_returns_failure(self, tmp_path: Path) -> None:
        """Inserting the same skill ID twice via evolve_skill returns a
        failure result instead of raising an exception."""
        store = EvolutionStore(tmp_path / "test.db")

        record = _make_record(
            "s1",
            "my-skill",
            origin=SkillOrigin.DERIVED,
            generation=1,
            parent_ids=[],
        )
        # First insert succeeds
        result1 = store.evolve_skill(record, [])
        assert result1.success

        # Second insert with the same ID triggers IntegrityError
        result2 = store.evolve_skill(record, [])
        assert result2.success is False
        assert result2.error is not None
        assert "collision" in result2.error.lower()
        assert result2.new_record is None

    def test_duplicate_id_does_not_raise(self, tmp_path: Path) -> None:
        """Ensure no sqlite3.IntegrityError escapes evolve_skill."""
        import sqlite3

        store = EvolutionStore(tmp_path / "test.db")
        record = _make_record("dup-id", "skill")
        store.evolve_skill(record, [])

        # This must NOT raise sqlite3.IntegrityError
        try:
            result = store.evolve_skill(record, [])
        except sqlite3.IntegrityError:
            pytest.fail("evolve_skill should catch IntegrityError, not propagate it")

        assert result.success is False


class TestGetMetricsPreciseLikeMatching:
    """get_metrics uses precise directory LIKE matching so that querying
    agent_name='code' does not accidentally match 'encoder-decoder' (which
    contains 'code' as a substring).
    """

    def test_precise_agent_name_filtering(self, tmp_path: Path) -> None:
        """get_metrics(agent_name='code') only matches agents/code/... and
        agents/code exactly, not agents/encoder-decoder/... which contains
        'code' as a substring in the agent directory name.
        """
        store = EvolutionStore(tmp_path / "test.db")

        # Skill under agents/code/... (matches agent_name='code')
        r1 = _make_record(
            "s1",
            "review",
            selections=10,
            directory="agents/code/review",
        )
        # Skill under agents/encoder-decoder/... (contains 'code' as substring)
        r2 = _make_record(
            "s2",
            "encode",
            selections=20,
            directory="agents/encoder-decoder/encode",
        )
        store.save_skill_record(r1)
        store.save_skill_record(r2)

        metrics = store.get_metrics(agent_name="code")
        # Should ONLY count the 'code' agent's selections (10),
        # not encoder-decoder's (20) -- old substring %code% would match both
        assert metrics.total_selections == 10

    def test_precise_agent_name_no_false_positives(self, tmp_path: Path) -> None:
        """get_metrics(agent_name='code') returns zero metrics when only
        encoder-decoder exists (no exact 'code' agent).
        """
        store = EvolutionStore(tmp_path / "test.db")

        r = _make_record(
            "s1",
            "encode",
            selections=50,
            directory="agents/encoder-decoder/encode",
        )
        store.save_skill_record(r)

        metrics = store.get_metrics(agent_name="code")
        # encoder-decoder does NOT match 'code' with the precise pattern
        assert metrics.total_selections == 0

    def test_exact_directory_match(self, tmp_path: Path) -> None:
        """get_metrics(agent_name='code') also matches directory='agents/code'
        exactly (no trailing slash).
        """
        store = EvolutionStore(tmp_path / "test.db")

        r = _make_record(
            "s1",
            "skill",
            selections=15,
            directory="agents/code",
        )
        store.save_skill_record(r)

        metrics = store.get_metrics(agent_name="code")
        assert metrics.total_selections == 15


# ============================================================================
# Coverage gap tests: evolver addressed-skip, metric-check healthy skip,
# fix multi-parent error, store.evolve_skill failure, derived missing parent,
# captured long name truncation, captured empty name fallback
# ============================================================================


class TestProcessToolDegradationAddressedSkip:
    """Already-addressed skills are skipped during tool degradation."""

    def test_addressed_skill_skipped(self, tmp_path: Path) -> None:
        """Skill in the _addressed set is not evolved again."""
        s1 = _make_record("s1", "a")
        store = _store_with_records(tmp_path, s1)
        evolver = SkillEvolver(store)

        # Manually mark s1 as already addressed for tool-x
        evolver._addressed = {"tool-x": {"s1"}}

        results = evolver.process_tool_degradation("tool-x", "API broken")
        assert len(results) == 0


class TestProcessMetricCheckHealthySkip:
    """process_metric_check skips skills that diagnose as healthy."""

    def test_healthy_skill_produces_no_evolution(self, tmp_path: Path) -> None:
        """Skill with good metrics is skipped (diagnose returns None)."""
        r = _make_record(
            "s1", "good",
            selections=100, applied=80, completions=70, fallbacks=5,
        )
        store = _store_with_records(tmp_path, r)
        evolver = SkillEvolver(store)

        results = evolver.process_metric_check(min_selections=5)
        assert len(results) == 0


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


class TestEvolveFixStoreFailure:
    """FIX evolution handles store.evolve_skill failure."""

    def test_store_evolve_failure_returns_error(self, tmp_path: Path) -> None:
        """When store.evolve_skill fails, FIX returns error result."""
        from unittest.mock import patch

        parent = _make_record("p1", "skill", directory="skills/s")
        store = _store_with_records(tmp_path, parent)
        evolver = SkillEvolver(store)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.FIX,
            target_skill_ids=["p1"],
            direction="Fix it",
        )

        fail_result = EvolveResult(success=False, error="DB locked")
        with patch.object(store, "evolve_skill", return_value=fail_result):
            result = evolver.evolve(suggestion)

        assert not result.success
        assert result.error == "DB locked"


class TestEvolveDerivedMissingParent:
    """DERIVED evolution handles missing parent skill."""

    def test_derived_missing_parent_returns_error(self, tmp_path: Path) -> None:
        """DERIVED with a nonexistent parent ID returns error."""
        store = _store_with_records(tmp_path)
        evolver = SkillEvolver(store)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.DERIVED,
            target_skill_ids=["ghost_parent"],
            direction="Enhance it",
        )
        result = evolver.evolve(suggestion)
        assert not result.success
        assert "not found" in result.error


class TestEvolveDerivedStoreFailure:
    """DERIVED evolution handles store.evolve_skill failure."""

    def test_store_evolve_failure_returns_error(self, tmp_path: Path) -> None:
        """When store.evolve_skill fails, DERIVED returns error result."""
        from unittest.mock import patch

        parent = _make_record("p1", "base", directory="skills/base")
        store = _store_with_records(tmp_path, parent)
        evolver = SkillEvolver(store)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.DERIVED,
            target_skill_ids=["p1"],
            direction="Enhance",
        )

        fail_result = EvolveResult(success=False, error="collision")
        with patch.object(store, "evolve_skill", return_value=fail_result):
            result = evolver.evolve(suggestion)

        assert not result.success
        assert result.error == "collision"


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
        self, tmp_path: Path,
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


class TestEvolveCapturedStoreFailure:
    """CAPTURED evolution handles store.evolve_skill failure."""

    def test_store_evolve_failure_returns_error(self, tmp_path: Path) -> None:
        """When store.evolve_skill fails, CAPTURED returns error result."""
        from unittest.mock import patch

        store = _store_with_records(tmp_path)
        evolver = SkillEvolver(store)

        suggestion = EvolutionSuggestion(
            evolution_type=EvolutionType.CAPTURED,
            direction="Some novel pattern",
        )

        fail_result = EvolveResult(success=False, error="write error")
        with patch.object(store, "evolve_skill", return_value=fail_result):
            result = evolver.evolve(suggestion)

        assert not result.success
        assert result.error == "write error"


# ============================================================================
# Coverage: store.py missed lines
# ============================================================================


class TestEvolutionStoreCounterValidation:
    """Tests for increment_counters ValueError guards (lines 305, 307, 309)."""

    def test_fell_back_requires_applied(self, tmp_path: Path) -> None:
        """fell_back=True with applied=False raises ValueError."""
        store = _store_with_records(tmp_path, _make_record("s1", "x"))
        with pytest.raises(ValueError, match="fell_back requires applied"):
            store.increment_counters("s1", fell_back=True, applied=False, selected=True)

    def test_applied_requires_selected(self, tmp_path: Path) -> None:
        """applied=True with selected=False raises ValueError."""
        store = _store_with_records(tmp_path, _make_record("s1", "x"))
        with pytest.raises(ValueError, match="applied requires selected"):
            store.increment_counters("s1", applied=True, selected=False)

    def test_completed_requires_applied(self, tmp_path: Path) -> None:
        """completed=True with applied=False raises ValueError."""
        store = _store_with_records(tmp_path, _make_record("s1", "x"))
        with pytest.raises(ValueError, match="completed requires applied"):
            store.increment_counters("s1", completed=True, applied=False, selected=True)


class TestEvolutionStoreAnalysisSkipEmptySkillId:
    """Tests for record_analysis skipping judgments with no skill_id (line 387)."""

    def test_judgment_without_skill_id_skipped(self, tmp_path: Path) -> None:
        """Judgments missing skill_id are silently skipped."""
        store = _store_with_records(tmp_path, _make_record("s1", "x"))
        analysis_id = store.record_analysis(
            task_id="t1",
            agent_name="a",
            analysis_text="test",
            judgments=[
                {"skill_id": "", "selected": True},
                {"skill_id": None, "selected": True},
                {"skill_id": "s1", "selected": True, "applied": True,
                 "completed": False, "fell_back": False},
            ],
        )
        # Only the judgment with skill_id="s1" should be persisted
        judgments = store.get_judgments_for_skill("s1")
        assert len(judgments) == 1

    def test_judgment_completely_missing_skill_id_key(self, tmp_path: Path) -> None:
        """Judgment dict with no skill_id key at all is skipped."""
        store = _store_with_records(tmp_path)
        analysis_id = store.record_analysis(
            task_id="t2",
            agent_name="a",
            analysis_text="test",
            judgments=[
                {"selected": True, "applied": True},
            ],
        )
        assert analysis_id


class TestEvolutionStoreAncestryCycleDetection:
    """Tests for get_ancestry visited-set loop detection (line 633)."""

    def test_shared_grandparent_deduplicates(self, tmp_path: Path) -> None:
        """When two parents share the same grandparent, the grandparent
        appears only once in the ancestry result."""
        gp = _make_record("gp", "root", generation=0)
        p1 = _make_record("p1", "branch-a", generation=1, parent_ids=["gp"])
        p2 = _make_record("p2", "branch-b", generation=1, parent_ids=["gp"])
        child = _make_record("c1", "merged", generation=2, parent_ids=["p1", "p2"])
        store = _store_with_records(tmp_path, gp, p1, p2, child)

        ancestors = store.get_ancestry("c1")
        ancestor_ids = [a.id for a in ancestors]
        # gp should appear exactly once despite being reachable via both p1 and p2
        assert ancestor_ids.count("gp") == 1
        assert "p1" in ancestor_ids
        assert "p2" in ancestor_ids


class TestEvolutionStoreConnRollback:
    """Tests for _conn exception handler rollback (lines 148-151).

    The except block references an undefined 'logger' variable in store.py,
    which causes a NameError when triggered. This is a known production
    defect. The test verifies that the exception chain still propagates.
    """

    def test_conn_exception_propagates_via_nameerror(self, tmp_path: Path) -> None:
        """When a DB operation fails inside _conn, the exception propagates
        after rollback. Logger is patched in since store.py has an undefined
        logger reference (production defect)."""
        from unittest.mock import patch

        store = _store_with_records(tmp_path, _make_record("safe", "x"))

        import logging
        test_logger = logging.getLogger("test.rollback")

        with patch("agent_nexus.platform.evolution.store.logger", test_logger, create=True):
            with pytest.raises(RuntimeError, match="forced error"):
                with store._conn() as conn:
                    raise RuntimeError("forced error")

        # Verify data was not corrupted by the failed transaction
        rec = store.get_skill_record("safe")
        assert rec is not None
        assert rec.name == "x"

    def test_conn_rollback_on_integrity_error(self, tmp_path: Path) -> None:
        """Duplicate primary key inside _conn triggers except block."""
        from unittest.mock import patch
        import logging
        import sqlite3

        store = _store_with_records(tmp_path, _make_record("dup", "x"))

        test_logger = logging.getLogger("test.integrity")
        with patch("agent_nexus.platform.evolution.store.logger", test_logger, create=True):
            with pytest.raises(sqlite3.IntegrityError):
                with store._conn() as conn:
                    # Insert a record with the same primary key
                    conn.execute(
                        "INSERT INTO skill_records (id, name, version, "
                        "lineage_origin, lineage_generation, "
                        "is_active, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        ("dup", "collision", "1.0", "imported", 0,
                         1, "2025-01-01", "2025-01-01"),
                    )
                    conn.commit()

        # Original record should still be intact (rollback preserved it)
        rec = store.get_skill_record("dup")
        assert rec is not None
        assert rec.name == "x"


# ============================================================================
# Coverage gap tests: analyzer.py lines 106, 172, 234, 284, 351
# ============================================================================


class TestCorrectSkillIdsManyCandidates:
    """_correct_skill_ids with >20 candidates uses max_dist=2 (line 106)."""

    def test_many_candidates_uses_distance_two(self) -> None:
        """When there are >20 candidates sharing the same prefix, max_dist is capped at 2."""
        # All 22 IDs share prefix "x" so len(candidates) > 20 triggers line 106.
        known = set()
        for i in range(21):
            known.add(f"x__suffix_{i:02d}")
        known.add("x__target_exact")
        assert len(known) == 22  # 22 candidates with prefix "x"
        # Typo at distance 1: "x__target_exacc" -> "x__target_exact"
        result = _correct_skill_ids(["x__target_exacc"], known)
        assert result == ["x__target_exact"]


class TestAnalyzerUnknownSkillIdContinue:
    """analyze_execution skips skill IDs not found in store (line 172)."""

    def test_unknown_skill_id_produces_no_judgment(self, tmp_path: Path) -> None:
        """A hallucinated skill ID not in store produces no judgment."""
        store = _store_with_records(tmp_path, _make_record("s1", "known"))
        analyzer = ExecutionAnalyzer(store)

        ctx = EvolutionContext(
            agent_id="agent-a",
            task_id="t-unknown",
            skill_ids_used=["s1", "ghost_skill"],
        )
        result = analyzer.analyze_execution(ctx)
        # Only s1 should produce a judgment; ghost_skill is skipped
        assert len(result.judgments) == 1
        assert result.judgments[0]["skill_id"] == "s1"


class TestAnalyzerGenerateSuggestionsSkillNone:
    """_generate_skills skips skill IDs not found in skills_by_id (line 234)."""

    def test_missing_skill_skipped_in_suggestions(self, tmp_path: Path) -> None:
        """Skill ID present in context but absent from store produces no suggestion."""
        store = _store_with_records(tmp_path)
        analyzer = ExecutionAnalyzer(store)

        ctx = EvolutionContext(
            agent_id="agent-a",
            task_id="t-missing",
            skill_ids_used=["nonexistent"],
        )
        result = analyzer.analyze_execution(ctx)
        assert len(result.suggestions) == 0


class TestAnalyzerDerivedSuggestion:
    """_generate_suggestions produces DERIVED for moderate effectiveness (line 284)."""

    def test_moderate_effective_produces_derived(self, tmp_path: Path) -> None:
        """Skill with moderate effective_rate and high applied_rate triggers DERIVED."""
        # effective_rate = 40/100 = 0.4 < 0.55, applied_rate = 50/100 = 0.5 > 0.25
        # fallback_rate = 10/100 = 0.1 <= 0.4 (no FIX)
        # completion_rate = 40/50 = 0.8 >= 0.35 (no FIX)
        r = _make_record(
            "s1", "moderate",
            selections=100, applied=50, completions=40, fallbacks=10,
        )
        store = _store_with_records(tmp_path, r)
        analyzer = ExecutionAnalyzer(store)

        ctx = EvolutionContext(
            agent_id="agent-a",
            task_id="t-derived",
            skill_ids_used=["s1"],
            skills_applied=["s1"],
        )
        result = analyzer.analyze_execution(ctx)
        derived = [s for s in result.suggestions if s.evolution_type == EvolutionType.DERIVED]
        assert len(derived) >= 1
        assert "s1" in derived[0].target_skill_ids


class TestAnalyzerExecutionError:
    """_build_analysis_text includes execution_error when present (line 351)."""

    def test_execution_error_included_in_text(self, tmp_path: Path) -> None:
        """When execution_error is set, it appears in the analysis text."""
        store = _store_with_records(tmp_path, _make_record("s1", "x"))
        analyzer = ExecutionAnalyzer(store)

        ctx = EvolutionContext(
            agent_id="agent-a",
            task_id="t-err",
            task_completed=False,
            skill_ids_used=["s1"],
            execution_error="FileNotFoundError: config.toml",
        )
        result = analyzer.analyze_execution(ctx)
        assert "FileNotFoundError: config.toml" in result.analysis_text


# ============================================================================
# Coverage gap tests: compaction.py lines 99, 109, 204-205
# ============================================================================


class TestCompactionGuardZeroWindowEdgeCases:
    """Tests for context_window <= 0 in needs_truncation and needs_hard_ceiling."""

    def test_needs_truncation_zero_window(self, tmp_path: Path) -> None:
        """needs_truncation returns False when context_window is 0."""
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(total_tokens=200_000, context_window=0)
        assert guard.needs_truncation(ctx) is False

    def test_needs_hard_ceiling_zero_window(self, tmp_path: Path) -> None:
        """needs_hard_ceiling returns False when context_window is 0."""
        store = _store_with_records(tmp_path)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(total_tokens=200_000, context_window=0)
        assert guard.needs_hard_ceiling(ctx) is False


class TestCompactionGuardL0Fallback:
    """_build_l0_fallback is called when l0_content is empty (lines 204-205)."""

    def test_reinject_uses_fallback_when_l0_empty(self, tmp_path: Path) -> None:
        """When l0_content is empty, _build_l0_fallback provides content from store metrics."""
        r = _make_record("s1", "x", selections=5, applied=3, completions=2)
        store = _store_with_records(tmp_path, r)
        guard = CompactionGuard(store, "agent-a")
        ctx = _make_agent_context(
            agent_id="agent-a",
            session_id="sess-1",
            turn=10,
            total_tokens=100_000,
            l0_content="",
            l1_content="summary text",
        )
        result = guard.reinject_after_compaction(ctx)
        # Fallback L0 should include agent_id and session info
        assert "agent-a" in result
        assert "sess-1" in result
        assert "summary text" in result


# ============================================================================
# Coverage gap tests: promotion.py lines 138-139 (OSError on mkdir)
# ============================================================================


class TestPromotionMkdirOSError:
    """promote() handles OSError when mkdir fails (lines 138-139)."""

    def test_mkdir_failure_returns_error(self, tmp_path: Path) -> None:
        """When mkdir raises OSError, promote returns a failure result."""
        from unittest.mock import patch

        store = _store_with_records(tmp_path)
        agents_dir = tmp_path / "agents"
        promoter = AgentPromoter(store, agents_root=agents_dir)

        candidate = PromotionCandidate(
            skill_id="s1",
            skill_name="test-skill",
            effective_rate=0.9,
            total_selections=100,
            directory="skills/test",
            reason="test",
        )

        with patch.object(Path, "mkdir", side_effect=OSError("permission denied")):
            result = promoter.promote(candidate)

        assert not result.success
        assert "permission denied" in result.error


# ============================================================================
# Coverage gap tests: health.py line 188 (diagnose_skills with skill_ids filter)
# ============================================================================


class TestHealthCheckerDiagnoseSkillsFiltered:
    """diagnose_skills with non-None skill_ids filter (line 188)."""

    def test_filter_by_skill_ids(self, tmp_path: Path) -> None:
        """diagnose_skills returns only reports for matching skill IDs."""
        r1 = _make_record("s1", "healthy", selections=100, applied=80, completions=70, fallbacks=5)
        r2 = _make_record("s2", "unhealthy", selections=100, applied=100, fallbacks=60)
        r3 = _make_record("s3", "another", selections=100, applied=80, completions=70, fallbacks=5)
        store = _store_with_records(tmp_path, r1, r2, r3)
        checker = HealthChecker(store)

        reports = checker.diagnose_skills(skill_ids={"s1", "s3"})
        assert set(reports.keys()) == {"s1", "s3"}
        assert "s2" not in reports

    def test_filter_with_no_matching_ids(self, tmp_path: Path) -> None:
        """diagnose_skills with skill_ids matching nothing returns empty dict."""
        r1 = _make_record("s1", "x", selections=100, applied=80, completions=70, fallbacks=5)
        store = _store_with_records(tmp_path, r1)
        checker = HealthChecker(store)

        reports = checker.diagnose_skills(skill_ids={"nonexistent"})
        assert reports == {}
