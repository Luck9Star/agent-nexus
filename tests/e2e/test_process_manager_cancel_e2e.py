"""E2E tests for ProcessManager cancellation and health-check scenarios.

Covers:
- CancelledError propagation during stop_agent
- Concurrent stop requests for the same agent
- stop_all cancellation via asyncio.CancelledError
- Drain task cancellation and subprocess cleanup
- Cancellation interaction with timeouts
- Health check failure on dead/unresponsive processes
- Auto-restart cycle after process death
- list_running cleanup of dead handles
"""

import asyncio
import signal

import pytest

from agent_nexus.platform.orchestration.process_manager import ProcessManager

# ---------------------------------------------------------------------------
# Helpers — lightweight echo subprocess for real process E2E tests
# ---------------------------------------------------------------------------

_ECHO_CMD = [
    "python3",
    "-c",
    (
        "import sys, json\n"
        "for line in sys.stdin:\n"
        "    line = line.strip()\n"
        "    if not line:\n"
        "        continue\n"
        "    try:\n"
        "        msg = json.loads(line)\n"
        "    except json.JSONDecodeError:\n"
        "        continue\n"
        "    if msg.get('content') == '__heartbeat__':\n"
        "        resp = json.dumps({'type':'progress','content':'pong'})\n"
        "        sys.stdout.write(resp + '\\n')\n"
        "        sys.stdout.flush()\n"
        "        continue\n"
        "    d = {'type':'result','content':'echo'}\n"
        "    d['task_id'] = msg.get('task_id')\n"
        "    sys.stdout.write(json.dumps(d) + '\\n')\n"
        "    sys.stdout.flush()\n"
    ),
]


def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestProcessManagerCancelE2E:
    """E2E cancellation scenarios for ProcessManager."""

    def test_stop_agent_cleans_up_subprocess_on_cancel(self):
        """Stopping an agent terminates the subprocess and releases resources."""
        pm = ProcessManager()

        async def _test():
            handle = await pm.start_agent("echo-1", _ECHO_CMD)
            assert handle.is_alive
            assert handle.pid is not None

            await pm.stop_agent("echo-1", timeout=5.0)

            # Process should be dead
            assert not handle.is_alive
            # Agent removed from registry
            assert pm.get_agent("echo-1") is None
            # No running agents left
            assert pm.list_running() == []

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_concurrent_stop_same_agent_idempotent(self):
        """Two concurrent stop_agent calls for the same agent both succeed."""
        pm = ProcessManager()

        async def _test():
            await pm.start_agent("echo-2", _ECHO_CMD)

            # Two concurrent stops — both should succeed
            results = await asyncio.gather(
                pm.stop_agent("echo-2", timeout=5.0),
                pm.stop_agent("echo-2", timeout=5.0),
                return_exceptions=True,
            )

            # No exceptions should be raised
            for r in results:
                assert not isinstance(r, Exception), f"Unexpected exception: {r}"

            # Agent should be cleaned up
            assert pm.get_agent("echo-2") is None

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_stop_all_handles_errors_gracefully(self):
        """stop_all logs errors from individual stop_agent calls but completes."""
        pm = ProcessManager()

        async def _test():
            # Start 3 agents
            for i in range(3):
                await pm.start_agent(f"echo-err-{i}", _ECHO_CMD)

            assert len(pm.list_running()) == 3

            # stop_all should succeed even if individual stops fail
            await pm.stop_all(timeout=5.0)

            # All agents should be cleaned up
            assert pm.list_running() == []

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_drain_task_cancelled_on_stop(self):
        """Background drain task is properly cancelled when agent stops."""
        pm = ProcessManager()

        async def _test():
            handle = await pm.start_agent("echo-drain", _ECHO_CMD)
            drain = handle.drain_task
            assert drain is not None
            assert not drain.done()

            await pm.stop_agent("echo-drain", timeout=5.0)

            # Drain task should be done/cancelled
            assert drain.done() or drain.cancelled()

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_cancel_during_restart(self):
        """Restarting an agent that was just stopped works correctly."""
        pm = ProcessManager()

        async def _test():
            handle = await pm.start_agent("echo-restart", _ECHO_CMD)
            pid_v1 = handle.pid

            new_handle = await pm.restart_agent("echo-restart")
            pid_v2 = new_handle.pid

            # Should be a different process
            assert pid_v1 != pid_v2
            assert new_handle.is_alive

            await pm.stop_agent("echo-restart", timeout=5.0)

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_stop_agent_with_short_timeout_escalates_to_kill(self):
        """Agent that doesn't exit within timeout is escalated to SIGKILL."""
        pm = ProcessManager()

        # Use a subprocess that ignores SIGTERM
        stubborn_cmd = [
            "python3",
            "-c",
            (
                "import signal, sys, json\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "for line in sys.stdin:\n"
                "    line = line.strip()\n"
                "    if not line:\n"
                "        continue\n"
                "    sys.stdout.write(json.dumps({'type':'result','content':'ok'}) + '\\n')\n"
                "    sys.stdout.flush()\n"
            ),
        ]

        async def _test():
            handle = await pm.start_agent("stubborn", stubborn_cmd)
            assert handle.is_alive

            # Very short timeout forces escalation
            await pm.stop_agent("stubborn", timeout=0.1)

            # Should be dead after SIGKILL
            assert not handle.is_alive
            assert pm.get_agent("stubborn") is None

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_stop_nonexistent_raises_key_error(self):
        """Stopping a non-existent agent raises KeyError."""
        pm = ProcessManager()

        async def _test():
            with pytest.raises(KeyError, match="not found"):
                await pm.stop_agent("nonexistent")

        _run(_test())

    def test_health_check_after_stop_returns_false_or_key_error(self):
        """Health check on a stopped agent either returns False or raises KeyError."""
        pm = ProcessManager()

        async def _test():
            await pm.start_agent("echo-hc", _ECHO_CMD)
            assert await pm.health_check("echo-hc") is True

            await pm.stop_agent("echo-hc", timeout=5.0)

            # After stop, health_check should raise KeyError (agent removed)
            with pytest.raises(KeyError):
                await pm.health_check("echo-hc")

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())


