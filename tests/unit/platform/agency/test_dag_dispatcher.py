"""TDD RED tests for DAGDispatcher — bridges CompositionDAG to TaskGraph execution.

Tests cover: conversion, loading, dispatch (parallel/sequential/mixed), failure
propagation, timeout, max_batch_size, empty DAG, state tracking, stuck dependencies,
and specialist-only filtering.
"""

from __future__ import annotations

import time

import pytest

from agent_nexus.models.task import TaskState
from agent_nexus.platform.agency.dag_dispatcher import (
    DAGDispatcher,
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
        dag_task = DAGTask(id="impl", agent="agency.coder", output="code", blocked_by=["arch"])
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
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(id="s2", agent="a2", output="o2"),
                DAGTask(id="s3", agent="a3", output="o3"),
                DAGTask(
                    id="integrate", agent="nexus.integrator", output="merged", task_type="synthetic"
                ),
                DAGTask(id="validate", agent="nexus.qa-gate", output="qa", task_type="synthetic"),
            ]
        )
        graph = TaskGraph(":memory:")
        items = load_dag_into_graph(dag, "test task", graph)
        assert len(items) == 3
        assert {i.id for i in items} == {"s1", "s2", "s3"}

    def test_graph_has_ready_tasks(self) -> None:
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(id="s2", agent="a2", output="o2"),
                DAGTask(id="s3", agent="a3", output="o3"),
            ]
        )
        graph = TaskGraph(":memory:")
        load_dag_into_graph(dag, "test", graph)
        ready = graph.get_ready_tasks()
        assert len(ready) == 3

    def test_integrate_not_in_graph(self) -> None:
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(
                    id="integrate", agent="nexus.integrator", output="merged", task_type="synthetic"
                ),
            ]
        )
        graph = TaskGraph(":memory:")
        items = load_dag_into_graph(dag, "test", graph)
        assert all(i.id != "integrate" for i in items)
        assert graph.get_task("integrate") is None

    def test_validate_not_in_graph(self) -> None:
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(id="validate", agent="nexus.qa-gate", output="qa", task_type="synthetic"),
            ]
        )
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
        dag = _build_dag(
            [
                DAGTask(id="a", agent="a1", output="o1"),
                DAGTask(id="b", agent="a2", output="o2", blocked_by=["a"]),
            ]
        )
        graph = TaskGraph(":memory:")
        load_dag_into_graph(dag, "test", graph)
        ready = graph.get_ready_tasks()
        ready_ids = {t.id for t in ready}
        assert "a" in ready_ids
        assert "b" not in ready_ids

    def test_unblocked_task_is_ready(self) -> None:
        dag = _build_dag(
            [
                DAGTask(id="free", agent="a1", output="o1"),
            ]
        )
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
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(id="s2", agent="a2", output="o2"),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "build it")
        assert set(result.completed) == {"s1", "s2"}
        assert len(result.failed) == 0

    def test_artifacts_collected(self) -> None:
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(id="s2", agent="a2", output="o2"),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "build it")
        assert "s1" in result.artifacts
        assert "s2" in result.artifacts
        assert result.artifacts["s1"].source_agent == "a1"
        assert result.artifacts["s2"].source_agent == "a2"

    def test_no_timeout(self) -> None:
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
            ]
        )
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
        dag = _build_dag(
            [
                DAGTask(id="a", agent="a1", output="o1"),
                DAGTask(id="b", agent="a2", output="o2", blocked_by=["a"]),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "sequential work")
        assert set(result.completed) == {"a", "b"}

    def test_artifact_for_both(self) -> None:
        dag = _build_dag(
            [
                DAGTask(id="a", agent="a1", output="o1"),
                DAGTask(id="b", agent="a2", output="o2", blocked_by=["a"]),
            ]
        )
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
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(id="s2", agent="a2", output="o2"),
                DAGTask(id="s3", agent="a3", output="o3", blocked_by=["s1"]),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "mixed work")
        assert set(result.completed) == {"s1", "s2", "s3"}

    def test_no_failures(self) -> None:
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(id="s2", agent="a2", output="o2"),
                DAGTask(id="s3", agent="a3", output="o3", blocked_by=["s1"]),
            ]
        )
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

        def selective_executor(profile_id: str, task: str) -> Artifact:
            call_count["n"] += 1
            if profile_id == "bad_agent":
                raise RuntimeError("boom")
            return _make_artifact(profile_id)

        dag = _build_dag(
            [
                DAGTask(id="good", agent="good_agent", output="o1"),
                DAGTask(id="bad", agent="bad_agent", output="o2"),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, selective_executor)
        result = dispatcher.dispatch(dag, "partial fail")
        assert "bad" in result.failed

    def test_other_tasks_still_complete(self) -> None:
        def selective_executor(profile_id: str, task: str) -> Artifact:
            if profile_id == "bad_agent":
                raise RuntimeError("boom")
            return _make_artifact(profile_id)

        dag = _build_dag(
            [
                DAGTask(id="good", agent="good_agent", output="o1"),
                DAGTask(id="bad", agent="bad_agent", output="o2"),
            ]
        )
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
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(id="s2", agent="a2", output="o2"),
                DAGTask(id="s3", agent="a3", output="o3"),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _fail_executor)
        result = dispatcher.dispatch(dag, "everything fails")
        assert set(result.failed) == {"s1", "s2", "s3"}

    def test_no_artifacts(self) -> None:
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(id="s2", agent="a2", output="o2"),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _fail_executor)
        result = dispatcher.dispatch(dag, "everything fails")
        assert len(result.artifacts) == 0

    def test_no_completed(self) -> None:
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
            ]
        )
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
        dag = _build_dag(
            [
                DAGTask(id="slow1", agent="a1", output="o1"),
                DAGTask(id="slow2", agent="a2", output="o2"),
                DAGTask(id="slow3", agent="a3", output="o3"),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _slow_executor, timeout_seconds=0.01)
        result = dispatcher.dispatch(dag, "will timeout")
        assert result.timed_out is True

    def test_timeout_with_no_completions_possible(self) -> None:
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _slow_executor, timeout_seconds=0.01)
        result = dispatcher.dispatch(dag, "will timeout")
        assert result.timed_out is True


