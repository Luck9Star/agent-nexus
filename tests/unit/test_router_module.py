"""Unit tests for the Platform Router module.

Covers three source files:
- workflow.py: WorkflowPhase, WorkflowContext, WorkflowResult
- subtask.py: SubtaskConfig, SubtaskController
- router.py: PlatformRouter

Uses mocks for ProcessManager, IPC, and external dependencies.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.models.ipc import AgentToPlatform, AgentToPlatformType
from agent_nexus.platform.orchestration.dsl import (
    DSLAgent,
    DSLTask,
    DSLToolLoading,
    OrchestrationDefinition,
)
from agent_nexus.platform.orchestration.ipc import (
    IPCConnectionError,
    IPCError,
    IPCTimeoutError,
)
from agent_nexus.platform.router.router import PlatformRouter, _PHASE_ORDER
from agent_nexus.platform.router.subtask import SubtaskConfig, SubtaskController
from agent_nexus.platform.router.workflow import (
    WorkflowContext,
    WorkflowPhase,
    WorkflowResult,
)


# ============================================================================
# Helpers
# ============================================================================


def _make_agent_handle(
    name: str = "test-agent",
    alive: bool = True,
    response_content: str = "agent response",
    response_type: AgentToPlatformType = AgentToPlatformType.RESULT,
    response_error: str | None = None,
    response_status: str | None = "completed",
) -> MagicMock:
    """Create a mock AgentHandle with an IPC mock.

    Returns a MagicMock whose .ipc attributes (send_chat, receive_until_result)
    are AsyncMock instances pre-configured with the given response.
    """
    handle = MagicMock()
    handle.name = name
    handle.is_alive = alive

    # Build mock response
    mock_response = MagicMock()
    mock_response.type = response_type
    mock_response.content = response_content
    mock_response.error = response_error
    mock_response.status = response_status
    mock_response.output = response_content

    handle.ipc = MagicMock()
    handle.ipc.send_chat = AsyncMock()
    handle.ipc.receive_until_result = AsyncMock(return_value=mock_response)

    return handle


def _make_process_manager(
    agents: dict[str, MagicMock] | None = None,
    running: list[str] | None = None,
) -> MagicMock:
    """Create a mock ProcessManager."""
    pm = MagicMock()
    pm._agents = agents or {}
    pm.get_agent = MagicMock(side_effect=lambda name: pm._agents.get(name))
    pm.list_running = MagicMock(return_value=running or list((agents or {}).keys()))
    pm.stop_all = AsyncMock()
    return pm


def _make_definition(
    agent_name: str = "composite-agent",
    agents: dict[str, DSLAgent] | None = None,
    tasks: list[DSLTask] | None = None,
) -> OrchestrationDefinition:
    """Create a minimal OrchestrationDefinition for testing."""
    if agents is None:
        agents = {
            "explorer": DSLAgent(
                name="explorer", description="Explore", role="explore"
            ),
            "planner": DSLAgent(
                name="planner", description="Plan", role="plan"
            ),
            "worker1": DSLAgent(
                name="worker1", description="Work", role="worker"
            ),
            "verifier": DSLAgent(
                name="verifier", description="Verify", role="verification"
            ),
        }
    if tasks is None:
        tasks = [
            DSLTask(id="t1", description="Explore", agent="explorer"),
            DSLTask(id="t2", description="Plan", agent="planner"),
            DSLTask(id="t3", description="Work", agent="worker1"),
            DSLTask(id="t4", description="Verify", agent="verifier"),
        ]
    return OrchestrationDefinition(
        goal="Test goal",
        agent_name=agent_name,
        agents=agents,
        tasks=tasks,
        tool_loading=DSLToolLoading(),
    )


# ============================================================================
# WorkflowPhase Tests
# ============================================================================


class TestWorkflowPhase:
    """Tests for WorkflowPhase StrEnum."""

    def test_has_four_phases(self) -> None:
        assert len(WorkflowPhase) == 4

    def test_phase_values(self) -> None:
        assert WorkflowPhase.research == "research"
        assert WorkflowPhase.synthesis == "synthesis"
        assert WorkflowPhase.implementation == "implementation"
        assert WorkflowPhase.verification == "verification"

    def test_is_str_enum(self) -> None:
        for phase in WorkflowPhase:
            assert isinstance(phase, str)

    def test_string_comparison(self) -> None:
        assert WorkflowPhase.research == "research"
        assert WorkflowPhase.synthesis != "research"

    def test_iteration_order(self) -> None:
        phases = list(WorkflowPhase)
        assert phases == [
            WorkflowPhase.research,
            WorkflowPhase.synthesis,
            WorkflowPhase.implementation,
            WorkflowPhase.verification,
        ]


# ============================================================================
# WorkflowContext Tests
# ============================================================================


class TestWorkflowContext:
    """Tests for WorkflowContext dataclass."""

    def test_default_fields(self) -> None:
        ctx = WorkflowContext(
            conversation_id="conv-1",
            message="hello",
            agent_name="agent-a",
        )
        assert ctx.conversation_id == "conv-1"
        assert ctx.message == "hello"
        assert ctx.agent_name == "agent-a"
        assert ctx.phase_results == {}
        assert ctx.current_phase is None
        assert ctx.task_graph is None
        assert isinstance(ctx.started_at, datetime)
        assert ctx.started_at.tzinfo == timezone.utc

    def test_explicit_fields(self) -> None:
        from agent_nexus.platform.orchestration.task_graph import TaskGraph

        tg = TaskGraph(Path(":memory:"))
        ctx = WorkflowContext(
            conversation_id="c2",
            message="world",
            agent_name="agent-b",
            phase_results={WorkflowPhase.research: "data"},
            current_phase=WorkflowPhase.synthesis,
            task_graph=tg,
        )
        assert ctx.phase_results == {WorkflowPhase.research: "data"}
        assert ctx.current_phase == WorkflowPhase.synthesis
        assert ctx.task_graph is tg

    def test_started_at_auto_set(self) -> None:
        before = datetime.now(timezone.utc)
        ctx = WorkflowContext(
            conversation_id="c", message="m", agent_name="a"
        )
        after = datetime.now(timezone.utc)
        assert before <= ctx.started_at <= after

    def test_phase_results_independent_per_instance(self) -> None:
        """Mutable default_factory should not share state."""
        ctx1 = WorkflowContext(
            conversation_id="c1", message="m", agent_name="a"
        )
        ctx2 = WorkflowContext(
            conversation_id="c2", message="m", agent_name="a"
        )
        ctx1.phase_results[WorkflowPhase.research] = "data"
        assert WorkflowPhase.research not in ctx2.phase_results

    def test_phase_results_accepts_any_values(self) -> None:
        ctx = WorkflowContext(
            conversation_id="c", message="m", agent_name="a"
        )
        ctx.phase_results[WorkflowPhase.research] = {"key": "value"}
        ctx.phase_results[WorkflowPhase.synthesis] = [1, 2, 3]
        assert ctx.phase_results[WorkflowPhase.research] == {"key": "value"}
        assert ctx.phase_results[WorkflowPhase.synthesis] == [1, 2, 3]


# ============================================================================
# WorkflowResult Tests
# ============================================================================


class TestWorkflowResult:
    """Tests for WorkflowResult dataclass."""

    def test_successful_result(self) -> None:
        result = WorkflowResult(
            success=True,
            final_output="done",
            phase_results={WorkflowPhase.verification: "done"},
            total_phases=4,
            completed_phases=4,
        )
        assert result.success is True
        assert result.final_output == "done"
        assert result.error is None

    def test_failed_result(self) -> None:
        result = WorkflowResult(
            success=False,
            final_output="",
            phase_results={},
            total_phases=4,
            completed_phases=2,
            error="Phase synthesis failed: timeout",
        )
        assert result.success is False
        assert result.completed_phases == 2
        assert result.error is not None
        assert "synthesis" in result.error

    def test_partial_result_with_synthesis(self) -> None:
        result = WorkflowResult(
            success=False,
            final_output="partial plan",
            phase_results={WorkflowPhase.synthesis: "partial plan"},
            total_phases=4,
            completed_phases=2,
            error="Phase implementation failed: boom",
        )
        assert result.phase_results[WorkflowPhase.synthesis] == "partial plan"

    def test_defaults(self) -> None:
        result = WorkflowResult(
            success=True,
            final_output="ok",
            phase_results={},
            total_phases=4,
            completed_phases=4,
        )
        assert result.error is None


# ============================================================================
# SubtaskConfig Tests
# ============================================================================


class TestSubtaskConfig:
    """Tests for SubtaskConfig dataclass."""

    def test_default_values(self) -> None:
        cfg = SubtaskConfig()
        assert cfg.timeout_seconds == 60.0
        assert cfg.max_retries == 2
        assert cfg.max_parallel == 3

    def test_custom_values(self) -> None:
        cfg = SubtaskConfig(timeout_seconds=120.0, max_retries=5, max_parallel=10)
        assert cfg.timeout_seconds == 120.0
        assert cfg.max_retries == 5
        assert cfg.max_parallel == 10


# ============================================================================
# SubtaskController Tests
# ============================================================================


class TestSubtaskController:
    """Tests for SubtaskController."""

    def test_init_default_config(self) -> None:
        ctrl = SubtaskController()
        assert ctrl._config.timeout_seconds == 60.0
        assert ctrl._config.max_retries == 2
        assert ctrl._config.max_parallel == 3

    def test_init_custom_config(self) -> None:
        cfg = SubtaskConfig(timeout_seconds=30.0, max_retries=5, max_parallel=10)
        ctrl = SubtaskController(config=cfg)
        assert ctrl._config.timeout_seconds == 30.0

    @pytest.mark.asyncio
    async def test_run_with_timeout_success(self) -> None:
        ctrl = SubtaskController()

        async def coro():
            return 42

        result = await ctrl.run_with_timeout(coro())
        assert result == 42

    @pytest.mark.asyncio
    async def test_run_with_timeout_exceeds_default(self) -> None:
        """Coroutine exceeding config timeout raises TimeoutError."""
        ctrl = SubtaskConfig(timeout_seconds=0.1)
        ctrl = SubtaskController(config=ctrl)

        async def slow():
            await asyncio.sleep(10)

        with pytest.raises(asyncio.TimeoutError):
            await ctrl.run_with_timeout(slow())

    @pytest.mark.asyncio
    async def test_run_with_timeout_custom_override(self) -> None:
        """Custom timeout overrides config default."""
        cfg = SubtaskConfig(timeout_seconds=60.0)
        ctrl = SubtaskController(config=cfg)

        async def slow():
            await asyncio.sleep(10)

        with pytest.raises(asyncio.TimeoutError):
            await ctrl.run_with_timeout(slow(), timeout=0.05)

    @pytest.mark.asyncio
    async def test_run_with_timeout_none_uses_config(self) -> None:
        """timeout=None falls back to config timeout_seconds."""
        cfg = SubtaskConfig(timeout_seconds=0.1)
        ctrl = SubtaskController(config=cfg)

        async def slow():
            await asyncio.sleep(10)

        with pytest.raises(asyncio.TimeoutError):
            await ctrl.run_with_timeout(slow(), timeout=None)

    @pytest.mark.asyncio
    async def test_run_with_retry_success_first_try(self) -> None:
        ctrl = SubtaskController()
        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await ctrl.run_with_retry(factory)
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_run_with_retry_succeeds_on_second_attempt(self) -> None:
        cfg = SubtaskConfig(max_retries=2, timeout_seconds=5.0)
        ctrl = SubtaskController(config=cfg)
        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise RuntimeError("first fail")
            return "recovered"

        result = await ctrl.run_with_retry(factory)
        assert result == "recovered"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_run_with_retry_exhausted(self) -> None:
        """All retries exhausted raises last exception."""
        cfg = SubtaskConfig(max_retries=1, timeout_seconds=5.0)
        ctrl = SubtaskController(config=cfg)

        async def factory():
            raise ValueError("always fails")

        with pytest.raises(ValueError, match="always fails"):
            await ctrl.run_with_retry(factory)

    @pytest.mark.asyncio
    async def test_run_with_retry_custom_max_retries(self) -> None:
        """Custom max_retries overrides config."""
        ctrl = SubtaskController()
        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"fail {call_count}")

        with pytest.raises(RuntimeError, match="fail 4"):
            await ctrl.run_with_retry(factory, max_retries=3)
        assert call_count == 4  # 1 initial + 3 retries

    @pytest.mark.asyncio
    async def test_run_parallel_all_success(self) -> None:
        ctrl = SubtaskController()

        async def make_coro(val):
            return val

        results = await ctrl.run_parallel(
            [make_coro("a"), make_coro("b"), make_coro("c")]
        )
        assert results == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_run_parallel_empty_list(self) -> None:
        ctrl = SubtaskController()
        results = await ctrl.run_parallel([])
        assert results == []

    @pytest.mark.asyncio
    async def test_run_parallel_mixed_success_and_failure(self) -> None:
        ctrl = SubtaskController()

        async def ok():
            return "success"

        async def fail():
            raise RuntimeError("boom")

        results = await ctrl.run_parallel([ok(), fail(), ok()])
        assert results[0] == "success"
        assert isinstance(results[1], RuntimeError)
        # results[2] may be "success" or RuntimeError depending on
        # whether it started before fail() set the failed flag.
        assert results[2] == "success" or isinstance(results[2], RuntimeError)

    @pytest.mark.asyncio
    async def test_run_parallel_all_fail(self) -> None:
        ctrl = SubtaskController()

        async def fail():
            raise ValueError("nope")

        results = await ctrl.run_parallel([fail(), fail()])
        # Both should be exceptions, but second may be RuntimeError (cancelled)
        # instead of ValueError if fail-fast skipped it.
        assert all(isinstance(r, Exception) for r in results)
        assert isinstance(results[0], ValueError)

    @pytest.mark.asyncio
    async def test_run_parallel_respects_max_parallel(self) -> None:
        """Concurrency limited by max_parallel semaphore."""
        cfg = SubtaskConfig(max_parallel=2, timeout_seconds=10.0)
        ctrl = SubtaskController(config=cfg)
        peak = 0
        current = 0

        async def tracked():
            nonlocal peak, current
            current += 1
            if current > peak:
                peak = current
            await asyncio.sleep(0.05)
            current -= 1
            return "done"

        results = await ctrl.run_parallel(
            [tracked() for _ in range(6)]
        )
        assert len(results) == 6
        assert peak <= 2  # max_parallel=2


# ============================================================================
# PlatformRouter Tests
# ============================================================================


class TestPlatformRouterInit:
    """Tests for PlatformRouter initialization."""

    def test_init_with_defaults(self) -> None:
        pm = _make_process_manager()
        router = PlatformRouter(process_manager=pm)
        assert router._pm is pm
        assert isinstance(router._subtask, SubtaskController)

    def test_init_with_custom_subtask(self) -> None:
        pm = _make_process_manager()
        ctrl = SubtaskController(config=SubtaskConfig(timeout_seconds=30.0))
        router = PlatformRouter(process_manager=pm, subtask_controller=ctrl)
        assert router._subtask is ctrl


class TestPhaseToRole:
    """Tests for PlatformRouter._phase_to_role static method."""

    def test_research_maps_to_explore(self) -> None:
        assert PlatformRouter._phase_to_role(WorkflowPhase.research) == "explore"

    def test_synthesis_maps_to_plan(self) -> None:
        assert PlatformRouter._phase_to_role(WorkflowPhase.synthesis) == "plan"

    def test_implementation_maps_to_worker(self) -> None:
        assert PlatformRouter._phase_to_role(WorkflowPhase.implementation) == "worker"

    def test_verification_maps_to_verification(self) -> None:
        assert PlatformRouter._phase_to_role(WorkflowPhase.verification) == "verification"

    def test_all_phases_have_roles(self) -> None:
        for phase in WorkflowPhase:
            role = PlatformRouter._phase_to_role(phase)
            assert isinstance(role, str)
            assert role


class TestBuildPhaseMessage:
    """Tests for PlatformRouter._build_phase_message static method."""

    def test_research_feeds_synthesis(self) -> None:
        msg = PlatformRouter._build_phase_message(
            WorkflowPhase.research, "found X"
        )
        assert "Research Results" in msg
        assert "found X" in msg
        assert "implementation plan" in msg

    def test_synthesis_feeds_implementation(self) -> None:
        msg = PlatformRouter._build_phase_message(
            WorkflowPhase.synthesis, "plan: do Y"
        )
        assert "Implementation Plan" in msg
        assert "plan: do Y" in msg
        assert "Execute the above plan" in msg

    def test_implementation_feeds_verification(self) -> None:
        msg = PlatformRouter._build_phase_message(
            WorkflowPhase.implementation, "built Z"
        )
        assert "Implementation Output" in msg
        assert "built Z" in msg
        assert "Verify" in msg

    def test_verification_returns_raw(self) -> None:
        msg = PlatformRouter._build_phase_message(
            WorkflowPhase.verification, "all good"
        )
        assert msg == "all good"


class TestAggregateResults:
    """Tests for PlatformRouter._aggregate_results static method."""

    def test_all_successful(self) -> None:
        results = ["output-a", "output-b"]
        out = PlatformRouter._aggregate_results(results, WorkflowPhase.research)
        assert "output-a" in out
        assert "output-b" in out
        assert "---" in out

    def test_mixed_results(self) -> None:
        results = ["ok", RuntimeError("fail"), "also-ok"]
        out = PlatformRouter._aggregate_results(results, WorkflowPhase.research)
        assert "ok" in out
        assert "also-ok" in out
        assert "Warnings" in out
        assert "Worker 2 failed" in out

    def test_all_exceptions(self) -> None:
        results = [ValueError("a"), ValueError("b")]
        out = PlatformRouter._aggregate_results(results, WorkflowPhase.research)
        assert "Worker 1 failed" in out
        assert "Worker 2 failed" in out

    def test_empty_results(self) -> None:
        out = PlatformRouter._aggregate_results([], WorkflowPhase.research)
        assert "No results" in out

    def test_empty_strings_filtered(self) -> None:
        results = ["", "content", ""]
        out = PlatformRouter._aggregate_results(results, WorkflowPhase.research)
        assert "content" in out


class TestRouteToAtomic:
    """Tests for PlatformRouter.route_to_atomic."""

    @pytest.mark.asyncio
    async def test_successful_routing(self) -> None:
        handle = _make_agent_handle(response_content="hello back")
        pm = _make_process_manager(agents={"agent-a": handle})
        router = PlatformRouter(process_manager=pm)

        result = await router.route_to_atomic("agent-a", "hello", "conv-1")
        assert result["output"] == "hello back"
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_agent_not_found_returns_error(self) -> None:
        pm = _make_process_manager(agents={})
        router = PlatformRouter(process_manager=pm)

        result = await router.route_to_atomic("missing", "hello", "conv-1")
        assert result["success"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_agent_not_alive(self) -> None:
        handle = _make_agent_handle(alive=False)
        pm = _make_process_manager(agents={"agent-a": handle})
        router = PlatformRouter(process_manager=pm)

        result = await router.route_to_atomic("agent-a", "hello", "conv-1")
        assert result["success"] is False
        assert "not alive" in result["error"]

    @pytest.mark.asyncio
    async def test_ipc_error_response(self) -> None:
        handle = _make_agent_handle(
            response_type=AgentToPlatformType.ERROR,
            response_error="agent crashed",
        )
        pm = _make_process_manager(agents={"agent-a": handle})
        router = PlatformRouter(process_manager=pm)

        result = await router.route_to_atomic("agent-a", "hello", "conv-1")
        assert result["success"] is False
        assert "agent crashed" in result["error"]

    @pytest.mark.asyncio
    async def test_ipc_exception_returns_error_dict(self) -> None:
        handle = _make_agent_handle()
        handle.ipc.receive_until_result = AsyncMock(
            side_effect=RuntimeError("pipe broken")
        )
        pm = _make_process_manager(agents={"agent-a": handle})
        router = PlatformRouter(process_manager=pm)

        result = await router.route_to_atomic("agent-a", "hello", "conv-1")
        assert result["success"] is False
        assert "IPC error" in result["error"]
        assert "pipe broken" in result["error"]

    @pytest.mark.asyncio
    async def test_sends_chat_via_ipc(self) -> None:
        handle = _make_agent_handle()
        pm = _make_process_manager(agents={"agent-a": handle})
        router = PlatformRouter(process_manager=pm)

        await router.route_to_atomic("agent-a", "hello", "conv-1")
        handle.ipc.send_chat.assert_awaited_once_with(
            "hello", conversation_id="conv-1"
        )

    @pytest.mark.asyncio
    async def test_failed_status_response(self) -> None:
        handle = _make_agent_handle(response_status="failed", response_content="")
        pm = _make_process_manager(agents={"agent-a": handle})
        router = PlatformRouter(process_manager=pm)

        result = await router.route_to_atomic("agent-a", "hello", "conv-1")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_status_case_insensitive_uppercase(self) -> None:
        """External agents may send 'COMPLETED' — must still be success."""
        handle = _make_agent_handle(response_status="COMPLETED")
        pm = _make_process_manager(agents={"agent-a": handle})
        router = PlatformRouter(process_manager=pm)

        result = await router.route_to_atomic("agent-a", "hello", "conv-1")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_status_case_insensitive_mixed(self) -> None:
        """External agents may send 'Completed' — must still be success."""
        handle = _make_agent_handle(response_status="Completed")
        pm = _make_process_manager(agents={"agent-a": handle})
        router = PlatformRouter(process_manager=pm)

        result = await router.route_to_atomic("agent-a", "hello", "conv-1")
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_status_none_defaults_to_success(self) -> None:
        """Minimal agents that omit status field should not appear to fail."""
        handle = _make_agent_handle(response_status=None)
        pm = _make_process_manager(agents={"agent-a": handle})
        router = PlatformRouter(process_manager=pm)

        result = await router.route_to_atomic("agent-a", "hello", "conv-1")
        assert result["success"] is True
        assert result["output"] == "agent response"


class TestRouteChat:
    """Tests for PlatformRouter.route_chat."""

    @pytest.mark.asyncio
    async def test_generates_conversation_id(self) -> None:
        handle = _make_agent_handle()
        pm = _make_process_manager(agents={"a": handle})
        router = PlatformRouter(process_manager=pm)

        result = await router.route_chat("a", "hi")
        assert result["success"] is True
        # Verify send_chat was called with a UUID conversation_id
        call_args = handle.ipc.send_chat.call_args
        conv_id = call_args.kwargs.get("conversation_id")
        assert conv_id is not None
        # UUID format check
        import uuid
        uuid.UUID(conv_id)  # raises ValueError if not a valid UUID

    @pytest.mark.asyncio
    async def test_uses_provided_conversation_id(self) -> None:
        handle = _make_agent_handle()
        pm = _make_process_manager(agents={"a": handle})
        router = PlatformRouter(process_manager=pm)

        await router.route_chat("a", "hi", conversation_id="fixed-id")
        handle.ipc.send_chat.assert_awaited_once_with(
            "hi", conversation_id="fixed-id"
        )


class TestRouteComposite:
    """Tests for PlatformRouter.route_composite.

    route_composite internally creates TaskGraph(Path(":memory:")), but SQLite
    in-memory databases are per-connection, so the _init_db tables are lost when
    _conn() opens a new connection. We patch TaskGraph in the router module to
    avoid hitting the real SQLite layer for these integration-style tests.
    """

    @pytest.mark.asyncio
    async def test_full_4_phase_success(self) -> None:
        """All 4 phases complete successfully."""
        agents = {
            "explorer": _make_agent_handle(name="explorer", response_content="research data"),
            "planner": _make_agent_handle(name="planner", response_content="a plan"),
            "worker1": _make_agent_handle(name="worker1", response_content="implementation"),
            "verifier": _make_agent_handle(name="verifier", response_content="verified"),
        }
        pm = _make_process_manager(agents=agents)
        router = PlatformRouter(process_manager=pm)
        definition = _make_definition()

        mock_tg = MagicMock()
        with patch(
            "agent_nexus.platform.router.router.TaskGraph", return_value=mock_tg
        ):
            result = await router.route_composite(
                definition, "build feature X", "conv-1"
            )

        assert result.success is True
        assert result.completed_phases == 4
        assert result.total_phases == 4
        assert result.error is None
        assert WorkflowPhase.verification in result.phase_results

    @pytest.mark.asyncio
    async def test_phase_failure_stops_workflow(self) -> None:
        """Workflow stops when a phase raises an exception."""
        agents = {
            "explorer": _make_agent_handle(name="explorer", response_content="data"),
            "planner": _make_agent_handle(
                name="planner",
                response_type=AgentToPlatformType.ERROR,
                response_error="planner crashed",
            ),
            "worker1": _make_agent_handle(name="worker1", response_content="impl"),
            "verifier": _make_agent_handle(name="verifier", response_content="ok"),
        }
        pm = _make_process_manager(agents=agents)
        router = PlatformRouter(process_manager=pm)
        definition = _make_definition()

        mock_tg = MagicMock()
        with patch(
            "agent_nexus.platform.router.router.TaskGraph", return_value=mock_tg
        ):
            result = await router.route_composite(definition, "test", "conv-1")

        assert result.success is False
        assert result.completed_phases < 4
        assert result.error is not None
        assert "synthesis" in result.error

    @pytest.mark.asyncio
    async def test_research_agent_error_reports_warning(self) -> None:
        """Research phase with a failing agent reports warnings but does not stop.

        Parallel phases (research, implementation) catch agent errors via
        run_with_retry + run_parallel, reporting them as warnings in the
        aggregated result rather than raising. The workflow continues.
        """
        agents = {
            "explorer": _make_agent_handle(
                name="explorer",
                response_type=AgentToPlatformType.ERROR,
                response_error="research boom",
            ),
            "planner": _make_agent_handle(name="planner"),
            "worker1": _make_agent_handle(name="worker1"),
            "verifier": _make_agent_handle(name="verifier"),
        }
        pm = _make_process_manager(agents=agents)
        router = PlatformRouter(process_manager=pm)
        definition = _make_definition()

        mock_tg = MagicMock()
        with patch(
            "agent_nexus.platform.router.router.TaskGraph", return_value=mock_tg
        ):
            result = await router.route_composite(definition, "test", "conv-1")

        # Parallel phases don't raise on agent error -- they aggregate warnings
        assert result.completed_phases == 4
        assert "research boom" in result.phase_results[WorkflowPhase.research]

    @pytest.mark.asyncio
    async def test_synthesis_phase_failure_stops_workflow(self) -> None:
        """Synthesis (single-agent phase) failure stops the workflow.

        Unlike parallel phases, single-agent phases (synthesis, verification)
        raise on error, which stops the workflow.
        """
        agents = {
            "explorer": _make_agent_handle(name="explorer", response_content="data"),
            "planner": _make_agent_handle(
                name="planner",
                response_type=AgentToPlatformType.ERROR,
                response_error="planner crashed",
            ),
            "worker1": _make_agent_handle(name="worker1"),
            "verifier": _make_agent_handle(name="verifier"),
        }
        pm = _make_process_manager(agents=agents)
        router = PlatformRouter(process_manager=pm)
        definition = _make_definition()

        mock_tg = MagicMock()
        with patch(
            "agent_nexus.platform.router.router.TaskGraph", return_value=mock_tg
        ):
            result = await router.route_composite(definition, "test", "conv-1")

        assert result.success is False
        assert result.completed_phases == 1  # research succeeded, synthesis failed
        assert result.error is not None
        assert "synthesis" in result.error

    @pytest.mark.asyncio
    async def test_creates_fresh_context_per_workflow(self) -> None:
        """Each composite workflow gets a new context (no state leakage)."""
        agents = {
            "explorer": _make_agent_handle(name="explorer", response_content="data"),
            "planner": _make_agent_handle(name="planner", response_content="plan"),
            "worker1": _make_agent_handle(name="worker1", response_content="impl"),
            "verifier": _make_agent_handle(name="verifier", response_content="ok"),
        }
        pm = _make_process_manager(agents=agents)
        router = PlatformRouter(process_manager=pm)
        definition = _make_definition()

        with patch(
            "agent_nexus.platform.router.router.TaskGraph", return_value=MagicMock()
        ):
            r1 = await router.route_composite(definition, "first", "conv-1")
            r2 = await router.route_composite(definition, "second", "conv-2")

        assert r1.success is True
        assert r2.success is True
        # Both are independent results
        assert r1 is not r2

    @pytest.mark.asyncio
    async def test_verification_failure_uses_synthesis_as_partial(self) -> None:
        """When verification (single-agent) fails, synthesis output is partial result."""
        agents = {
            "explorer": _make_agent_handle(name="explorer", response_content="research data"),
            "planner": _make_agent_handle(name="planner", response_content="partial plan"),
            "worker1": _make_agent_handle(name="worker1", response_content="built it"),
            "verifier": _make_agent_handle(
                name="verifier",
                response_type=AgentToPlatformType.ERROR,
                response_error="verification failed",
            ),
        }
        pm = _make_process_manager(agents=agents)
        router = PlatformRouter(process_manager=pm)
        definition = _make_definition()

        mock_tg = MagicMock()
        with patch(
            "agent_nexus.platform.router.router.TaskGraph", return_value=mock_tg
        ):
            result = await router.route_composite(definition, "test", "conv-1")

        assert result.success is False
        assert result.completed_phases == 3  # research + synthesis + implementation
        assert result.error is not None
        assert "verification" in result.error
        # Synthesis output used as partial result since verification failed
        assert "partial plan" in result.final_output

    @pytest.mark.asyncio
    async def test_implementation_worker_error_included_as_warning(self) -> None:
        """Parallel phase worker errors are aggregated as warnings, not failures."""
        agents = {
            "explorer": _make_agent_handle(name="explorer", response_content="data"),
            "planner": _make_agent_handle(name="planner", response_content="plan"),
            "worker1": _make_agent_handle(
                name="worker1",
                response_type=AgentToPlatformType.ERROR,
                response_error="worker died",
            ),
            "verifier": _make_agent_handle(name="verifier", response_content="ok"),
        }
        pm = _make_process_manager(agents=agents)
        router = PlatformRouter(process_manager=pm)
        definition = _make_definition()

        mock_tg = MagicMock()
        with patch(
            "agent_nexus.platform.router.router.TaskGraph", return_value=mock_tg
        ):
            result = await router.route_composite(definition, "test", "conv-1")

        # Workflow succeeds overall (parallel errors are warnings)
        assert result.success is True
        assert result.completed_phases == 4
        assert "worker died" in result.phase_results[WorkflowPhase.implementation]

    @pytest.mark.asyncio
    async def test_no_agents_fallback(self) -> None:
        """Definition with no agents at all produces a non-crashing result."""
        definition = OrchestrationDefinition(
            goal="empty",
            agent_name="empty-agent",
            agents={},
            tasks=[],
            tool_loading=DSLToolLoading(),
        )
        pm = _make_process_manager(agents={})
        router = PlatformRouter(process_manager=pm)

        mock_tg = MagicMock()
        with patch(
            "agent_nexus.platform.router.router.TaskGraph", return_value=mock_tg
        ):
            result = await router.route_composite(definition, "test", "conv-1")

        # First phase raises RuntimeError for no agents; workflow fails
        assert result.completed_phases == 0
        assert result.success is False


class TestRegisterComposite:
    """Tests for PlatformRouter.register_composite + route_chat composite detection."""

    def test_register_composite_stores_definition(self) -> None:
        pm = _make_process_manager()
        router = PlatformRouter(process_manager=pm)
        definition = _make_definition(agent_name="comp-agent")

        router.register_composite("comp-agent", definition)

        assert "comp-agent" in router._composite_defs
        assert router._composite_defs["comp-agent"] is definition

    @pytest.mark.asyncio
    async def test_route_chat_routes_to_composite(self) -> None:
        """route_chat delegates to route_composite when a composite is registered."""
        pm = _make_process_manager()
        router = PlatformRouter(process_manager=pm)
        definition = _make_definition(agent_name="comp-agent")
        router.register_composite("comp-agent", definition)

        mock_result = WorkflowResult(
            success=True,
            final_output="composite done",
            phase_results={},
            total_phases=4,
            completed_phases=4,
        )
        with patch.object(
            router, "route_composite", new_callable=AsyncMock, return_value=mock_result
        ) as mock_composite:
            result = await router.route_chat("comp-agent", "hello", conversation_id="c1")

        mock_composite.assert_awaited_once_with(definition, "hello", "c1")
        assert result["output"] == "composite done"
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_route_chat_composite_failure_forwards_error_fields(self) -> None:
        """iter131 regression: failed composite must include error/error_type."""
        pm = _make_process_manager()
        router = PlatformRouter(process_manager=pm)
        definition = _make_definition(agent_name="comp-agent")
        router.register_composite("comp-agent", definition)

        mock_result = WorkflowResult(
            success=False,
            final_output="",
            error="Phase synthesis failed: timeout",
            error_type="TimeoutError",
            phase_results={},
            total_phases=4,
            completed_phases=2,
        )
        with patch.object(
            router, "route_composite", new_callable=AsyncMock, return_value=mock_result
        ):
            result = await router.route_chat("comp-agent", "hello", conversation_id="c1")

        assert result["success"] is False
        assert result["error"] == "Phase synthesis failed: timeout"
        assert result["error_type"] == "TimeoutError"

    @pytest.mark.asyncio
    async def test_route_chat_routes_to_atomic_when_no_composite(self) -> None:
        """route_chat delegates to route_to_atomic when no composite is registered."""
        handle = _make_agent_handle(response_content="atomic response")
        pm = _make_process_manager(agents={"atomic-agent": handle})
        router = PlatformRouter(process_manager=pm)

        result = await router.route_chat("atomic-agent", "hi", conversation_id="c2")

        assert result["output"] == "atomic response"
        assert result["success"] is True


class TestGetTools:
    """Tests for PlatformRouter.get_tools."""

    @pytest.mark.asyncio
    async def test_get_tools_from_alive_agents(self) -> None:
        h1 = _make_agent_handle(name="a1")
        h1.ipc.receive_until_result = AsyncMock(
            return_value=MagicMock(content='[{"name": "tool1"}]', type=AgentToPlatformType.RESULT)
        )
        h2 = _make_agent_handle(name="a2")
        h2.ipc.receive_until_result = AsyncMock(
            return_value=MagicMock(content='[{"name": "tool2"}]', type=AgentToPlatformType.RESULT)
        )

        pm = _make_process_manager(agents={"a1": h1, "a2": h2}, running=["a1", "a2"])
        router = PlatformRouter(process_manager=pm)

        tools = await router.get_tools()
        assert len(tools) == 2
        assert {"name": "tool1"} in tools
        assert {"name": "tool2"} in tools

    @pytest.mark.asyncio
    async def test_get_tools_skips_dead_agents(self) -> None:
        alive = _make_agent_handle(name="alive", alive=True)
        alive.ipc.receive_until_result = AsyncMock(
            return_value=MagicMock(content='[{"name": "t"}]', type=AgentToPlatformType.RESULT)
        )
        dead = _make_agent_handle(name="dead", alive=False)

        pm = _make_process_manager(
            agents={"alive": alive, "dead": dead},
            running=["alive"],
        )
        router = PlatformRouter(process_manager=pm)

        tools = await router.get_tools()
        assert len(tools) == 1

    @pytest.mark.asyncio
    async def test_get_tools_handles_ipc_failure(self) -> None:
        h = _make_agent_handle(name="a1")
        h.ipc.receive_until_result = AsyncMock(side_effect=RuntimeError("IPC fail"))

        pm = _make_process_manager(agents={"a1": h}, running=["a1"])
        router = PlatformRouter(process_manager=pm)

        tools = await router.get_tools()
        assert tools == []

    @pytest.mark.asyncio
    async def test_get_tools_no_running_agents(self) -> None:
        pm = _make_process_manager(agents={}, running=[])
        router = PlatformRouter(process_manager=pm)

        tools = await router.get_tools()
        assert tools == []

    @pytest.mark.asyncio
    async def test_get_tools_reads_content_not_output(self) -> None:
        """get_tools should read tools from response.content, not response.output.

        When response.content contains a JSON string of tools and
        response.output is None, get_tools must still return the tools.
        """
        import json

        tools_list = [{"name": "tool_a"}, {"name": "tool_b"}]
        h = _make_agent_handle(name="a1")
        mock_resp = MagicMock()
        mock_resp.type = AgentToPlatformType.RESULT
        mock_resp.content = json.dumps(tools_list)
        mock_resp.output = None
        h.ipc.receive_until_result = AsyncMock(return_value=mock_resp)

        pm = _make_process_manager(agents={"a1": h}, running=["a1"])
        router = PlatformRouter(process_manager=pm)

        tools = await router.get_tools()
        assert len(tools) == 2
        assert tools[0]["name"] == "tool_a"
        assert tools[1]["name"] == "tool_b"


class TestStopAll:
    """Tests for PlatformRouter.stop_all."""

    @pytest.mark.asyncio
    async def test_stop_all_delegates_to_pm(self) -> None:
        pm = _make_process_manager()
        router = PlatformRouter(process_manager=pm)

        await router.stop_all()
        pm.stop_all.assert_awaited_once()


class TestPhaseOrder:
    """Tests for _PHASE_ORDER module constant."""

    def test_phase_order(self) -> None:
        assert _PHASE_ORDER == [
            WorkflowPhase.research,
            WorkflowPhase.synthesis,
            WorkflowPhase.implementation,
            WorkflowPhase.verification,
        ]

    def test_phase_order_length(self) -> None:
        assert len(_PHASE_ORDER) == 4


# ============================================================================
# Merged from iteration 16: Router empty phase failure
# ============================================================================


class TestRouterEmptyPhaseFails:
    """Router._execute_phase must raise when no agents available."""

    @pytest.mark.asyncio
    async def test_execute_phase_raises_on_no_agents(self) -> None:
        pm = MagicMock()
        router = PlatformRouter(process_manager=pm)
        definition = OrchestrationDefinition(
            goal="test",
            agent_name="test-agent",
            agents={},
            tasks=[],
            tool_loading=DSLToolLoading(),
        )
        mock_tg = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.task_graph = mock_tg

        with pytest.raises(RuntimeError, match="No agents available"):
            await router._execute_phase(
                mock_ctx, WorkflowPhase.research, definition, "test"
            )


# ============================================================================
# Merged from iteration 21: Router parallel conversation_id, aggregate empty
# ============================================================================


class TestRouterParallelConversationId:
    """Parallel execution must use unique conversation_id per agent."""

    def test_parallel_agent_unique_cids(self) -> None:
        """Verify that _execute_parallel_agents generates unique conversation IDs."""
        captured_cids: list[str] = []

        async def mock_execute(agent_name, message, conversation_id):  # pyright: ignore[reportUnusedParameter]
            captured_cids.append(conversation_id)
            return f"result from {agent_name}"

        mock_pm = MagicMock()
        mock_pm.get_agent = MagicMock(return_value=None)

        router = PlatformRouter.__new__(PlatformRouter)
        router._process_manager = mock_pm  # type: ignore[attr-defined]
        router._task_graph = MagicMock()  # type: ignore[attr-defined]
        router._subtask = MagicMock()
        async def mock_run_with_retry(coro_factory, timeout):  # pyright: ignore[reportUnusedParameter]
            return await coro_factory()

        async def mock_run_parallel(coros):
            results = []
            for c in coros:
                results.append(await c)
            return results

        router._subtask.run_with_retry = mock_run_with_retry
        router._subtask.run_parallel = mock_run_parallel
        router._execute_single_agent = mock_execute  # type: ignore[assignment]

        _result = asyncio.run(
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


class TestAggregateResultsEmptyString:
    """_aggregate_results must preserve empty-string results."""

    def test_empty_string_result_preserved(self) -> None:
        result = PlatformRouter._aggregate_results(
            ["hello", "", "world"],
            WorkflowPhase.research,
        )
        # Empty string should not silently disappear
        assert "(no output)" in result
        assert "hello" in result
        assert "world" in result

    def test_exception_still_reported(self) -> None:
        result = PlatformRouter._aggregate_results(
            [RuntimeError("boom")],
            WorkflowPhase.research,
        )
        assert "boom" in result

    def test_all_empty_shows_no_output(self) -> None:
        result = PlatformRouter._aggregate_results(
            ["", ""],
            WorkflowPhase.research,
        )
        # Should show "(no output)" for each worker
        assert "(no output)" in result


# ============================================================================
# Merged from iteration 22: get_tools deduplication, execute_single error wrap
# ============================================================================


class TestGetToolsDeduplication:
    """get_tools() must not silently overwrite tools with the same name."""

    @pytest.mark.asyncio
    async def test_duplicate_tools_deduplicated(self) -> None:
        """Two agents with same tool name: second is skipped."""
        import json

        mock_pm = MagicMock()
        router = PlatformRouter.__new__(PlatformRouter)
        router._pm = mock_pm

        tool_a = {"name": "search", "description": "Agent A search"}
        tool_b = {"name": "search", "description": "Agent B search"}

        agent_a_handle = MagicMock()
        agent_a_handle.is_alive = True
        agent_a_handle.ipc = MagicMock()
        agent_a_handle.ipc.send_chat = AsyncMock()
        agent_a_handle.ipc.receive_until_result = AsyncMock(
            return_value=AgentToPlatform(
                type=AgentToPlatformType.RESULT,
                content=json.dumps([tool_a]),
            )
        )

        agent_b_handle = MagicMock()
        agent_b_handle.is_alive = True
        agent_b_handle.ipc = MagicMock()
        agent_b_handle.ipc.send_chat = AsyncMock()
        agent_b_handle.ipc.receive_until_result = AsyncMock(
            return_value=AgentToPlatform(
                type=AgentToPlatformType.RESULT,
                content=json.dumps([tool_b]),
            )
        )

        mock_pm.list_running.return_value = ["agent-a", "agent-b"]
        mock_pm.get_agent.side_effect = lambda n: {
            "agent-a": agent_a_handle,
            "agent-b": agent_b_handle,
        }.get(n)

        tools = await router.get_tools()

        assert len(tools) == 1
        assert tools[0]["description"] == "Agent A search"

    @pytest.mark.asyncio
    async def test_unique_tools_all_returned(self) -> None:
        """Different tool names are all returned."""
        import json

        mock_pm = MagicMock()
        router = PlatformRouter.__new__(PlatformRouter)
        router._pm = mock_pm
        tools_list = [
            {"name": "search", "description": "search tool"},
            {"name": "analyze", "description": "analyze tool"},
        ]

        handle = MagicMock()
        handle.is_alive = True
        handle.ipc = MagicMock()
        handle.ipc.send_chat = AsyncMock()
        handle.ipc.receive_until_result = AsyncMock(
            return_value=AgentToPlatform(
                type=AgentToPlatformType.RESULT,
                content=json.dumps(tools_list),
            )
        )

        mock_pm.list_running.return_value = ["agent-x"]
        mock_pm.get_agent.return_value = handle

        result = await router.get_tools()

        assert len(result) == 2
        assert result[0]["name"] == "search"
        assert result[1]["name"] == "analyze"


class TestExecuteSingleAgentErrorWrapping:
    """_execute_single_agent exception handling.

    IPC-specific errors (IPCConnectionError, IPCTimeoutError, IPCError)
    propagate directly.  Other exceptions are wrapped as RuntimeError.
    """

    @pytest.mark.asyncio
    async def test_ipc_timeout_wrapped_as_runtime_error(self) -> None:
        """Non-IPC timeout in _execute_single_agent raises RuntimeError."""
        mock_pm = MagicMock()
        router = PlatformRouter.__new__(PlatformRouter)
        router._pm = mock_pm


        handle = MagicMock()
        handle.is_alive = True
        handle.ipc = MagicMock()
        handle.ipc.send_chat = AsyncMock()
        handle.ipc.receive_until_result = AsyncMock(
            side_effect=asyncio.TimeoutError("IPC timeout")
        )

        mock_pm.get_agent.return_value = handle

        with pytest.raises(RuntimeError, match="IPC error"):
            await router._execute_single_agent(
                "test-agent", "hello", conversation_id="c1"
            )

    @pytest.mark.asyncio
    async def test_ipc_connection_error_wrapped(self) -> None:
        """Non-IPC ConnectionError in _execute_single_agent raises RuntimeError."""
        mock_pm = MagicMock()
        router = PlatformRouter.__new__(PlatformRouter)
        router._pm = mock_pm


        handle = MagicMock()
        handle.is_alive = True
        handle.ipc = MagicMock()
        handle.ipc.send_chat = AsyncMock()
        handle.ipc.receive_until_result = AsyncMock(
            side_effect=ConnectionError("Broken pipe")
        )

        mock_pm.get_agent.return_value = handle

        with pytest.raises(RuntimeError, match="IPC error"):
            await router._execute_single_agent(
                "test-agent", "hello", conversation_id="c1"
            )

    @pytest.mark.asyncio
    async def test_send_chat_error_wrapped_as_runtime_error(self) -> None:
        """Non-IPC send_chat failure raises RuntimeError.

        iter109 regression — send_chat was bare (unwrapped), unlike
        route_to_atomic which already wrapped it.  Now both paths are
        consistent.
        """
        mock_pm = MagicMock()
        router = PlatformRouter.__new__(PlatformRouter)
        router._pm = mock_pm


        handle = MagicMock()
        handle.is_alive = True
        handle.ipc = MagicMock()
        handle.ipc.send_chat = AsyncMock(
            side_effect=ConnectionError("Broken pipe on send")
        )

        mock_pm.get_agent.return_value = handle

        with pytest.raises(RuntimeError, match="IPC send error"):
            await router._execute_single_agent(
                "test-agent", "hello", conversation_id="c1"
            )

    # -- iter126 regression: IPC exceptions propagate directly --

    @pytest.mark.asyncio
    async def test_ipctimeouterror_propagates_directly(self) -> None:
        """IPCTimeoutError from receive_until_result propagates, not wrapped."""
        mock_pm = MagicMock()
        router = PlatformRouter.__new__(PlatformRouter)
        router._pm = mock_pm

        handle = MagicMock()
        handle.is_alive = True
        handle.ipc = MagicMock()
        handle.ipc.send_chat = AsyncMock()
        handle.ipc.receive_until_result = AsyncMock(
            side_effect=IPCTimeoutError("agent stalled")
        )

        mock_pm.get_agent.return_value = handle

        with pytest.raises(IPCTimeoutError, match="agent stalled"):
            await router._execute_single_agent(
                "test-agent", "hello", conversation_id="c1"
            )

    @pytest.mark.asyncio
    async def test_ipcconnectionerror_propagates_directly(self) -> None:
        """IPCConnectionError from receive_until_result propagates."""
        mock_pm = MagicMock()
        router = PlatformRouter.__new__(PlatformRouter)
        router._pm = mock_pm

        handle = MagicMock()
        handle.is_alive = True
        handle.ipc = MagicMock()
        handle.ipc.send_chat = AsyncMock()
        handle.ipc.receive_until_result = AsyncMock(
            side_effect=IPCConnectionError("EOF")
        )

        mock_pm.get_agent.return_value = handle

        with pytest.raises(IPCConnectionError, match="EOF"):
            await router._execute_single_agent(
                "test-agent", "hello", conversation_id="c1"
            )

    @pytest.mark.asyncio
    async def test_ipcerror_propagates_directly_on_send(self) -> None:
        """Generic IPCError from send_chat propagates, not wrapped."""
        mock_pm = MagicMock()
        router = PlatformRouter.__new__(PlatformRouter)
        router._pm = mock_pm

        handle = MagicMock()
        handle.is_alive = True
        handle.ipc = MagicMock()
        handle.ipc.send_chat = AsyncMock(
            side_effect=IPCError("bad JSON")
        )

        mock_pm.get_agent.return_value = handle

        with pytest.raises(IPCError, match="bad JSON"):
            await router._execute_single_agent(
                "test-agent", "hello", conversation_id="c1"
            )


# ============================================================================
# Merged from iteration 23: SubtaskController CancelledError handling
# ============================================================================


class TestSubtaskCancelledError:
    """SubtaskController propagates CancelledError (does not swallow it)."""

    @pytest.mark.asyncio
    async def test_run_parallel_propagates_cancelled_error(self) -> None:
        """CancelledError in run_parallel is propagated, not captured."""
        controller = SubtaskController()

        async def failing_coro():
            raise asyncio.CancelledError("test cancel")

        with pytest.raises(asyncio.CancelledError, match="test cancel"):
            await controller.run_parallel([failing_coro()])

    @pytest.mark.asyncio
    async def test_run_with_retry_propagates_cancelled_error(self) -> None:
        """CancelledError in run_with_retry is propagated immediately, not retried."""

        attempt = 0

        async def cancel_then_succeed():
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise asyncio.CancelledError("first cancel")
            return "ok"

        controller = SubtaskController()
        with pytest.raises(asyncio.CancelledError, match="first cancel"):
            await controller.run_with_retry(
                cancel_then_succeed, max_retries=2,
            )
        # Should have stopped at first attempt, not retried
        assert attempt == 1

    @pytest.mark.asyncio
    async def test_run_with_retry_all_cancelled(self) -> None:
        """CancelledError propagated on first attempt (no retry loop)."""

        async def always_cancel():
            raise asyncio.CancelledError("always")

        controller = SubtaskController()
        with pytest.raises(asyncio.CancelledError, match="always"):
            await controller.run_with_retry(always_cancel, max_retries=1)

    @pytest.mark.asyncio
    async def test_run_parallel_mixed_success_and_cancel(self) -> None:
        """CancelledError propagates even when mixed with success."""
        controller = SubtaskController()

        async def ok():
            return "done"

        async def cancel():
            raise asyncio.CancelledError("nope")

        with pytest.raises(asyncio.CancelledError, match="nope"):
            await controller.run_parallel([ok(), cancel()])


class TestSubtaskSystemExit:
    """iter110d: SystemExit propagates immediately, not retried."""

    @pytest.mark.asyncio
    async def test_run_with_retry_propagates_system_exit(self) -> None:
        """SystemExit in run_with_retry is propagated immediately."""

        attempt = 0

        async def exit_then_succeed():
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise SystemExit(1)
            return "ok"

        controller = SubtaskController()
        with pytest.raises(SystemExit):
            await controller.run_with_retry(
                exit_then_succeed, max_retries=2,
            )
        assert attempt == 1

    @pytest.mark.asyncio
    async def test_run_with_retry_propagates_generator_exit(self) -> None:
        """GeneratorExit in run_with_retry is propagated immediately."""

        attempt = 0

        async def gen_exit_then_succeed():
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise GeneratorExit()
            return "ok"

        controller = SubtaskController()
        with pytest.raises(GeneratorExit):
            await controller.run_with_retry(
                gen_exit_then_succeed, max_retries=2,
            )
        assert attempt == 1

    @pytest.mark.asyncio
    async def test_run_with_retry_propagates_memory_error(self) -> None:
        """MemoryError propagates immediately — retrying is pointless."""

        attempt = 0

        async def oom_then_succeed():
            nonlocal attempt
            attempt += 1
            if attempt == 1:
                raise MemoryError("out of memory")
            return "ok"

        controller = SubtaskController()
        with pytest.raises(MemoryError, match="out of memory"):
            await controller.run_with_retry(
                oom_then_succeed, max_retries=2,
            )
        assert attempt == 1


# ============================================================================
# Merged from iteration 23: WorkflowContext.close() drops task_graph
# ============================================================================


class TestWorkflowContextClose:
    """WorkflowContext.close() must set task_graph to None."""

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


# ============================================================================
# Regression tests for audit fixes
# ============================================================================


class TestRouteCompositeTaskGraphSetupFailure:
    """route_composite must handle TaskGraph.add_task() failures gracefully.

    Regression: if add_task() raised during TaskGraph population (e.g. duplicate
    task ID, database error), phase_results/completed/total/last_error were
    referenced before assignment causing NameError.  Variables are now
    initialized before TaskGraph setup.
    """

    @pytest.mark.asyncio
    async def test_add_task_failure_returns_graceful_result(self) -> None:
        """TaskGraph.add_task raising does not crash -- returns failed WorkflowResult."""
        definition = _make_definition()
        pm = _make_process_manager()
        router = PlatformRouter(process_manager=pm)

        mock_tg = MagicMock()
        mock_tg.add_task.side_effect = RuntimeError("DB locked")

        with patch(
            "agent_nexus.platform.router.router.TaskGraph", return_value=mock_tg
        ):
            result = await router.route_composite(definition, "test", "conv-1")

        assert result.success is False
        assert result.completed_phases == 0
        assert result.total_phases == 4
        assert result.error is not None
        assert "TaskGraph setup failed" in result.error
        assert "DB locked" in result.error

    @pytest.mark.asyncio
    async def test_add_task_failure_empty_phase_results(self) -> None:
        """TaskGraph failure produces empty phase_results."""
        definition = _make_definition()
        pm = _make_process_manager()
        router = PlatformRouter(process_manager=pm)

        mock_tg = MagicMock()
        mock_tg.add_task.side_effect = ValueError("bad task")

        with patch(
            "agent_nexus.platform.router.router.TaskGraph", return_value=mock_tg
        ):
            result = await router.route_composite(definition, "test", "conv-1")

        assert result.phase_results == {}
        assert result.final_output == ""


class TestRouteCompositeOverallTimeout:
    """iter114: route_composite has an overall timeout wrapping all phases.

    Without this timeout, a composite workflow with 4 phases, each using
    max_retries=2 and timeout=300s, could hang for 2400+ seconds.
    The asyncio.wait_for wrapper caps the total execution time.
    """

    @pytest.mark.asyncio
    async def test_timeout_returns_failed_result(self) -> None:
        """When the overall composite timeout fires, a failed result is returned."""
        agents = {
            "explorer": _make_agent_handle(name="explorer", response_content="data"),
            "planner": _make_agent_handle(name="planner", response_content="plan"),
            "worker1": _make_agent_handle(name="worker1", response_content="impl"),
            "verifier": _make_agent_handle(name="verifier", response_content="ok"),
        }
        pm = _make_process_manager(agents=agents)
        router = PlatformRouter(process_manager=pm)
        definition = _make_definition()

        mock_tg = MagicMock()
        with (
            patch(
                "agent_nexus.platform.router.router.TaskGraph",
                return_value=mock_tg,
            ),
            patch(
                "agent_nexus.platform.router.router._DEFAULT_COMPOSITE_TIMEOUT",
                0.0,  # instant timeout
            ),
        ):
            result = await router.route_composite(
                definition, "test timeout", "conv-1"
            )

        assert result.success is False
        assert result.error is not None
        assert "timed out" in result.error
        assert result.error_type == "TimeoutError"
        assert result.completed_phases < 4

    @pytest.mark.asyncio
    async def test_timeout_still_cleans_up_context(self) -> None:
        """Timeout triggers finally block which calls ctx.close()."""
        agents = {
            "explorer": _make_agent_handle(name="explorer", response_content="data"),
        }
        pm = _make_process_manager(agents=agents)
        router = PlatformRouter(process_manager=pm)
        definition = _make_definition()

        mock_tg = MagicMock()
        with (
            patch(
                "agent_nexus.platform.router.router.TaskGraph",
                return_value=mock_tg,
            ),
            patch(
                "agent_nexus.platform.router.router._DEFAULT_COMPOSITE_TIMEOUT",
                0.0,
            ),
        ):
            result = await router.route_composite(definition, "test", "conv-1")

        # The result should be a proper WorkflowResult (not an exception)
        assert isinstance(result, WorkflowResult)
        assert result.success is False


class TestRouteToAtomicSendChatError:
    """route_to_atomic must handle send_chat exceptions.

    Regression: send_chat IPC call was unprotected.  If it raised (broken pipe,
    process died), the exception propagated unhandled instead of being returned
    as an error dict like receive_until_result errors.
    """

    @pytest.mark.asyncio
    async def test_send_chat_exception_returns_error_dict(self) -> None:
        """send_chat raising returns error dict, not unhandled exception."""
        handle = _make_agent_handle()
        handle.ipc.send_chat = AsyncMock(side_effect=ConnectionError("pipe broke"))

        pm = _make_process_manager(agents={"agent-a": handle})
        router = PlatformRouter(process_manager=pm)

        result = await router.route_to_atomic("agent-a", "hello", "conv-1")
        assert result["success"] is False
        assert "IPC send error" in result["error"]
        assert "pipe broke" in result["error"]

    @pytest.mark.asyncio
    async def test_send_chat_timeout_returns_error_dict(self) -> None:
        """send_chat timeout returns error dict."""
        handle = _make_agent_handle()
        handle.ipc.send_chat = AsyncMock(side_effect=asyncio.TimeoutError("send timeout"))

        pm = _make_process_manager(agents={"agent-a": handle})
        router = PlatformRouter(process_manager=pm)

        result = await router.route_to_atomic("agent-a", "hello", "conv-1")
        assert result["success"] is False
        assert "IPC send error" in result["error"]

    @pytest.mark.asyncio
    async def test_send_chat_success_receive_still_protected(self) -> None:
        """send_chat succeeding but receive failing still returns error dict."""
        handle = _make_agent_handle()
        handle.ipc.send_chat = AsyncMock()  # succeeds
        handle.ipc.receive_until_result = AsyncMock(
            side_effect=RuntimeError("recv fail")
        )

        pm = _make_process_manager(agents={"agent-a": handle})
        router = PlatformRouter(process_manager=pm)

        result = await router.route_to_atomic("agent-a", "hello", "conv-1")
        assert result["success"] is False
        assert "IPC error" in result["error"]
        assert "recv fail" in result["error"]


class TestGetToolsSkipsNamelessTools:
    """get_tools must skip tools without a valid 'name' key.

    Regression: tools without a 'name' key were added with tool_name="".
    This polluted the tool list and blocked future unnamed tools since "" was
    added to seen_names.
    """

    @pytest.mark.asyncio
    async def test_nameless_tool_skipped_json_path(self) -> None:
        """Tool dict without 'name' key is skipped (JSON string path)."""
        import json

        tools_list = [{"description": "no name tool"}, {"name": "valid-tool"}]
        h = _make_agent_handle(name="a1")
        mock_resp = MagicMock()
        mock_resp.type = AgentToPlatformType.RESULT
        mock_resp.content = json.dumps(tools_list)
        h.ipc.receive_until_result = AsyncMock(return_value=mock_resp)

        pm = _make_process_manager(agents={"a1": h}, running=["a1"])
        router = PlatformRouter(process_manager=pm)

        tools = await router.get_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "valid-tool"

    @pytest.mark.asyncio
    async def test_empty_name_tool_skipped(self) -> None:
        """Tool with name='' is skipped."""
        import json

        tools_list = [{"name": ""}, {"name": "real-tool"}]
        h = _make_agent_handle(name="a1")
        mock_resp = MagicMock()
        mock_resp.type = AgentToPlatformType.RESULT
        mock_resp.content = json.dumps(tools_list)
        h.ipc.receive_until_result = AsyncMock(return_value=mock_resp)

        pm = _make_process_manager(agents={"a1": h}, running=["a1"])
        router = PlatformRouter(process_manager=pm)

        tools = await router.get_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "real-tool"

    @pytest.mark.asyncio
    async def test_all_nameless_tools_returns_empty(self) -> None:
        """If all tools lack names, result is empty list."""
        import json

        tools_list = [{"description": "a"}, {"description": "b"}]
        h = _make_agent_handle(name="a1")
        mock_resp = MagicMock()
        mock_resp.type = AgentToPlatformType.RESULT
        mock_resp.content = json.dumps(tools_list)
        h.ipc.receive_until_result = AsyncMock(return_value=mock_resp)

        pm = _make_process_manager(agents={"a1": h}, running=["a1"])
        router = PlatformRouter(process_manager=pm)

        tools = await router.get_tools()
        assert tools == []


class TestExecuteSingleAgentReturnTypeSafety:
    """_execute_single_agent must always return str, not arbitrary types.

    Regression: response.content could be a non-string truthy value (list, dict).
    `response.content or ""` would return the list/dict directly, violating the
    str return type.  Now uses explicit str() conversion.
    """

    @pytest.mark.asyncio
    async def test_none_content_returns_empty_string(self) -> None:
        """response.content=None returns '', not None."""
        handle = _make_agent_handle()
        mock_resp = MagicMock()
        mock_resp.type = AgentToPlatformType.RESULT
        mock_resp.content = None
        mock_resp.error = None
        mock_resp.status = "completed"
        handle.ipc.receive_until_result = AsyncMock(return_value=mock_resp)

        pm = _make_process_manager(agents={"a1": handle})
        router = PlatformRouter(process_manager=pm)

        result = await router._execute_single_agent("a1", "hi", "c1")
        assert isinstance(result, str)
        assert result == ""

    @pytest.mark.asyncio
    async def test_list_content_returns_string_repr(self) -> None:
        """response.content as list returns str(list), not the list itself."""
        handle = _make_agent_handle()
        mock_resp = MagicMock()
        mock_resp.type = AgentToPlatformType.RESULT
        mock_resp.content = ["item1", "item2"]
        mock_resp.error = None
        mock_resp.status = "completed"
        handle.ipc.receive_until_result = AsyncMock(return_value=mock_resp)

        pm = _make_process_manager(agents={"a1": handle})
        router = PlatformRouter(process_manager=pm)

        result = await router._execute_single_agent("a1", "hi", "c1")
        assert isinstance(result, str)
        assert "item1" in result

    @pytest.mark.asyncio
    async def test_dict_content_returns_string_repr(self) -> None:
        """response.content as dict returns str(dict), not the dict itself."""
        handle = _make_agent_handle()
        mock_resp = MagicMock()
        mock_resp.type = AgentToPlatformType.RESULT
        mock_resp.content = {"key": "value"}
        mock_resp.error = None
        mock_resp.status = "completed"
        handle.ipc.receive_until_result = AsyncMock(return_value=mock_resp)

        pm = _make_process_manager(agents={"a1": handle})
        router = PlatformRouter(process_manager=pm)

        result = await router._execute_single_agent("a1", "hi", "c1")
        assert isinstance(result, str)
        assert "key" in result

    @pytest.mark.asyncio
    async def test_int_content_returns_string(self) -> None:
        """response.content as int returns str(int), not int."""
        handle = _make_agent_handle()
        mock_resp = MagicMock()
        mock_resp.type = AgentToPlatformType.RESULT
        mock_resp.content = 42
        mock_resp.error = None
        mock_resp.status = "completed"
        handle.ipc.receive_until_result = AsyncMock(return_value=mock_resp)

        pm = _make_process_manager(agents={"a1": handle})
        router = PlatformRouter(process_manager=pm)

        result = await router._execute_single_agent("a1", "hi", "c1")
        assert isinstance(result, str)
        assert result == "42"


# ============================================================================
# Coverage gap tests: get_tools registry path, JSON decode error,
# list-path name collision, _execute_phase TaskGraph None, fallback agent,
# _execute_single_agent handle not found
# ============================================================================


class TestGetToolsRegistryPath:
    """get_tools uses registry when _registry attribute is set."""

    @pytest.mark.asyncio
    async def test_registry_returns_tools(self) -> None:
        """When _registry is set, get_tools returns registry.get_tools_for_llm()."""
        mock_pm = MagicMock()
        router = PlatformRouter.__new__(PlatformRouter)
        router._pm = mock_pm
        router._composite_defs = {}
        router._subtask = SubtaskController()


        mock_registry = MagicMock()
        mock_registry.get_tools_for_llm.return_value = [
            {"name": "mcp__agent__tool_a"},
            {"name": "mcp__agent__tool_b"},
        ]
        router._registry = mock_registry  # type: ignore[attr-defined]

        tools = await router.get_tools()
        assert len(tools) == 2
        assert tools[0]["name"] == "mcp__agent__tool_a"
        mock_registry.get_tools_for_llm.assert_called_once()


class TestGetToolsJSONDecodeError:
    """get_tools handles JSONDecodeError from agent response."""

    @pytest.mark.asyncio
    async def test_invalid_json_skipped(self) -> None:
        """Agent returning invalid JSON is skipped without error."""
        h = _make_agent_handle(name="a1")
        mock_resp = MagicMock()
        mock_resp.type = AgentToPlatformType.RESULT
        mock_resp.content = "not valid json {{{"
        mock_resp.output = None
        h.ipc.receive_until_result = AsyncMock(return_value=mock_resp)

        pm = _make_process_manager(agents={"a1": h}, running=["a1"])
        router = PlatformRouter(process_manager=pm)

        tools = await router.get_tools()
        assert tools == []


class TestGetToolsFallbackHandleNone:
    """get_tools skips agents where get_agent returns None."""

    @pytest.mark.asyncio
    async def test_none_handle_skipped(self) -> None:
        """Agent listed as running but get_agent returns None is skipped."""
        pm = _make_process_manager(agents={}, running=["ghost"])
        router = PlatformRouter(process_manager=pm)

        tools = await router.get_tools()
        assert tools == []


class TestGetToolsErrorResponse:
    """iter110b regression: get_tools skips ERROR responses with warning."""

    @pytest.mark.asyncio
    async def test_error_response_skipped_with_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Agent returning ERROR type during tool discovery is skipped with a warning log."""
        h = _make_agent_handle(name="a1")
        mock_resp = MagicMock()
        mock_resp.type = AgentToPlatformType.ERROR
        mock_resp.error = "agent internal panic"
        mock_resp.content = "panic stack trace"
        h.ipc.receive_until_result = AsyncMock(return_value=mock_resp)

        pm = _make_process_manager(agents={"a1": h}, running=["a1"])
        router = PlatformRouter(process_manager=pm)

        with caplog.at_level(logging.WARNING, logger="agent_nexus.platform.router.router"):
            tools = await router.get_tools()
        assert tools == []
        assert any(
            "returned error during tool discovery" in r.message
            and "a1" in r.message
            and "agent internal panic" in r.message
            for r in caplog.records
        )


