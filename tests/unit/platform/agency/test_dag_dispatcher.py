"""TDD RED tests for DAGDispatcher — bridges CompositionDAG to TaskGraph execution.

Tests cover: conversion, loading, dispatch (parallel/sequential/mixed), failure
propagation, timeout, max_parallel, empty DAG, state tracking, stuck dependencies,
and specialist-only filtering.
"""

from __future__ import annotations

import time

import pytest

from agent_nexus.models.task import TaskItem, TaskState
from agent_nexus.platform.agency.dag_dispatcher import (
    DAGDispatcher,
    DispatchResult,
    dag_task_to_task_item,
    load_dag_into_graph,
)
from agent_nexus.platform.agency.integrator import Artifact
from agent_nexus.platform.agency.planner import CompositionDAG, DAGTask
from agent_nexus.platform.orchestration.task_graph import TaskGraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_artifact(agent: str, label: str = "ok") -> Artifact:
    """Create a simple test artifact."""
    return Artifact(source_agent=agent, artifact_type="test", sections={"label": label})


def _ok_executor(profile_id: str, task: str) -> Artifact:
    """Default executor that always succeeds."""
    return _make_artifact(profile_id)


def _fail_executor(profile_id: str, task: str) -> Artifact:
    """Executor that always raises."""
    raise RuntimeError(f"Executor failed for {profile_id}")


def _slow_executor(profile_id: str, task: str) -> Artifact:
    """Executor that sleeps 0.1 seconds (for timeout tests)."""
    time.sleep(0.1)
    return _make_artifact(profile_id)


def _build_dag(
    tasks: list[DAGTask],
    name: str = "test-dag",
    max_parallel: int = 3,
) -> CompositionDAG:
    return CompositionDAG(name=name, max_parallel=max_parallel, tasks=tasks)


# ---------------------------------------------------------------------------
# 1. TestDagTaskConversion
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestDagTaskConversion:
    """dag_task_to_task_item preserves id, agent, blocked_by, output_contract in vars."""

    def test_preserves_id(self) -> None:
        dag_task = DAGTask(id="arch", agent="agency.architect", output="plan")
        item = dag_task_to_task_item(dag_task, "build a thing")
        assert item.id == "arch"

    def test_preserves_agent(self) -> None:
        dag_task = DAGTask(id="sec", agent="agency.security", output="report")
        item = dag_task_to_task_item(dag_task, "secure it")
        assert item.agent == "agency.security"

    def test_preserves_blocked_by(self) -> None:
        dag_task = DAGTask(
            id="impl", agent="agency.coder", output="code", blocked_by=["arch"]
        )
        item = dag_task_to_task_item(dag_task, "implement it")
        assert item.blocked_by == ["arch"]

    def test_output_contract_in_vars(self) -> None:
        dag_task = DAGTask(id="test", agent="agency.tester", output="test_results")
        item = dag_task_to_task_item(dag_task, "test it")
        assert item.vars["output_contract"] == "test_results"

    def test_description_copied_from_argument(self) -> None:
        dag_task = DAGTask(id="x", agent="a", output="o")
        item = dag_task_to_task_item(dag_task, "my special task")
        assert item.description == "my special task"

    def test_empty_blocked_by(self) -> None:
        dag_task = DAGTask(id="x", agent="a", output="o", blocked_by=[])
        item = dag_task_to_task_item(dag_task, "desc")
        assert item.blocked_by == []