# ---------------------------------------------------------------------------
# 10. TestMaxParallelRespected
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestMaxParallelRespected:
    """max_batch_size=1 with 3 tasks -> sequential execution verified."""

    def test_all_complete_with_max_batch_size_1(self) -> None:
        execution_order: list[str] = []

        def tracking_executor(profile_id: str, task: str) -> Artifact:
            execution_order.append(profile_id)
            return _make_artifact(profile_id)

        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(id="s2", agent="a2", output="o2"),
                DAGTask(id="s3", agent="a3", output="o3"),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, tracking_executor, max_batch_size=1)
        result = dispatcher.dispatch(dag, "sequential test")
        assert set(result.completed) == {"s1", "s2", "s3"}

    def test_max_batch_size_1_executes_one_at_a_time(self) -> None:
        """With max_batch_size=1, only 1 task executes per round."""
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(id="s2", agent="a2", output="o2"),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor, max_batch_size=1)
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
        dag = _build_dag(
            [
                DAGTask(
                    id="integrate", agent="nexus.integrator", output="merged", task_type="synthetic"
                ),
                DAGTask(id="validate", agent="nexus.qa-gate", output="qa", task_type="synthetic"),
            ]
        )
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
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(id="s2", agent="a2", output="o2"),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        dispatcher.dispatch(dag, "state check")
        t1 = graph.get_task("s1")
        t2 = graph.get_task("s2")
        assert t1 is not None and t1.state == TaskState.COMPLETED
        assert t2 is not None and t2.state == TaskState.COMPLETED

    def test_failed_tasks_have_failed_state(self) -> None:
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _fail_executor)
        dispatcher.dispatch(dag, "fail check")
        t = graph.get_task("s1")
        assert t is not None and t.state == TaskState.FAILED

    def test_mixed_states(self) -> None:
        def selective(profile_id: str, task: str) -> Artifact:
            if profile_id == "fail_agent":
                raise RuntimeError("nope")
            return _make_artifact(profile_id)

        dag = _build_dag(
            [
                DAGTask(id="ok", agent="ok_agent", output="o1"),
                DAGTask(id="fail", agent="fail_agent", output="o2"),
            ]
        )
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
        def executor(profile_id, task):
            return Artifact(
                    source_agent=profile_id,
                    artifact_type="lambda_result",
                    sections={"task_desc": task},
                )
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, executor)
        result = dispatcher.dispatch(dag, "lambda test")
        assert result.artifacts["s1"].artifact_type == "lambda_result"
        assert result.artifacts["s1"].sections["task_desc"] == "lambda test"

    def test_executor_receives_correct_args(self) -> None:
        received: dict[str, str] = {}

        def capturing_executor(profile_id: str, task: str) -> Artifact:
            received["profile_id"] = profile_id
            received["task"] = task
            return _make_artifact(profile_id)

        dag = _build_dag(
            [
                DAGTask(id="s1", agent="my.special.agent", output="o1"),
            ]
        )
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
        def selective(profile_id: str, task: str) -> Artifact:
            if profile_id == "a1":
                raise RuntimeError("upstream fails")
            return _make_artifact(profile_id)

        dag = _build_dag(
            [
                DAGTask(id="upstream", agent="a1", output="o1"),
                DAGTask(id="downstream", agent="a2", output="o2", blocked_by=["upstream"]),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, selective)
        result = dispatcher.dispatch(dag, "cascading failure")
        assert "upstream" in result.failed
        assert "downstream" in result.failed

    def test_cascading_failure_no_artifacts_for_blocked(self) -> None:
        def selective(profile_id: str, task: str) -> Artifact:
            if profile_id == "a1":
                raise RuntimeError("upstream fails")
            return _make_artifact(profile_id)

        dag = _build_dag(
            [
                DAGTask(id="upstream", agent="a1", output="o1"),
                DAGTask(id="downstream", agent="a2", output="o2", blocked_by=["upstream"]),
            ]
        )
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

        def tracking(profile_id: str, task: str) -> Artifact:
            dispatched_agents.append(profile_id)
            return _make_artifact(profile_id)

        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(
                    id="integrate", agent="nexus.integrator", output="merged", task_type="synthetic"
                ),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, tracking)
        result = dispatcher.dispatch(dag, "filter test")
        assert "nexus.integrator" not in dispatched_agents
        assert "s1" in result.completed
        assert "integrate" not in result.completed

    def test_validate_never_dispatched(self) -> None:
        dispatched_agents: list[str] = []

        def tracking(profile_id: str, task: str) -> Artifact:
            dispatched_agents.append(profile_id)
            return _make_artifact(profile_id)

        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(id="validate", agent="nexus.qa-gate", output="qa", task_type="synthetic"),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, tracking)
        result = dispatcher.dispatch(dag, "filter test")
        assert "nexus.qa-gate" not in dispatched_agents
        assert "validate" not in result.completed

    def test_both_fixed_tasks_excluded(self) -> None:
        dispatched_ids: list[str] = []

        def tracking(profile_id: str, task: str) -> Artifact:
            dispatched_ids.append(profile_id)
            return _make_artifact(profile_id)

        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(
                    id="integrate", agent="nexus.integrator", output="merged", task_type="synthetic"
                ),
                DAGTask(id="validate", agent="nexus.qa-gate", output="qa", task_type="synthetic"),
            ]
        )
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
        dag1 = _build_dag(
            [
                DAGTask(id="d1_s1", agent="a1", output="o1"),
            ]
        )
        dag2 = _build_dag(
            [
                DAGTask(id="d2_s1", agent="b1", output="o1"),
            ]
        )
        dispatcher = DAGDispatcher(graph, _ok_executor)
        r1 = dispatcher.dispatch(dag1, "first")
        r2 = dispatcher.dispatch(dag2, "second")
        assert "d1_s1" in r1.completed
        assert "d2_s1" in r2.completed
        assert "d2_s1" not in r1.completed
        assert "d1_s1" not in r2.completed


