"""Unit tests for BudgetStore — context budget event persistence.

BudgetStore is typically created by EvolutionStore, which handles schema
initialization.  For these tests we create an in-memory SQLite database,
run the shared DDL, then instantiate BudgetStore with a conn_factory
that reuses the same connection.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

import pytest

from agent_nexus.platform.evolution._shared import _SCHEMA_SQL
from agent_nexus.platform.evolution.budget_store import BudgetStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys=ON")
    for stmt in _SCHEMA_SQL.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)


class _StoreFactory:
    """Creates a BudgetStore backed by a single in-memory connection."""

    def __init__(self) -> None:
        self._conn = sqlite3.connect(":memory:")
        _init_schema(self._conn)

    @contextmanager
    def conn(self, *, immediate: bool = False) -> Generator[sqlite3.Connection, None, None]:
        yield self._conn

    def make_store(self) -> BudgetStore:
        return BudgetStore(Path(":memory:"), conn_factory=self.conn)


@pytest.fixture()
def factory() -> _StoreFactory:
    return _StoreFactory()


@pytest.fixture()
def store(factory: _StoreFactory) -> BudgetStore:
    return factory.make_store()


# ---------------------------------------------------------------------------
# log_budget_event / get_budget_log
# ---------------------------------------------------------------------------


class TestLogBudgetEvent:
    def test_basic_insert(self, store: BudgetStore) -> None:
        log_id = store.log_budget_event(
            agent_name="test-agent",
            event_type="trim",
            tokens_before=1000,
            tokens_after=500,
        )
        assert isinstance(log_id, str) and len(log_id) == 36  # UUID format

        rows = store.get_budget_log("test-agent")
        assert len(rows) == 1
        r = rows[0]
        assert r["agent_name"] == "test-agent"
        assert r["event_type"] == "trim"
        assert r["tokens_before"] == 1000
        assert r["tokens_after"] == 500
        assert r["details"] == "{}"

    def test_with_details(self, store: BudgetStore) -> None:
        details = {"reason": "overflow", "segments_removed": 3}
        store.log_budget_event("a1", "evict", details=details)

        rows = store.get_budget_log("a1")
        import json

        details = json.loads(rows[0]["details"])
        assert details["reason"] == "overflow"
        assert details["segments_removed"] == 3

    def test_optional_token_fields(self, store: BudgetStore) -> None:
        store.log_budget_event("a2", "snapshot")
        rows = store.get_budget_log("a2")
        assert rows[0]["tokens_before"] is None
        assert rows[0]["tokens_after"] is None
        assert rows[0]["event_type"] == "snapshot"

    def test_multiple_agents_isolated(self, store: BudgetStore) -> None:
        store.log_budget_event("agent-x", "trim")
        store.log_budget_event("agent-y", "evict")
        store.log_budget_event("agent-x", "trim")

        assert len(store.get_budget_log("agent-x")) == 2
        assert len(store.get_budget_log("agent-y")) == 1

    def test_created_at_populated(self, store: BudgetStore) -> None:
        store.log_budget_event("a3", "trim")
        rows = store.get_budget_log("a3")
        assert rows[0]["created_at"] is not None
        assert "T" in rows[0]["created_at"]  # ISO format


class TestGetBudgetLog:
    def test_default_limit_50(self, store: BudgetStore) -> None:
        for i in range(60):
            store.log_budget_event("a", f"ev-{i}")

        rows = store.get_budget_log("a")
        assert len(rows) == 50

    def test_custom_limit(self, store: BudgetStore) -> None:
        for i in range(20):
            store.log_budget_event("a", f"ev-{i}")

        rows = store.get_budget_log("a", limit=5)
        assert len(rows) == 5

    def test_limit_below_1_clamped(self, store: BudgetStore) -> None:
        store.log_budget_event("a", "ev")
        rows = store.get_budget_log("a", limit=0)
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


class TestClear:
    def test_clears_budget_log(self, store: BudgetStore, factory: _StoreFactory) -> None:
        store.log_budget_event("a", "ev")
        store.clear()
        assert store.get_budget_log("a") == []

    def test_clears_all_tables(self, store: BudgetStore, factory: _StoreFactory) -> None:
        conn = factory._conn
        # Insert into related tables to verify clear crosses boundaries
        conn.execute(
            "INSERT INTO agent_records (agent_id, name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            ("id-1", "agent-1", "2025-01-01T00:00:00", "2025-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO skill_records "
            "(id, name, lineage_origin, lineage_generation, is_active, directory, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "sk-1",
                "my-skill",
                "imported",
                0,
                1,
                "/tmp",
                "2025-01-01T00:00:00",
                "2025-01-01T00:00:00",
            ),
        )
        store.log_budget_event("agent-1", "ev")
        store.clear()

        assert conn.execute("SELECT COUNT(*) FROM context_budget_log").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM agent_records").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM skill_records").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# prune_budget_log
# ---------------------------------------------------------------------------


class TestPruneBudgetLog:
    def test_prune_by_age(self, store: BudgetStore, factory: _StoreFactory) -> None:
        conn = factory._conn
        # Insert an old record manually (bypassing _now_iso)
        conn.execute(
            "INSERT INTO context_budget_log "
            "(id, agent_name, event_type, created_at) VALUES (?, ?, ?, ?)",
            ("old-1", "a", "ev", "2000-01-01T00:00:00"),
        )
        store.log_budget_event("a", "ev")  # new record

        deleted = store.prune_budget_log(max_age_days=30)
        assert deleted == 1

        rows = store.get_budget_log("a")
        assert len(rows) == 1
        assert rows[0]["id"] != "old-1"

    def test_prune_by_max_rows(self, store: BudgetStore, factory: _StoreFactory) -> None:
        conn = factory._conn
        # Insert 10 records with old dates
        for i in range(10):
            conn.execute(
                "INSERT INTO context_budget_log "
                "(id, agent_name, event_type, created_at) VALUES (?, ?, ?, ?)",
                (f"pr-{i}", "a", "ev", "2025-06-01T00:00:00"),
            )

        deleted = store.prune_budget_log(max_age_days=0, max_rows=5)
        # max_rows=5 trims the 10 down to 5
        assert deleted >= 5
        count = conn.execute(
            "SELECT COUNT(*) FROM context_budget_log WHERE agent_name='a'"
        ).fetchone()[0]
        assert count <= 5

    def test_nothing_to_prune(self, store: BudgetStore) -> None:
        store.log_budget_event("a", "ev")
        deleted = store.prune_budget_log(max_age_days=365, max_rows=10_000)
        assert deleted == 0

    def test_combined_age_and_max_rows(self, store: BudgetStore, factory: _StoreFactory) -> None:
        conn = factory._conn
        # 3 old records (deleted by age)
        for i in range(3):
            conn.execute(
                "INSERT INTO context_budget_log "
                "(id, agent_name, event_type, created_at) VALUES (?, ?, ?, ?)",
                (f"old-{i}", "a", "ev", "2000-01-01T00:00:00"),
            )
        # 8 recent records (survive age filter, then pruned to max_rows=5)
        for i in range(8):
            conn.execute(
                "INSERT INTO context_budget_log "
                "(id, agent_name, event_type, created_at) VALUES (?, ?, ?, ?)",
                (f"new-{i}", "a", "ev", "2099-12-01T00:00:00"),
            )

        deleted = store.prune_budget_log(max_age_days=30, max_rows=5)
        # 3 old removed by age + 3 excess recent removed by max_rows = 6
        assert deleted == 6
        count = conn.execute(
            "SELECT COUNT(*) FROM context_budget_log WHERE agent_name='a'"
        ).fetchone()[0]
        assert count == 5


# ---------------------------------------------------------------------------
# Standalone mode (no conn_factory)
# ---------------------------------------------------------------------------
