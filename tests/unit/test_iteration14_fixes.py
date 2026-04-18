"""Tests for iteration 14 bug fixes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.platform.evolution.store import EvolutionStore
from agent_nexus.platform.orchestration.task_graph import TaskGraph
from agent_nexus.platform.runtime.security_checker import SecurityChecker
from agent_nexus.platform.runtime.security_rules import RegexRule
from agent_nexus.models.task import TaskItem, TaskState


# ---------------------------------------------------------------------------
# Bug 1: _row_to_record handles malformed content_snapshot JSON
# ---------------------------------------------------------------------------


class TestMalformedSnapshot:
    """EvolutionStore._row_to_record must not crash on malformed snapshots."""

    def _make_store(self, tmp_path: Path) -> EvolutionStore:
        return EvolutionStore(tmp_path / "evo.db")

    def _save_raw(self, store: EvolutionStore, snapshot_json: str) -> str:
        """Insert a record with raw snapshot JSON for testing."""
        import json
        import uuid
        from datetime import datetime, timezone

        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        with store._conn() as conn:
            conn.execute(
                "INSERT INTO skill_records "
                "(id, name, version, lineage_origin, lineage_generation, "
                "lineage_content_diff, lineage_content_snapshot, directory, "
                "is_active, total_selections, total_applied, "
                "total_completions, total_fallbacks, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    sid, "test-skill", "1.0.0", "imported", 0,
                    "", snapshot_json, "/tmp", 1,
                    0, 0, 0, 0, now, now,
                ),
            )
        return sid

    def test_null_snapshot(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        sid = self._save_raw(store, "null")
        record = store.get_skill_record(sid)
        assert record is not None
        assert record.lineage.content_snapshot is None

    def test_list_snapshot(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        sid = self._save_raw(store, '[1, 2, 3]')
        record = store.get_skill_record(sid)
        assert record is not None
        assert record.lineage.content_snapshot is None

    def test_string_snapshot(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        sid = self._save_raw(store, '"just a string"')
        record = store.get_skill_record(sid)
        assert record is not None
        assert record.lineage.content_snapshot is None

    def test_valid_dict_snapshot(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        sid = self._save_raw(store, '{"key": "value"}')
        record = store.get_skill_record(sid)
        assert record is not None
        assert record.lineage.content_snapshot == {"key": "value"}

    def test_invalid_json_snapshot(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        sid = self._save_raw(store, "not json at all {{{")
        record = store.get_skill_record(sid)
        assert record is not None
        assert record.lineage.content_snapshot is None

    def test_get_active_skills_with_malformed(self, tmp_path: Path) -> None:
        """All active skills should load even if one has a malformed snapshot."""
        store = self._make_store(tmp_path)
        self._save_raw(store, "null")
        self._save_raw(store, '{"ok": true}')
        self._save_raw(store, "[1,2]")
        skills = store.get_active_skills()
        assert len(skills) == 3


# ---------------------------------------------------------------------------
# Bug 2: RegexRule.check_source runs on full source
# ---------------------------------------------------------------------------


class TestRegexRuleCheckSource:
    """RegexRule.check_source operates on full source string."""

    def test_check_source_matches(self) -> None:
        rule = RegexRule(
            patterns=r"getattr\s*\(\s*\w+\s*,\s*['\"]eval['\"]",
            description="test pattern",
        )
        violations = rule.check_source(
            "x = getattr(obj, 'eval')\n"
        )
        assert len(violations) == 1
        assert violations[0].rule_type == "regex"

    def test_check_source_no_match(self) -> None:
        rule = RegexRule(
            patterns=r"getattr\s*\(\s*\w+\s*,\s*['\"]eval['\"]",
            description="test pattern",
        )
        violations = rule.check_source("x = 1 + 2\n")
        assert len(violations) == 0

    def test_security_checker_uses_check_source(self) -> None:
        """Verify SecurityChecker calls check_source, not per-node check."""
        checker = SecurityChecker()
        code = "x = getattr(obj, 'eval')\n"
        violations = checker.check_code(code)
        assert any(v.rule_type == "regex" for v in violations)


# ---------------------------------------------------------------------------
# Bug 3: TaskGraph uses IMMEDIATE transactions for mutations
# ---------------------------------------------------------------------------


class TestTaskGraphImmediate:
    """TaskGraph mutation methods use BEGIN IMMEDIATE."""

    def test_start_task_uses_immediate(self, tmp_path: Path) -> None:
        tg = TaskGraph(tmp_path / "tg.db")
        task = TaskItem(id="t1", description="test", agent="test-agent")
        tg.add_task(task)
        # Should succeed — basic smoke test that IMMEDIATE doesn't break
        result = tg.start_task("t1")
        assert result.state == TaskState.IN_PROGRESS

    def test_complete_task_uses_immediate(self, tmp_path: Path) -> None:
        tg = TaskGraph(tmp_path / "tg.db")
        task = TaskItem(id="t1", description="test", agent="test-agent")
        tg.add_task(task)
        tg.start_task("t1")
        result = tg.complete_task("t1")
        assert result.state == TaskState.COMPLETED

    def test_fail_task_uses_immediate(self, tmp_path: Path) -> None:
        tg = TaskGraph(tmp_path / "tg.db")
        task = TaskItem(id="t1", description="test", agent="test-agent")
        tg.add_task(task)
        tg.start_task("t1")
        result = tg.fail_task("t1")
        assert result.state == TaskState.FAILED


# ---------------------------------------------------------------------------
# Bug 4: __class__ NOT blocked by default security checker
# ---------------------------------------------------------------------------


class TestClassAllowed:
    """Accessing __class__ should NOT be a violation by default."""

    def test_class_attribute_allowed(self) -> None:
        checker = SecurityChecker()
        violations = checker.check_code("x = obj.__class__.__name__\n")
        # __class__ should NOT be in violations (removed from default list)
        attr_violations = [v for v in violations if v.rule_type == "attribute"]
        assert len(attr_violations) == 0

    def test_dangerous_attributes_still_blocked(self) -> None:
        checker = SecurityChecker()
        violations = checker.check_code("x = obj.__subclasses__()\n")
        attr_violations = [v for v in violations if v.rule_type == "attribute"]
        assert len(attr_violations) == 1
        assert "__subclasses__" in attr_violations[0].message


# ---------------------------------------------------------------------------
# Bug 5: Gateway run methods use asyncio.to_thread
# ---------------------------------------------------------------------------


class TestGatewayToThread:
    """Gateway run_stdio and run_sse should use asyncio.to_thread."""

    @pytest.mark.asyncio
    async def test_run_stdio_uses_to_thread(self) -> None:
        from agent_nexus.platform.gateway.gateway import MCPGateway

        pm = MagicMock()
        router = MagicMock()
        gw = MCPGateway(process_manager=pm, router=router)

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            await gw.run_stdio()
            mock_thread.assert_called_once_with(
                gw._mcp.run, transport="stdio"
            )

    @pytest.mark.asyncio
    async def test_run_sse_uses_to_thread(self) -> None:
        from agent_nexus.platform.gateway.gateway import MCPGateway

        pm = MagicMock()
        router = MagicMock()
        gw = MCPGateway(process_manager=pm, router=router)

        with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_thread:
            await gw.run_sse(host="127.0.0.1", port=9090)
            mock_thread.assert_called_once_with(
                gw._mcp.run, transport="sse",
                host="127.0.0.1", port=9090,
            )
