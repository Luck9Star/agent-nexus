"""Unit tests for the Platform Router module.

Covers three source files:
- workflow.py: WorkflowPhase, WorkflowContext, WorkflowResult
- subtask.py: SubtaskConfig, SubtaskController
- router.py: PlatformRouter

Uses mocks for ProcessManager, IPC, and external dependencies.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.models.ipc import AgentToPlatform, AgentToPlatformType
from agent_nexus.platform.orchestration.dsl import (
    DSLAgent,
    DSLTask,
    DSLToolLoading,
    OrchestrationDefinition,
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
    response_status: str = "completed",
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
        ctrl = SubtaskConfig(timeout_seconds=0.05)
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
        cfg = SubtaskConfig(timeout_seconds=0.05)
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
        assert results[2] == "success"

    @pytest.mark.asyncio
    async def test_run_parallel_all_fail(self) -> None:
        ctrl = SubtaskController()

        async def fail():
            raise ValueError("nope")

        results = await ctrl.run_parallel([fail(), fail()])
        assert all(isinstance(r, ValueError) for r in results)

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
    async def test_agent_not_found_raises(self) -> None:
        pm = _make_process_manager(agents={})
        router = PlatformRouter(process_manager=pm)

        with pytest.raises(KeyError, match="not found"):
            await router.route_to_atomic("missing", "hello", "conv-1")

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

        result = await router.route_chat("a", "hi", conversation_id="fixed-id")
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

        # All phases should report "no agents available"
        assert result.completed_phases == 4  # phases don't raise, just return text
        assert result.success is True


class TestGetTools:
    """Tests for PlatformRouter.get_tools."""

    @pytest.mark.asyncio
    async def test_get_tools_from_alive_agents(self) -> None:
        h1 = _make_agent_handle(name="a1")
        h1.ipc.receive_until_result = AsyncMock(
            return_value=MagicMock(output=[{"name": "tool1"}], type=AgentToPlatformType.RESULT)
        )
        h2 = _make_agent_handle(name="a2")
        h2.ipc.receive_until_result = AsyncMock(
            return_value=MagicMock(output=[{"name": "tool2"}], type=AgentToPlatformType.RESULT)
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
            return_value=MagicMock(output=[{"name": "t"}], type=AgentToPlatformType.RESULT)
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
