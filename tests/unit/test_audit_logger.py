"""Unit tests for AuditLogger, AuditEvent, and AuditFilter."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import time
from pathlib import Path

import pytest  # noqa: F401 — needed for tmp_path fixture

from agent_nexus.platform.gateway.audit import (
    _MAX_SUMMARY_LEN,
    AuditEvent,
    AuditFilter,
    AuditLogger,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(**overrides) -> AuditEvent:
    """Create an AuditEvent with sensible defaults."""
    defaults = dict(
        event_type="tool_call",
        client_id="client-1",
        agent_id="agent-1",
        tool_name="test_tool",
        request_summary="test request",
        response_status="success",
        duration_ms=42.5,
    )
    defaults.update(overrides)
    return AuditEvent(**defaults)


def _make_logger(tmp_path: Path, max_size_mb: float = 500) -> AuditLogger:
    return AuditLogger(str(tmp_path / "audit.db"), max_size_mb=max_size_mb)


# ============================================================================
# AuditEvent model
# ============================================================================


class TestAuditEvent:
    def test_invalid_event_type(self) -> None:
        with pytest.raises(Exception):
            AuditEvent(event_type="invalid_type")


# ============================================================================
# AuditFilter model
# ============================================================================


class TestAuditFilter:
    def test_create_with_defaults(self) -> None:
        filt = AuditFilter()
        assert filt.event_types is None
        assert filt.client_id is None
        assert filt.limit == 100

    def test_create_with_all_fields(self) -> None:
        filt = AuditFilter(
            event_types=["tool_call", "auth_success"],
            client_id="c1",
            agent_id="a1",
            tool_name="t1",
            since=1000.0,
            until=2000.0,
            limit=50,
        )
        assert filt.event_types == ["tool_call", "auth_success"]
        assert filt.client_id == "c1"
        assert filt.since == 1000.0
        assert filt.until == 2000.0
        assert filt.limit == 50


# ============================================================================
# AuditLogger.log — writes event to SQLite
# ============================================================================


class TestAuditLoggerLog:
    @pytest.mark.asyncio
    async def test_log_writes_to_db(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        event = _make_event()
        await al.log(event)

        conn = sqlite3.connect(str(tmp_path / "audit.db"))
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM audit_events").fetchone()
            assert row is not None
            assert row["event_id"] == event.event_id
            assert row["event_type"] == "tool_call"
            assert row["client_id"] == "client-1"
            assert row["tool_name"] == "test_tool"
            assert row["response_status"] == "success"
            assert row["duration_ms"] == 42.5
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_log_multiple_events(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        for i in range(5):
            await al.log(_make_event(event_type="tool_call", agent_id=f"agent-{i}"))

        conn = sqlite3.connect(str(tmp_path / "audit.db"))
        try:
            count = conn.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0]
            assert count == 5
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_log_stores_metadata_as_json(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        event = _make_event(metadata={"user": "alice", "ip": "127.0.0.1"})
        await al.log(event)

        conn = sqlite3.connect(str(tmp_path / "audit.db"))
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM audit_events").fetchone()
            stored_meta = json.loads(row["metadata"])
            assert stored_meta == {"user": "alice", "ip": "127.0.0.1"}
        finally:
            conn.close()


# ============================================================================
# AuditLogger.query — filter-based querying
# ============================================================================


class TestAuditLoggerQuery:
    @pytest.mark.asyncio
    async def test_query_all(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        await al.log(_make_event(event_type="tool_call"))
        await al.log(_make_event(event_type="auth_success"))

        results = await al.query(AuditFilter())
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_query_by_event_type(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        await al.log(_make_event(event_type="tool_call"))
        await al.log(_make_event(event_type="auth_success"))
        await al.log(_make_event(event_type="tool_result"))

        results = await al.query(AuditFilter(event_types=["tool_call"]))
        assert len(results) == 1
        assert results[0].event_type == "tool_call"

    @pytest.mark.asyncio
    async def test_query_by_client_id(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        await al.log(_make_event(client_id="client-A"))
        await al.log(_make_event(client_id="client-B"))
        await al.log(_make_event(client_id="client-A"))

        results = await al.query(AuditFilter(client_id="client-A"))
        assert len(results) == 2
        assert all(e.client_id == "client-A" for e in results)

    @pytest.mark.asyncio
    async def test_query_by_agent_id(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        await al.log(_make_event(agent_id="agent-X"))
        await al.log(_make_event(agent_id="agent-Y"))

        results = await al.query(AuditFilter(agent_id="agent-X"))
        assert len(results) == 1
        assert results[0].agent_id == "agent-X"

    @pytest.mark.asyncio
    async def test_query_by_tool_name(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        await al.log(_make_event(tool_name="search_and_activate"))
        await al.log(_make_event(tool_name="list_agents"))

        results = await al.query(AuditFilter(tool_name="search_and_activate"))
        assert len(results) == 1
        assert results[0].tool_name == "search_and_activate"

    @pytest.mark.asyncio
    async def test_query_by_time_range(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        t0 = time.time()
        await al.log(_make_event(timestamp=t0 - 100))
        await al.log(_make_event(timestamp=t0))
        await al.log(_make_event(timestamp=t0 + 100))

        results = await al.query(AuditFilter(since=t0, until=t0 + 50))
        assert len(results) == 1
        assert results[0].timestamp == t0

    @pytest.mark.asyncio
    async def test_query_limit(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        for i in range(10):
            await al.log(_make_event(agent_id=f"agent-{i}"))

        results = await al.query(AuditFilter(limit=3))
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_query_returns_audit_events(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        event = _make_event(metadata={"k": "v"})
        await al.log(event)

        results = await al.query(AuditFilter())
        assert len(results) == 1
        result = results[0]
        assert isinstance(result, AuditEvent)
        assert result.metadata == {"k": "v"}


# ============================================================================
# AuditLogger.export — JSON and CSV
# ============================================================================


class TestAuditLoggerExport:
    @pytest.mark.asyncio
    async def test_export_json(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        t0 = time.time()
        await al.log(_make_event(timestamp=t0, event_type="tool_call"))
        await al.log(_make_event(timestamp=t0 + 1, event_type="auth_success"))

        exported = await al.export("json", since=t0)
        data = json.loads(exported)
        assert isinstance(data, list)
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_export_csv(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        t0 = time.time()
        await al.log(_make_event(timestamp=t0, event_type="tool_call"))

        exported = await al.export("csv", since=t0)
        reader = csv.DictReader(io.StringIO(exported))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["event_type"] == "tool_call"

    @pytest.mark.asyncio
    async def test_export_csv_metadata_as_json_string(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        t0 = time.time()
        await al.log(_make_event(timestamp=t0, metadata={"foo": "bar"}))

        exported = await al.export("csv", since=t0)
        reader = csv.DictReader(io.StringIO(exported))
        row = next(reader)
        meta = json.loads(row["metadata"])
        assert meta == {"foo": "bar"}

    @pytest.mark.asyncio
    async def test_export_since_filters_old_events(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        t0 = time.time()
        await al.log(_make_event(timestamp=t0 - 1000))
        await al.log(_make_event(timestamp=t0))

        exported = await al.export("json", since=t0)
        data = json.loads(exported)
        assert len(data) == 1

    @pytest.mark.asyncio
    async def test_export_csv_empty(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        exported = await al.export("csv", since=0)
        assert exported == ""


# ============================================================================
# Size-based rotation
# ============================================================================


class TestAuditLoggerRotation:
    @pytest.mark.asyncio
    async def test_rotation_archives_db(self, tmp_path: Path) -> None:
        # SQLite empty DB with WAL is ~8KB, grows in 4KB pages.
        # Use 0.015 MB (~15KB) threshold: above empty DB (8KB) but below
        # what we get after 50+ events.  Each event with a 200-char summary
        # is ~500 bytes, so 50 events = ~25KB data + indexes > 15KB threshold.
        al = _make_logger(tmp_path, max_size_mb=0.015)
        db_path = tmp_path / "audit.db"

        # Write enough data to exceed the threshold
        for _ in range(50):
            await al.log(_make_event(request_summary="x" * 200))

        # The db file should still exist (fresh after rotation)
        assert db_path.exists()

        # At least one .bak archive should exist
        bak_files = list(tmp_path.glob("audit.db.*.bak"))
        assert len(bak_files) >= 1


# ============================================================================
# request_summary truncation
# ============================================================================


class TestRequestSummaryTruncation:
    @pytest.mark.asyncio
    async def test_summary_truncated_in_db(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        long_summary = "A" * 500
        event = _make_event(request_summary=long_summary)
        await al.log(event)

        conn = sqlite3.connect(str(tmp_path / "audit.db"))
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT request_summary FROM audit_events").fetchone()
            assert len(row["request_summary"]) == _MAX_SUMMARY_LEN
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_short_summary_not_truncated(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        short_summary = "short"
        event = _make_event(request_summary=short_summary)
        await al.log(event)

        conn = sqlite3.connect(str(tmp_path / "audit.db"))
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT request_summary FROM audit_events").fetchone()
            assert row["request_summary"] == short_summary
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_none_summary_stored_as_none(self, tmp_path: Path) -> None:
        al = _make_logger(tmp_path)
        event = _make_event(request_summary=None)
        await al.log(event)

        conn = sqlite3.connect(str(tmp_path / "audit.db"))
        try:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT request_summary FROM audit_events").fetchone()
            assert row["request_summary"] is None
        finally:
            conn.close()
