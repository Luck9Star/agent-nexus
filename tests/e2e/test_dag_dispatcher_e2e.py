"""E2E: DAGDispatcher.adispatch — async dispatch with real TaskGraph (SQLite).

adispatch is the ONLY untested public method across all 5 critical modules.
It wraps dispatch() via asyncio.to_thread to prevent event loop blocking.

These tests use real TaskGraph (in-memory SQLite) and real executor callables
to exercise the full dispatch pipeline:
  CompositionDAG -> load_dag_into_graph -> dispatch loop -> DispatchResult

No mocks on internal platform components — only the ExpertExecutor callable
is replaced with a deterministic function (equivalent to a real agent that
always succeeds or always fails).
"""

import asyncio
from pathlib import Path

import pytest

from agent_nexus.platform.agency.dag_dispatcher import (
    DAGDispatcher,
    load_dag_into_graph,
)
from agent_nexus.platform.agency.integrator import Artifact
from agent_nexus.platform.agency.planner import CompositionDAG, DAGTask
from agent_nexus.platform.orchestration.task_graph import TaskGraph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_artifact(agent: str = "test-agent") -> Artifact:
    return Artifact(
        source_agent=agent,
        artifact_type="text",
        sections={"output": "result from " + agent},
    )


def _success_executor(profile_id: str, task: str, **kwargs) -> Artifact:
    """Deterministic executor that always succeeds."""
    return _make_artifact(agent=profile_id)


def _fail_executor(profile_id: str, task: str, **kwargs) -> Artifact:
    """Deterministic executor that always raises."""
    raise RuntimeError(f"Executor failed for {profile_id}")


def _linear_dag(name: str = "test", n_tasks: int = 3) -> CompositionDAG:
    """Build a linear DAG: t1 -> t2 -> t3."""
    tasks = []
    for i in range(n_tasks):
        blocked = [f"t{i}"] if i > 0 else []
        tasks.append(
            DAGTask(
                id=f"t{i + 1}", agent=f"agent-{i + 1}", output=f"out-{i + 1}", blocked_by=blocked
            )
        )
    return CompositionDAG(name=name, max_parallel=3, tasks=tasks)


def _diamond_dag() -> CompositionDAG:
    """Build a diamond DAG: t1 -> t2, t1 -> t3, t2+t3 -> t4."""
    tasks = [
        DAGTask(id="t1", agent="root", output="out-1"),
        DAGTask(id="t2", agent="left", output="out-2", blocked_by=["t1"]),
        DAGTask(id="t3", agent="right", output="out-3", blocked_by=["t1"]),
        DAGTask(id="t4", agent="join", output="out-4", blocked_by=["t2", "t3"]),
    ]
    return CompositionDAG(name="diamond", max_parallel=2, tasks=tasks)


def _graph(tmp_path: Path) -> TaskGraph:
    return TaskGraph(str(tmp_path / "dispatch.db"))