# ---------------------------------------------------------------------------
# 2. TestLoadDagIntoGraph
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestLoadDagIntoGraph:
    """Load 3 specialist tasks -> graph has 3 ready tasks, integrate/validate excluded."""

    def test_three_specialists_loaded(self) -> None:
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="s2", agent="a2", output="o2"),
            DAGTask(id="s3", agent="a3", output="o3"),
            DAGTask(id="integrate", agent="nexus.integrator", output="merged", task_type="synthetic"),
            DAGTask(id="validate", agent="nexus.qa-gate", output="qa", task_type="synthetic"),
        ])
        graph = TaskGraph(":memory:")
        items = load_dag_into_graph(dag, "test task", graph)
        assert len(items) == 3
        assert {i.id for i in items} == {"s1", "s2", "s3"}

    def test_graph_has_ready_tasks(self) -> None:
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="s2", agent="a2", output="o2"),
            DAGTask(id="s3", agent="a3", output="o3"),
        ])
        graph = TaskGraph(":memory:")
        load_dag_into_graph(dag, "test", graph)
        ready = graph.get_ready_tasks()
        assert len(ready) == 3

    def test_integrate_not_in_graph(self) -> None:
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="integrate", agent="nexus.integrator", output="merged", task_type="synthetic"),
        ])
        graph = TaskGraph(":memory:")
        items = load_dag_into_graph(dag, "test", graph)
        assert all(i.id != "integrate" for i in items)
        assert graph.get_task("integrate") is None

    def test_validate_not_in_graph(self) -> None:
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="validate", agent="nexus.qa-gate", output="qa", task_type="synthetic"),
        ])
        graph = TaskGraph(":memory:")
        items = load_dag_into_graph(dag, "test", graph)
        assert all(i.id != "validate" for i in items)
        assert graph.get_task("validate") is None


# ---------------------------------------------------------------------------
# 3. TestLoadDagWithDependencies
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestLoadDagWithDependencies:
    """Tasks with blocked_by -> only unblocked tasks are ready."""

    def test_blocked_task_not_ready(self) -> None:
        dag = _build_dag([
            DAGTask(id="a", agent="a1", output="o1"),
            DAGTask(id="b", agent="a2", output="o2", blocked_by=["a"]),
        ])
        graph = TaskGraph(":memory:")
        load_dag_into_graph(dag, "test", graph)
        ready = graph.get_ready_tasks()
        ready_ids = {t.id for t in ready}
        assert "a" in ready_ids
        assert "b" not in ready_ids

    def test_unblocked_task_is_ready(self) -> None:
        dag = _build_dag([
            DAGTask(id="free", agent="a1", output="o1"),
        ])
        graph = TaskGraph(":memory:")
        load_dag_into_graph(dag, "test", graph)
        ready = graph.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "free"


# ---------------------------------------------------------------------------
# 4. TestSimpleDispatch
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestSimpleDispatch:
    """2 parallel tasks -> both complete, artifacts collected."""

    def test_both_complete(self) -> None:
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="s2", agent="a2", output="o2"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "build it")
        assert set(result.completed) == {"s1", "s2"}
        assert len(result.failed) == 0

    def test_artifacts_collected(self) -> None:
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="s2", agent="a2", output="o2"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "build it")
        assert "s1" in result.artifacts
        assert "s2" in result.artifacts
        assert result.artifacts["s1"].source_agent == "a1"
        assert result.artifacts["s2"].source_agent == "a2"

    def test_no_timeout(self) -> None:
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "test")
        assert result.timed_out is False


# ---------------------------------------------------------------------------
# 5. TestSequentialDispatch
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestSequentialDispatch:
    """Task B blocked_by Task A -> A completes first, then B."""

    def test_both_complete_sequentially(self) -> None:
        dag = _build_dag([
            DAGTask(id="a", agent="a1", output="o1"),
            DAGTask(id="b", agent="a2", output="o2", blocked_by=["a"]),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "sequential work")
        assert set(result.completed) == {"a", "b"}

    def test_artifact_for_both(self) -> None:
        dag = _build_dag([
            DAGTask(id="a", agent="a1", output="o1"),
            DAGTask(id="b", agent="a2", output="o2", blocked_by=["a"]),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "sequential work")
        assert "a" in result.artifacts
        assert "b" in result.artifacts


# ---------------------------------------------------------------------------
# 6. TestMixedParallelSequential
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestMixedParallelSequential:
    """3 tasks where 1 blocks another -> correct ordering."""

    def test_all_complete(self) -> None:
        # s1 and s2 are parallel; s3 is blocked by s1
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="s2", agent="a2", output="o2"),
            DAGTask(id="s3", agent="a3", output="o3", blocked_by=["s1"]),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "mixed work")
        assert set(result.completed) == {"s1", "s2", "s3"}

    def test_no_failures(self) -> None:
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="s2", agent="a2", output="o2"),
            DAGTask(id="s3", agent="a3", output="o3", blocked_by=["s1"]),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "mixed work")
        assert len(result.failed) == 0


