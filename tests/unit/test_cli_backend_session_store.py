"""Unit tests for CLISessionStore — SQLite session persistence."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_nexus.platform.agency.cli_backend.session_store import CLISessionStore
from agent_nexus.platform.agency.cli_backend.types import CLISessionRecord


@pytest.fixture
def store(tmp_path: Path) -> CLISessionStore:
    db_path = tmp_path / "test.db"
    return CLISessionStore(db_path)


class TestCLISessionStoreSchema:
    def test_tables_created(self, store: CLISessionStore):
        tables = store._list_tables()
        assert "cli_sessions" in tables
        assert "task_executions" in tables
        assert "backend_health" in tables
        assert "daily_stats" in tables

    def test_triggers_created(self, store: CLISessionStore):
        triggers = store._list_triggers()
        assert "trg_update_daily_stats" in triggers
        assert "trg_delete_daily_stats" in triggers

    def test_wal_mode_enabled(self, store: CLISessionStore):
        result = store._pragma("journal_mode")
        assert result == "wal"


class TestCLISessionStoreCRUD:
    def test_save_and_get(self, store: CLISessionStore):
        record = CLISessionRecord(
            session_id="sess-001",
            backend_name="claude-code",
            model="claude-sonnet-4-20250514",
            name="planning session",
        )
        store.save_session(record)
        retrieved = store.get_session("sess-001")
        assert retrieved is not None
        assert retrieved.session_id == "sess-001"
        assert retrieved.backend_name == "claude-code"
        assert retrieved.model == "claude-sonnet-4-20250514"
        assert retrieved.name == "planning session"

    def test_get_nonexistent_returns_none(self, store: CLISessionStore):
        assert store.get_session("nonexistent") is None

    def test_get_by_task(self, store: CLISessionStore):
        store.save_session(CLISessionRecord(session_id="s1", backend_name="cc", task_id="task-1"))
        store.save_session(CLISessionRecord(session_id="s2", backend_name="gc", task_id="task-1"))
        store.save_session(CLISessionRecord(session_id="s3", backend_name="cc", task_id="task-2"))
        results = store.get_sessions_by_task("task-1")
        assert len(results) == 2
        assert {r.session_id for r in results} == {"s1", "s2"}

    def test_update_session(self, store: CLISessionStore):
        store.save_session(CLISessionRecord(session_id="s1", backend_name="cc", turn_count=1))
        store.save_session(CLISessionRecord(session_id="s1", backend_name="cc", turn_count=3))
        retrieved = store.get_session("s1")
        assert retrieved.turn_count == 3


class TestTaskExecutions:
    def test_record_execution(self, store: CLISessionStore):
        store.record_execution(
            task_id="task-1",
            backend_type="cli",
            backend_name="claude-code",
            model="claude-sonnet-4-20250514",
            session_id="sess-001",
            input_tokens=100,
            output_tokens=50,
            duration_ms=1500,
            status="success",
        )

    def test_daily_stats_auto_updated_via_trigger(self, store: CLISessionStore):
        store.record_execution(
            task_id="t1",
            backend_type="cli",
            backend_name="claude-code",
            status="success",
            input_tokens=100,
            output_tokens=50,
            duration_ms=1000,
        )
        store.record_execution(
            task_id="t2",
            backend_type="cli",
            backend_name="claude-code",
            status="error",
            input_tokens=50,
            output_tokens=0,
            duration_ms=500,
        )
        stats = store.get_daily_stats()
        assert len(stats) == 1
        assert stats[0]["total_calls"] == 2
        assert stats[0]["success_calls"] == 1
        assert stats[0]["total_input_tokens"] == 150
        assert stats[0]["total_output_tokens"] == 50


class TestBackendHealth:
    def test_update_and_get_health(self, store: CLISessionStore):
        store.update_health("claude-code", available=True, version="1.0.0")
        health = store.get_health("claude-code")
        assert health is not None
        assert health["is_available"] == 1
        assert health["version"] == "1.0.0"

    def test_get_nonexistent_health(self, store: CLISessionStore):
        assert store.get_health("nonexistent") is None


class TestCleanup:
    def test_cleanup_old_sessions(self, store: CLISessionStore):
        store._conn.execute(
            "INSERT INTO cli_sessions (session_id, backend_name, created_at, last_used_at) "
            "VALUES ('old-sess', 'cc', '2020-01-01T00:00:00', '2020-01-01T00:00:00')"
        )
        store._conn.commit()
        store.save_session(CLISessionRecord(session_id="new-sess", backend_name="cc"))
        deleted = store.cleanup_sessions(max_age_days=30)
        assert deleted >= 1
        assert store.get_session("old-sess") is None
        assert store.get_session("new-sess") is not None
