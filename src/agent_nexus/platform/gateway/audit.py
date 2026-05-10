"""AuditLogger — SQLite-backed audit trail for MCP Gateway.

Phase 0-1 of the Gateway Security roadmap (P0-2).

Phase 0 — FastMCP Middleware Feasibility (findings documented below)
Phase 1 — AuditLogger + AuditEvent + AuditFilter

FastMCP Middleware Investigation (Phase 0)
==========================================

FastMCP 3.x (currently 3.2.4 in this project) supports TWO middleware layers:

1. **Protocol-level middleware** via ``FastMCP.add_middleware(mw)``.
   Subclass ``fastmcp.server.middleware.Middleware`` and override hooks like
   ``on_call_tool``, ``on_request``, ``on_message``.  The hook receives a
   ``MiddlewareContext`` with ``.method``, ``.source``, ``.type``, and
   ``.timestamp``.  This is the **recommended** approach for Phase 2 auth
   because it works across ALL transports (stdio, SSE, HTTP).

   Integration sketch for Phase 2::

       from fastmcp.server.middleware import Middleware, MiddlewareContext

       class AuthMiddleware(Middleware):
           async def on_call_tool(self, context, call_next):
               # inspect context.message for tool name + params
               # audit log the call, check auth, then proceed
               result = await call_next(context)
               # audit log the result
               return result

       gateway._mcp.add_middleware(AuthMiddleware())

2. **ASGI middleware** via ``FastMCP.http_app(middleware=[...])``.
   Only applies to HTTP/SSE/streamable-http transports.  Useful for
   rate-limiting, CORS, or any HTTP-layer concern.  NOT suitable for
   auth because stdio transport bypasses ASGI entirely.

Conclusion: Use protocol-level middleware (approach 1) for Phase 2 auth.
No reverse proxy needed.

Phase 1 — AuditLogger Design
=============================

- SQLite WAL mode for concurrent reads/writes without blocking.
- ``asyncio.to_thread`` wraps all sqlite3 calls (no aiosqlite dependency).
- Size-based rotation: archive + recreate when db exceeds ``max_size_mb``.
- ``request_summary`` truncated to 200 chars to prevent sensitive data leaks.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Maximum length for request_summary to prevent sensitive data leakage.
_MAX_SUMMARY_LEN = 200

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class AuditEvent(BaseModel):
    """A single audit event recorded by the gateway."""

    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: float = Field(default_factory=time.time)
    event_type: Literal[
        "auth_success",
        "auth_failure",
        "tool_call",
        "tool_result",
        "agent_activation",
        "agent_error",
        "external_server_call",
        "config_change",
    ]
    client_id: str | None = None
    agent_id: str | None = None
    tool_name: str | None = None
    request_summary: str | None = None
    response_status: str | None = None
    duration_ms: float | None = None
    metadata: dict = Field(default_factory=dict)


class AuditFilter(BaseModel):
    """Filter for querying audit events."""

    event_types: list[str] | None = None
    client_id: str | None = None
    agent_id: str | None = None
    tool_name: str | None = None
    since: float | None = None
    until: float | None = None
    limit: int = 100


# ---------------------------------------------------------------------------
# AuditSink protocol (G5: pluggable event sinks)
# ---------------------------------------------------------------------------


class AuditSink(Protocol):
    """Protocol for audit event sinks."""

    async def write(self, event: AuditEvent) -> None: ...


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS audit_events (
    event_id        TEXT PRIMARY KEY,
    timestamp       REAL NOT NULL,
    event_type      TEXT NOT NULL,
    client_id       TEXT,
    agent_id        TEXT,
    tool_name       TEXT,
    request_summary TEXT,
    response_status TEXT,
    duration_ms     REAL,
    metadata        TEXT  -- JSON string
)
"""

_CREATE_INDEX_SQL = """\
CREATE INDEX IF NOT EXISTS idx_audit_timestamp
    ON audit_events (timestamp)
"""

_CREATE_EVENT_TYPE_INDEX_SQL = """\
CREATE INDEX IF NOT EXISTS idx_audit_event_type
    ON audit_events (event_type)
"""


# ---------------------------------------------------------------------------
# AuditLogger
# ---------------------------------------------------------------------------