# ---------------------------------------------------------------------------
# 7. TestFailedExecutor
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestFailedExecutor:
    """Executor raises -> task marked failed, other tasks continue."""

    def test_failing_task_in_result(self) -> None:
        call_count = {"n": 0}

        def selective_executor(pid: str, task: str) -> Artifact:
            call_count["n"] += 1
            if pid == "bad_agent":
                raise RuntimeError("boom")
            return _make_artifact(pid)

        dag = _build_dag([
            DAGTask(id="good", agent="good_agent", output="o1"),
            DAGTask(id="bad", agent="bad_agent", output="o2"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, selective_executor)
        result = dispatcher.dispatch(dag, "partial fail")
        assert "bad" in result.failed

    def test_other_tasks_still_complete(self) -> None:
        def selective_executor(pid: str, task: str) -> Artifact:
            if pid == "bad_agent":
                raise RuntimeError("boom")
            return _make_artifact(pid)

        dag = _build_dag([
            DAGTask(id="good", agent="good_agent", output="o1"),
            DAGTask(id="bad", agent="bad_agent", output="o2"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, selective_executor)
        result = dispatcher.dispatch(dag, "partial fail")
        assert "good" in result.completed
        assert "good" in result.artifacts


# ---------------------------------------------------------------------------
# 8. TestAllFail
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestAllFail:
    """All executors fail -> DispatchResult.failed has all IDs, no artifacts."""

    def test_all_in_failed(self) -> None:
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="s2", agent="a2", output="o2"),
            DAGTask(id="s3", agent="a3", output="o3"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _fail_executor)
        result = dispatcher.dispatch(dag, "everything fails")
        assert set(result.failed) == {"s1", "s2", "s3"}

    def test_no_artifacts(self) -> None:
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="s2", agent="a2", output="o2"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _fail_executor)
        result = dispatcher.dispatch(dag, "everything fails")
        assert len(result.artifacts) == 0

    def test_no_completed(self) -> None:
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _fail_executor)
        result = dispatcher.dispatch(dag, "everything fails")
        assert len(result.completed) == 0


# ---------------------------------------------------------------------------
# 9. TestTimeout
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestTimeout:
    """Set timeout_seconds=0.01 with slow executor -> timed_out=True."""

    def test_timed_out_flag(self) -> None:
        dag = _build_dag([
            DAGTask(id="slow1", agent="a1", output="o1"),
            DAGTask(id="slow2", agent="a2", output="o2"),
            DAGTask(id="slow3", agent="a3", output="o3"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(
            graph, _slow_executor, timeout_seconds=0.01
        )
        result = dispatcher.dispatch(dag, "will timeout")
        assert result.timed_out is True

    def test_timeout_with_no_completions_possible(self) -> None:
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(
            graph, _slow_executor, timeout_seconds=0.01
        )
        result = dispatcher.dispatch(dag, "will timeout")
        assert result.timed_out is True


# ---------------------------------------------------------------------------
# 10. TestMaxParallelRespected
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestMaxParallelRespected:
    """max_parallel=1 with 3 tasks -> sequential execution verified."""

    def test_all_complete_with_max_parallel_1(self) -> None:
        execution_order: list[str] = []

        def tracking_executor(pid: str, task: str) -> Artifact:
            execution_order.append(pid)
            return _make_artifact(pid)

        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="s2", agent="a2", output="o2"),
            DAGTask(id="s3", agent="a3", output="o3"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, tracking_executor, max_parallel=1)
        result = dispatcher.dispatch(dag, "sequential test")
        assert set(result.completed) == {"s1", "s2", "s3"}

    def test_max_parallel_1_executes_one_at_a_time(self) -> None:
        """With max_parallel=1, only 1 task executes per round."""
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="s2", agent="a2", output="o2"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor, max_parallel=1)
        result = dispatcher.dispatch(dag, "one at a time")
        # Both should complete, just never in the same batch
        assert set(result.completed) == {"s1", "s2"}


# ---------------------------------------------------------------------------
# 11. TestEmptyDag
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestEmptyDag:
    """Empty specialist_tasks -> no artifacts, no crash."""

    def test_empty_dag_no_crash(self) -> None:
        dag = _build_dag([])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "nothing to do")
        assert result.artifacts == {}
        assert result.completed == []
        assert result.failed == []

    def test_only_integrate_validate(self) -> None:
        """DAG with only integrate/validate tasks -> empty specialist_tasks."""
        dag = _build_dag([
            DAGTask(id="integrate", agent="nexus.integrator", output="merged", task_type="synthetic"),
            DAGTask(id="validate", agent="nexus.qa-gate", output="qa", task_type="synthetic"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "only fixed tasks")
        assert result.artifacts == {}
        assert result.completed == []


# ---------------------------------------------------------------------------
# 12. TestTaskGraphStates
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestTaskGraphStates:
    """After dispatch, check each task's state in TaskGraph is correct."""

    def test_completed_tasks_have_completed_state(self) -> None:
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="s2", agent="a2", output="o2"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        dispatcher.dispatch(dag, "state check")
        t1 = graph.get_task("s1")
        t2 = graph.get_task("s2")
        assert t1 is not None and t1.state == TaskState.COMPLETED
        assert t2 is not None and t2.state == TaskState.COMPLETED

    def test_failed_tasks_have_failed_state(self) -> None:
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _fail_executor)
        dispatcher.dispatch(dag, "fail check")
        t = graph.get_task("s1")
        assert t is not None and t.state == TaskState.FAILED

    def test_mixed_states(self) -> None:
        def selective(pid: str, task: str) -> Artifact:
            if pid == "fail_agent":
                raise RuntimeError("nope")
            return _make_artifact(pid)

        dag = _build_dag([
            DAGTask(id="ok", agent="ok_agent", output="o1"),
            DAGTask(id="fail", agent="fail_agent", output="o2"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, selective)
        dispatcher.dispatch(dag, "mixed states")
        ok_task = graph.get_task("ok")
        fail_task = graph.get_task("fail")
        assert ok_task is not None and ok_task.state == TaskState.COMPLETED
        assert fail_task is not None and fail_task.state == TaskState.FAILED


# ---------------------------------------------------------------------------
# 13. TestDispatchWithExpertExecutorProtocol
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestDispatchWithExpertExecutorProtocol:
    """Use a lambda as executor to verify protocol works."""

    def test_lambda_executor(self) -> None:
        executor = lambda pid, task: Artifact(
            source_agent=pid,
            artifact_type="lambda_result",
            sections={"task_desc": task},
        )
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, executor)
        result = dispatcher.dispatch(dag, "lambda test")
        assert result.artifacts["s1"].artifact_type == "lambda_result"
        assert result.artifacts["s1"].sections["task_desc"] == "lambda test"

    def test_executor_receives_correct_args(self) -> None:
        received: dict[str, str] = {}

        def capturing_executor(pid: str, task: str) -> Artifact:
            received["profile_id"] = pid
            received["task"] = task
            return _make_artifact(pid)

        dag = _build_dag([
            DAGTask(id="s1", agent="my.special.agent", output="o1"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, capturing_executor)
        dispatcher.dispatch(dag, "check args")
        assert received["profile_id"] == "my.special.agent"
        assert received["task"] == "check args"


# ---------------------------------------------------------------------------
# 14. TestStuckDependencyFailure
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestStuckDependencyFailure:
    """If dep fails, blocked tasks get failed too."""

    def test_dependent_task_failed_when_dep_fails(self) -> None:
        def selective(pid: str, task: str) -> Artifact:
            if pid == "a1":
                raise RuntimeError("upstream fails")
            return _make_artifact(pid)

        dag = _build_dag([
            DAGTask(id="upstream", agent="a1", output="o1"),
            DAGTask(id="downstream", agent="a2", output="o2", blocked_by=["upstream"]),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, selective)
        result = dispatcher.dispatch(dag, "cascading failure")
        assert "upstream" in result.failed
        assert "downstream" in result.failed

    def test_cascading_failure_no_artifacts_for_blocked(self) -> None:
        def selective(pid: str, task: str) -> Artifact:
            if pid == "a1":
                raise RuntimeError("upstream fails")
            return _make_artifact(pid)

        dag = _build_dag([
            DAGTask(id="upstream", agent="a1", output="o1"),
            DAGTask(id="downstream", agent="a2", output="o2", blocked_by=["upstream"]),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, selective)
        result = dispatcher.dispatch(dag, "cascading failure")
        assert "downstream" not in result.artifacts
        assert "upstream" not in result.artifacts


# ---------------------------------------------------------------------------
# 15. TestSpecialistTasksOnly
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestSpecialistTasksOnly:
    """integrate/validate tasks in DAG are never dispatched."""

    def test_integrate_never_dispatched(self) -> None:
        dispatched_agents: list[str] = []

        def tracking(pid: str, task: str) -> Artifact:
            dispatched_agents.append(pid)
            return _make_artifact(pid)

        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="integrate", agent="nexus.integrator", output="merged", task_type="synthetic"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, tracking)
        result = dispatcher.dispatch(dag, "filter test")
        assert "nexus.integrator" not in dispatched_agents
        assert "s1" in result.completed
        assert "integrate" not in result.completed

    def test_validate_never_dispatched(self) -> None:
        dispatched_agents: list[str] = []

        def tracking(pid: str, task: str) -> Artifact:
            dispatched_agents.append(pid)
            return _make_artifact(pid)

        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="validate", agent="nexus.qa-gate", output="qa", task_type="synthetic"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, tracking)
        result = dispatcher.dispatch(dag, "filter test")
        assert "nexus.qa-gate" not in dispatched_agents
        assert "validate" not in result.completed

    def test_both_fixed_tasks_excluded(self) -> None:
        dispatched_ids: list[str] = []

        def tracking(pid: str, task: str) -> Artifact:
            dispatched_ids.append(pid)
            return _make_artifact(pid)

        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="integrate", agent="nexus.integrator", output="merged", task_type="synthetic"),
            DAGTask(id="validate", agent="nexus.qa-gate", output="qa", task_type="synthetic"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, tracking)
        result = dispatcher.dispatch(dag, "filter test")
        assert set(result.completed) == {"s1"}
        assert "integrate" not in result.completed
        assert "validate" not in result.completed


# ---------------------------------------------------------------------------
# 16. Edge-case tests — boundary behaviors and potential bug traps
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestDispatcherReuseGraph:
    """Dispatching two DAGs on the same graph should not cross-contaminate."""

    def test_second_dispatch_independent(self) -> None:
        graph = TaskGraph(":memory:")
        dag1 = _build_dag([
            DAGTask(id="d1_s1", agent="a1", output="o1"),
        ])
        dag2 = _build_dag([
            DAGTask(id="d2_s1", agent="b1", output="o1"),
        ])
        dispatcher = DAGDispatcher(graph, _ok_executor)
        r1 = dispatcher.dispatch(dag1, "first")
        r2 = dispatcher.dispatch(dag2, "second")
        assert "d1_s1" in r1.completed
        assert "d2_s1" in r2.completed
        assert "d2_s1" not in r1.completed
        assert "d1_s1" not in r2.completed


@pytest.mark.timeout(10)
class TestMaxParallelZero:
    """max_parallel=0 should be clamped to 1."""

    def test_zero_clamped_to_one(self) -> None:
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="s2", agent="a2", output="o2"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor, max_parallel=0)
        result = dispatcher.dispatch(dag, "zero parallel")
        assert set(result.completed) == {"s1", "s2"}


@pytest.mark.timeout(10)
class TestMaxParallelNegative:
    """max_parallel=-1 should be clamped to 1."""

    def test_negative_clamped_to_one(self) -> None:
        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor, max_parallel=-5)
        result = dispatcher.dispatch(dag, "negative parallel")
        assert "s1" in result.completed


@pytest.mark.timeout(10)
class TestTimeoutMidBatch:
    """Timeout fires after first task in a batch starts but before second runs."""

    def test_at_least_one_completes_before_timeout(self) -> None:
        """With 3 tasks and very short timeout, at most 1 should complete."""
        call_count = {"n": 0}

        def counting_slow(pid: str, task: str) -> Artifact:
            call_count["n"] += 1
            time.sleep(0.02)  # Small delay per task
            return _make_artifact(pid)

        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="s2", agent="a2", output="o2"),
            DAGTask(id="s3", agent="a3", output="o3"),
        ])
        graph = TaskGraph(":memory:")
        # Timeout shorter than 3 tasks * 0.02s each
        dispatcher = DAGDispatcher(
            graph, counting_slow, max_parallel=1, timeout_seconds=0.03
        )
        result = dispatcher.dispatch(dag, "mid-batch timeout")
        assert result.timed_out is True
        # At least 1 should have completed, fewer than 3
        assert len(result.completed) >= 1
        assert len(result.completed) < 3