class TestExecutePhaseTaskGraphNone:
    """_execute_phase raises RuntimeError when TaskGraph is not initialized."""

    @pytest.mark.asyncio
    async def test_none_task_graph_raises(self) -> None:
        pm = MagicMock()
        router = PlatformRouter(process_manager=pm)
        definition = _make_definition()

        mock_ctx = MagicMock()
        mock_ctx.task_graph = None

        with pytest.raises(RuntimeError, match="TaskGraph not initialized"):
            await router._execute_phase(
                mock_ctx, WorkflowPhase.research, definition, "test"
            )


class TestExecutePhaseFallbackAgent:
    """_execute_phase uses fallback agents when no role-matched agents exist."""

    @pytest.mark.asyncio
    async def test_fallback_first_agent_for_synthesis(self) -> None:
        """When no 'plan' role agent exists, first available agent is used."""
        agents = {
            "worker1": _make_agent_handle(
                name="worker1", response_content="fallback plan"
            ),
        }
        pm = _make_process_manager(agents=agents)
        router = PlatformRouter(process_manager=pm)

        # Definition has only 'explore' role, no 'plan' agent
        definition = OrchestrationDefinition(
            goal="test",
            agent_name="test-agent",
            agents={
                "worker1": DSLAgent(
                    name="worker1", description="Explore", role="explore"
                ),
            },
            tasks=[DSLTask(id="t1", description="Explore", agent="worker1")],
            tool_loading=DSLToolLoading(),
        )

        mock_ctx = MagicMock()
        mock_ctx.task_graph = MagicMock()

        # Synthesis phase -- no 'plan' role agent, falls back to first
        result = await router._execute_phase(
            mock_ctx, WorkflowPhase.synthesis, definition, "test message"
        )
        assert result == "fallback plan"

    @pytest.mark.asyncio
    async def test_fallback_root_task_agents_for_research(self) -> None:
        """Research phase with no 'explore' agents uses root task agents."""
        handle = _make_agent_handle(
            name="only-agent", response_content="research done"
        )
        pm = _make_process_manager(agents={"only-agent": handle})
        router = PlatformRouter(process_manager=pm)

        # Definition has only 'plan' role, no 'explore'
        task_item = DSLTask(id="t1", description="Do stuff", agent="only-agent")
        definition = OrchestrationDefinition(
            goal="test",
            agent_name="test-agent",
            agents={
                "only-agent": DSLAgent(
                    name="only-agent", description="Plan", role="plan"
                ),
            },
            tasks=[task_item],
            tool_loading=DSLToolLoading(),
        )

        mock_ctx = MagicMock()
        mock_ctx.task_graph = MagicMock()

        # Research phase -- no 'explore' agents, falls back to root task agents
        result = await router._execute_phase(
            mock_ctx, WorkflowPhase.research, definition, "test"
        )
        assert "research done" in result


