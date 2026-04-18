"""Tests for iteration 15 bug fixes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.platform.evolution.store import EvolutionStore
from agent_nexus.platform.evolution.health import HealthReport


# ---------------------------------------------------------------------------
# Bug 1: save_agent_record preserves counters on re-save (ON CONFLICT)
# ---------------------------------------------------------------------------


class TestAgentRecordCounterPreservation:
    """save_agent_record must preserve effective_rate, avg_steps, etc."""

    def _make_store(self, tmp_path: Path) -> EvolutionStore:
        return EvolutionStore(tmp_path / "evo.db")

    def test_preserves_effective_rate_on_resave(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.save_agent_record("a1", "my-agent", "composite", ["s1"])
        # Simulate metrics being set via direct SQL
        with store._conn() as conn:
            conn.execute(
                "UPDATE agent_records SET effective_rate = 0.87, "
                "avg_steps = 4.5, avg_duration_ms = 1200.0 "
                "WHERE agent_id = ?",
                ("a1",),
            )
        # Re-save with updated skill_ids
        store.save_agent_record("a1", "my-agent", "composite", ["s1", "s2"])
        rec = store.get_agent_record("a1")
        assert rec is not None
        assert rec["effective_rate"] == 0.87
        assert rec["avg_steps"] == 4.5
        assert rec["avg_duration_ms"] == 1200.0
        # skill_ids should be updated
        assert rec["skill_ids"] == ["s1", "s2"]

    def test_preserves_is_active_on_resave(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.save_agent_record("a1", "agent", "atomic", [])
        # Deactivate
        with store._conn() as conn:
            conn.execute(
                "UPDATE agent_records SET is_active = 0 WHERE agent_id = ?",
                ("a1",),
            )
        # Re-save should NOT reactivate
        store.save_agent_record("a1", "agent", "atomic", ["s1"])
        rec = store.get_agent_record("a1")
        assert rec is not None
        assert rec["is_active"] is False

    def test_preserves_created_at_on_resave(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.save_agent_record("a1", "agent", "atomic", [])
        rec1 = store.get_agent_record("a1")
        original_created = rec1["created_at"]
        # Re-save
        store.save_agent_record("a1", "agent", "atomic", ["s1", "s2"])
        rec2 = store.get_agent_record("a1")
        assert rec2["created_at"] == original_created

    def test_initial_save_defaults(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.save_agent_record("a1", "agent", "atomic", ["s1"])
        rec = store.get_agent_record("a1")
        assert rec is not None
        assert rec["effective_rate"] == 0.0
        assert rec["avg_steps"] is None
        assert rec["is_active"] is True


# ---------------------------------------------------------------------------
# Bug 2: _run_git uses communicate() not wait()+read()
# ---------------------------------------------------------------------------


class TestRunGitUsesCommunicate:
    """GitInstaller._run_git should use communicate() to avoid pipe deadlock."""

    @pytest.mark.asyncio
    async def test_run_git_uses_communicate(self) -> None:
        from agent_nexus.platform.local.installer import GitInstaller

        # Patch create_subprocess_exec to return a mock process
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_proc
        ) as mock_create:
            await GitInstaller._run_git(["status"], Path("/tmp"))
            mock_create.assert_called_once()
            mock_proc.communicate.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_git_includes_stderr_on_failure(self) -> None:
        from agent_nexus.platform.local.installer import (
            GitInstaller,
            InstallationError,
        )

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"error: pathspec 'x' did not match")
        )
        mock_proc.returncode = 128

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(InstallationError) as exc_info:
                await GitInstaller._run_git(["checkout", "x"], Path("/tmp"))
            assert "pathspec" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_git_capture_includes_stderr_on_failure(self) -> None:
        from agent_nexus.platform.local.installer import (
            GitInstaller,
            InstallationError,
        )

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"remote: Repository not found")
        )
        mock_proc.returncode = 128

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(InstallationError) as exc_info:
                await GitInstaller._run_git_capture(["ls-remote", "url"], Path("/tmp"))
            assert "Repository not found" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Bug 3: IPC heartbeat checks for "pong" content
# ---------------------------------------------------------------------------


class TestIPCHearbeatPongCheck:
    """send_heartbeat should only accept PROGRESS messages with pong content."""

    @pytest.mark.asyncio
    async def test_heartbeat_rejects_progress_without_pong(self) -> None:
        """A PROGRESS message without 'pong' content should NOT be accepted."""
        from agent_nexus.platform.orchestration.ipc import (
            IPCProtocol,
            IPCStream,
            AgentToPlatformType,
        )

        mock_stdin = AsyncMock()
        mock_stdout = MagicMock()
        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        protocol = IPCProtocol(stream)

        # Mock send_chat and receive
        with patch.object(protocol, "send_chat", new_callable=AsyncMock):
            with patch.object(
                protocol._stream,
                "receive",
                new_callable=AsyncMock,
                side_effect=[
                    MagicMock(
                        type=AgentToPlatformType.PROGRESS,
                        content="working on task...",
                        task_id="t1",
                    ),
                    asyncio.TimeoutError(),
                ],
            ):
                result = await protocol.send_heartbeat()
        # Should NOT return True for non-pong progress
        assert result is False
        # The non-pong message should be buffered
        assert len(protocol._peek_buffer) >= 1

    @pytest.mark.asyncio
    async def test_heartbeat_accepts_progress_with_pong(self) -> None:
        """A PROGRESS message with 'pong' content should be accepted."""
        from agent_nexus.platform.orchestration.ipc import (
            IPCProtocol,
            IPCStream,
            AgentToPlatformType,
        )

        mock_stdin = AsyncMock()
        mock_stdout = MagicMock()
        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        protocol = IPCProtocol(stream)

        with patch.object(protocol, "send_chat", new_callable=AsyncMock):
            with patch.object(
                protocol._stream,
                "receive",
                new_callable=AsyncMock,
                return_value=MagicMock(
                    type=AgentToPlatformType.PROGRESS,
                    content="pong",
                ),
            ):
                result = await protocol.send_heartbeat()
        assert result is True


# ---------------------------------------------------------------------------
# Bug 4: HealthReport.summary uses key-based formatting
# ---------------------------------------------------------------------------


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
# Bug 5: IPython executor uses asyncio.to_thread for timeout enforcement
# ---------------------------------------------------------------------------


class TestExecutorUsesToThread:
    """IPythonExecutor should use asyncio.to_thread for timeout enforcement."""

    @pytest.mark.asyncio
    async def test_execute_uses_to_thread(self) -> None:
        """Verify execute delegates to asyncio.to_thread."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as mock_thread:
            result = await executor.execute("x = 1 + 2", timeout=10)
            mock_thread.assert_called_once()
            assert result.success is True
        assert executor.get("x") == 3

    @pytest.mark.asyncio
    async def test_run_cell_sync_returns_execution_result(self) -> None:
        """_run_cell_sync should return an IPython ExecutionResult."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        result = executor._run_cell_sync("x = 42")
        # Should be an IPython ExecutionResult-like object
        assert result is not None
        assert executor.get("x") == 42

    @pytest.mark.asyncio
    async def test_timeout_fires_for_long_running_code(self) -> None:
        """Timeout should fire even for synchronous CPU-bound code."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        # time.sleep is synchronous and blocks — to_thread lets the event
        # loop cancel the wrapper on timeout
        result = await executor.execute(
            "import time; time.sleep(10)", timeout=0.3
        )
        assert result.success is False
        assert "timed out" in (result.error or "").lower()
