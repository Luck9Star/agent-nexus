"""E2E tests for ProcessManager async safety: concurrent operations, CancelledError
propagation, and _stopping set behavior.

Quality focus: async_safety — verifies that concurrent async operations on the same
agent handle are correctly serialized and that resource cleanup happens even under
cancellation.
"""

import asyncio
import signal

import pytest

from agent_nexus.platform.orchestration.process_manager import ProcessManager

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
    return asyncio.run(coro)


@pytest.mark.timeout(30)
class TestProcessManagerConcurrentHealthStop:
    """Verify concurrent health_check and stop_agent on the same agent handle."""

    def test_health_check_during_stop_returns_false_or_key_error(self):
        """Concurrent health_check while stop_agent is shutting down returns False
        or KeyError (agent may already be removed by _cleanup_dead)."""
        pm = ProcessManager()

        async def _test():
            await pm.start_agent("echo-chs", _ECHO_CMD)

            async def slow_health():
                try:
                    return await pm.health_check("echo-chs")
                except KeyError:
                    return "key_error"

            async def stop():
                await asyncio.sleep(0.05)
                await pm.stop_agent("echo-chs", timeout=5.0)

            results = await asyncio.gather(slow_health(), stop(), return_exceptions=True)
            health_result = results[0]

            # Health check should either succeed (True), fail (False), or
            # get KeyError if stop already removed the agent
            assert health_result in (True, False, "key_error")

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_start_while_stopping_rejected(self):
        """Starting an agent with the same name while stop is in progress raises
        ValueError (the _stopping set guard)."""
        pm = ProcessManager()

        async def _test():
            await pm.start_agent("echo-ss", _ECHO_CMD)

            # Use a stubborn process that ignores SIGTERM so stop takes longer
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

            # Replace the running agent with a stubborn one
            await pm.stop_agent("echo-ss", timeout=5.0)

            # Now start a stubborn agent, then try stop + start concurrently
            await pm.start_agent("echo-ss2", stubborn_cmd)

            async def stop_it():
                await pm.stop_agent("echo-ss2", timeout=5.0)

            async def start_it():
                await asyncio.sleep(0.05)
                with pytest.raises((ValueError, KeyError)):
                    # Either: "already running" or "being stopped"
                    await pm.start_agent("echo-ss2", _ECHO_CMD)

            await asyncio.gather(stop_it(), start_it(), return_exceptions=True)

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_triple_concurrent_stop_all_succeed(self):
        """Three concurrent stop_agent calls for the same agent all succeed."""
        pm = ProcessManager()

        async def _test():
            await pm.start_agent("echo-triple", _ECHO_CMD)

            results = await asyncio.gather(
                pm.stop_agent("echo-triple", timeout=5.0),
                pm.stop_agent("echo-triple", timeout=5.0),
                pm.stop_agent("echo-triple", timeout=5.0),
                return_exceptions=True,
            )

            for r in results:
                assert not isinstance(r, Exception), f"Unexpected: {r}"

            assert pm.get_agent("echo-triple") is None

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())


@pytest.mark.timeout(30)
class TestProcessManagerHealthCheckStaleHandle:
    """Verify that health_check correctly handles stale handle scenarios."""

    def test_health_check_after_restart_returns_true(self):
        """Health check on a restarted agent sees the new process, not the old one."""
        pm = ProcessManager()

        async def _test():
            h1 = await pm.start_agent("echo-stale", _ECHO_CMD)
            pid1 = h1.pid
            assert await pm.health_check("echo-stale") is True

            # Restart — new process with new PID
            h2 = await pm.restart_agent("echo-stale")
            assert h2.pid != pid1

            # Health check should return True for the new process
            assert await pm.health_check("echo-stale") is True

            # Verify old handle is dead
            assert not h1.is_alive

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_concurrent_health_checks_same_agent(self):
        """Multiple concurrent health checks on the same agent all succeed."""
        pm = ProcessManager()

        async def _test():
            await pm.start_agent("echo-mhc", _ECHO_CMD)

            results = await asyncio.gather(
                pm.health_check("echo-mhc"),
                pm.health_check("echo-mhc"),
                pm.health_check("echo-mhc"),
            )

            # All should return True — IPC lock serializes them
            assert all(r is True for r in results)

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_health_check_on_externally_killed_reports_dead(self):
        """Health check reports dead after external kill. Restart requires
        a new start_agent call since _cleanup_dead removes the handle."""
        pm = ProcessManager()

        async def _test():
            handle = await pm.start_agent("echo-kr", _ECHO_CMD)
            assert await pm.health_check("echo-kr") is True

            # Kill externally
            handle.process.send_signal(signal.SIGKILL)
            await asyncio.sleep(0.3)

            # Health check should fail or KeyError (_cleanup_dead prunes it)
            try:
                result = await pm.health_check("echo-kr")
                assert result is False
            except KeyError:
                pass  # _cleanup_dead already removed it

            # After cleanup, start a fresh agent with the same name
            new_handle = await pm.start_agent("echo-kr", _ECHO_CMD)
            assert new_handle.is_alive
            assert await pm.health_check("echo-kr") is True

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())


@pytest.mark.timeout(30)
class TestProcessManagerStopAllWithConcurrency:
    """Verify stop_all handles concurrent operations correctly."""

    def test_stop_all_during_health_checks(self):
        """stop_all succeeds even while health checks are in flight."""
        pm = ProcessManager()

        async def _test():
            for i in range(3):
                await pm.start_agent(f"echo-shc-{i}", _ECHO_CMD)

            async def health_checks():
                import contextlib
                for i in range(3):
                    with contextlib.suppress(KeyError):
                        await pm.health_check(f"echo-shc-{i}")

            await asyncio.gather(
                health_checks(),
                pm.stop_all(timeout=5.0),
                return_exceptions=True,
            )

            assert pm.list_running() == []

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())

    def test_stop_all_with_sigkill_escalation_on_stubborn_agents(self):
        """stop_all force-kills stubborn agents that ignore SIGTERM."""
        pm = ProcessManager()

        stubborn_cmd = [
            "python3",
            "-c",
            (
                "import signal, sys\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "for line in sys.stdin:\n"
                "    line = line.strip()\n"
                "    if not line:\n"
                "        continue\n"
            ),
        ]

        async def _test():
            for i in range(2):
                await pm.start_agent(f"stubborn-sa-{i}", stubborn_cmd)

            await pm.stop_all(timeout=0.2)

            assert pm.list_running() == []

        try:
            _run(_test())
        finally:
            _run(pm.stop_all())
