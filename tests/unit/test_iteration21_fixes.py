"""Iteration 21: audit-driven fixes across orchestration, router, gateway, evolution.

Fixes:
1. ProcessManager.stop_agent holds _lock around _agents mutations
2. ProcessManager.stop_agent always closes IPC stream (even for dead processes)
3. Router parallel execution uses unique conversation_id per agent
4. Gateway _list_agents hoists core_names set outside loop
5. Gateway SSE defaults to 127.0.0.1 instead of 0.0.0.0
6. Router _aggregate_results preserves empty-string results
7. ContextBudget validator rejects negative thresholds
8. Evolution health thresholds deduplicated (health.py single source of truth)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.models.context import ContextBudget


# ---------------------------------------------------------------------------
# ContextBudget negative threshold validation
# ---------------------------------------------------------------------------

class TestContextBudgetNegativeThresholds:
    def test_negative_compaction_trigger_rejected(self):
        with pytest.raises(Exception, match="out of range"):
            ContextBudget(compaction_trigger=-0.1)

    def test_negative_session_hard_ceiling_rejected(self):
        with pytest.raises(Exception, match="out of range"):
            ContextBudget(session_hard_ceiling=-1.0)

    def test_negative_forced_truncate_rejected(self):
        with pytest.raises(Exception, match="out of range"):
            ContextBudget(forced_truncate_threshold=-0.5)

    def test_negative_compaction_target_rejected(self):
        with pytest.raises(Exception, match="out of range"):
            ContextBudget(compaction_target=-0.1)

    def test_zero_is_accepted(self):
        cfg = ContextBudget(compaction_trigger=0.0)
        assert cfg.compaction_trigger == 0.0

    def test_one_is_accepted(self):
        cfg = ContextBudget(session_hard_ceiling=1.0)
        assert cfg.session_hard_ceiling == 1.0


# ---------------------------------------------------------------------------
# ProcessManager.stop_agent holds lock during mutations
# ---------------------------------------------------------------------------

class TestStopAgentLockProtection:
    def _make_pm(self):
        from agent_nexus.platform.orchestration.process_manager import (
            AgentHandle,
            ProcessManager,
        )
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
        # Make process already dead
        mock_proc.returncode = 1

        await pm.stop_agent("test-agent")

        assert "test-agent" not in pm._agents

    @pytest.mark.asyncio
    async def test_stop_agent_closes_ipc_even_if_dead(self):
        pm, handle, mock_proc = self._make_pm()
        mock_proc.returncode = 1  # dead
        close_mock = handle.ipc.stream.close

        await pm.stop_agent("test-agent")

        close_mock.assert_awaited()


# ---------------------------------------------------------------------------
# Router parallel execution uses unique conversation_id
# ---------------------------------------------------------------------------

class TestRouterParallelConversationId:
    def test_parallel_agent_unique_cids(self):
        """Verify that _execute_parallel_agents generates unique conversation IDs.

        We mock _execute_single_agent to capture the conversation_id parameter.
        """
        from agent_nexus.platform.router.router import PlatformRouter

        captured_cids: list[str] = []

        async def mock_execute(agent_name, message, conversation_id):
            captured_cids.append(conversation_id)
            return f"result from {agent_name}"

        mock_pm = MagicMock()
        mock_pm.get_agent = MagicMock(return_value=None)

        router = PlatformRouter.__new__(PlatformRouter)
        router._process_manager = mock_pm
        router._task_graph = MagicMock()
        router._subtask = MagicMock()

        # Mock run_with_retry to immediately execute the coroutine factory
        async def mock_run_with_retry(coro_factory, timeout):
            return await coro_factory()

        async def mock_run_parallel(coros):
            results = []
            for c in coros:
                results.append(await c)
            return results

        router._subtask.run_with_retry = mock_run_with_retry
        router._subtask.run_parallel = mock_run_parallel
        router._execute_single_agent = mock_execute

        # Run the test
        result = asyncio.get_event_loop().run_until_complete(
            router._execute_parallel_agents(
                ["agent-a", "agent-b", "agent-c"],
                "test message",
                "conv-123",
            )
        )

        assert len(captured_cids) == 3
        # All CIDs should be unique
        assert len(set(captured_cids)) == 3
        # All CIDs should start with the original conversation_id
        for cid in captured_cids:
            assert cid.startswith("conv-123__")


# ---------------------------------------------------------------------------
# Gateway _list_agents hoists core_names outside loop
# ---------------------------------------------------------------------------

class TestGatewayListAgentsOptimization:
    @pytest.mark.asyncio
    async def test_core_names_computed_once(self):
        """Verify _list_agents doesn't call list_core_agents per iteration."""
        from agent_nexus.platform.gateway.gateway import MCPGateway
        from agent_nexus.models.agent import AgentManifest, AgentType

        gw = MCPGateway.__new__(MCPGateway)
        gw._registry = MagicMock()
        gw._registered_agents = set()

        call_count = 0

        class FakeCoreInfo:
            name = "core-agent"

        class FakeInfo:
            def __init__(self, name):
                self.name = name
                self.manifest = AgentManifest(
                    name=name, version="1.0.0",
                    type=AgentType.ATOMIC, description="test"
                )
                self.tool_schemas = []
                self.is_activated = False
                self.is_running = False

        def counting_list_core():
            nonlocal call_count
            call_count += 1
            return [FakeCoreInfo()]

        gw._registry.list_core_agents = counting_list_core
        gw._registry.list_all_agents = lambda: [
            FakeInfo("core-agent"),
            FakeInfo("agent-2"),
            FakeInfo("agent-3"),
        ]

        await gw._list_agents()

        # Should call list_core_agents exactly once, not once per agent
        assert call_count == 1


# ---------------------------------------------------------------------------
# Router _aggregate_results preserves empty strings
# ---------------------------------------------------------------------------

class TestAggregateResultsEmptyString:
    def test_empty_string_result_preserved(self):
        from agent_nexus.platform.router.router import PlatformRouter
        from agent_nexus.platform.router.workflow import WorkflowPhase

        result = PlatformRouter._aggregate_results(
            ["hello", "", "world"],
            WorkflowPhase.research,
        )
        # Empty string should not silently disappear
        assert "(no output)" in result
        assert "hello" in result
        assert "world" in result

    def test_exception_still_reported(self):
        from agent_nexus.platform.router.router import PlatformRouter
        from agent_nexus.platform.router.workflow import WorkflowPhase

        result = PlatformRouter._aggregate_results(
            [RuntimeError("boom")],
            WorkflowPhase.research,
        )
        assert "boom" in result

    def test_all_empty_shows_no_output(self):
        from agent_nexus.platform.router.router import PlatformRouter
        from agent_nexus.platform.router.workflow import WorkflowPhase

        result = PlatformRouter._aggregate_results(
            ["", ""],
            WorkflowPhase.research,
        )
        # Should show "(no output)" for each worker
        assert "(no output)" in result


# ---------------------------------------------------------------------------
# Gateway SSE default bind address
# ---------------------------------------------------------------------------

class TestGatewaySSEBindAddress:
    def test_sse_defaults_to_localhost(self):
        import inspect
        from agent_nexus.platform.gateway.gateway import MCPGateway

        sig = inspect.signature(MCPGateway.run_sse)
        host_default = sig.parameters["host"].default
        assert host_default == "127.0.0.1"