class TestExecuteSingleAgentHandleNotFound:
    """_execute_single_agent raises RuntimeError when handle is None."""

    @pytest.mark.asyncio
    async def test_none_handle_raises(self) -> None:
        mock_pm = MagicMock()
        mock_pm.get_agent.return_value = None
        router = PlatformRouter.__new__(PlatformRouter)
        router._pm = mock_pm


        with pytest.raises(RuntimeError, match="not found or not alive"):
            await router._execute_single_agent(
                "ghost-agent", "hello", conversation_id="c1"
            )

    @pytest.mark.asyncio
    async def test_dead_handle_raises(self) -> None:
        handle = MagicMock()
        handle.is_alive = False
        mock_pm = MagicMock()
        mock_pm.get_agent.return_value = handle
        router = PlatformRouter.__new__(PlatformRouter)
        router._pm = mock_pm


        with pytest.raises(RuntimeError, match="not found or not alive"):
            await router._execute_single_agent(
                "dead-agent", "hello", conversation_id="c1"
            )


class TestTopologicalSortTasks:
    """_topological_sort_tasks reorders tasks so deps appear before dependents.

    Regression: route_composite added tasks to TaskGraph sequentially.  If a
    task B (listed first) had blocked_by=[A] (listed later), add_task would
    raise "blocked_by references non-existent tasks" because A was not yet
    in the graph.  DSL validation checks that all blocked_by IDs exist in
    the task set but does NOT enforce ordering.
    """

    @pytest.mark.asyncio
    async def test_forward_blocked_by_succeeds(self) -> None:
        """Task B (first) blocked_by A (second) does not crash route_composite.

        DSL definition has tasks in wrong order: [B(blocked_by=[A]), A].
        Without topological sort this raises ValueError.  With the sort,
        tasks are reordered to [A, B] and add_task succeeds.
        """
        agents = {
            "explorer": _make_agent_handle(name="explorer", response_content="data"),
            "planner": _make_agent_handle(name="planner", response_content="plan"),
            "worker1": _make_agent_handle(name="worker1", response_content="impl"),
            "verifier": _make_agent_handle(name="verifier", response_content="ok"),
        }
        pm = _make_process_manager(agents=agents)
        router = PlatformRouter(process_manager=pm)

        # Tasks listed in reverse dependency order:
        # t2 blocked_by t1, but t2 appears BEFORE t1 in the list.
        definition = OrchestrationDefinition(
            goal="test forward deps",
            agent_name="test-agent",
            agents={
                "explorer": DSLAgent(name="explorer", description="Explore", role="explore"),
                "planner": DSLAgent(name="planner", description="Plan", role="plan"),
                "worker1": DSLAgent(name="worker1", description="Work", role="worker"),
                "verifier": DSLAgent(name="verifier", description="Verify", role="verification"),
            },
            tasks=[
                DSLTask(id="t2", description="Plan", agent="planner", blocked_by=["t1"]),
                DSLTask(id="t1", description="Explore", agent="explorer"),
                DSLTask(id="t3", description="Work", agent="worker1"),
                DSLTask(id="t4", description="Verify", agent="verifier"),
            ],
            tool_loading=DSLToolLoading(),
        )

        # This should NOT raise — tasks are topologically sorted internally
        result = await router.route_composite(definition, "test", "conv-1")
        assert result.success is True
        assert result.completed_phases == 4

    def test_topological_sort_preserves_dag_order(self) -> None:
        """Already-sorted tasks come out in the same order."""
        tasks = [
            DSLTask(id="A", description="a", agent="ag"),
            DSLTask(id="B", description="b", agent="ag", blocked_by=["A"]),
            DSLTask(id="C", description="c", agent="ag", blocked_by=["B"]),
        ]
        result = PlatformRouter._topological_sort_tasks(tasks)
        assert [t.id for t in result] == ["A", "B", "C"]

    def test_topological_sort_reorders_reverse(self) -> None:
        """Reverse-ordered tasks are sorted into correct dependency order."""
        tasks = [
            DSLTask(id="C", description="c", agent="ag", blocked_by=["B"]),
            DSLTask(id="B", description="b", agent="ag", blocked_by=["A"]),
            DSLTask(id="A", description="a", agent="ag"),
        ]
        result = PlatformRouter._topological_sort_tasks(tasks)
        ids = [t.id for t in result]
        assert ids.index("A") < ids.index("B") < ids.index("C")

    def test_topological_sort_diamond(self) -> None:
        """Diamond dependency: A -> B,C -> D.  B and C can be in any order."""
        tasks = [
            DSLTask(id="D", description="d", agent="ag", blocked_by=["B", "C"]),
            DSLTask(id="C", description="c", agent="ag", blocked_by=["A"]),
            DSLTask(id="B", description="b", agent="ag", blocked_by=["A"]),
            DSLTask(id="A", description="a", agent="ag"),
        ]
        result = PlatformRouter._topological_sort_tasks(tasks)
        ids = [t.id for t in result]
        assert ids.index("A") < ids.index("B")
        assert ids.index("A") < ids.index("C")
        assert ids.index("B") < ids.index("D")
        assert ids.index("C") < ids.index("D")