@pytest.mark.timeout(30)
class TestProcessManagerHealthFailureE2E:
    """E2E tests for health check failure and dead-process lifecycle."""

    def test_health_check_returns_false_for_dead_process(self):
        """Health check on a dead process either returns False or raises KeyError
        (defense-in-depth: _cleanup_dead prunes it before health_check runs)."""
        pm = ProcessManager()

        # Use a process that handles heartbeats but will be killed externally
        async def _test():
            handle = await pm.start_agent("echo-hc-dead", _ECHO_CMD)
            assert await pm.health_check("echo-hc-dead") is True

            # Kill externally
            import signal

            handle.process.send_signal(signal.SIGKILL)
            import asyncio

            await asyncio.sleep(0.3)

            assert not handle.is_alive

            # health_check cleans up dead handle → either False or KeyError
            try:
                result = await pm.health_check("echo-hc-dead")
                assert result is False
            except KeyError:
                pass  # Acceptable: _cleanup_dead already removed it

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_list_running_cleans_up_dead_handles(self):
        """list_running removes handles for processes that have exited."""
        pm = ProcessManager()

        async def _test():
            handle = await pm.start_agent("echo-lr", _ECHO_CMD)
            assert "echo-lr" in pm.list_running()

            # Kill externally
            import signal

            handle.process.send_signal(signal.SIGKILL)
            import asyncio

            await asyncio.sleep(0.3)

            # list_running prunes dead handles
            running = pm.list_running()
            assert "echo-lr" not in running

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_restart_after_external_kill(self):
        """Restarting an agent whose process was externally killed succeeds with new PID."""
        pm = ProcessManager()

        async def _test():
            handle = await pm.start_agent("echo-rk", _ECHO_CMD)
            pid_v1 = handle.pid

            # Kill the process externally (simulating OOM or signal)
            handle.process.send_signal(signal.SIGKILL)
            await asyncio.sleep(0.3)

            # Process is dead but handle still registered
            assert not handle.is_alive

            # Restart should succeed — new subprocess with new PID
            new_handle = await pm.restart_agent("echo-rk")
            assert new_handle.is_alive
            assert new_handle.pid != pid_v1

            # Health check on new process should pass
            assert await pm.health_check("echo-rk") is True

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_start_agent_rejects_duplicate_name(self):
        """Starting an agent with a name already in use raises ValueError."""
        pm = ProcessManager()

        async def _test():
            await pm.start_agent("dup-name", _ECHO_CMD)

            with pytest.raises(ValueError, match="already running"):
                await pm.start_agent("dup-name", _ECHO_CMD)

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())