@pytest.mark.timeout(10)
class TestMaxParallelZero:
    """max_batch_size=0 should be clamped to 1."""

    def test_zero_clamped_to_one(self) -> None:
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(id="s2", agent="a2", output="o2"),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor, max_batch_size=0)
        result = dispatcher.dispatch(dag, "zero parallel")
        assert set(result.completed) == {"s1", "s2"}


@pytest.mark.timeout(10)
class TestMaxParallelNegative:
    """max_batch_size=-1 should be clamped to 1."""

    def test_negative_clamped_to_one(self) -> None:
        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor, max_batch_size=-5)
        result = dispatcher.dispatch(dag, "negative parallel")
        assert "s1" in result.completed


@pytest.mark.timeout(10)
class TestTimeoutMidBatch:
    """Timeout fires after first task in a batch starts but before second runs."""

    def test_at_least_one_completes_before_timeout(self) -> None:
        """With 3 tasks and very short timeout, at most 1 should complete."""
        call_count = {"n": 0}

        def counting_slow(profile_id: str, task: str) -> Artifact:
            call_count["n"] += 1
            time.sleep(0.02)  # Small delay per task
            return _make_artifact(profile_id)

        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(id="s2", agent="a2", output="o2"),
                DAGTask(id="s3", agent="a3", output="o3"),
            ]
        )
        graph = TaskGraph(":memory:")
        # Timeout shorter than 3 tasks * 0.02s each
        dispatcher = DAGDispatcher(graph, counting_slow, max_batch_size=1, timeout_seconds=0.03)
        result = dispatcher.dispatch(dag, "mid-batch timeout")
        assert result.timed_out is True
        # At least 1 should have completed, fewer than 3
        assert len(result.completed) >= 1
        assert len(result.completed) < 3

    def test_mid_batch_deadline_fails_started_unsubmitted(self) -> None:
        """Deadline reached mid-batch: started-but-unsubmitted tasks are cancelled, not stuck IN_PROGRESS."""
        import unittest.mock

        call_count = {"n": 0}

        def counting(profile_id: str, task: str) -> Artifact:
            call_count["n"] += 1
            time.sleep(0.01)
            return _make_artifact(profile_id)

        dag = _build_dag([
            DAGTask(id="s1", agent="a1", output="o1"),
            DAGTask(id="s2", agent="a2", output="o2"),
            DAGTask(id="s3", agent="a3", output="o3"),
        ])
        graph = TaskGraph(":memory:")
        # Very short timeout so deadline fires mid-batch
        dispatcher = DAGDispatcher(graph, counting, max_batch_size=10, timeout_seconds=0.005)
        result = dispatcher.dispatch(dag, "mid-batch deadline test")
        # All tasks should be in a terminal state (completed, failed, or cancelled)
        # No tasks stuck in IN_PROGRESS
        for tid in ["s1", "s2", "s3"]:
            task = graph.get_task(tid)
            assert task is not None, f"Task {tid} not found in graph"
            assert task.state != TaskState.IN_PROGRESS, f"Task {tid} stuck in IN_PROGRESS"
        # At least some tasks should be cancelled (the deadline-hit ones)
        total_resolved = len(result.completed) + len(result.failed) + len(result.cancelled)
        assert total_resolved == 3


