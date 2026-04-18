"""Unit tests for ProcessManager — asyncio.subprocess agent lifecycle.

Tests start, stop (3-stage shutdown), restart, health check, and cleanup
using mocked subprocess and IPC layer.
"""

from __future__ import annotations

import asyncio
import signal
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.platform.orchestration.process_manager import (
    AgentHandle,
    ProcessManager,
)


# ============================================================================
# Fixtures
# ============================================================================


def _make_mock_process(
    returncode: int | None = None, pid: int = 12345
) -> MagicMock:
    """Create a mock asyncio.subprocess.Process."""
    proc = MagicMock(spec=asyncio.subprocess.Process)
    proc.returncode = returncode
    proc.pid = pid
    proc.stdin = MagicMock(spec=asyncio.StreamWriter)
    proc.stdout = MagicMock(spec=asyncio.StreamReader)
    proc.stderr = MagicMock(spec=asyncio.StreamReader)
    proc.wait = AsyncMock()
    proc.send_signal = MagicMock()
    proc.kill = MagicMock()
    return proc


@pytest.fixture
def pm() -> ProcessManager:
    """Fresh ProcessManager instance."""
    return ProcessManager()


# ============================================================================
# start_agent()
# ============================================================================


class TestStartAgent:
    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_start_agent_success(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """start_agent returns AgentHandle with correct fields."""
        mock_proc = _make_mock_process()
        mock_spawn.return_value = mock_proc

        handle = await pm.start_agent(
            name="agent-1",
            command=["python", "-m", "my_agent"],
            cwd=Path("/tmp/work"),
            env={"KEY": "value"},
        )

        assert isinstance(handle, AgentHandle)
        assert handle.name == "agent-1"
        assert handle.process is mock_proc
        assert handle.ipc is not None
        assert handle.start_command == ["python", "-m", "my_agent"]
        assert handle.start_cwd == Path("/tmp/work")
        assert handle.start_env == {"KEY": "value"}
        assert handle.pid == 12345

        # Verify subprocess created with correct params
        mock_spawn.assert_awaited_once()
        call_kwargs = mock_spawn.call_args
        assert "python" in call_kwargs[0]
        assert "-m" in call_kwargs[0]

    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_start_agent_duplicate_alive_raises(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """Starting agent with same name while alive raises ValueError."""
        # Use side_effect to return different mock objects per call
        mock_spawn.side_effect = [_make_mock_process(returncode=None)]

        await pm.start_agent(name="dup", command=["echo"])
        with pytest.raises(ValueError, match="already running"):
            await pm.start_agent(name="dup", command=["echo"])

    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_start_agent_dead_reuse(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """Starting agent with same name after process died succeeds (stale cleanup)."""
        # First start
        mock_spawn.return_value = _make_mock_process(returncode=None)
        await pm.start_agent(name="recycle", command=["echo"])

        # Simulate process death
        pm._agents["recycle"].process.returncode = 1

        # Second start should succeed
        mock_spawn.return_value = _make_mock_process(pid=99999)
        handle = await pm.start_agent(name="recycle", command=["echo", "2"])

        assert handle.pid == 99999
        assert handle.start_command == ["echo", "2"]

    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_start_agent_failure(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """Subprocess creation failure raises RuntimeError."""
        mock_spawn.side_effect = OSError("spawn failed")

        with pytest.raises(RuntimeError, match="Failed to start agent"):
            await pm.start_agent(name="bad", command=["nonexistent-cmd"])

    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_start_agent_registers_handle(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """Handle is registered in internal dict."""
        mock_spawn.return_value = _make_mock_process()
        handle = await pm.start_agent(name="tracked", command=["echo"])

        assert pm.get_agent("tracked") is handle


# ============================================================================
# stop_agent()
# ============================================================================


class TestStopAgent:
    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_stop_clean_exit(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """Stop: IPC close + process exits cleanly (stage 1).

        Mock asyncio.wait_for so process.wait() returns immediately without
        real timeout delays.
        """
        mock_proc = _make_mock_process(returncode=None)
        mock_spawn.return_value = mock_proc

        await pm.start_agent(name="graceful", command=["echo"])

        # process.wait() returns None (success), wrapped by wait_for
        mock_proc.wait = AsyncMock(return_value=None)

        with patch(
            "agent_nexus.platform.orchestration.process_manager.asyncio.wait_for",
            new=AsyncMock(return_value=None),
        ):
            await pm.stop_agent("graceful", timeout=1.0)

        # Agent should be removed from registry
        assert pm.get_agent("graceful") is None

    async def test_stop_not_found_raises(self, pm: ProcessManager) -> None:
        """Stopping unknown agent raises KeyError."""
        with pytest.raises(KeyError, match="not found"):
            await pm.stop_agent("ghost")

    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_stop_already_dead(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """Stopping an already-dead process cleans up gracefully."""
        mock_proc = _make_mock_process(returncode=0)
        mock_spawn.return_value = mock_proc

        await pm.start_agent(name="dead", command=["echo"])
        # Process already has returncode set (dead)
        await pm.stop_agent("dead")

        assert pm.get_agent("dead") is None

    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_stop_sigterm_stage(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """Stop: stage 1 timeout, then SIGTERM (stage 2) succeeds.

        Note: IPCStream.close() also calls asyncio.wait_for internally,
        so call_count=1 is from close(), call_count=2 is stage 1 (process.wait),
        and call_count=3 is stage 2 (process.wait after SIGTERM).
        """
        mock_proc = _make_mock_process(returncode=None)
        mock_spawn.return_value = mock_proc

        await pm.start_agent(name="stubborn", command=["echo"])

        call_count = 0

        async def _fake_wait_for(coro, timeout=None):
            nonlocal call_count
            call_count += 1
            coro.close()
            if call_count <= 2:
                # call 1: IPCStream.close() drain (tolerated)
                # call 2: stage 1 process.wait timeout
                raise asyncio.TimeoutError()
            # call 3: stage 2 process.wait succeeds after SIGTERM
            mock_proc.returncode = -signal.SIGTERM

        with patch(
            "agent_nexus.platform.orchestration.process_manager.asyncio.wait_for",
            side_effect=_fake_wait_for,
        ):
            await pm.stop_agent("stubborn", timeout=1.0)

        mock_proc.send_signal.assert_called_with(signal.SIGTERM)
        assert pm.get_agent("stubborn") is None

    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_stop_sigkill_stage(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """Stop: SIGTERM timeout, then SIGKILL (stage 3)."""
        mock_proc = _make_mock_process(returncode=None)
        mock_spawn.return_value = mock_proc

        await pm.start_agent(name="zombie", command=["echo"])

        async def _always_timeout(coro, timeout=None):
            # Close coroutine to suppress "never awaited" warning
            coro.close()
            raise asyncio.TimeoutError()

        with patch(
            "agent_nexus.platform.orchestration.process_manager.asyncio.wait_for",
            side_effect=_always_timeout,
        ):
            await pm.stop_agent("zombie", timeout=1.0)

        # SIGTERM sent, then SIGKILL
        mock_proc.send_signal.assert_called_with(signal.SIGTERM)
        mock_proc.kill.assert_called_once()
        assert pm.get_agent("zombie") is None

    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_stop_sigterm_process_lookup(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """Stop handles ProcessLookupError during SIGTERM (race condition)."""
        mock_proc = _make_mock_process(returncode=None)
        mock_spawn.return_value = mock_proc

        await pm.start_agent(name="race", command=["echo"])

        # Stage 1 timeout, then SIGTERM raises ProcessLookupError
        async def _timeout_then_race(coro, timeout=None):
            coro.close()
            raise asyncio.TimeoutError()

        mock_proc.send_signal.side_effect = ProcessLookupError()

        with patch(
            "agent_nexus.platform.orchestration.process_manager.asyncio.wait_for",
            side_effect=_timeout_then_race,
        ):
            await pm.stop_agent("race", timeout=1.0)

        assert pm.get_agent("race") is None


# ============================================================================
# restart_agent()
# ============================================================================


class TestRestartAgent:
    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_restart_reuses_params(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """restart_agent stops and starts with same params."""
        mock_spawn.return_value = _make_mock_process(returncode=None, pid=100)

        original = await pm.start_agent(
            name="restarter",
            command=["cmd"],
            cwd=Path("/work"),
            env={"X": "1"},
        )

        # For restart: first call's process should "die" so stop works
        pm._agents["restarter"].process.returncode = 0

        mock_spawn.return_value = _make_mock_process(returncode=None, pid=200)
        restarted = await pm.restart_agent("restarter")

        assert restarted.pid == 200
        assert restarted.start_command == ["cmd"]
        assert restarted.start_cwd == Path("/work")
        assert restarted.start_env == {"X": "1"}

    async def test_restart_not_found(self, pm: ProcessManager) -> None:
        """Restarting unknown agent raises KeyError."""
        with pytest.raises(KeyError, match="not found"):
            await pm.restart_agent("ghost")


# ============================================================================
# health_check()
# ============================================================================


class TestHealthCheck:
    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_health_check_alive(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """Health check returns True when heartbeat succeeds."""
        mock_spawn.return_value = _make_mock_process(returncode=None)
        handle = await pm.start_agent(name="healthy", command=["echo"])

        # Mock the IPC heartbeat
        handle.ipc.send_heartbeat = AsyncMock(return_value=True)

        result = await pm.health_check("healthy")
        assert result is True
        # last_heartbeat should be updated
        assert handle.last_heartbeat > datetime(2000, 1, 1, tzinfo=timezone.utc)

    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_health_check_dead_process(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """Health check returns False for dead process."""
        mock_spawn.return_value = _make_mock_process(returncode=None)
        handle = await pm.start_agent(name="dead-hc", command=["echo"])

        # Simulate process death
        handle.process.returncode = 1

        result = await pm.health_check("dead-hc")
        assert result is False

    async def test_health_check_not_found(self, pm: ProcessManager) -> None:
        """Health check for unknown agent raises KeyError."""
        with pytest.raises(KeyError, match="not found"):
            await pm.health_check("ghost")

    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_health_check_ipc_error(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """Health check returns False on IPC error."""
        from agent_nexus.platform.orchestration.ipc import IPCError

        mock_spawn.return_value = _make_mock_process(returncode=None)
        handle = await pm.start_agent(name="failing", command=["echo"])

        handle.ipc.send_heartbeat = AsyncMock(side_effect=IPCError("broken"))

        result = await pm.health_check("failing")
        assert result is False


# ============================================================================
# Query helpers
# ============================================================================


class TestGetAgent:
    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_get_agent_found(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """get_agent returns handle for known agent."""
        mock_spawn.return_value = _make_mock_process()
        handle = await pm.start_agent(name="known", command=["echo"])

        assert pm.get_agent("known") is handle

    async def test_get_agent_not_found(self, pm: ProcessManager) -> None:
        """get_agent returns None for unknown agent."""
        assert pm.get_agent("unknown") is None


class TestListRunning:
    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_list_running_alive_only(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """list_running returns only alive agents."""
        # Each start_agent call needs its own mock process
        mock_spawn.side_effect = [
            _make_mock_process(returncode=None, pid=1),
            _make_mock_process(returncode=None, pid=2),
        ]
        await pm.start_agent(name="alive1", command=["echo"])
        await pm.start_agent(name="alive2", command=["echo"])

        # Kill one
        pm._agents["alive2"].process.returncode = 1

        running = pm.list_running()
        assert "alive1" in running
        assert "alive2" not in running

    async def test_list_running_empty(self, pm: ProcessManager) -> None:
        """list_running returns empty when no agents."""
        assert pm.list_running() == []


# ============================================================================
# stop_all()
# ============================================================================


class TestStopAll:
    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_stop_all_parallel(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """stop_all stops all agents."""
        mock_spawn.side_effect = [
            _make_mock_process(returncode=None, pid=1),
            _make_mock_process(returncode=None, pid=2),
        ]
        await pm.start_agent(name="a1", command=["echo"])
        await pm.start_agent(name="a2", command=["echo"])

        # Make processes already dead so stop is quick
        pm._agents["a1"].process.returncode = 0
        pm._agents["a2"].process.returncode = 0

        await pm.stop_all()

        assert pm.get_agent("a1") is None
        assert pm.get_agent("a2") is None

    async def test_stop_all_empty(self, pm: ProcessManager) -> None:
        """stop_all on empty manager is a no-op."""
        await pm.stop_all()  # Should not raise


# ============================================================================
# _cleanup_dead()
# ============================================================================


class TestCleanupDead:
    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_cleanup_dead_removes_stale(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """_cleanup_dead removes handles for dead processes."""
        # Each start_agent call needs its own mock process
        mock_spawn.side_effect = [
            _make_mock_process(returncode=None, pid=10),
            _make_mock_process(returncode=None, pid=20),
        ]
        await pm.start_agent(name="alive", command=["echo"])
        await pm.start_agent(name="dead", command=["echo"])

        # Kill one
        pm._agents["dead"].process.returncode = 1

        cleaned = pm._cleanup_dead()
        assert "dead" in cleaned
        assert "alive" not in cleaned
        assert pm.get_agent("dead") is None
        assert pm.get_agent("alive") is not None

    async def test_cleanup_dead_none(self, pm: ProcessManager) -> None:
        """_cleanup_dead returns empty when all alive (or none)."""
        cleaned = pm._cleanup_dead()
        assert cleaned == []