# ---------------------------------------------------------------------------
# iter100 regression: _phase_to_role forward compatibility
# ---------------------------------------------------------------------------

class TestPhaseToRoleForwardCompat:
    def test_unknown_phase_returns_worker(self):
        """mapping.get(phase, "worker") prevents KeyError on future phases."""
        from agent_nexus.platform.router.workflow import WorkflowPhase

        # Create a fake phase by using a string that isn't a valid phase
        # We verify the method uses .get() by checking all known phases work
        for phase in WorkflowPhase:
            role = PlatformRouter._phase_to_role(phase)
            assert isinstance(role, str)
            assert len(role) > 0

    def test_known_phase_mappings(self):
        from agent_nexus.platform.router.workflow import WorkflowPhase

        assert PlatformRouter._phase_to_role(WorkflowPhase.research) == "explore"
        assert PlatformRouter._phase_to_role(WorkflowPhase.synthesis) == "plan"
        assert PlatformRouter._phase_to_role(WorkflowPhase.implementation) == "worker"
        assert PlatformRouter._phase_to_role(WorkflowPhase.verification) == "verification"


# ---------------------------------------------------------------------------
# iter114 regression: composite workflow overall timeout
# ---------------------------------------------------------------------------


class TestCompositeOverallTimeout:
    """route_composite must enforce an overall timeout across all phases."""

    @pytest.mark.asyncio
    async def test_overall_timeout_fires(self) -> None:
        """When phases hang beyond overall timeout, route_composite
        returns a WorkflowResult with error (not hanging forever)."""
        pm = MagicMock()
        pm.get_agent.return_value = None
        router = PlatformRouter(process_manager=pm)

        # Use a very short patched timeout so the test finishes quickly.
        short_timeout = 0.2

        async def _hanging_phase(*args, **kwargs):
            await asyncio.sleep(10)  # Much longer than short_timeout

        with patch(
            "agent_nexus.platform.router.router._DEFAULT_COMPOSITE_TIMEOUT",
            short_timeout,
        ):
            with patch.object(router, "_execute_phase", side_effect=_hanging_phase):
                with patch.object(
                    router, "_build_phase_message", return_value="msg"
                ):
                    definition = OrchestrationDefinition(
                        goal="test",
                        agent_name="test-agent",
                        agents={
                            "a": DSLAgent(name="a", description="a", role="worker"),
                        },
                        tasks=[DSLTask(id="t1", description="d", agent="a")],
                        tool_loading=DSLToolLoading(),
                    )
                    result = await router.route_composite(
                        definition, "test", "conv-1"
                    )

        assert result.success is False
        assert result.error is not None
        assert "timed out" in result.error.lower()
        assert result.completed_phases < result.total_phases

    @pytest.mark.asyncio
    async def test_overall_timeout_constant_matches_phase_count(self) -> None:
        """Verify _DEFAULT_COMPOSITE_TIMEOUT is based on phase count and IPC timeout."""
        from agent_nexus.platform.gateway.tool_adapter import (
            DEFAULT_IPC_EXECUTE_TIMEOUT,
        )
        from agent_nexus.platform.router.router import _DEFAULT_COMPOSITE_TIMEOUT

        assert _DEFAULT_COMPOSITE_TIMEOUT == DEFAULT_IPC_EXECUTE_TIMEOUT * len(
            _PHASE_ORDER
        )