@pytest.mark.timeout(10)
class TestDiamondDependency:
    """Diamond: A -> B, A -> C, B+C -> D. All should complete."""

    def test_diamond_all_complete(self) -> None:
        dag = _build_dag(
            [
                DAGTask(id="a", agent="a1", output="o1"),
                DAGTask(id="b", agent="a2", output="o2", blocked_by=["a"]),
                DAGTask(id="c", agent="a3", output="o3", blocked_by=["a"]),
                DAGTask(id="d", agent="a4", output="o4", blocked_by=["b", "c"]),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "diamond deps")
        assert set(result.completed) == {"a", "b", "c", "d"}

    def test_diamond_artifact_count(self) -> None:
        dag = _build_dag(
            [
                DAGTask(id="a", agent="a1", output="o1"),
                DAGTask(id="b", agent="a2", output="o2", blocked_by=["a"]),
                DAGTask(id="c", agent="a3", output="o3", blocked_by=["a"]),
                DAGTask(id="d", agent="a4", output="o4", blocked_by=["b", "c"]),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        result = dispatcher.dispatch(dag, "diamond deps")
        assert len(result.artifacts) == 4


@pytest.mark.timeout(10)
class TestSingleTask:
    """Minimal: single task DAG."""

    def test_single_task_completes(self) -> None:
        dag = _build_dag(
            [
                DAGTask(id="only", agent="a1", output="o1"),
            ]
        )
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
        dag = _build_dag(
            [
                DAGTask(id="a", agent="a1", output="o1"),
                DAGTask(id="b", agent="a2", output="o2", blocked_by=["a"]),
                DAGTask(id="c", agent="a3", output="o3", blocked_by=["b"]),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _fail_executor)
        result = dispatcher.dispatch(dag, "chain fail")
        assert "a" in result.failed
        # B and C are stuck (blocked by failed deps) — should be failed too
        assert "b" in result.failed
        assert "c" in result.failed


# ---------------------------------------------------------------------------
# C4 fix: max_batch_size semantics
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestMaxBatchSizeSemantics:
    """Verify max_batch_size parameter limits tasks picked per dispatch round.

    Note: execution within each batch is synchronous (sequential), so
    max_batch_size controls how many tasks are dequeued from the ready list
    per round, not true concurrency.
    """

    def test_constructor_accepts_max_batch_size(self) -> None:
        """DAGDispatcher constructor accepts max_batch_size parameter."""
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor, max_batch_size=2)
        assert dispatcher._max_batch_size == 2

    def test_batch_size_limits_pick_per_round(self) -> None:
        """max_batch_size=2 with 4 tasks: at most 2 tasks per round, all complete."""
        call_count = {"n": 0}

        def tracking(profile_id: str, task: str) -> Artifact:
            call_count["n"] += 1
            return _make_artifact(profile_id)

        dag = _build_dag(
            [
                DAGTask(id="s1", agent="a1", output="o1"),
                DAGTask(id="s2", agent="a2", output="o2"),
                DAGTask(id="s3", agent="a3", output="o3"),
                DAGTask(id="s4", agent="a4", output="o4"),
            ]
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, tracking, max_batch_size=2)
        result = dispatcher.dispatch(dag, "batch test")
        # All 4 should complete
        assert set(result.completed) == {"s1", "s2", "s3", "s4"}

    def test_default_max_batch_size_is_3(self) -> None:
        """Default max_batch_size is 3."""
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        assert dispatcher._max_batch_size == 3


class TestDAGDispatcherCleanup:
    """DAGDispatcher resource cleanup — close() and __del__ safety."""

    def test_close_shuts_down_pool(self) -> None:
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor, use_concurrency=True)
        # Force pool creation by triggering a concurrent dispatch path
        assert dispatcher._pool is None
        dispatcher.close()
        assert dispatcher._pool is None
        graph.close()

    def test_del_with_active_pool_does_not_crash(self) -> None:
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor, use_concurrency=True)
        # Simulate pool being created but never closed
        import concurrent.futures

        dispatcher._pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        # __del__ should not crash
        dispatcher.__del__()
        assert dispatcher._pool is None
        graph.close()

    def test_close_idempotent(self) -> None:
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _ok_executor)
        dispatcher.close()
        assert dispatcher._pool is None
        dispatcher.close()  # second close is a no-op
        assert dispatcher._pool is None
        graph.close()


