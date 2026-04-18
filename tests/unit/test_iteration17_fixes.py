"""Tests for iteration 17 bug fixes — ProcessManager asyncio.Lock."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.platform.orchestration.ipc import IPCProtocol, IPCStream
from agent_nexus.platform.orchestration.process_manager import (
    AgentHandle,
    ProcessManager,
)


def _make_mock_process(pid: int = 12345, returncode=None) -> MagicMock:
    """Create a mock process with proper EOF-behavior for stream methods."""
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode

    # stdin: is_closing() returns False so close() path is exercised
    proc.stdin = AsyncMock()
    proc.stdin.is_closing = MagicMock(return_value=False)
    proc.stdin.close = MagicMock()
    proc.stdin.wait_closed = AsyncMock()

    # stdout: read() returns b"" so IPCStream.close() drain loop terminates
    proc.stdout = AsyncMock()
    proc.stdout.read = AsyncMock(return_value=b"")
    proc.stdout.readline = AsyncMock(return_value=b"")

    # stderr: readline() returns b"" so drain tasks terminate
    mock_stderr = AsyncMock()
    mock_stderr.readline = AsyncMock(return_value=b"")
    proc.stderr = mock_stderr
    return proc


def _make_handle(name: str, pid: int = 10000, returncode=None) -> AgentHandle:
    """Create an AgentHandle with a mock process + IPC."""
    proc = _make_mock_process(pid=pid, returncode=returncode)
    stream = IPCStream(stdin=proc.stdin, stdout=proc.stdout)
    ipc = IPCProtocol(stream)
    return AgentHandle(name=name, process=proc, ipc=ipc)


_SUBPROCESS_PATCH = (
    "agent_nexus.platform.orchestration.process_manager"
    ".asyncio.create_subprocess_exec"
)


class TestProcessManagerLock:
    """ProcessManager.start_agent must serialize concurrent calls."""

    def test_lock_initialized(self) -> None:
        pm = ProcessManager()
        assert isinstance(pm._lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_start_agent_acquires_lock(self) -> None:
        pm = ProcessManager()
        mock_process = _make_mock_process()

        with patch(_SUBPROCESS_PATCH, return_value=mock_process):
            await pm.start_agent("test-agent", command=["echo", "hi"])

        assert pm.get_agent("test-agent") is not None
        assert "test-agent" in pm.list_running()

    @pytest.mark.asyncio
    async def test_concurrent_start_rejected(self) -> None:
        """Two concurrent start_agent calls with the same name — second fails."""
        pm = ProcessManager()
        call_count = 0

        async def fake_create(*_a, **_kw):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)
            return _make_mock_process(pid=10000 + call_count)

        with patch(_SUBPROCESS_PATCH, side_effect=fake_create):
            results = await asyncio.gather(
                pm.start_agent("dup-agent", command=["echo", "1"]),
                pm.start_agent("dup-agent", command=["echo", "2"]),
                return_exceptions=True,
            )

        errors = [r for r in results if isinstance(r, ValueError)]
        successes = [r for r in results if isinstance(r, AgentHandle)]
        assert len(errors) == 1
        assert len(successes) == 1
        assert "already running" in str(errors[0])

    @pytest.mark.asyncio
    async def test_different_names_concurrent(self) -> None:
        """start_agent with different names — both succeed."""
        pm = ProcessManager()

        async def fake_create(*_a, **_kw):
            return _make_mock_process()

        with patch(_SUBPROCESS_PATCH, side_effect=fake_create):
            results = await asyncio.gather(
                pm.start_agent("agent-a", command=["echo", "a"]),
                pm.start_agent("agent-b", command=["echo", "b"]),
                return_exceptions=True,
            )

        successes = [r for r in results if isinstance(r, AgentHandle)]
        assert len(successes) == 2
        assert pm.get_agent("agent-a") is not None
        assert pm.get_agent("agent-b") is not None

    @pytest.mark.asyncio
    async def test_stale_handle_cleanup_under_lock(self) -> None:
        """A dead handle for the same name is replaced by a new start."""
        pm = ProcessManager()
        dead_handle = _make_handle("recycle-agent", pid=11111, returncode=1)
        pm._agents["recycle-agent"] = dead_handle

        new_proc = _make_mock_process(pid=55555)
        with patch(_SUBPROCESS_PATCH, return_value=new_proc):
            handle = await pm.start_agent(
                "recycle-agent", command=["echo", "new"],
            )

        assert handle.pid == 55555
        assert pm.get_agent("recycle-agent") is handle

    @pytest.mark.asyncio
    async def test_stop_all_runs_concurrently(self) -> None:
        """stop_all should stop all agents without deadlock."""
        pm = ProcessManager()
        for name in ("a", "b", "c"):
            pm._agents[name] = _make_handle(name, returncode=0)

        await pm.stop_all(timeout=1.0)
        assert len(pm.list_running()) == 0

    @pytest.mark.asyncio
    async def test_restart_preserves_params(self) -> None:
        """restart_agent reuses original command/cwd/env."""
        pm = ProcessManager()
        original = _make_mock_process(pid=77777)
        with patch(_SUBPROCESS_PATCH, return_value=original):
            await pm.start_agent(
                "restart-me",
                command=["python", "-m", "agent"],
                cwd="/tmp/test",
                env={"KEY": "val"},
            )

        pm.get_agent("restart-me").process.returncode = 0

        restarted = _make_mock_process(pid=88888)
        with patch(_SUBPROCESS_PATCH, return_value=restarted):
            handle = await pm.restart_agent("restart-me")

        assert handle.pid == 88888
        assert handle.start_command == ["python", "-m", "agent"]
