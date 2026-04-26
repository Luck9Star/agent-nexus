"""End-to-end dynamic verification: real subprocess IPC via ProcessManager.

Spawns tests/integration/echo_agent.py as a real subprocess and verifies the
complete IPC lifecycle: spawn -> send/receive -> heartbeat -> stop.

These tests validate what 2820 unit tests with mocks cannot: actual pipe I/O,
real asyncio subprocess lifecycle, and real JSON-lines framing over stdin/stdout.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from agent_nexus.models.ipc import (
    AgentToPlatform,
    AgentToPlatformType,
    PlatformToAgent,
    PlatformToAgentType,
)
from agent_nexus.models.task import TaskItem, TaskState
from agent_nexus.platform.orchestration.ipc import (
    IPCConnectionError,
    IPCProtocol,
    IPCStream,
    IPCTimeoutError,
)
from agent_nexus.platform.orchestration.process_manager import (
    AgentHandle,
    ProcessManager,
)

# Path to the echo agent subprocess script
_ECHO_AGENT = Path(__file__).parent / "echo_agent.py"


def _echo_command() -> list[str]:
    """Build the command to launch the echo agent subprocess."""
    return [sys.executable, str(_ECHO_AGENT)]


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def pm():
    """Provide a fresh ProcessManager with auto-cleanup."""
    manager = ProcessManager()
    yield manager
    await manager.stop_all(timeout=5.0)


@pytest.fixture
async def echo_agent(pm: ProcessManager):
    """Start the echo agent and return its handle.

    Auto-stops the agent after the test.
    """
    handle = await pm.start_agent(
        name="echo-test",
        command=_echo_command(),
    )
    yield handle
    try:
        await pm.stop_agent("echo-test", timeout=5.0)
    except KeyError:
        pass  # Already stopped by the test


# ---------------------------------------------------------------------------
# 1. ProcessManager: Real subprocess lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestProcessManagerRealSubprocess:
    """Verify ProcessManager with real subprocess, not mocks."""

    @pytest.mark.asyncio
    async def test_start_agent_creates_live_process(
        self, pm: ProcessManager, echo_agent: AgentHandle,
    ) -> None:
        """Verify the echo agent is alive after start."""
        assert echo_agent.is_alive
        assert echo_agent.pid is not None
        assert echo_agent.name == "echo-test"

    @pytest.mark.asyncio
    async def test_start_agent_registers_in_manager(
        self, pm: ProcessManager, echo_agent: AgentHandle,
    ) -> None:
        """Verify the agent is registered in ProcessManager._agents."""
        assert pm.get_agent("echo-test") is echo_agent
        assert "echo-test" in pm.list_running()

    @pytest.mark.asyncio
    async def test_duplicate_start_raises(
        self, pm: ProcessManager, echo_agent: AgentHandle,
    ) -> None:
        """Starting an agent with the same name while alive raises ValueError."""
        with pytest.raises(ValueError, match="already running"):
            await pm.start_agent(name="echo-test", command=_echo_command())

    @pytest.mark.asyncio
    async def test_stop_agent_clean_exit(
        self, pm: ProcessManager,
    ) -> None:
        """Stopping an agent results in clean process exit."""
        handle = await pm.start_agent(
            name="stop-test",
            command=_echo_command(),
        )
        pid = handle.pid
        assert handle.is_alive

        await pm.stop_agent("stop-test", timeout=5.0)

        # Process should be dead
        assert not handle.is_alive
        assert handle.process.returncode is not None
        # Agent should be unregistered
        assert pm.get_agent("stop-test") is None

    @pytest.mark.asyncio
    async def test_stop_all_terminates_agents(
        self, pm: ProcessManager,
    ) -> None:
        """stop_all() terminates multiple running agents."""
        handles = []
        for i in range(3):
            h = await pm.start_agent(
                name=f"multi-{i}",
                command=_echo_command(),
            )
            handles.append(h)

        running = pm.list_running()
        assert len(running) == 3

        await pm.stop_all(timeout=5.0)

        # All agents should be gone
        assert len(pm.list_running()) == 0
        for h in handles:
            assert not h.is_alive

    @pytest.mark.asyncio
    async def test_restart_agent_spawns_new_process(
        self, pm: ProcessManager,
    ) -> None:
        """restart_agent() kills old process and creates new one."""
        h1 = await pm.start_agent(
            name="restart-test",
            command=_echo_command(),
        )
        pid1 = h1.pid
        assert pid1 is not None

        h2 = await pm.restart_agent("restart-test")
        pid2 = h2.pid

        # New process should have a different PID
        assert pid2 is not None
        assert pid2 != pid1
        assert h2.is_alive

        # Old process should be dead
        assert not h1.is_alive

        await pm.stop_agent("restart-test", timeout=5.0)

    @pytest.mark.asyncio
    async def test_stop_nonexistent_raises(self, pm: ProcessManager) -> None:
        """Stopping a non-existent agent raises KeyError."""
        with pytest.raises(KeyError, match="not found"):
            await pm.stop_agent("ghost")

    @pytest.mark.asyncio
    async def test_dead_process_cleanup(
        self, pm: ProcessManager,
    ) -> None:
        """Killing a process externally triggers cleanup on next list_running."""
        handle = await pm.start_agent(
            name="kill-test",
            command=_echo_command(),
        )
        assert handle.is_alive

        # Force-kill the process externally (simulates crash)
        handle.process.kill()
        await handle.process.wait()
        assert not handle.is_alive

        # list_running triggers _cleanup_dead
        running = pm.list_running()
        assert "kill-test" not in running
        assert pm.get_agent("kill-test") is None


# ---------------------------------------------------------------------------
# 2. IPC Protocol: Real pipe I/O
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestIPCProtocolRealPipes:
    """Verify IPC send/receive with real subprocess pipes."""

    @pytest.mark.asyncio
    async def test_send_chat_receive_result(
        self, echo_agent: AgentHandle,
    ) -> None:
        """Send a chat message and receive an echoed result."""
        await echo_agent.ipc.send_chat("hello world")
        resp = await echo_agent.ipc.receive_result(timeout=5.0)

        assert resp.type == AgentToPlatformType.RESULT
        assert "echo: hello world" in resp.content
        assert resp.status == "completed"

    @pytest.mark.asyncio
    async def test_send_task_receive_progress_then_result(
        self, echo_agent: AgentHandle,
    ) -> None:
        """Send a task and receive progress + result in order."""
        task = TaskItem(
            id="t-001",
            description="do the thing",
            agent="echo-test",
        )
        await echo_agent.ipc.send_task(task)

        # First message should be progress
        msg1 = await echo_agent.ipc.receive_result(timeout=5.0)
        assert msg1.type == AgentToPlatformType.PROGRESS
        assert "working on" in msg1.content
        assert msg1.task_id == "t-001"
        assert msg1.progress_pct == 50.0

        # Second message should be result
        msg2 = await echo_agent.ipc.receive_result(timeout=5.0)
        assert msg2.type == AgentToPlatformType.RESULT
        assert "completed: do the thing" in msg2.content
        assert msg2.task_id == "t-001"
        assert msg2.status == "completed"

    @pytest.mark.asyncio
    async def test_receive_until_result_collects_progress(
        self, echo_agent: AgentHandle,
    ) -> None:
        """receive_until_result() collects intermediate progress messages."""
        progress_msgs: list[AgentToPlatform] = []

        async def track_progress(msg: AgentToPlatform) -> None:
            progress_msgs.append(msg)

        task = TaskItem(
            id="t-002",
            description="progress test",
            agent="echo-test",
        )
        await echo_agent.ipc.send_task(task)

        result = await echo_agent.ipc.receive_until_result(
            task_id="t-002",
            timeout=10.0,
            progress_callback=track_progress,
        )

        assert result.type == AgentToPlatformType.RESULT
        assert result.status == "completed"
        # Should have collected the progress message
        assert len(progress_msgs) >= 1

    @pytest.mark.asyncio
    async def test_data_reference_acknowledged(
        self, echo_agent: AgentHandle,
    ) -> None:
        """Send a data reference and get acknowledgment."""
        await echo_agent.ipc.send_data_reference(
            ref_id="var://agent-x/result",
            summary="output from agent-x",
            agent_source="agent-x",
        )

        # Should get progress acknowledgment
        msg1 = await echo_agent.ipc.receive_result(timeout=5.0)
        assert msg1.type == AgentToPlatformType.PROGRESS

        # Then result
        msg2 = await echo_agent.ipc.receive_result(timeout=5.0)
        assert msg2.type == AgentToPlatformType.RESULT

    @pytest.mark.asyncio
    async def test_rapid_fire_messages(
        self, echo_agent: AgentHandle,
    ) -> None:
        """Send multiple messages rapidly and verify all responses received."""
        n = 10
        for i in range(n):
            await echo_agent.ipc.send_chat(f"msg-{i}")

        responses = []
        for _ in range(n):
            resp = await echo_agent.ipc.receive_result(timeout=5.0)
            responses.append(resp)

        assert len(responses) == n
        # Verify all responses are results with echo content
        for resp in responses:
            assert resp.type == AgentToPlatformType.RESULT
            assert resp.content.startswith("echo: msg-")


# ---------------------------------------------------------------------------
# 3. Heartbeat: Real ping/pong
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestHeartbeatReal:
    """Verify heartbeat with real subprocess I/O."""

    @pytest.mark.asyncio
    async def test_heartbeat_pong_received(
        self, echo_agent: AgentHandle,
    ) -> None:
        """Heartbeat returns True for a responsive agent."""
        ok = await echo_agent.ipc.send_heartbeat()
        assert ok is True

    @pytest.mark.asyncio
    async def test_health_check_returns_true(
        self, pm: ProcessManager, echo_agent: AgentHandle,
    ) -> None:
        """ProcessManager.health_check returns True for responsive agent."""
        ok = await pm.health_check("echo-test")
        assert ok is True

    @pytest.mark.asyncio
    async def test_health_check_updates_last_heartbeat(
        self, pm: ProcessManager, echo_agent: AgentHandle,
    ) -> None:
        """Health check updates the handle's last_heartbeat timestamp."""
        before = echo_agent.last_heartbeat
        await pm.health_check("echo-test")
        after = echo_agent.last_heartbeat
        assert after >= before

    @pytest.mark.asyncio
    async def test_health_check_dead_process_raises_key_error(
        self, pm: ProcessManager,
    ) -> None:
        """Health check raises KeyError for a dead process (cleaned up by _cleanup_dead).

        This is the correct behavior: _cleanup_dead() inside health_check
        removes dead handles, so the agent appears "not found".
        """
        handle = await pm.start_agent(
            name="dead-hb",
            command=_echo_command(),
        )
        # Kill the process
        handle.process.kill()
        await handle.process.wait()

        with pytest.raises(KeyError, match="not found"):
            await pm.health_check("dead-hb")