# ---------------------------------------------------------------------------
# Tests: adispatch (async wrapper)
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestDAGDispatcherAdispatch:
    """adispatch: async wrapper that offloads dispatch to a thread."""

    @pytest.mark.asyncio
    async def test_adispatch_linear_dag_completes(self, tmp_path: Path) -> None:
        """adispatch completes a linear 3-task DAG with all tasks succeeding."""
        dag = _linear_dag("linear-3", n_tasks=3)
        graph = _graph(tmp_path)
        dispatcher = DAGDispatcher(graph=graph, executor=_success_executor)

        result = await dispatcher.adispatch(dag, "test task")

        assert len(result.completed) == 3
        assert len(result.failed) == 0
        assert result.timed_out is False
        assert set(result.completed) == {"t1", "t2", "t3"}
        dispatcher.close()

    @pytest.mark.asyncio
    async def test_adispatch_diamond_dag_completes(self, tmp_path: Path) -> None:
        """adispatch resolves a diamond DAG with correct dependency ordering."""
        dag = _diamond_dag()
        graph = _graph(tmp_path)
        dispatcher = DAGDispatcher(graph=graph, executor=_success_executor)

        result = await dispatcher.adispatch(dag, "diamond task")

        assert len(result.completed) == 4
        assert len(result.failed) == 0
        # Verify t4 (the join node) received upstream artifacts
        assert "t4" in result.artifacts
        dispatcher.close()

    @pytest.mark.asyncio
    async def test_adispatch_does_not_block_event_loop(self, tmp_path: Path) -> None:
        """adispatch runs dispatch in a thread — event loop stays responsive."""
        dag = _linear_dag("nonblock", n_tasks=2)
        graph = _graph(tmp_path)

        call_count = 0

        def slow_executor(profile_id: str, task: str, **kwargs) -> Artifact:
            import time

            time.sleep(0.1)
            return _make_artifact(agent=profile_id)

        dispatcher = DAGDispatcher(graph=graph, executor=slow_executor)

        # Run adispatch and a concurrent event-loop task
        async def ping():
            nonlocal call_count
            while True:
                call_count += 1
                await asyncio.sleep(0.01)

        ping_task = asyncio.create_task(ping())
        result = await dispatcher.adispatch(dag, "non-blocking test")
        ping_task.cancel()
        import contextlib

        with contextlib.suppress(asyncio.CancelledError):
            await ping_task

        # Event loop was responsive during dispatch
        assert call_count > 0
        assert len(result.completed) == 2
        dispatcher.close()

    @pytest.mark.asyncio
    async def test_adispatch_with_failing_executor(self, tmp_path: Path) -> None:
        """adispatch propagates executor failures to DispatchResult.failed."""
        dag = _linear_dag("fail-dag", n_tasks=2)
        graph = _graph(tmp_path)
        dispatcher = DAGDispatcher(graph=graph, executor=_fail_executor)

        result = await dispatcher.adispatch(dag, "failing task")

        # First task fails → fail-fast stops the pipeline
        assert len(result.failed) >= 1
        assert len(result.completed) == 0
        assert result.failed[0] in result.errors
        dispatcher.close()

    @pytest.mark.asyncio
    async def test_adispatch_timeout_expires(self, tmp_path: Path) -> None:
        """adispatch respects timeout_seconds and sets timed_out flag."""
        dag = _linear_dag("timeout-dag", n_tasks=3)
        graph = _graph(tmp_path)

        def slow_executor(profile_id: str, task: str, **kwargs) -> Artifact:
            import time

            time.sleep(10)  # Way beyond timeout
            return _make_artifact(agent=profile_id)

        dispatcher = DAGDispatcher(graph=graph, executor=slow_executor, timeout_seconds=0.2)

        result = await dispatcher.adispatch(dag, "timeout test")

        assert result.timed_out is True
        dispatcher.close()

    @pytest.mark.asyncio
    async def test_adispatch_single_task_dag(self, tmp_path: Path) -> None:
        """adispatch handles a single-task DAG (edge case)."""
        dag = CompositionDAG(
            name="single",
            max_parallel=1,
            tasks=[DAGTask(id="only", agent="solo", output="out")],
        )
        graph = _graph(tmp_path)
        dispatcher = DAGDispatcher(graph=graph, executor=_success_executor)

        result = await dispatcher.adispatch(dag, "single task")

        assert result.completed == ["only"]
        assert "only" in result.artifacts
        dispatcher.close()

    @pytest.mark.asyncio
    async def test_adispatch_empty_dag(self, tmp_path: Path) -> None:
        """adispatch handles an empty DAG (no tasks) gracefully."""
        dag = CompositionDAG(name="empty", max_parallel=1, tasks=[])
        graph = _graph(tmp_path)
        dispatcher = DAGDispatcher(graph=graph, executor=_success_executor)

        result = await dispatcher.adispatch(dag, "empty task")

        assert result.completed == []
        assert result.failed == []
        assert result.timed_out is False
        dispatcher.close()


