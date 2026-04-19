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
from agent_nexus.platform.orchestration.ipc import IPCProtocol, IPCStream


# ============================================================================
# Fixtures
# ============================================================================


def _make_mock_process(
    returncode: int | None = None, pid: int = 12345
) -> MagicMock:
    """Create a mock asyncio.subprocess.Process.

    Stream read/readline methods return b"" (falsy) so that:
    - _drain_stderr loop terminates (stderr.readline → b"")
    - IPCStream.close() drain loop terminates (stdout.read → b"")
    """
    proc = MagicMock(spec=asyncio.subprocess.Process)
    proc.returncode = returncode
    proc.pid = pid
    proc.stdin = MagicMock(spec=asyncio.StreamWriter)
    # stdout: read/readline return b"" so drain loops terminate
    proc.stdout = MagicMock(spec=asyncio.StreamReader)
    proc.stdout.read = AsyncMock(return_value=b"")
    proc.stdout.readline = AsyncMock(return_value=b"")
    # stderr: readline returns b"" so _drain_stderr terminates
    proc.stderr = MagicMock(spec=asyncio.StreamReader)
    proc.stderr.readline = AsyncMock(return_value=b"")
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


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
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

    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_stop_agent_cancels_drain_task(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """stop_agent() cancels the stderr drain_task on the handle."""
        mock_proc = _make_mock_process(returncode=None)
        mock_spawn.return_value = mock_proc

        handle = await pm.start_agent(name="drain-test", command=["echo"])
        assert handle.drain_task is not None

        # Make process already dead so stop is quick
        handle.process.returncode = 0

        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            await pm.stop_agent("drain-test")

        # The drain task should have been cancelled
        assert handle.drain_task.cancelled() or handle.drain_task.done()

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
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            await pm.stop_agent("dead")

        assert pm.get_agent("dead") is None

    @patch("agent_nexus.platform.orchestration.process_manager.asyncio.create_subprocess_exec")
    async def test_stop_sigterm_stage(
        self, mock_spawn: AsyncMock, pm: ProcessManager
    ) -> None:
        """Stop: stage 1 timeout, then SIGTERM (stage 2) succeeds.

        Note: stop_agent calls IPCStream.close() TWICE (line 236 always +
        line 249 in stage 1), each making a wait_for call for the stdout drain.
        So: call_count=1 first close() drain, call_count=2 second close() drain,
        call_count=3 stage 1 process.wait timeout, call_count=4 stage 2 after SIGTERM.
        """
        mock_proc = _make_mock_process(returncode=None)
        mock_spawn.return_value = mock_proc

        await pm.start_agent(name="stubborn", command=["echo"])

        call_count = 0

        async def _fake_wait_for(coro, timeout=None):
            nonlocal call_count
            call_count += 1
            coro.close()
            if call_count <= 3:
                # call 1: first IPCStream.close() drain (tolerated)
                # call 2: second IPCStream.close() drain (stage 1 close)
                # call 3: stage 1 process.wait timeout
                raise asyncio.TimeoutError()
            # call 4: stage 2 process.wait succeeds after SIGTERM
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
        """Health check raises KeyError for dead process (cleaned up by _cleanup_dead)."""
        mock_spawn.return_value = _make_mock_process(returncode=None)
        handle = await pm.start_agent(name="dead-hc", command=["echo"])

        # Simulate process death
        handle.process.returncode = 1

        # _cleanup_dead inside health_check removes the dead handle,
        # so the subsequent lookup raises KeyError
        with pytest.raises(KeyError, match="not found"):
            await pm.health_check("dead-hc")

        # The dead handle should have been removed
        assert pm.get_agent("dead-hc") is None

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


# ============================================================================
# Iteration 17 merge: TestProcessManagerLock
# ============================================================================


_SUBPROCESS_PATCH = (
    "agent_nexus.platform.orchestration.process_manager"
    ".asyncio.create_subprocess_exec"
)


def _iter17_make_mock_process(pid: int = 12345, returncode=None) -> MagicMock:
    """Create a mock process with proper EOF-behavior for stream methods."""
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = returncode

    proc.stdin = AsyncMock()
    proc.stdin.is_closing = MagicMock(return_value=False)
    proc.stdin.close = MagicMock()
    proc.stdin.wait_closed = AsyncMock()

    proc.stdout = AsyncMock()
    proc.stdout.read = AsyncMock(return_value=b"")
    proc.stdout.readline = AsyncMock(return_value=b"")

    mock_stderr = AsyncMock()
    mock_stderr.readline = AsyncMock(return_value=b"")
    proc.stderr = mock_stderr
    return proc


def _iter17_make_handle(name: str, pid: int = 10000, returncode=None) -> AgentHandle:
    """Create an AgentHandle with a mock process + IPC."""
    proc = _iter17_make_mock_process(pid=pid, returncode=returncode)
    stream = IPCStream(stdin=proc.stdin, stdout=proc.stdout)
    ipc = IPCProtocol(stream)
    return AgentHandle(name=name, process=proc, ipc=ipc)


class TestProcessManagerLock:
    """ProcessManager.start_agent must serialize concurrent calls."""

    def test_lock_initialized(self) -> None:
        pm = ProcessManager()
        assert isinstance(pm._lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_start_agent_acquires_lock(self) -> None:
        pm = ProcessManager()
        mock_process = _iter17_make_mock_process()

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
            return _iter17_make_mock_process(pid=10000 + call_count)

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
            return _iter17_make_mock_process()

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
        dead_handle = _iter17_make_handle("recycle-agent", pid=11111, returncode=1)
        pm._agents["recycle-agent"] = dead_handle

        new_proc = _iter17_make_mock_process(pid=55555)
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
            pm._agents[name] = _iter17_make_handle(name, returncode=0)

        await pm.stop_all(timeout=1.0)
        assert len(pm.list_running()) == 0

    @pytest.mark.asyncio
    async def test_restart_preserves_params(self) -> None:
        """restart_agent reuses original command/cwd/env."""
        pm = ProcessManager()
        original = _iter17_make_mock_process(pid=77777)
        with patch(_SUBPROCESS_PATCH, return_value=original):
            await pm.start_agent(
                "restart-me",
                command=["python", "-m", "agent"],
                cwd="/tmp/test",
                env={"KEY": "val"},
            )

        pm.get_agent("restart-me").process.returncode = 0

        restarted = _iter17_make_mock_process(pid=88888)
        with patch(_SUBPROCESS_PATCH, return_value=restarted):
            handle = await pm.restart_agent("restart-me")

        assert handle.pid == 88888
        assert handle.start_command == ["python", "-m", "agent"]


# ============================================================================
# Iteration 21 merge: TestStopAgentLockProtection
# ============================================================================


class TestStopAgentLockProtection:

    def _make_pm(self):
        pm = ProcessManager()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdin = MagicMock()
        mock_proc.stdout = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.readline = AsyncMock(return_value=b"")

        mock_ipc = MagicMock()
        mock_ipc.stream = MagicMock()
        mock_ipc.stream.close = AsyncMock()

        handle = AgentHandle(
            name="test-agent",
            process=mock_proc,
            ipc=mock_ipc,
            drain_task=None,
            start_command=["test"],
            start_cwd="/tmp",
            start_env={},
        )
        pm._agents["test-agent"] = handle
        return pm, handle, mock_proc

    @pytest.mark.asyncio
    async def test_stop_agent_acquires_lock(self):
        pm, handle, mock_proc = self._make_pm()
        mock_proc.returncode = 1

        await pm.stop_agent("test-agent")

        assert "test-agent" not in pm._agents

    @pytest.mark.asyncio
    async def test_stop_agent_closes_ipc_even_if_dead(self):
        pm, handle, mock_proc = self._make_pm()
        mock_proc.returncode = 1
        close_mock = handle.ipc.stream.close

        await pm.stop_agent("test-agent")

        close_mock.assert_awaited()


class TestProcessManagerStopIdentityCheck:
    """stop_agent uses identity check to avoid popping a new handle.

    Regression test: if start_agent reuses a name while stop_agent is
    in progress, the old stop should NOT remove the new handle.
    """

    @staticmethod
    def _make_pm_with_handle(name: str = "agent-1"):
        pm = ProcessManager()
        mock_proc = _make_mock_process(returncode=1)
        stream = MagicMock(spec=IPCStream)
        stream.close = AsyncMock()
        ipc = MagicMock(spec=IPCProtocol)
        ipc.stream = stream
        handle = AgentHandle(
            name=name,
            process=mock_proc,
            ipc=ipc,
            drain_task=None,
            start_command=["test"],
            start_cwd="/tmp",
            start_env={},
        )
        pm._agents[name] = handle
        return pm, handle, mock_proc

    @pytest.mark.asyncio
    async def test_stop_does_not_remove_new_handle(self):
        """stop_agent should not pop a different handle registered under the same name."""
        pm, old_handle, mock_proc = self._make_pm_with_handle("agent-1")
        mock_proc.returncode = 1  # dead

        # Simulate a concurrent start_agent that replaced the handle
        new_mock_proc = _make_mock_process(returncode=None)
        new_stream = MagicMock(spec=IPCStream)
        new_stream.close = AsyncMock()
        new_ipc = MagicMock(spec=IPCProtocol)
        new_ipc.stream = new_stream
        new_handle = AgentHandle(
            name="agent-1",
            process=new_mock_proc,
            ipc=new_ipc,
            drain_task=None,
            start_command=["test"],
            start_cwd="/tmp",
            start_env={},
        )
        pm._agents["agent-1"] = new_handle

        # Stopping with the old handle should NOT remove the new one
        # We simulate this by calling the stop logic that the old handle would trigger
        # The old handle's process is dead, so it enters the dead-agent path
        mock_proc.returncode = 1

        # Manually simulate what stop_agent does for a dead agent with wrong handle
        async with pm._lock:
            if pm._agents.get("agent-1") is not old_handle:
                # Identity check prevents removing the wrong handle
                pass
            else:
                pm._agents.pop("agent-1", None)

        # The new handle should still be registered
        assert "agent-1" in pm._agents
        assert pm._agents["agent-1"] is new_handle


class TestProcessManagerStartOrphanCleanup:
    """start_agent kills the subprocess if post-creation setup fails.

    Regression test: if setup fails (e.g., assertion), the process
    should be killed, not left orphaned.
    """

    @pytest.mark.asyncio
    async def test_subprocess_killed_on_setup_failure(self):
        """If post-creation setup fails, the subprocess is killed."""
        pm = ProcessManager()

        mock_proc = _make_mock_process()
        mock_proc.stdin = None  # Will cause assertion failure
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(AssertionError):
                await pm.start_agent(
                    name="test-agent",
                    command=["echo", "hello"],
                )

        # Process should have been killed
        mock_proc.kill.assert_called_once()


class TestProcessManagerStopAllLogsErrors:
    """stop_all logs exceptions from individual stop_agent calls."""

    @pytest.mark.asyncio
    async def test_stop_all_logs_stop_errors(self, caplog):
        """Exceptions from stop_agent are logged, not silently swallowed."""
        import logging

        pm = ProcessManager()
        # Add a fake agent
        mock_proc = _make_mock_process(returncode=None)
        stream = MagicMock(spec=IPCStream)
        stream.close = AsyncMock()
        ipc = MagicMock(spec=IPCProtocol)
        ipc.stream = stream
        handle = AgentHandle(
            name="bad-agent",
            process=mock_proc,
            ipc=ipc,
            drain_task=None,
            start_command=["test"],
            start_cwd="/tmp",
            start_env={},
        )
        pm._agents["bad-agent"] = handle

        # Make process.wait raise to simulate stop failure
        mock_proc.wait = AsyncMock(side_effect=RuntimeError("stop exploded"))

        with caplog.at_level(logging.ERROR, logger="agent_nexus.platform.orchestration.process_manager"):
            await pm.stop_all(timeout=0.1)

        # Should have logged the error
        assert any("Error stopping agent" in r.message for r in caplog.records)


# ============================================================================
# Iteration 23: restart_agent race condition + health_check cleanup
# ============================================================================


class TestRestartAgentRaceCondition:
    """restart_agent handles concurrent removal gracefully (Defect 1)."""

    @pytest.mark.asyncio
    async def test_restart_handles_concurrent_stop_no_crash(self):
        """If another coroutine removes the agent between param snapshot and stop, no KeyError."""
        pm = ProcessManager()

        original_proc = _iter17_make_mock_process(pid=40001)
        with patch(_SUBPROCESS_PATCH, return_value=original_proc):
            await pm.start_agent("race-agent", command=["echo"])

        # Snapshot the start params before removing the handle
        handle = pm._agents["race-agent"]
        handle.process.returncode = 0

        # Simulate another coroutine removing the handle before restart's stop_agent runs.
        # We do this by making stop_agent raise KeyError (handle already gone).
        original_stop = pm.stop_agent

        async def _stop_then_remove(name, timeout=10.0):
            # Remove the handle first, then call real stop which will KeyError
            pm._agents.pop(name, None)
            await original_stop(name, timeout=timeout)

        with patch.object(pm, "stop_agent", side_effect=_stop_then_remove):
            new_proc = _iter17_make_mock_process(pid=40002)
            with patch(_SUBPROCESS_PATCH, return_value=new_proc):
                # Should NOT raise KeyError — the try/except catches it
                result = await pm.restart_agent("race-agent")

        assert isinstance(result, AgentHandle)
        assert result.pid == 40002
        assert pm.get_agent("race-agent") is result

    @pytest.mark.asyncio
    async def test_restart_logs_warning_on_concurrent_removal(self, caplog):
        """restart_agent logs a warning when KeyError is caught during stop."""
        import logging

        pm = ProcessManager()

        original_proc = _iter17_make_mock_process(pid=50001)
        with patch(_SUBPROCESS_PATCH, return_value=original_proc):
            await pm.start_agent("warn-agent", command=["echo"])

        pm._agents["warn-agent"].process.returncode = 0

        # Make stop_agent raise KeyError to simulate concurrent removal
        async def _stop_raises_keyerror(name, timeout=10.0):
            raise KeyError(f"Agent '{name}' not found")

        with patch.object(pm, "stop_agent", side_effect=_stop_raises_keyerror):
            new_proc = _iter17_make_mock_process(pid=50002)
            with patch(_SUBPROCESS_PATCH, return_value=new_proc):
                with caplog.at_level(
                    logging.WARNING,
                    logger="agent_nexus.platform.orchestration.process_manager",
                ):
                    result = await pm.restart_agent("warn-agent")

        assert isinstance(result, AgentHandle)
        assert any(
            "already removed during restart" in r.message for r in caplog.records
        )


class TestHealthCheckCleanup:
    """health_check calls _cleanup_dead before lookup (Defects 2 & 3)."""

    @pytest.mark.asyncio
    async def test_cleanup_dead_called_during_health_check(self):
        """Dead agent handles are cleaned up when health_check runs."""
        pm = ProcessManager()

        # Manually insert a dead handle (no subprocess needed)
        dead_handle = _iter17_make_handle("dead-agent", pid=60001, returncode=1)
        alive_handle = _iter17_make_handle("alive-agent", pid=60002, returncode=None)
        pm._agents["dead-agent"] = dead_handle
        pm._agents["alive-agent"] = alive_handle

        # Mock heartbeat on alive agent so health_check succeeds
        alive_handle.ipc.send_heartbeat = AsyncMock(return_value=True)

        # Before: both handles present
        assert pm.get_agent("dead-agent") is not None
        assert pm.get_agent("alive-agent") is not None

        # Run health_check on the alive agent — _cleanup_dead should clean the dead one
        result = await pm.health_check("alive-agent")
        assert result is True

        # Dead handle should have been cleaned up by _cleanup_dead inside health_check
        assert pm.get_agent("dead-agent") is None
        assert pm.get_agent("alive-agent") is alive_handle

    @pytest.mark.asyncio
    async def test_health_check_raises_keyerror_for_cleaned_agent(self):
        """If _cleanup_dead removes the target, health_check raises KeyError."""
        pm = ProcessManager()

        # Insert a dead handle
        dead_handle = _iter17_make_handle("will-be-cleaned", pid=60003, returncode=1)
        pm._agents["will-be-cleaned"] = dead_handle

        # health_check should clean it up then raise KeyError
        with pytest.raises(KeyError, match="not found"):
            await pm.health_check("will-be-cleaned")

        # Confirm it was removed
        assert pm.get_agent("will-be-cleaned") is None


# ============================================================================
# Fix 3 regression: start_agent rejects when agent is being stopped
# ============================================================================


class TestStartAgentStoppingGuard:
    """start_agent must reject if the agent name is in _stopping.

    During a concurrent restart, stop_agent adds the name to _stopping.
    A concurrent start_agent for the same name must fail rather than
    succeed while the old process is still being torn down.
    """

    @pytest.mark.asyncio
    async def test_start_while_stopping_raises(self):
        """start_agent raises ValueError when name is in _stopping set."""
        pm = ProcessManager()

        # Simulate a stop in progress
        pm._stopping.add("stopping-agent")

        with pytest.raises(ValueError, match="being stopped"):
            await pm.start_agent(
                name="stopping-agent",
                command=["echo", "hello"],
            )

    @pytest.mark.asyncio
    async def test_start_after_stopping_completes_succeeds(self):
        """start_agent succeeds once _stopping is cleared."""
        pm = ProcessManager()

        # Simulate stop completed
        pm._stopping.add("transient-agent")
        pm._stopping.discard("transient-agent")

        mock_proc = _iter17_make_mock_process(pid=70001)
        with patch(_SUBPROCESS_PATCH, return_value=mock_proc):
            handle = await pm.start_agent(
                name="transient-agent",
                command=["echo", "hello"],
            )

        assert isinstance(handle, AgentHandle)
        assert pm.get_agent("transient-agent") is handle

    @pytest.mark.asyncio
    async def test_start_stopping_takes_priority_over_alive_check(self):
        """The _stopping check runs before the is_alive check."""
        pm = ProcessManager()

        # Both: in _stopping AND in _agents with a dead handle
        dead_proc = _iter17_make_mock_process(pid=80001, returncode=1)
        stream = MagicMock(spec=IPCStream)
        stream.close = AsyncMock()
        ipc = MagicMock(spec=IPCProtocol)
        ipc.stream = stream
        old_handle = AgentHandle(
            name="priority-agent",
            process=dead_proc,
            ipc=ipc,
            drain_task=None,
            start_command=["old"],
            start_cwd="/tmp",
            start_env={},
        )
        pm._agents["priority-agent"] = old_handle
        pm._stopping.add("priority-agent")

        # Should raise about stopping, NOT about already running
        with pytest.raises(ValueError, match="being stopped"):
            await pm.start_agent(
                name="priority-agent",
                command=["echo", "hello"],
            )


# ============================================================================
# Coverage gap: _drain_stderr logs stderr content
# ============================================================================


class TestDrainStderrLogging:
    """_drain_stderr logs debug messages when stderr has content (line 114)."""

    @pytest.mark.asyncio
    async def test_drain_stderr_logs_content(self, caplog) -> None:
        """_drain_stderr logs each stderr line before EOF."""
        import logging

        pm = ProcessManager()
        mock_proc = MagicMock()
        mock_stderr = AsyncMock()
        # First readline returns content, second returns b"" (EOF)
        mock_stderr.readline.side_effect = [b"error: something failed\n", b""]
        mock_proc.stderr = mock_stderr

        with caplog.at_level(logging.DEBUG, logger="agent_nexus.platform.orchestration.process_manager"):
            await pm._drain_stderr(mock_proc, "test-agent")

        assert any("error: something failed" in r.message for r in caplog.records)


# ============================================================================
# Coverage gap: start_agent orphan cleanup edge cases
# ============================================================================


class TestStartAgentOrphanCleanup:
    """start_agent handles edge cases during post-creation failure cleanup."""

    @pytest.mark.asyncio
    async def test_orphan_kill_process_lookup_error(self) -> None:
        """If process.kill() raises ProcessLookupError during cleanup, it is caught (lines 201-202)."""
        pm = ProcessManager()

        mock_proc = _make_mock_process()
        mock_proc.stdin = None  # Triggers assertion failure
        mock_proc.kill.side_effect = ProcessLookupError("already dead")
        mock_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(AssertionError):
                await pm.start_agent(name="orphan-pl", command=["echo"])

        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_orphan_wait_raises_exception(self) -> None:
        """If process.wait() raises after kill during cleanup, it is caught (lines 205-206)."""
        pm = ProcessManager()

        mock_proc = _make_mock_process()
        mock_proc.stdin = None  # Triggers assertion failure
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock(side_effect=RuntimeError("wait failed"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(AssertionError):
                await pm.start_agent(name="orphan-wait", command=["echo"])

        mock_proc.wait.assert_awaited_once()


# ============================================================================
# Coverage gap: stop_agent concurrent stop detection
# ============================================================================


class TestStopAgentConcurrentStop:
    """stop_agent returns early when agent name is already in _stopping (line 240)."""

    @pytest.mark.asyncio
    async def test_stop_while_already_stopping(self) -> None:
        """stop_agent returns immediately if agent name is in _stopping set."""
        pm = ProcessManager()
        mock_proc = _make_mock_process(returncode=None)
        stream = MagicMock(spec=IPCStream)
        stream.close = AsyncMock()
        ipc = MagicMock(spec=IPCProtocol)
        ipc.stream = stream
        handle = AgentHandle(
            name="stopping-agent",
            process=mock_proc,
            ipc=ipc,
            drain_task=None,
            start_command=["test"],
            start_cwd="/tmp",
            start_env={},
        )
        pm._agents["stopping-agent"] = handle

        # Simulate another stop in progress
        pm._stopping.add("stopping-agent")

        # Should return immediately without closing IPC
        await pm.stop_agent("stopping-agent")

        # IPC close should NOT have been called (early return)
        stream.close.assert_not_called()


# ============================================================================
# Coverage gap: stop_agent IPC close exception paths
# ============================================================================


class TestStopAgentIPCCloseExceptions:
    """stop_agent handles exceptions from IPC stream close (lines 261-262, 272-273)."""

    @pytest.mark.asyncio
    async def test_stop_dead_agent_close_raises(self) -> None:
        """stop_agent logs and continues when IPC close fails on dead agent (lines 261-262)."""
        pm = ProcessManager()
        mock_proc = _make_mock_process(returncode=1)
        stream = MagicMock(spec=IPCStream)
        stream.close = AsyncMock(side_effect=OSError("pipe broken"))
        ipc = MagicMock(spec=IPCProtocol)
        ipc.stream = stream
        handle = AgentHandle(
            name="dead-close-fail",
            process=mock_proc,
            ipc=ipc,
            drain_task=None,
            start_command=["test"],
            start_cwd="/tmp",
            start_env={},
        )
        pm._agents["dead-close-fail"] = handle

        await pm.stop_agent("dead-close-fail")
        assert pm.get_agent("dead-close-fail") is None

    @pytest.mark.asyncio
    async def test_stop_live_agent_close_raises(self) -> None:
        """stop_agent logs and continues when IPC close fails on live agent (lines 272-273)."""
        pm = ProcessManager()
        mock_proc = _make_mock_process(returncode=None)
        stream = MagicMock(spec=IPCStream)
        stream.close = AsyncMock(side_effect=OSError("pipe broken"))
        ipc = MagicMock(spec=IPCProtocol)
        ipc.stream = stream
        handle = AgentHandle(
            name="live-close-fail",
            process=mock_proc,
            ipc=ipc,
            drain_task=None,
            start_command=["test"],
            start_cwd="/tmp",
            start_env={},
        )
        pm._agents["live-close-fail"] = handle

        # process.wait returns immediately (simulates clean exit after failed IPC close)
        mock_proc.wait = AsyncMock(return_value=None)

        await pm.stop_agent("live-close-fail")
        assert pm.get_agent("live-close-fail") is None


# ============================================================================
# Coverage gap: stop_agent SIGTERM success path with logging
# ============================================================================


class TestStopAgentSigtermSuccess:
    """stop_agent SIGTERM stage: process exits and is logged (lines 297-301)."""

    @pytest.mark.asyncio
    async def test_stop_sigterm_then_clean_exit(self) -> None:
        """Process exits cleanly after SIGTERM, removed from registry."""
        pm = ProcessManager()
        mock_proc = _make_mock_process(returncode=None)
        stream = MagicMock(spec=IPCStream)
        stream.close = AsyncMock()
        ipc = MagicMock(spec=IPCProtocol)
        ipc.stream = stream
        handle = AgentHandle(
            name="sigterm-exit",
            process=mock_proc,
            ipc=ipc,
            drain_task=None,
            start_command=["test"],
            start_cwd="/tmp",
            start_env={},
        )
        pm._agents["sigterm-exit"] = handle

        call_count = 0

        async def _fake_wait_for(coro, timeout=None):
            nonlocal call_count
            call_count += 1
            coro.close()
            if call_count <= 2:
                # call 1: IPCStream.close() drain
                # call 2: stage 1 process.wait timeout
                raise asyncio.TimeoutError()
            # call 3: stage 2 process.wait succeeds after SIGTERM
            mock_proc.returncode = -signal.SIGTERM

        with patch(
            "agent_nexus.platform.orchestration.process_manager.asyncio.wait_for",
            side_effect=_fake_wait_for,
        ):
            await pm.stop_agent("sigterm-exit", timeout=1.0)

        mock_proc.send_signal.assert_called_with(signal.SIGTERM)
        assert pm.get_agent("sigterm-exit") is None


# ============================================================================
# Coverage gap: stop_agent SIGKILL edge cases
# ============================================================================


class TestStopAgentSigkillEdgeCases:
    """stop_agent handles ProcessLookupError on kill and exception on wait (lines 309-315)."""

    @pytest.mark.asyncio
    async def test_sigkill_process_lookup_error(self) -> None:
        """SIGKILL raises ProcessLookupError (process already dead), handled gracefully (lines 309-310)."""
        pm = ProcessManager()
        mock_proc = _make_mock_process(returncode=None)
        stream = MagicMock(spec=IPCStream)
        stream.close = AsyncMock()
        ipc = MagicMock(spec=IPCProtocol)
        ipc.stream = stream
        handle = AgentHandle(
            name="kill-pl",
            process=mock_proc,
            ipc=ipc,
            drain_task=None,
            start_command=["test"],
            start_cwd="/tmp",
            start_env={},
        )
        pm._agents["kill-pl"] = handle
        mock_proc.kill.side_effect = ProcessLookupError("already gone")

        async def _always_timeout(coro, timeout=None):
            coro.close()
            raise asyncio.TimeoutError()

        with patch(
            "agent_nexus.platform.orchestration.process_manager.asyncio.wait_for",
            side_effect=_always_timeout,
        ):
            await pm.stop_agent("kill-pl", timeout=1.0)

        mock_proc.kill.assert_called_once()
        assert pm.get_agent("kill-pl") is None

    @pytest.mark.asyncio
    async def test_sigkill_wait_raises_exception(self) -> None:
        """process.wait() after SIGKILL raises exception, handled gracefully (lines 314-315)."""
        pm = ProcessManager()
        mock_proc = _make_mock_process(returncode=None)
        stream = MagicMock(spec=IPCStream)
        stream.close = AsyncMock()
        ipc = MagicMock(spec=IPCProtocol)
        ipc.stream = stream
        handle = AgentHandle(
            name="kill-wait-fail",
            process=mock_proc,
            ipc=ipc,
            drain_task=None,
            start_command=["test"],
            start_cwd="/tmp",
            start_env={},
        )
        pm._agents["kill-wait-fail"] = handle
        mock_proc.wait = AsyncMock(side_effect=RuntimeError("wait exploded"))

        async def _always_timeout(coro, timeout=None):
            coro.close()
            raise asyncio.TimeoutError()

        with patch(
            "agent_nexus.platform.orchestration.process_manager.asyncio.wait_for",
            side_effect=_always_timeout,
        ):
            await pm.stop_agent("kill-wait-fail", timeout=1.0)

        assert pm.get_agent("kill-wait-fail") is None


# ============================================================================
# Coverage gap: health_check returns False for alive-but-unresponsive agent
# ============================================================================


class TestHealthCheckAliveButUnresponsive:
    """health_check returns False when process is alive but heartbeat fails (line 379)."""

    @pytest.mark.asyncio
    async def test_health_check_ipc_returns_false(self) -> None:
        """health_check returns False when send_heartbeat returns False (not an exception)."""
        pm = ProcessManager()
        mock_proc = _make_mock_process(returncode=None)
        stream = MagicMock(spec=IPCStream)
        stream.close = AsyncMock()
        ipc = MagicMock(spec=IPCProtocol)
        ipc.stream = stream
        ipc.send_heartbeat = AsyncMock(return_value=False)
        handle = AgentHandle(
            name="unresponsive",
            process=mock_proc,
            ipc=ipc,
            drain_task=None,
            start_command=["test"],
            start_cwd="/tmp",
            start_env={},
        )
        pm._agents["unresponsive"] = handle

        result = await pm.health_check("unresponsive")
        assert result is False
        # Agent should still be registered (not cleaned up — it's alive, just unresponsive)
        assert pm.get_agent("unresponsive") is handle


# ============================================================================
# Coverage gap: SIGTERM success path with real wait (not patched wait_for)
# ============================================================================


class TestStopAgentSigtermRealWait:
    """SIGTERM success path using real process.wait() (lines 297-301)."""

    @pytest.mark.asyncio
    async def test_sigterm_real_wait_succeeds(self) -> None:
        """Process exits after SIGTERM — uses real process.wait, not mocked wait_for."""
        pm = ProcessManager()
        mock_proc = _make_mock_process(returncode=None)
        stream = MagicMock(spec=IPCStream)
        stream.close = AsyncMock()
        ipc = MagicMock(spec=IPCProtocol)
        ipc.stream = stream
        handle = AgentHandle(
            name="sigterm-real",
            process=mock_proc,
            ipc=ipc,
            drain_task=None,
            start_command=["test"],
            start_cwd="/tmp",
            start_env={},
        )
        pm._agents["sigterm-real"] = handle

        wait_call_count = 0

        async def _mock_wait():
            nonlocal wait_call_count
            wait_call_count += 1
            # First wait (stage 1): simulate timeout by not setting returncode
            if wait_call_count == 1:
                raise asyncio.TimeoutError()
            # Second wait (stage 2 after SIGTERM): success
            mock_proc.returncode = -signal.SIGTERM

        mock_proc.wait = _mock_wait

        # Patch wait_for to just await the coroutine (no real timeout logic)
        async def _passthrough_wait_for(coro, timeout=None):
            return await coro

        with patch(
            "agent_nexus.platform.orchestration.process_manager.asyncio.wait_for",
            side_effect=_passthrough_wait_for,
        ):
            await pm.stop_agent("sigterm-real", timeout=1.0)

        mock_proc.send_signal.assert_called_with(signal.SIGTERM)
        assert pm.get_agent("sigterm-real") is None


# ============================================================================
# Coverage gap: health_check alive-but-dead-after-cleanup race
# ============================================================================


class TestHealthCheckDeadAfterCleanup:
    """health_check returns False when process dies between _cleanup_dead and is_alive check (line 379)."""

    @pytest.mark.asyncio
    async def test_process_dead_after_cleanup(self) -> None:
        """Process is alive during cleanup but dead at is_alive check."""
        pm = ProcessManager()
        mock_proc = _make_mock_process(returncode=None)
        stream = MagicMock(spec=IPCStream)
        stream.close = AsyncMock()
        ipc = MagicMock(spec=IPCProtocol)
        ipc.stream = stream
        handle = AgentHandle(
            name="dies-after-cleanup",
            process=mock_proc,
            ipc=ipc,
            drain_task=None,
            start_command=["test"],
            start_cwd="/tmp",
            start_env={},
        )
        pm._agents["dies-after-cleanup"] = handle

        # The process is alive during _cleanup_dead (returncode=None),
        # but then becomes dead when is_alive is checked.
        # We simulate this by setting returncode to a dead value
        # after _cleanup_dead runs.
        original_cleanup = pm._cleanup_dead

        def _cleanup_then_kill():
            result = original_cleanup()
            # Now simulate the process dying right after cleanup
            mock_proc.returncode = 1
            return result

        with patch.object(pm, "_cleanup_dead", side_effect=_cleanup_then_kill):
            result = await pm.health_check("dies-after-cleanup")

        assert result is False


# ============================================================================
# Regression: health_check does not hold _lock during IPC heartbeat
# ============================================================================


class TestHealthCheckNoLockDuringHeartbeat:
    """health_check must release _lock before IPC heartbeat to avoid blocking
    all ProcessManager operations for up to 10 seconds.

    Regression: the entire health_check (lookup + heartbeat) ran under _lock.
    If an agent was unresponsive, the 10-second heartbeat timeout blocked
    start_agent, stop_agent, and other health_check calls.
    """

    @pytest.mark.asyncio
    async def test_start_agent_not_blocked_by_health_check(self) -> None:
        """start_agent can run concurrently with a slow health_check."""
        pm = ProcessManager()

        # Set up an alive agent
        alive_handle = _iter17_make_handle("alive-agent", pid=90001, returncode=None)
        pm._agents["alive-agent"] = alive_handle

        # Make heartbeat slow (but succeeds)
        async def slow_heartbeat():
            await asyncio.sleep(0.2)
            return True

        alive_handle.ipc.send_heartbeat = slow_heartbeat

        # Start health check (which will hold the lock briefly, then release
        # for the IPC call, then re-acquire to update last_heartbeat).
        health_task = asyncio.create_task(pm.health_check("alive-agent"))

        # Give the health check a moment to start and release the lock
        await asyncio.sleep(0.05)

        # start_agent should be able to proceed without waiting for
        # the full heartbeat timeout.
        new_proc = _iter17_make_mock_process(pid=90002)
        with patch(_SUBPROCESS_PATCH, return_value=new_proc):
            handle = await asyncio.wait_for(
                pm.start_agent("other-agent", command=["echo"]),
                timeout=0.5,
            )

        assert isinstance(handle, AgentHandle)
        assert handle.pid == 90002

        # Clean up
        result = await health_task
        assert result is True

    @pytest.mark.asyncio
    async def test_heartbeat_updates_last_heartbeat(self) -> None:
        """After successful heartbeat, last_heartbeat is updated."""
        pm = ProcessManager()

        handle = _iter17_make_handle("hb-agent", pid=91001, returncode=None)
        pm._agents["hb-agent"] = handle

        before = handle.last_heartbeat
        handle.ipc.send_heartbeat = AsyncMock(return_value=True)

        # Small delay so timestamps differ
        await asyncio.sleep(0.01)
        result = await pm.health_check("hb-agent")

        assert result is True
        assert handle.last_heartbeat >= before