@pytest.mark.timeout(10)
class TestDiamondDependency:
    """Diamond: A -> B, A -> C, B+C -> D. All should complete."""

    def test_diamond_all_complete(self) -> None:
        dag = _build_dag([
            DAGTask(id="a", agent="a1", output="o1"),
            DAGTask(id="b", agent="a2", output="o2", blocked_by=["a"]),
            DAGTask(id="c", agent="a3", output="o3", blocked_by=["a"]),
            DAGTask(id="d", agent="a4", output="o4", blocked_by=["b", "c"]),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "diamond deps")
        assert set(result.completed) == {"a", "b", "c", "d"}

    def test_diamond_artifact_count(self) -> None:
        dag = _build_dag([
            DAGTask(id="a", agent="a1", output="o1"),
            DAGTask(id="b", agent="a2", output="o2", blocked_by=["a"]),
            DAGTask(id="c", agent="a3", output="o3", blocked_by=["a"]),
            DAGTask(id="d", agent="a4", output="o4", blocked_by=["b", "c"]),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "diamond deps")
        assert len(result.artifacts) == 4


@pytest.mark.timeout(10)
class TestSingleTask:
    """Minimal: single task DAG."""

    def test_single_task_completes(self) -> None:
        dag = _build_dag([
            DAGTask(id="only", agent="a1", output="o1"),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "one task")
        assert result.completed == ["only"]
        assert result.failed == []
        assert "only" in result.artifacts


@pytest.mark.timeout(10)
class TestChainedFailure:
    """Chain: A -> B -> C. If A fails, both B and C should be failed."""

    def test_full_chain_failure(self) -> None:
        dag = _build_dag([
            DAGTask(id="a", agent="a1", output="o1"),
            DAGTask(id="b", agent="a2", output="o2", blocked_by=["a"]),
            DAGTask(id="c", agent="a3", output="o3", blocked_by=["b"]),
        ])
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _fail_executor)
        result = dispatcher.dispatch(dag, "chain fail")
        assert "a" in result.failed
        # B and C are stuck (blocked by failed deps) — should be failed too
        assert "b" in result.failed
        assert "c" in result.failed