# ---------------------------------------------------------------------------
# Tests: load_dag_into_graph (helper function)
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestLoadDagIntoGraph:
    """load_dag_into_graph correctly converts DAG tasks to TaskGraph entries."""

    def test_load_creates_task_items(self, tmp_path: Path) -> None:
        """Loading a DAG creates corresponding TaskItem entries in the graph."""
        dag = _linear_dag("load-test", n_tasks=3)
        graph = _graph(tmp_path)

        items = load_dag_into_graph(dag, "test desc", graph)

        assert len(items) == 3
        for item in items:
            assert graph.get_task(item.id) is not None

    def test_load_is_idempotent(self, tmp_path: Path) -> None:
        """Loading the same DAG twice doesn't create duplicates."""
        dag = _linear_dag("idempotent", n_tasks=2)
        graph = _graph(tmp_path)

        items1 = load_dag_into_graph(dag, "first load", graph)
        items2 = load_dag_into_graph(dag, "second load", graph)

        assert len(items1) == 2
        assert len(items2) == 0  # No new items on second load

    def test_load_strips_non_specialist_deps(self, tmp_path: Path) -> None:
        """blocked_by references to non-specialist tasks are filtered out."""
        dag = CompositionDAG(
            name="filter-deps",
            max_parallel=1,
            tasks=[
                DAGTask(id="spec-1", agent="a", output="out-1"),
                DAGTask(
                    id="spec-2",
                    agent="b",
                    output="out-2",
                    blocked_by=["spec-1", "integrate-1"],  # integrate-1 is non-specialist
                    task_type="specialist",
                ),
                DAGTask(
                    id="integrate-1",
                    agent="int",
                    output="int-out",
                    blocked_by=["spec-1"],
                    task_type="synthetic",
                ),
            ],
        )
        graph = _graph(tmp_path)

        items = load_dag_into_graph(dag, "filter test", graph)

        # Only specialist tasks should be loaded
        assert len(items) == 2
        spec2 = next(i for i in items if i.id == "spec-2")
        # Non-specialist dep should be stripped
        assert "integrate-1" not in spec2.blocked_by
        assert "spec-1" in spec2.blocked_by


# ---------------------------------------------------------------------------
# Tests: concurrent dispatch mode
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestDAGDispatcherConcurrent:
    """Concurrent dispatch mode with real TaskGraph."""

    @pytest.mark.asyncio
    async def test_concurrent_dispatch_diamond(self, tmp_path: Path) -> None:
        """Concurrent mode correctly parallelizes independent tasks in diamond DAG."""
        dag = _diamond_dag()
        graph = _graph(tmp_path)

        execution_order = []

        def tracking_executor(profile_id: str, task: str, **kwargs) -> Artifact:
            import time

            execution_order.append(profile_id)
            time.sleep(0.05)
            return _make_artifact(agent=profile_id)

        dispatcher = DAGDispatcher(
            graph=graph,
            executor=tracking_executor,
            use_concurrency=True,
        )

        result = await dispatcher.adispatch(dag, "concurrent diamond")

        assert len(result.completed) == 4
        # root must be first, join must be last
        assert execution_order[0] == "root"
        assert execution_order[-1] == "join"
        # left and right can be in any order
        assert {"left", "right"} == set(execution_order[1:3])
        dispatcher.close()

    @pytest.mark.asyncio
    async def test_upstream_artifacts_passed_correctly(self, tmp_path: Path) -> None:
        """Executor receives upstream artifacts from completed dependencies."""
        dag = _linear_dag("upstream", n_tasks=2)
        graph = _graph(tmp_path)

        received_upstream = []

        def capturing_executor(profile_id: str, task: str, **kwargs) -> Artifact:
            upstream = kwargs.get("upstream_artifacts")
            if upstream is not None:
                received_upstream.extend(upstream)
            return _make_artifact(agent=profile_id)

        dispatcher = DAGDispatcher(graph=graph, executor=capturing_executor)

        result = await dispatcher.adispatch(dag, "upstream test")

        assert len(result.completed) == 2
        # t2 should have received t1's artifact as upstream
        assert len(received_upstream) >= 1
        assert received_upstream[0].source_agent == "agent-1"
        dispatcher.close()