# ---------------------------------------------------------------------------
# 4. IPC Error paths with real pipes
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestIPCErrorPathsReal:
    """Verify IPC error handling with real subprocess scenarios."""

    @pytest.mark.asyncio
    async def test_receive_after_stop_raises_connection_error(
        self, pm: ProcessManager,
    ) -> None:
        """Receiving from a stopped process raises IPCConnectionError."""
        handle = await pm.start_agent(
            name="stop-recv",
            command=_echo_command(),
        )

        # Stop the agent (closes pipes)
        await pm.stop_agent("stop-recv", timeout=5.0)

        # Trying to receive should fail
        with pytest.raises((IPCConnectionError, IPCConnectionError)):
            await handle.ipc.stream.receive(timeout=2.0)

    @pytest.mark.asyncio
    async def test_receive_timeout_no_message(
        self, echo_agent: AgentHandle,
    ) -> None:
        """Receive times out when agent sends nothing."""
        # Don't send any message — the agent is waiting for input
        with pytest.raises(IPCTimeoutError):
            await echo_agent.ipc.stream.receive(timeout=0.5)

    @pytest.mark.asyncio
    async def test_send_to_killed_process(
        self, pm: ProcessManager,
    ) -> None:
        """Sending to a killed process raises IPCConnectionError."""
        handle = await pm.start_agent(
            name="kill-send",
            command=_echo_command(),
        )

        # Kill the process externally (simulates crash)
        handle.process.kill()
        await handle.process.wait()

        # Send should fail because stdin pipe is broken
        with pytest.raises((IPCConnectionError, BrokenPipeError, OSError)):
            await handle.ipc.send_chat("should fail")