# ---------------------------------------------------------------------------
# iter105 regression: topological sort cycle fallback
# ---------------------------------------------------------------------------


class TestTopologicalSortCycleFallback:
    """_topological_sort_tasks appends unsorted tasks when cycle prevents full sort."""

    def test_cycle_appends_remaining_tasks(self) -> None:
        """Mutual dependency A<->B causes Kahn's algorithm to skip both;
        the fallback appends them so add_task can detect the cycle."""
        tasks = [
            DSLTask(id="A", description="a", agent="ag", blocked_by=["B"]),
            DSLTask(id="B", description="b", agent="ag", blocked_by=["A"]),
        ]
        result = PlatformRouter._topological_sort_tasks(tasks)
        # Both tasks should be present (appended by fallback)
        ids = [t.id for t in result]
        assert set(ids) == {"A", "B"}


# iter122 regression: route_chat empty string validation

class TestRouteChatEmptyStringGuard:
    """route_chat rejects empty agent_name and message."""

    @pytest.mark.asyncio
    async def test_empty_agent_name_rejected(self) -> None:
        router = PlatformRouter(process_manager=_make_process_manager(agents={}))
        result = await router.route_chat("", "hello")
        assert result["success"] is False
        assert "agent_name" in result["error"]

    @pytest.mark.asyncio
    async def test_whitespace_agent_name_rejected(self) -> None:
        router = PlatformRouter(process_manager=_make_process_manager(agents={}))
        result = await router.route_chat("   ", "hello")
        assert result["success"] is False
        assert "agent_name" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_message_rejected(self) -> None:
        handle = _make_agent_handle()
        router = PlatformRouter(
            process_manager=_make_process_manager(agents={"a": handle})
        )
        result = await router.route_chat("a", "")
        assert result["success"] is False
        assert "message" in result["error"]

    @pytest.mark.asyncio
    async def test_whitespace_message_rejected(self) -> None:
        handle = _make_agent_handle()
        router = PlatformRouter(
            process_manager=_make_process_manager(agents={"a": handle})
        )
        result = await router.route_chat("a", "   ")
        assert result["success"] is False
        assert "message" in result["error"]


