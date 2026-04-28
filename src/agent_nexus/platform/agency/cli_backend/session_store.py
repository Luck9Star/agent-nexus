"""CLISessionStore — SQLite session persistence with WAL mode and triggers."""
from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_nexus.platform.agency.cli_backend.types import CLISessionRecord

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cli_sessions (
    session_id   TEXT PRIMARY KEY,
    name         TEXT,
    backend_name TEXT NOT NULL,
    model        TEXT,
    task_id      TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    last_used_at TEXT DEFAULT (datetime('now')),
    turn_count   INTEGER DEFAULT 1,
    metadata     TEXT
);

CREATE TABLE IF NOT EXISTS task_executions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL,
    backend_type  TEXT NOT NULL,
    backend_name  TEXT NOT NULL,
    model         TEXT,
    session_id    TEXT REFERENCES cli_sessions(session_id),
    input_tokens  INTEGER,
    output_tokens INTEGER,
    duration_ms   INTEGER,
    status        TEXT DEFAULT 'success',
    error         TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS backend_health (
    backend_name TEXT PRIMARY KEY,
    is_available INTEGER DEFAULT 0,
    last_check   TEXT,
    version      TEXT,
    error_msg    TEXT
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date         TEXT NOT NULL,
    backend_name TEXT NOT NULL,
    total_calls  INTEGER DEFAULT 0,
    success_calls INTEGER DEFAULT 0,
    total_input_tokens  INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    avg_duration_ms     REAL DEFAULT 0,
    PRIMARY KEY (date, backend_name)
);

CREATE TRIGGER IF NOT EXISTS trg_update_daily_stats
AFTER INSERT ON task_executions
BEGIN
    INSERT INTO daily_stats (date, backend_name, total_calls, success_calls,
                             total_input_tokens, total_output_tokens, avg_duration_ms)
    VALUES (DATE('now'), NEW.backend_name, 1,
            CASE WHEN NEW.status = 'success' THEN 1 ELSE 0 END,
            COALESCE(NEW.input_tokens, 0), COALESCE(NEW.output_tokens, 0),
            COALESCE(NEW.duration_ms, 0))
    ON CONFLICT(date, backend_name) DO UPDATE SET
        total_calls = total_calls + 1,
        success_calls = success_calls + CASE WHEN NEW.status = 'success' THEN 1 ELSE 0 END,
        total_input_tokens = total_input_tokens + COALESCE(NEW.input_tokens, 0),
        total_output_tokens = total_output_tokens + COALESCE(NEW.output_tokens, 0),
        avg_duration_ms =
            (avg_duration_ms * (total_calls - 1)
             + COALESCE(NEW.duration_ms, 0)) / total_calls;
END;

CREATE TRIGGER IF NOT EXISTS trg_delete_daily_stats
AFTER DELETE ON task_executions
BEGIN
    UPDATE daily_stats SET
        total_calls = total_calls - 1,
        success_calls = success_calls - CASE WHEN OLD.status = 'success' THEN 1 ELSE 0 END,
        total_input_tokens = total_input_tokens - COALESCE(OLD.input_tokens, 0),
        total_output_tokens = total_output_tokens - COALESCE(OLD.output_tokens, 0)
    WHERE date = DATE(OLD.created_at) AND backend_name = OLD.backend_name;
END;
"""

_PRAGMAS = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=1000;
PRAGMA synchronous=NORMAL;
"""


class CLISessionStore:
    """SQLite-backed session store with WAL mode and auto-stats triggers."""

    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_PRAGMAS)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- Schema inspection helpers (for testing) --

    def _list_tables(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows]

    def _list_triggers(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows]

    _ALLOWED_PRAGMAS = frozenset({
        "journal_mode", "busy_timeout", "synchronous", "foreign_keys",
        "wal_autocheckpoint", "page_count", "freelist_count",
    })

    def _pragma(self, key: str) -> str:
        if key not in self._ALLOWED_PRAGMAS:
            raise ValueError(f"PRAGMA '{key}' not in allowlist")
        row = self._conn.execute(f"PRAGMA {key}").fetchone()
        return dict(row)[key] if row else ""

    # -- Sessions --

    def save_session(self, record: CLISessionRecord) -> None:
        now = datetime.now(UTC).isoformat()
        metadata_json = json.dumps(record.metadata) if record.metadata else None
        self._conn.execute(
            "INSERT OR REPLACE INTO cli_sessions "
            "(session_id, name, backend_name, model, task_id, "
            " created_at, last_used_at, turn_count, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.session_id,
                record.name,
                record.backend_name,
                record.model,
                record.task_id,
                record.created_at or now,
                record.last_used_at or now,
                record.turn_count,
                metadata_json,
            ),
        )
        self._conn.commit()

    def get_session(self, session_id: str) -> CLISessionRecord | None:
        row = self._conn.execute(
            "SELECT * FROM cli_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_session(row)

    def get_sessions_by_task(self, task_id: str) -> list[CLISessionRecord]:
        rows = self._conn.execute(
            "SELECT * FROM cli_sessions WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def cleanup_sessions(self, max_age_days: int = 30) -> int:
        cursor = self._conn.execute(
            "DELETE FROM cli_sessions WHERE last_used_at < datetime('now', ?)",
            (f"-{max_age_days} days",),
        )
        self._conn.commit()
        return cursor.rowcount

    # -- Task Executions --

    def record_execution(
        self,
        task_id: str,
        backend_type: str,
        backend_name: str,
        model: str | None = None,
        session_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        duration_ms: int | None = None,
        status: str = "success",
        error: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO task_executions "
            "(task_id, backend_type, backend_name, model, session_id, "
            " input_tokens, output_tokens, duration_ms, status, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                backend_type,
                backend_name,
                model,
                session_id,
                input_tokens,
                output_tokens,
                duration_ms,
                status,
                error,
            ),
        )
        self._conn.commit()

    # -- Daily Stats --

    def get_daily_stats(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM daily_stats ORDER BY date DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # -- Backend Health --

    def update_health(
        self,
        backend_name: str,
        available: bool,
        version: str | None = None,
        error_msg: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO backend_health "
            "(backend_name, is_available, last_check, version, error_msg) "
            "VALUES (?, ?, ?, ?, ?)",
            (backend_name, int(available), now, version, error_msg),
        )
        self._conn.commit()

    def get_health(self, backend_name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM backend_health WHERE backend_name = ?",
            (backend_name,),
        ).fetchone()
        return dict(row) if row else None

    # -- Internal --

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> CLISessionRecord:
        metadata = None
        if row["metadata"]:
            with contextlib.suppress(json.JSONDecodeError):
                metadata = json.loads(row["metadata"])
        return CLISessionRecord(
            session_id=row["session_id"],
            backend_name=row["backend_name"],
            name=row["name"],
            model=row["model"],
            task_id=row["task_id"],
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
            turn_count=row["turn_count"] or 1,
            metadata=metadata,
        )