class AuditLogger:
    """SQLite-backed async audit logger for MCP Gateway.

    Uses SQLite WAL mode for concurrent read/write and ``asyncio.to_thread``
    to avoid blocking the event loop.  Supports size-based rotation.

    Args:
        db_path: Path to the SQLite database file.
        max_size_mb: Maximum database size in MB before rotation (default 500).
    """

    def __init__(
        self,
        db_path: str,
        max_size_mb: float = 500,
        sinks: list[AuditSink] | None = None,
    ) -> None:
        self._db_path = Path(db_path)
        self._max_size_bytes = int(max_size_mb * 1024 * 1024)
        self._sinks: list[AuditSink] = sinks or []
        self._rotate_lock = threading.Lock()
        self._init_db()

    # ------------------------------------------------------------------
    # Internal: synchronous SQLite operations
    # ------------------------------------------------------------------

    def _init_db(self) -> None:
        """Create the database, table, and indexes if they don't exist."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(_CREATE_TABLE_SQL)
            conn.execute(_CREATE_INDEX_SQL)
            conn.execute(_CREATE_EVENT_TYPE_INDEX_SQL)
            conn.commit()
        finally:
            conn.close()

    def _insert_event(self, event: AuditEvent) -> None:
        """Insert a single event into the database.

        Design note: each call opens and closes its own SQLite connection.
        This trades raw throughput for simplicity and correctness under
        concurrent ``asyncio.to_thread`` calls.  SQLite WAL mode (enabled
        in ``_init_db``) allows concurrent readers and writers without
        blocking, mitigating the performance impact.
        """
        summary = event.request_summary
        if summary is not None and len(summary) > _MAX_SUMMARY_LEN:
            summary = summary[:_MAX_SUMMARY_LEN]

        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.execute(
                """\
                INSERT INTO audit_events
                    (event_id, timestamp, event_type, client_id, agent_id,
                     tool_name, request_summary, response_status, duration_ms,
                     metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.timestamp,
                    event.event_type,
                    event.client_id,
                    event.agent_id,
                    event.tool_name,
                    summary,
                    event.response_status,
                    event.duration_ms,
                    json.dumps(event.metadata),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _query_events(self, filt: AuditFilter) -> list[dict]:
        """Query events matching the filter."""
        clauses: list[str] = []
        params: list = []

        if filt.event_types:
            placeholders = ",".join("?" for _ in filt.event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(filt.event_types)
        if filt.client_id is not None:
            clauses.append("client_id = ?")
            params.append(filt.client_id)
        if filt.agent_id is not None:
            clauses.append("agent_id = ?")
            params.append(filt.agent_id)
        if filt.tool_name is not None:
            clauses.append("tool_name = ?")
            params.append(filt.tool_name)
        if filt.since is not None:
            clauses.append("timestamp >= ?")
            params.append(filt.since)
        if filt.until is not None:
            clauses.append("timestamp <= ?")
            params.append(filt.until)

        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT * FROM audit_events WHERE {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(filt.limit)

        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(sql, params)
            rows = [dict(row) for row in cursor.fetchall()]
            return rows
        finally:
            conn.close()

    def _export_events(self, since: float) -> list[dict]:
        """Export all events since a given timestamp."""
        conn = sqlite3.connect(str(self._db_path))
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM audit_events WHERE timestamp >= ? ORDER BY timestamp ASC",
                (since,),
            )
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def _get_db_size_bytes(self) -> int:
        """Return current database file size in bytes."""
        try:
            return self._db_path.stat().st_size
        except OSError:
            return 0

    def _rotate_if_needed(self) -> None:
        """Archive the current db and create a fresh one if size exceeded."""
        if not self._rotate_lock.acquire(blocking=False):
            return  # Another thread is rotating, skip
        try:
            if self._get_db_size_bytes() < self._max_size_bytes:
                return

            archive_path = Path(f"{self._db_path}.{int(time.time())}.bak")
            try:
                os.rename(str(self._db_path), str(archive_path))
                logger.info("Audit log rotated: archived to %s", archive_path)
            except OSError:
                logger.exception("Failed to archive audit log %s", self._db_path)
                return
            self._init_db()
        finally:
            self._rotate_lock.release()

    # ------------------------------------------------------------------
    # Public async API
    # ------------------------------------------------------------------

    async def log(self, event: AuditEvent) -> None:
        """Async write event to audit log and forward to sinks."""
        await asyncio.to_thread(self._insert_event, event)
        await asyncio.to_thread(self._rotate_if_needed)
        # Forward to pluggable sinks (G5)
        for sink in self._sinks:
            await sink.write(event)

    async def query(self, filt: AuditFilter) -> list[AuditEvent]:
        """Query audit events with filters."""
        rows = await asyncio.to_thread(self._query_events, filt)
        return [self._row_to_event(r) for r in rows]

    async def export(self, format: Literal["json", "csv"], since: float) -> str:
        """Export audit events since timestamp.

        Args:
            format: Export format — "json" or "csv".
            since: Unix timestamp; only events at or after this time are included.

        Returns:
            String containing the exported data.
        """
        rows = await asyncio.to_thread(self._export_events, since)
        if format == "json":
            return json.dumps(rows, indent=2)
        return self._rows_to_csv(rows)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_event(row: dict) -> AuditEvent:
        """Convert a database row dict to an AuditEvent."""
        metadata = row.get("metadata")
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        elif metadata is None:
            metadata = {}

        return AuditEvent(
            event_id=row["event_id"],
            timestamp=row["timestamp"],
            event_type=row["event_type"],
            client_id=row.get("client_id"),
            agent_id=row.get("agent_id"),
            tool_name=row.get("tool_name"),
            request_summary=row.get("request_summary"),
            response_status=row.get("response_status"),
            duration_ms=row.get("duration_ms"),
            metadata=metadata,
        )

    @staticmethod
    def _rows_to_csv(rows: list[dict]) -> str:
        """Convert rows to CSV string with metadata as JSON string."""
        if not rows:
            return ""

        # Flatten metadata to JSON string for CSV
        flat_rows = []
        for row in rows:
            flat = dict(row)
            if isinstance(flat.get("metadata"), dict):
                flat["metadata"] = json.dumps(flat["metadata"])
            flat_rows.append(flat)

        fieldnames = list(flat_rows[0].keys())
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(flat_rows)
        return buf.getvalue()