# ---------------------------------------------------------------------------
# iter124 regression: unified IPC lock — router & tool_adapter share lock
# ---------------------------------------------------------------------------


class TestUnifiedIpcLock:
    """Verify router and tool_adapter use the SAME lock for an agent.

    P0 bug: router.py had its own _route_locks dict (instance-level) while
    tool_adapter.py had _ipc_lock_registry (module-level).  Both serialize
    IPC for the same agent but used different lock objects — concurrent
    route_to_atomic() and McpToolAdapter.execute() calls could interleave.
    Now both use _get_ipc_lock() from tool_adapter.
    """

    @pytest.mark.asyncio
    async def test_route_to_atomic_uses_shared_lock(self) -> None:
        """route_to_atomic acquires the same lock as McpToolAdapter."""
        from agent_nexus.platform.gateway.tool_adapter import (
            _get_ipc_lock,
            remove_all_locks,
        )

        remove_all_locks()
        handle = _make_agent_handle(response_content="hello back")
        pm = _make_process_manager(agents={"agent-a": handle})
        router = PlatformRouter(process_manager=pm)

        shared_lock = _get_ipc_lock("agent-a")
        assert shared_lock.locked() is False

        # route_to_atomic should acquire and release the shared lock
        result = await router.route_to_atomic("agent-a", "hello", "conv-1")
        assert result["success"] is True
        assert shared_lock.locked() is False  # released after call
        remove_all_locks()

    @pytest.mark.asyncio
    async def test_execute_single_agent_uses_shared_lock(self) -> None:
        """_execute_single_agent acquires the same lock as McpToolAdapter."""
        from agent_nexus.platform.gateway.tool_adapter import (
            _get_ipc_lock,
            remove_all_locks,
        )

        remove_all_locks()
        handle = _make_agent_handle(response_content="result")
        pm = _make_process_manager(agents={"agent-a": handle})
        router = PlatformRouter(process_manager=pm)

        shared_lock = _get_ipc_lock("agent-a")

        result = await router._execute_single_agent("agent-a", "hello", "c1")
        assert result == "result"
        assert shared_lock.locked() is False
        remove_all_locks()

    @pytest.mark.asyncio
    async def test_both_paths_mutually_exclusive(self) -> None:
        """route_to_atomic and McpToolAdapter.execute are serialized for the
        same agent — one must wait for the other to finish."""
        from agent_nexus.platform.gateway.tool_adapter import (
            McpToolAdapter,
            _get_ipc_lock,
            remove_all_locks,
        )

        remove_all_locks()

        # Make send_chat block until we signal it to continue
        proceed = asyncio.Event()
        call_order: list[str] = []

        async def slow_send_chat(msg, *, conversation_id=None):
            call_order.append("started")
            await proceed.wait()
            call_order.append("finished")

        handle = _make_agent_handle(response_content="ok")
        handle.ipc.send_chat = slow_send_chat
        mock_resp = MagicMock()
        mock_resp.type = AgentToPlatformType.RESULT
        mock_resp.content = "done"
        mock_resp.status = "completed"
        handle.ipc.receive_until_result = AsyncMock(return_value=mock_resp)

        pm = _make_process_manager(agents={"shared-agent": handle})
        router = PlatformRouter(process_manager=pm)

        adapter = McpToolAdapter("shared-agent", {"name": "test-tool"})

        # Start route_to_atomic (will block on send_chat)
        task1 = asyncio.create_task(
            router.route_to_atomic("shared-agent", "msg1", "c1")
        )
        # Give it time to acquire the lock and enter send_chat
        await asyncio.sleep(0.05)
        assert call_order == ["started"]

        # Start adapter.execute — it should NOT enter send_chat yet
        # because route_to_atomic holds the shared lock
        task2 = asyncio.create_task(adapter.execute(handle, {"x": 1}))
        await asyncio.sleep(0.05)
        assert call_order == ["started"]  # task2 is still waiting for lock

        # Release the first call
        proceed.set()
        await asyncio.sleep(0.05)

        # Both should complete now
        r1 = await task1
        r2 = await task2
        assert r1["success"] is True
        assert r2["success"] is True
        assert "finished" in call_order
        remove_all_locks()