# ---------------------------------------------------------------------------
# 5. TaskGraph + ProcessManager integration
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestTaskGraphProcessManagerIntegration:
    """Verify TaskGraph-driven task dispatch through real IPC."""

    @pytest.mark.asyncio
    async def test_task_lifecycle_through_ipc(
        self, pm: ProcessManager,
    ) -> None:
        """Full lifecycle: create task -> dispatch via IPC -> collect result."""
        from agent_nexus.platform.orchestration.task_graph import TaskGraph

        # Set up task graph
        graph = TaskGraph(":memory:")
        graph.add_task(TaskItem(
            id="ipc-t1",
            description="integration test task",
            agent="echo-test",
        ))

        # Start echo agent
        handle = await pm.start_agent(
            name="echo-test",
            command=_echo_command(),
        )

        try:
            # Dispatch task via IPC
            ready = graph.get_ready_tasks()
            assert len(ready) == 1
            task = ready[0]
            graph.start_task(task.id)

            await handle.ipc.send_task(task)
            result = await handle.ipc.receive_until_result(
                task_id=task.id,
                timeout=10.0,
            )

            # Verify result
            assert result.type == AgentToPlatformType.RESULT
            assert result.status == "completed"

            # Update task graph
            graph.complete_task(task.id)

            # Verify task is completed
            completed = graph.get_task(task.id)
            assert completed is not None
            assert completed.state == TaskState.COMPLETED
        finally:
            await pm.stop_agent("echo-test", timeout=5.0)
            graph.close()
