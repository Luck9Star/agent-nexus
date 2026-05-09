"""Tests for evolution/analysis_store.py — AnalysisStore.

Covers: record_analysis, get_analyses_for_task, get_judgments_for_skill,
get_judgments_batch, counter invariant validation, edge cases.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_nexus.platform.evolution._shared import _SCHEMA_SQL
from agent_nexus.platform.evolution.analysis_store import AnalysisStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db() -> sqlite3.Connection:
    """In-memory DB with evolution schema + a seed skill_record."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA_SQL)
    # Seed a skill so FK constraints on skill_judgments pass.
    conn.execute(
        "INSERT INTO skill_records (id, name, version, created_at, updated_at) "
        "VALUES ('skill-1', 'test-skill', '1.0.0', '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')"
    )
    conn.execute(
        "INSERT INTO skill_records (id, name, version, created_at, updated_at) "
        "VALUES ('skill-2', 'other-skill', '1.0.0', '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')"
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def store(db: sqlite3.Connection) -> AnalysisStore:
    """AnalysisStore using injected conn_factory backed by *db*."""

    def factory(*, immediate: bool = False):
        class _Ctx:
            def __enter__(self):
                return db

            def __exit__(self, *a):
                pass

        return _Ctx()

    return AnalysisStore(Path(":memory:"), conn_factory=factory)


# ---------------------------------------------------------------------------
# record_analysis — basic
# ---------------------------------------------------------------------------


class TestRecordAnalysis:
    def test_returns_analysis_id(self, store: AnalysisStore):
        aid = store.record_analysis("t1", "agent-a", "looks good")
        assert isinstance(aid, str) and len(aid) == 36

    def test_persists_analysis_row(self, store: AnalysisStore, db: sqlite3.Connection):
        aid = store.record_analysis("t1", "agent-a", "text")
        row = db.execute(
            "SELECT task_id, agent_name, analysis FROM execution_analyses WHERE id=?",
            (aid,),
        ).fetchone()
        assert row == ("t1", "agent-a", "text")

    def test_stores_evolution_suggestions_as_json(self, store: AnalysisStore, db: sqlite3.Connection):
        suggestions = [{"type": "evolve", "target": "skill-1"}]
        aid = store.record_analysis("t1", "agent-a", "text", evolution_suggestions=suggestions)
        row = db.execute(
            "SELECT evolution_suggestions FROM execution_analyses WHERE id=?", (aid,)
        ).fetchone()
        assert json.loads(row[0]) == suggestions

    def test_null_suggestions_stored_as_empty_list(self, store: AnalysisStore, db: sqlite3.Connection):
        aid = store.record_analysis("t1", "agent-a", "text", evolution_suggestions=None)
        row = db.execute(
            "SELECT evolution_suggestions FROM execution_analyses WHERE id=?", (aid,)
        ).fetchone()
        assert json.loads(row[0]) == []


# ---------------------------------------------------------------------------
# record_analysis — judgments
# ---------------------------------------------------------------------------


class TestRecordAnalysisJudgments:
    def test_inserts_judgment_rows(self, store: AnalysisStore, db: sqlite3.Connection):
        judgments = [
            {"skill_id": "skill-1", "selected": True, "applied": True, "completed": False, "fell_back": False},
            {"skill_id": "skill-2", "selected": True, "applied": False, "completed": False, "fell_back": False},
        ]
        aid = store.record_analysis("t1", "agent-a", "text", judgments=judgments)
        rows = db.execute(
            "SELECT skill_id, selected, applied FROM skill_judgments WHERE analysis_id=?",
            (aid,),
        ).fetchall()
        assert len(rows) == 2
        assert {r[0] for r in rows} == {"skill-1", "skill-2"}

    def test_skips_judgments_without_skill_id(self, store: AnalysisStore, db: sqlite3.Connection):
        judgments = [
            {"skill_id": "skill-1", "selected": True},
            {"selected": True},  # no skill_id — skipped
        ]
        aid = store.record_analysis("t1", "agent-a", "text", judgments=judgments)
        count = db.execute(
            "SELECT COUNT(*) FROM skill_judgments WHERE analysis_id=?", (aid,)
        ).fetchone()[0]
        assert count == 1

    def test_empty_judgments_no_insert(self, store: AnalysisStore, db: sqlite3.Connection):
        aid = store.record_analysis("t1", "agent-a", "text", judgments=[])
        count = db.execute(
            "SELECT COUNT(*) FROM skill_judgments WHERE analysis_id=?", (aid,)
        ).fetchone()[0]
        assert count == 0


# ---------------------------------------------------------------------------
# record_analysis — counter updates
# ---------------------------------------------------------------------------


class TestRecordAnalysisCounters:
    def test_increments_counters(self, store: AnalysisStore, db: sqlite3.Connection):
        judgments = [
            {"skill_id": "skill-1", "selected": True, "applied": True, "completed": True, "fell_back": False},
        ]
        store.record_analysis("t1", "agent-a", "text", judgments=judgments)
        row = db.execute(
            "SELECT total_selections, total_applied, total_completions, total_fallbacks "
            "FROM skill_records WHERE id='skill-1'"
        ).fetchone()
        assert row == (1, 1, 1, 0)

    def test_multiple_judgments_accumulate(self, store: AnalysisStore, db: sqlite3.Connection):
        judgments = [
            {"skill_id": "skill-1", "selected": True, "applied": True, "completed": False, "fell_back": False},
            {"skill_id": "skill-1", "selected": True, "applied": False, "completed": False, "fell_back": True},
        ]
        store.record_analysis("t1", "agent-a", "text", judgments=judgments)
        row = db.execute(
            "SELECT total_selections, total_applied, total_completions, total_fallbacks "
            "FROM skill_records WHERE id='skill-1'"
        ).fetchone()
        assert row == (2, 1, 0, 1)

    def test_updates_updated_at(self, store: AnalysisStore, db: sqlite3.Connection):
        before = db.execute("SELECT updated_at FROM skill_records WHERE id='skill-1'").fetchone()[0]
        judgments = [{"skill_id": "skill-1", "selected": True}]
        store.record_analysis("t1", "agent-a", "text", judgments=judgments)
        after = db.execute("SELECT updated_at FROM skill_records WHERE id='skill-1'").fetchone()[0]
        assert after >= before


# ---------------------------------------------------------------------------
# record_analysis — counter invariant validation
# ---------------------------------------------------------------------------


class TestCounterInvariants:
    def test_applied_requires_selected(self, store: AnalysisStore):
        with pytest.raises(ValueError, match="applied requires selected"):
            store.record_analysis(
                "t1", "agent-a", "text",
                judgments=[{"skill_id": "skill-1", "selected": False, "applied": True}],
            )

    def test_completed_requires_applied(self, store: AnalysisStore):
        with pytest.raises(ValueError, match="completed requires applied"):
            store.record_analysis(
                "t1", "agent-a", "text",
                judgments=[{"skill_id": "skill-1", "selected": True, "applied": False, "completed": True}],
            )

    def test_fell_back_requires_selected(self, store: AnalysisStore):
        with pytest.raises(ValueError, match="fell_back requires selected"):
            store.record_analysis(
                "t1", "agent-a", "text",
                judgments=[{"skill_id": "skill-1", "selected": False, "fell_back": True}],
            )


# ---------------------------------------------------------------------------
# get_analyses_for_task
# ---------------------------------------------------------------------------


class TestGetAnalysesForTask:
    def test_empty_when_none(self, store: AnalysisStore):
        assert store.get_analyses_for_task("no-such-task") == []

    def test_returns_analysis_with_judgments(self, store: AnalysisStore):
        judgments = [{"skill_id": "skill-1", "selected": True, "applied": True}]
        aid = store.record_analysis("t1", "agent-a", "looks good", judgments=judgments)
        results = store.get_analyses_for_task("t1")
        assert len(results) == 1
        assert results[0]["id"] == aid
        assert results[0]["agent_name"] == "agent-a"
        assert results[0]["analysis"] == "looks good"
        assert len(results[0]["judgments"]) == 1

    def test_multiple_analyses_same_task(self, store: AnalysisStore):
        store.record_analysis("t1", "agent-a", "first")
        store.record_analysis("t1", "agent-b", "second")
        results = store.get_analyses_for_task("t1")
        assert len(results) == 2
        assert {r["agent_name"] for r in results} == {"agent-a", "agent-b"}

    def test_judgments_associated_correctly(self, store: AnalysisStore):
        j1 = [{"skill_id": "skill-1", "selected": True}]
        j2 = [{"skill_id": "skill-2", "selected": True}]
        store.record_analysis("t1", "agent-a", "a", judgments=j1)
        store.record_analysis("t1", "agent-b", "b", judgments=j2)
        results = store.get_analyses_for_task("t1")
        by_agent = {r["agent_name"]: r for r in results}
        assert by_agent["agent-a"]["judgments"][0]["skill_id"] == "skill-1"
        assert by_agent["agent-b"]["judgments"][0]["skill_id"] == "skill-2"

    def test_evolution_suggestions_parsed(self, store: AnalysisStore):
        suggestions = [{"type": "evolve"}]
        store.record_analysis("t1", "agent-a", "text", evolution_suggestions=suggestions)
        results = store.get_analyses_for_task("t1")
        assert results[0]["evolution_suggestions"] == suggestions


# ---------------------------------------------------------------------------
# get_judgments_for_skill
# ---------------------------------------------------------------------------


class TestGetJudgmentsForSkill:
    def test_empty_when_none(self, store: AnalysisStore):
        assert store.get_judgments_for_skill("no-skill") == []

    def test_returns_matching_judgments(self, store: AnalysisStore):
        judgments = [
            {"skill_id": "skill-1", "selected": True, "applied": True},
            {"skill_id": "skill-2", "selected": True},
        ]
        store.record_analysis("t1", "agent-a", "text", judgments=judgments)
        result = store.get_judgments_for_skill("skill-1")
        assert len(result) == 1
        assert result[0]["skill_id"] == "skill-1"
        assert result[0]["selected"] is True
        assert result[0]["applied"] is True

    def test_limit_parameter(self, store: AnalysisStore):
        for i in range(5):
            store.record_analysis(
                f"t{i}", "agent-a", "text",
                judgments=[{"skill_id": "skill-1", "selected": True}],
            )
        result = store.get_judgments_for_skill("skill-1", limit=3)
        assert len(result) == 3

    def test_limit_below_1_clamped(self, store: AnalysisStore):
        store.record_analysis("t1", "agent-a", "text", judgments=[{"skill_id": "skill-1", "selected": True}])
        result = store.get_judgments_for_skill("skill-1", limit=0)
        assert len(result) == 1  # limit clamped to 1


# ---------------------------------------------------------------------------
# get_judgments_batch
# ---------------------------------------------------------------------------


class TestGetJudgmentsBatch:
    def test_empty_skill_ids(self, store: AnalysisStore):
        assert store.get_judgments_batch(set()) == {}

    def test_returns_per_skill(self, store: AnalysisStore):
        judgments = [
            {"skill_id": "skill-1", "selected": True},
            {"skill_id": "skill-2", "selected": True},
        ]
        store.record_analysis("t1", "agent-a", "text", judgments=judgments)
        result = store.get_judgments_batch({"skill-1", "skill-2"})
        assert len(result["skill-1"]) == 1
        assert len(result["skill-2"]) == 1

    def test_limit_per_skill(self, store: AnalysisStore):
        for i in range(4):
            store.record_analysis(
                f"t{i}", "agent-a", "text",
                judgments=[{"skill_id": "skill-1", "selected": True}],
            )
        result = store.get_judgments_batch({"skill-1"}, limit_per_skill=2)
        assert len(result["skill-1"]) == 2

    def test_limit_below_1_clamped(self, store: AnalysisStore):
        store.record_analysis("t1", "agent-a", "text", judgments=[{"skill_id": "skill-1", "selected": True}])
        result = store.get_judgments_batch({"skill-1"}, limit_per_skill=0)
        assert len(result["skill-1"]) == 1

    def test_missing_skill_returns_empty_list(self, store: AnalysisStore):
        store.record_analysis("t1", "agent-a", "text", judgments=[{"skill_id": "skill-1", "selected": True}])
        result = store.get_judgments_batch({"skill-1", "skill-missing"})
        assert result["skill-1"]  # has data
        assert result["skill-missing"] == []


# ---------------------------------------------------------------------------
# _judgment_row_to_dict (static helper)
# ---------------------------------------------------------------------------


class TestJudgmentRowToDict:
    def test_converts_row(self):
        row = ("jid", "aid", "sid", 1, 0, 1, 0)
        d = AnalysisStore._judgment_row_to_dict(row)
        assert d == {
            "id": "jid",
            "analysis_id": "aid",
            "skill_id": "sid",
            "selected": True,
            "applied": False,
            "completed": True,
            "fell_back": False,
        }

    def test_all_true(self):
        row = ("jid", "aid", "sid", 1, 1, 1, 1)
        d = AnalysisStore._judgment_row_to_dict(row)
        assert all(d[k] is True for k in ("selected", "applied", "completed", "fell_back"))
