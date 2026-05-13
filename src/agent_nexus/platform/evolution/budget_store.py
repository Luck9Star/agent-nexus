"""BudgetStore -- context budget logging and maintenance operations.

Extracted from EvolutionStore to improve cohesion.  Each method operates on
context_budget_log and handles cross-table maintenance (clear, prune).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

from agent_nexus.platform.utils import (
    now_iso as _now_iso,
)

logger = logging.getLogger(__name__)

# Type alias for the connection factory callable used by sub-stores.
ConnFactory = Callable[..., AbstractContextManager[sqlite3.Connection]]


class BudgetStore:
    """SQLite-backed persistence for context budget events and maintenance.

    Uses WAL mode for concurrent read/write.  Connection-per-operation
    pattern for async compatibility.

    Accepts an optional ``conn_factory`` so the EvolutionStore facade
    can inject its own ``_conn`` method.
    """

    def __init__(
        self,
        db_path: Path,
        conn_factory: ConnFactory | None = None,
    ) -> None:
        self._db_path = db_path
        self._is_memory = str(db_path) == ":memory:"
        self._memory_conn: sqlite3.Connection | None = None
        self._conn_factory: ConnFactory | None = conn_factory

    @contextmanager
    def _conn(self, *, immediate: bool = False) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for DB connections.

        Delegates to the injected ``conn_factory`` when available (facade
        mode), otherwise creates its own connection (standalone mode).
        """
        if self._conn_factory is not None:
            with self._conn_factory(immediate=immediate) as conn:
                yield conn
            return

        # Standalone mode: manage connections internally.
        from agent_nexus.platform.utils import sqlite_connection

        if self._is_memory and self._memory_conn is None:
            self._memory_conn = sqlite3.connect(":memory:")
            self._memory_conn.execute("PRAGMA foreign_keys=ON")

        with sqlite_connection(
            self._db_path,
            immediate=immediate,
            persistent_conn=self._memory_conn,
        ) as conn:
            yield conn

    # ------------------------------------------------------------------
    # Context Budget Log
    # ------------------------------------------------------------------

    def log_budget_event(
        self,
        agent_name: str,
        event_type: str,
        tokens_before: int | None = None,
        tokens_after: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> str:
        """Record a context budget event."""
        log_id = str(uuid.uuid4())
        details_json = json.dumps(details or {}, ensure_ascii=False)
        with self._conn(immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO context_budget_log (
                    id, agent_name, event_type,
                    tokens_before, tokens_after, details, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log_id,
                    agent_name,
                    event_type,
                    tokens_before,
                    tokens_after,
                    details_json,
                    _now_iso(),
                ),
            )
        return log_id

    def get_budget_log(self, agent_name: str, limit: int = 50) -> list[dict[str, Any]]:
        """Load recent budget log entries for an agent."""
        if limit < 1:
            limit = 1
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, agent_name, event_type, tokens_before, "
                "tokens_after, details, created_at "
                "FROM context_budget_log WHERE agent_name = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (agent_name, limit),
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "agent_name": r[1],
                    "event_type": r[2],
                    "tokens_before": r[3],
                    "tokens_after": r[4],
                    "details": r[5],
                    "created_at": r[6],
                }
                for r in rows
            ]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Clear all tables. For testing only — crosses sub-store boundaries by design."""
        with self._conn(immediate=True) as conn:
            conn.execute("DELETE FROM experiments")
            conn.execute("DELETE FROM skill_judgments")
            conn.execute("DELETE FROM execution_analyses")
            conn.execute("DELETE FROM skill_lineage_parents")
            conn.execute("DELETE FROM context_budget_log")
            conn.execute("DELETE FROM agent_records")
            conn.execute("DELETE FROM skill_records")

    def prune_budget_log(
        self,
        max_age_days: int = 30,
        max_rows: int = 10_000,
    ) -> int:
        """Prune old or excess rows from ``context_budget_log``."""
        deleted = 0
        with self._conn(immediate=True) as conn:
            cur = conn.execute(
                "DELETE FROM context_budget_log WHERE created_at < datetime('now', ?)",
                (f"-{max_age_days} days",),
            )
            deleted += cur.rowcount

            count = conn.execute("SELECT COUNT(*) FROM context_budget_log").fetchone()[0]
            if count > max_rows:
                excess = count - max_rows
                cur = conn.execute(
                    "DELETE FROM context_budget_log "
                    "WHERE id IN ("
                    "  SELECT id FROM context_budget_log "
                    "  ORDER BY created_at ASC LIMIT ?"
                    ")",
                    (excess,),
                )
                deleted += cur.rowcount

        return deleted