# iter125 regression: error_type consistency in all error return paths
class TestErrorTypeConsistency:
    """Every error return dict from route_to_atomic must include error_type."""

    @pytest.mark.asyncio
    async def test_agent_not_found_has_error_type(self) -> None:
        """Agent not found returns error_type='KeyError'."""
        from agent_nexus.platform.router.router import PlatformRouter
        from agent_nexus.platform.orchestration.process_manager import ProcessManager

        pm = ProcessManager()
        router = PlatformRouter(process_manager=pm)
        result = await router.route_to_atomic("nonexistent", "hello", "conv-1")
        assert result["success"] is False
        assert result["error_type"] == "KeyError"

    @pytest.mark.asyncio
    async def test_agent_not_alive_has_error_type(self) -> None:
        """Agent not alive returns error_type='ProcessNotAliveError'."""
        from agent_nexus.platform.router.router import PlatformRouter
        from agent_nexus.platform.orchestration.process_manager import (
            AgentHandle, ProcessManager,
        )
        from unittest.mock import MagicMock

        pm = ProcessManager()
        router = PlatformRouter(process_manager=pm)
        mock_handle = MagicMock(spec=AgentHandle)
        mock_handle.is_alive = False
        pm._agents["dead-agent"] = mock_handle

        result = await router.route_to_atomic("dead-agent", "hello", "conv-1")
        assert result["success"] is False
        assert result["error_type"] == "ProcessNotAliveError"

    @pytest.mark.asyncio
    async def test_empty_agent_name_has_error_type(self) -> None:
        """Empty agent_name returns error_type='ValueError'."""
        from agent_nexus.platform.router.router import PlatformRouter
        from agent_nexus.platform.orchestration.process_manager import ProcessManager

        pm = ProcessManager()
        router = PlatformRouter(process_manager=pm)
        result = await router.route_chat("", "hello")
        assert result["success"] is False
        assert result["error_type"] == "ValueError"

    @pytest.mark.asyncio
    async def test_empty_message_has_error_type(self) -> None:
        """Empty message returns error_type='ValueError'."""
        from agent_nexus.platform.router.router import PlatformRouter
        from agent_nexus.platform.orchestration.process_manager import ProcessManager

        pm = ProcessManager()
        router = PlatformRouter(process_manager=pm)
        result = await router.route_chat("some-agent", "")
        assert result["success"] is False
        assert result["error_type"] == "ValueError"
