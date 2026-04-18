"""Unit tests for iteration 23 fixes.

Covers:
1. IPC send() BrokenPipeError → IPCConnectionError
2. IPC send() drain TimeoutError → IPCTimeoutError
3. IPC close() drain loop bounded (max 64 chunks)
4. SubtaskController _guarded catches CancelledError (Python 3.11+)
5. SubtaskController run_with_retry catches CancelledError
6. DSL get_task_depth returns -1 on cycles
7. WorkflowContext.close() drops task_graph reference
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_nexus.models.ipc import (
    PlatformToAgent,
    PlatformToAgentType,
)
from agent_nexus.platform.orchestration.dsl import (
    DSLAgent,
    DSLTask,
    DSLToolLoading,
    OrchestrationDefinition,
)
from agent_nexus.platform.orchestration.ipc import (
    IPCConnectionError,
    IPCTimeoutError,
    IPCStream,
)
from agent_nexus.platform.router.subtask import SubtaskController
from agent_nexus.platform.router.workflow import WorkflowContext


# ============================================================================
# 1. IPC send() BrokenPipeError → IPCConnectionError
# ============================================================================


class TestIPCSendBrokenPipe:
    @pytest.mark.asyncio
    async def test_write_broken_pipe(self) -> None:
        """write() BrokenPipeError is wrapped as IPCConnectionError."""
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock(side_effect=BrokenPipeError("pipe closed"))
        mock_stdout = MagicMock()

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hi")

        with pytest.raises(IPCConnectionError, match="stdin closed"):
            await stream.send(msg)

    @pytest.mark.asyncio
    async def test_drain_broken_pipe(self) -> None:
        """drain() BrokenPipeError is wrapped as IPCConnectionError."""
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock(side_effect=BrokenPipeError("gone"))
        mock_stdout = MagicMock()

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hi")

        with pytest.raises(IPCConnectionError, match="stdin closed during drain"):
            await stream.send(msg)

    @pytest.mark.asyncio
    async def test_drain_connection_reset(self) -> None:
        """drain() ConnectionResetError is wrapped as IPCConnectionError."""
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock(side_effect=ConnectionResetError("reset"))
        mock_stdout = MagicMock()

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hi")

        with pytest.raises(IPCConnectionError, match="stdin closed during drain"):
            await stream.send(msg)


# ============================================================================
# 2. IPC send() drain timeout → IPCTimeoutError
# ============================================================================


class TestIPCSendDrainTimeout:
    @pytest.mark.asyncio
    async def test_drain_timeout_wrapped(self) -> None:
        """drain() asyncio.TimeoutError is wrapped as IPCTimeoutError."""
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_stdout = MagicMock()
        mock_stdout.read = AsyncMock(return_value=b"")
        mock_stdout.readline = AsyncMock(return_value=b"")

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hi")

        with pytest.raises(IPCTimeoutError, match="draining stdin"):
            await stream.send(msg)


# ============================================================================
# 3. IPC close() drain bounded
# ============================================================================


class TestIPCCloseDrainBound:
    @pytest.mark.asyncio
    async def test_close_drain_stops_after_max_chunks(self) -> None:
        """close() drain loop stops after 64 chunks even with more data."""
        mock_stdin = MagicMock()
        mock_stdin.is_closing.return_value = True
        mock_stdin.wait_closed = AsyncMock()
        mock_stdout = MagicMock()
        # Simulate unlimited output
        call_count = 0

        async def infinite_read(n):
            nonlocal call_count
            call_count += 1
            return b"x" * n  # never returns b"" → would loop forever

        mock_stdout.read = infinite_read

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        await stream.close()

        # Should stop at 64 chunks, not loop forever
        assert call_count == 64


# ============================================================================
# 4. SubtaskController CancelledError handling
# ============================================================================


class TestSubtaskCancelledError:
    @pytest.mark.asyncio
    async def test_run_parallel_catches_cancelled_error(self) -> None:
        """CancelledError in run_parallel is captured, not raised."""
        controller = SubtaskController()

        async def failing_coro():
            raise asyncio.CancelledError("test cancel")

        results = await controller.run_parallel([failing_coro()])
        assert len(results) == 1
        assert isinstance(results[0], asyncio.CancelledError)

    @pytest.mark.asyncio
    async def test_run_with_retry_catches_cancelled_error(self) -> None:
        """CancelledError in run_with_retry is retried, not propagated."""

        attempt = 0

        async def cancel_then_succeed():
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise asyncio.CancelledError("first cancel")
            return "ok"

        controller = SubtaskController()
        result = await controller.run_with_retry(
            cancel_then_succeed, max_retries=2,
        )
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_run_with_retry_all_cancelled(self) -> None:
        """All attempts CancelledError raises the last one."""

        async def always_cancel():
            raise asyncio.CancelledError("always")

        controller = SubtaskController()
        with pytest.raises(asyncio.CancelledError, match="always"):
            await controller.run_with_retry(always_cancel, max_retries=1)

    @pytest.mark.asyncio
    async def test_run_parallel_mixed_success_and_cancel(self) -> None:
        """Mixed success and CancelledError results."""
        controller = SubtaskController()

        async def ok():
            return "done"

        async def cancel():
            raise asyncio.CancelledError("nope")

        results = await controller.run_parallel([ok(), cancel()])
        assert results[0] == "done"
        assert isinstance(results[1], asyncio.CancelledError)


# ============================================================================
# 6. DSL get_task_depth returns -1 on cycles
# ============================================================================


class TestDSLDepthCycleReturnsNegOne:
    def test_two_node_cycle(self) -> None:
        agents = {"a1": DSLAgent(name="a1", description="A")}
        tasks = [
            DSLTask(id="T1", description="", agent="a1", blocked_by=["T2"]),
            DSLTask(id="T2", description="", agent="a1", blocked_by=["T1"]),
        ]
        defn = OrchestrationDefinition(
            goal="Cycle",
            agent_name="cycle-test",
            agents=agents,
            tasks=tasks,
            tool_loading=DSLToolLoading(),
        )
        assert defn.get_task_depth("T1") == -1
        assert defn.get_task_depth("T2") == -1

    def test_self_loop(self) -> None:
        agents = {"a1": DSLAgent(name="a1", description="A")}
        tasks = [
            DSLTask(id="T1", description="", agent="a1", blocked_by=["T1"]),
        ]
        defn = OrchestrationDefinition(
            goal="Self-loop",
            agent_name="self-loop-test",
            agents=agents,
            tasks=tasks,
            tool_loading=DSLToolLoading(),
        )
        assert defn.get_task_depth("T1") == -1

    def test_downstream_of_cycle_also_neg_one(self) -> None:
        """Tasks depending on a cyclic task also get depth -1."""
        agents = {"a1": DSLAgent(name="a1", description="A")}
        tasks = [
            DSLTask(id="T1", description="", agent="a1", blocked_by=["T2"]),
            DSLTask(id="T2", description="", agent="a1", blocked_by=["T1"]),
            DSLTask(id="T3", description="", agent="a1", blocked_by=["T1"]),
        ]
        defn = OrchestrationDefinition(
            goal="Cycle + downstream",
            agent_name="test",
            agents=agents,
            tasks=tasks,
            tool_loading=DSLToolLoading(),
        )
        # T3 depends on T1 which is in a cycle
        assert defn.get_task_depth("T3") == -1


# ============================================================================
# 7. WorkflowContext.close() drops task_graph
# ============================================================================


class TestWorkflowContextClose:
    def test_close_sets_task_graph_none(self) -> None:
        ctx = WorkflowContext(
            conversation_id="c1",
            message="hi",
            agent_name="test",
        )
        assert ctx.task_graph is None
        ctx.close()
        assert ctx.task_graph is None

    def test_close_with_task_graph(self, tmp_path) -> None:
        from agent_nexus.platform.orchestration.task_graph import TaskGraph

        tg = TaskGraph(tmp_path / "test.db")
        ctx = WorkflowContext(
            conversation_id="c1",
            message="hi",
            agent_name="test",
            task_graph=tg,
        )
        assert ctx.task_graph is not None
        ctx.close()
        assert ctx.task_graph is None