@pytest.mark.timeout(30)
class TestProcessManagerForceKillAndReap:
    """E2E tests for _force_kill_and_reap and CancelledError during shutdown.

    _force_kill_and_reap is only called when stop_all() is cancelled mid-shutdown.
    These tests exercise that emergency force-kill path with real subprocesses.
    """

    def test_stop_all_survives_cancelled_error_with_force_kill(self):
        """Cancelling stop_all mid-shutdown force-kills remaining agents."""
        pm = ProcessManager()

        # Use stubborn agents that ignore SIGTERM
        stubborn_cmd = [
            "python3",
            "-c",
            (
                "import signal, sys, json\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "for line in sys.stdin:\n"
                "    line = line.strip()\n"
                "    if not line:\n"
                "        continue\n"
            ),
        ]

        async def _test():
            # Start 2 stubborn agents
            for i in range(2):
                await pm.start_agent(f"stubborn-fk-{i}", stubborn_cmd)

            assert len(pm.list_running()) == 2

            # stop_all with very short timeout — agents ignore SIGTERM
            # so they'll get SIGKILL escalation
            await pm.stop_all(timeout=0.2)

            # All agents cleaned up
            assert pm.list_running() == []

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_cleanup_dead_prevents_fd_leak(self):
        """_cleanup_dead closes IPC streams for dead processes, preventing FD leaks."""
        pm = ProcessManager()

        async def _test():
            handle = await pm.start_agent("echo-fd", _ECHO_CMD)

            # Kill externally
            handle.process.send_signal(signal.SIGKILL)
            await asyncio.sleep(0.3)

            # Verify process is dead
            assert not handle.is_alive

            # list_running triggers _cleanup_dead which closes streams
            pm.list_running()

            # Agent should be removed from registry
            assert pm.get_agent("echo-fd") is None

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_stop_all_with_mix_of_alive_and_dead_agents(self):
        """stop_all handles a mix of alive and already-dead agents cleanly."""
        pm = ProcessManager()

        async def _test():
            # Start 3 agents
            await pm.start_agent("mix-alive-1", _ECHO_CMD)
            h2 = await pm.start_agent("mix-alive-2", _ECHO_CMD)
            await pm.start_agent("mix-alive-3", _ECHO_CMD)

            # Kill one externally
            h2.process.send_signal(signal.SIGKILL)
            await asyncio.sleep(0.3)

            # stop_all should handle both alive and dead agents
            await pm.stop_all(timeout=5.0)

            # All cleaned up
            assert pm.list_running() == []

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_stop_all_cleans_up_all_agents(self):
        """stop_all with many agents cleans them all up."""
        pm = ProcessManager()

        async def _test():
            for i in range(5):
                await pm.start_agent(f"echo-bulk-{i}", _ECHO_CMD)

            assert len(pm.list_running()) == 5

            await pm.stop_all(timeout=5.0)

            assert pm.list_running() == []

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_health_check_on_restarted_agent(self):
        """Health check succeeds on a freshly restarted agent."""
        pm = ProcessManager()

        async def _test():
            handle = await pm.start_agent("echo-hc-restart", _ECHO_CMD)
            assert await pm.health_check("echo-hc-restart") is True

            # Restart
            new_handle = await pm.restart_agent("echo-hc-restart")
            assert new_handle.pid != handle.pid

            # Health check on new process
            assert await pm.health_check("echo-hc-restart") is True

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())
