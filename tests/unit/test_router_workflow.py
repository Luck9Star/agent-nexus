"""Unit tests for agent_nexus.platform.router.workflow — WorkflowPhase, WorkflowContext, WorkflowResult."""

from __future__ import annotations

from datetime import datetime

from agent_nexus.platform.router.workflow import (
    WorkflowContext,
    WorkflowPhase,
    WorkflowResult,
)

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