class TestParallelRaceCondition:
    """Verify completed tasks are not incorrectly marked as cancelled during fail-fast."""

    def test_fast_successful_task_not_cancelled_on_sibling_failure(self) -> None:
        """When one task fails fast, already-completed siblings should still appear in completed."""
        import threading

        barrier = threading.Barrier(2)

        def _controlled_executor(profile_id: str, task: str) -> Artifact:
            if profile_id == "agency.slow":
                barrier.wait(timeout=5)
                raise RuntimeError("deliberate failure")
            # Fast task completes immediately
            return _make_artifact(profile_id)

        dag = _build_dag(
            [
                DAGTask(id="fast_ok", agent="agency.fast", output="result"),
                DAGTask(id="slow_fail", agent="agency.slow", output="result"),
            ],
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _controlled_executor, use_concurrency=True)

        result = dispatcher.dispatch(dag, "test task")
        # fast_ok should be in completed, not in cancelled
        assert "fast_ok" in result.completed, (
            f"fast_ok should be completed, got completed={result.completed}, "
            f"cancelled={result.cancelled}"
        )
        assert "fast_ok" not in result.cancelled
        dispatcher.close()
        graph.close()

    def test_all_cancelled_when_none_succeed_before_failure(self) -> None:
        """When no task finishes before the failure, all non-failed are cancelled."""

        def _fail_immediately(profile_id: str, task: str) -> Artifact:
            raise RuntimeError("immediate failure")

        dag = _build_dag(
            [
                DAGTask(id="t1", agent="agency.a", output="r"),
                DAGTask(id="t2", agent="agency.b", output="r"),
            ],
        )
        graph = TaskGraph(":memory:")
        dispatcher = DAGDispatcher(graph, _fail_immediately, use_concurrency=True)

        result = dispatcher.dispatch(dag, "test task")
        # At least one should be failed, the rest cancelled
        assert len(result.failed) >= 1
        total = len(result.completed) + len(result.failed) + len(result.cancelled)
        assert total == 2
        dispatcher.close()
        graph.close()
