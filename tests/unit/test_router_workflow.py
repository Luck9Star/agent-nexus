"""Unit tests for agent_nexus.platform.router.workflow — WorkflowPhase, WorkflowContext, WorkflowResult."""

from __future__ import annotations

from datetime import datetime

from agent_nexus.platform.router.workflow import (
    WorkflowContext,
    WorkflowPhase,
    WorkflowResult,
)


# ---------------------------------------------------------------------------
# WorkflowPhase enum
# ---------------------------------------------------------------------------

class TestWorkflowPhase:
    def test_four_phases_exist(self):
        names = {p.value for p in WorkflowPhase}
        assert names == {"research", "synthesis", "implementation", "verification"}

    def test_phase_order(self):
        order = [WorkflowPhase.research, WorkflowPhase.synthesis,
                 WorkflowPhase.implementation, WorkflowPhase.verification]
        assert order == list(WorkflowPhase)

    def test_str_enum_comparison(self):
        assert WorkflowPhase.research == "research"
        assert WorkflowPhase.synthesis != "research"

    def test_string_conversion(self):
        # StrEnum: str() returns the value, repr() shows the enum name
        assert WorkflowPhase.verification.value == "verification"
        assert WorkflowPhase.implementation == "implementation"


# ---------------------------------------------------------------------------
# WorkflowContext
# ---------------------------------------------------------------------------

class TestWorkflowContext:
    def test_creation_sets_defaults(self):
        ctx = WorkflowContext(
            conversation_id="cid-1",
            message="hello",
            agent_name="test-agent",
        )
        assert ctx.conversation_id == "cid-1"
        assert ctx.message == "hello"
        assert ctx.agent_name == "test-agent"
        assert ctx.phase_results == {}
        assert ctx.current_phase is None
        assert ctx.task_graph is None
        assert isinstance(ctx.started_at, datetime)

    def test_creation_with_explicit_phase(self):
        ctx = WorkflowContext(
            conversation_id="cid-2",
            message="go",
            agent_name="agent",
            current_phase=WorkflowPhase.synthesis,
        )
        assert ctx.current_phase == WorkflowPhase.synthesis

    def test_close_sets_task_graph_none(self):
        from agent_nexus.platform.orchestration.task_graph import TaskGraph
        tg = TaskGraph(":memory:")  # pyright: ignore[reportArgumentType]
        ctx = WorkflowContext(
            conversation_id="cid-3",
            message="test",
            agent_name="agent",
            task_graph=tg,
        )
        assert ctx.task_graph is not None
        ctx.close()
        assert ctx.task_graph is None

    def test_close_releases_mem_conn(self):
        """Regression: close() must call task_graph.close() to release _mem_conn."""
        from agent_nexus.platform.orchestration.task_graph import TaskGraph
        tg = TaskGraph(":memory:")  # pyright: ignore[reportArgumentType]
        assert tg._mem_conn is not None
        ctx = WorkflowContext(
            conversation_id="cid-mem",
            message="test",
            agent_name="agent",
            task_graph=tg,
        )
        ctx.close()
        # After close(), the in-memory connection should have been released
        assert tg._mem_conn is None

    def test_close_idempotent(self):
        ctx = WorkflowContext(
            conversation_id="cid-4",
            message="test",
            agent_name="agent",
        )
        ctx.close()  # already None
        ctx.close()  # second call should not raise
        assert ctx.task_graph is None

    def test_phase_results_mutable(self):
        ctx = WorkflowContext(
            conversation_id="cid-5",
            message="msg",
            agent_name="agent",
        )
        ctx.phase_results[WorkflowPhase.research] = "data"
        assert ctx.phase_results[WorkflowPhase.research] == "data"


# ---------------------------------------------------------------------------
# WorkflowResult
# ---------------------------------------------------------------------------

class TestWorkflowResult:
    def test_success_result(self):
        result = WorkflowResult(
            success=True,
            final_output="done",
            phase_results={WorkflowPhase.research: "r1"},
            total_phases=4,
            completed_phases=4,
        )
        assert result.success is True
        assert result.final_output == "done"
        assert result.error is None
        assert result.completed_phases == 4

    def test_failure_result_with_error(self):
        result = WorkflowResult(
            success=False,
            final_output="",
            phase_results={},
            total_phases=4,
            completed_phases=2,
            error="synthesis timed out",
        )
        assert result.success is False
        assert result.error == "synthesis timed out"
        assert result.completed_phases == 2

    def test_partial_completion(self):
        phases = {
            WorkflowPhase.research: "r",
            WorkflowPhase.synthesis: "s",
        }
        result = WorkflowResult(
            success=False,
            final_output="",
            phase_results=phases,
            total_phases=4,
            completed_phases=2,
        )
        assert len(result.phase_results) == 2
        assert result.completed_phases < result.total_phases

    def test_error_defaults_none(self):
        result = WorkflowResult(
            success=True,
            final_output="ok",
            phase_results={},
            total_phases=4,
            completed_phases=4,
        )
        assert result.error is None
        assert result.error_type is None

    def test_empty_phase_results(self):
        result = WorkflowResult(
            success=False,
            final_output="",
            phase_results={},
            total_phases=4,
            completed_phases=0,
            error="immediate failure",
        )
        assert result.phase_results == {}


class TestWorkflowResultErrorType:
    """iter101 regression: error_type carries exception class name."""

    def test_error_type_on_failure(self):
        result = WorkflowResult(
            success=False,
            final_output="",
            phase_results={},
            total_phases=4,
            completed_phases=0,
            error="something failed",
            error_type="ValueError",
        )
        assert result.error_type == "ValueError"

    def test_error_type_defaults_none(self):
        result = WorkflowResult(
            success=True,
            final_output="ok",
            phase_results={},
            total_phases=1,
            completed_phases=1,
            error=None,
        )
        assert result.error_type is None
