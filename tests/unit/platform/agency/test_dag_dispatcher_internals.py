"""Tests for DAGDispatcher internal methods: _run_dispatch_loop, _drain_single_future,
_collect_futures, _drain_remaining.

These methods are the synchronous core of the dispatch engine and are tested
directly (without going through the public dispatch() entry point) to exercise
edge cases in isolation.
"""

from __future__ import annotations

import time
from concurrent.futures import Future

import pytest

from agent_nexus.models.task import TaskItem, TaskState
from agent_nexus.platform.agency.dag_dispatcher import (
    DAGDispatcher,
    DispatchResult,
)
from agent_nexus.platform.agency.integrator import Artifact
from agent_nexus.platform.orchestration.task_graph import TaskGraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_artifact(agent: str, label: str = "ok") -> Artifact:
    return Artifact(source_agent=agent, artifact_type="test", sections={"label": label})


def _make_task(tid: str, agent: str | None = None, blocked_by: list[str] | None = None) -> TaskItem:
    return TaskItem(
        id=tid,
        description="test task",
        agent=agent or f"agent.{tid}",
        blocked_by=blocked_by or [],
    )


def _make_dispatcher(graph: TaskGraph, **kwargs) -> DAGDispatcher:
    return DAGDispatcher(graph, lambda profile_id, task, **kw: _make_artifact(profile_id), **kwargs)  # type: ignore[arg-type]


def _insert_and_start(graph: TaskGraph, tid: str, agent: str | None = None) -> TaskItem:
    ti = _make_task(tid, agent)
    graph.add_tasks([ti])
    graph.start_task(tid)
    task = graph.get_task(tid)
    assert task is not None
    return task


# ---------------------------------------------------------------------------
# TestDrainSingleFuture
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestDrainSingleFuture:
    """Tests for DAGDispatcher._drain_single_future."""

    def test_completed_future_succeeds(self) -> None:
        graph = TaskGraph(":memory:")
        ti = _insert_and_start(graph, "t1")
        dispatcher = _make_dispatcher(graph)

        f: Future = Future()
        f.set_result((_make_artifact("a1"), None))
        result = DispatchResult()

        assert dispatcher._drain_single_future(f, ti, result) is True
        assert "t1" in result.completed
        task = graph.get_task("t1")
        assert task is not None
        assert task.state == TaskState.COMPLETED

    def test_future_with_error_fails(self) -> None:
        graph = TaskGraph(":memory:")
        ti = _insert_and_start(graph, "t1")
        dispatcher = _make_dispatcher(graph)

        f: Future = Future()
        f.set_result((None, "some error"))
        result = DispatchResult()

        assert dispatcher._drain_single_future(f, ti, result) is False
        assert "t1" in result.cancelled

    def test_future_exception_captured(self) -> None:
        graph = TaskGraph(":memory:")
        ti = _insert_and_start(graph, "t1")
        dispatcher = _make_dispatcher(graph)

        f: Future = Future()
        f.set_exception(RuntimeError("boom"))
        result = DispatchResult()

        assert dispatcher._drain_single_future(f, ti, result) is False
        # The exception is caught as "executor error: boom", but the method
        # falls through to the cancelled path which overwrites the error.
        assert "t1" in result.cancelled

    def test_cancelled_future_fails(self) -> None:
        graph = TaskGraph(":memory:")
        ti = _insert_and_start(graph, "t1")
        dispatcher = _make_dispatcher(graph)

        f: Future = Future()
        f.cancel()
        result = DispatchResult()

        assert dispatcher._drain_single_future(f, ti, result) is False
        assert "t1" in result.cancelled

    def test_not_done_future_fails(self) -> None:
        graph = TaskGraph(":memory:")
        ti = _insert_and_start(graph, "t1")
        dispatcher = _make_dispatcher(graph)

        f: Future = Future()
        result = DispatchResult()

        assert dispatcher._drain_single_future(f, ti, result) is False
        assert "t1" in result.cancelled

    def test_null_artifact_fails(self) -> None:
        graph = TaskGraph(":memory:")
        ti = _insert_and_start(graph, "t1")
        dispatcher = _make_dispatcher(graph)

        f: Future = Future()
        f.set_result((None, None))
        result = DispatchResult()

        assert dispatcher._drain_single_future(f, ti, result) is False
        assert "t1" in result.cancelled

    def test_updates_result_artifacts(self) -> None:
        graph = TaskGraph(":memory:")
        ti = _insert_and_start(graph, "t1")
        dispatcher = _make_dispatcher(graph)

        artifact = _make_artifact("a1", label="special")
        f: Future = Future()
        f.set_result((artifact, None))
        result = DispatchResult()

        dispatcher._drain_single_future(f, ti, result)
        assert result.artifacts["t1"] is artifact
        assert result.artifacts["t1"].sections["label"] == "special"

    def test_canceled_task_gets_error_message(self) -> None:
        graph = TaskGraph(":memory:")
        ti = _insert_and_start(graph, "t1")
        dispatcher = _make_dispatcher(graph)

        f: Future = Future()
        result = DispatchResult()

        dispatcher._drain_single_future(f, ti, result)
        assert result.errors["t1"] == "cancelled (sibling task failed)"


# ---------------------------------------------------------------------------
# TestCollectFutures
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestCollectFutures:
    """Tests for DAGDispatcher._collect_futures."""

    def test_all_succeed(self) -> None:
        graph = TaskGraph(":memory:")
        t1 = _insert_and_start(graph, "t1")
        t2 = _insert_and_start(graph, "t2")
        dispatcher = _make_dispatcher(graph)

        f1: Future = Future()
        f1.set_result((_make_artifact("a1"), None))
        f2: Future = Future()
        f2.set_result((_make_artifact("a2"), None))

        futures = {f1: t1, f2: t2}
        result = DispatchResult()
        dispatcher._collect_futures(futures, result)

        assert set(result.completed) == {"t1", "t2"}
        assert result.failed == []

    def test_first_failure_cancels_rest(self) -> None:
        graph = TaskGraph(":memory:")
        t1 = _insert_and_start(graph, "t1")
        t2 = _insert_and_start(graph, "t2")
        dispatcher = _make_dispatcher(graph)

        f1: Future = Future()
        f1.set_result((None, "bad"))
        f2: Future = Future()
        # f2 is left unresolved so it gets cancelled when f1 triggers fail-fast

        futures = {f1: t1, f2: t2}
        result = DispatchResult()
        dispatcher._collect_futures(futures, result)

        assert "t1" in result.failed
        assert f2.cancelled()

    def test_exception_in_future(self) -> None:
        graph = TaskGraph(":memory:")
        t1 = _insert_and_start(graph, "t1")
        dispatcher = _make_dispatcher(graph)

        f1: Future = Future()
        f1.set_exception(ValueError("oops"))

        futures = {f1: t1}
        result = DispatchResult()
        dispatcher._collect_futures(futures, result)

        assert "t1" in result.failed
        assert "oops" in result.errors["t1"]

    def test_timeout_cancels_all(self) -> None:
        graph = TaskGraph(":memory:")
        t1 = _insert_and_start(graph, "t1")
        t2 = _insert_and_start(graph, "t2")
        dispatcher = _make_dispatcher(graph, timeout_seconds=0.001)

        f1: Future = Future()
        f2: Future = Future()
        # Futures never set — as_completed will raise TimeoutError

        futures = {f1: t1, f2: t2}
        result = DispatchResult()
        dispatcher._collect_futures(futures, result)

        assert result.timed_out is True
        assert f1.cancelled()
        assert f2.cancelled()

    def test_empty_futures(self) -> None:
        graph = TaskGraph(":memory:")
        dispatcher = _make_dispatcher(graph)

        futures: dict[Future, TaskItem] = {}
        result = DispatchResult()
        dispatcher._collect_futures(futures, result)

        assert result.completed == []
        assert result.failed == []
        assert result.timed_out is False

    def test_mixed_results_first_fails(self) -> None:
        graph = TaskGraph(":memory:")
        t1 = _insert_and_start(graph, "t1")
        t2 = _insert_and_start(graph, "t2")
        dispatcher = _make_dispatcher(graph)

        f1: Future = Future()
        f1.set_result((None, "fail first"))
        f2: Future = Future()
        # f2 is unresolved — will be cancelled by fail-fast, never collected

        futures = {f1: t1, f2: t2}
        result = DispatchResult()
        dispatcher._collect_futures(futures, result)

        assert "t1" in result.failed
        assert "t2" not in result.completed

    def test_null_artifact_counts_as_failure(self) -> None:
        graph = TaskGraph(":memory:")
        t1 = _insert_and_start(graph, "t1")
        dispatcher = _make_dispatcher(graph)

        f1: Future = Future()
        f1.set_result((None, None))

        futures = {f1: t1}
        result = DispatchResult()
        dispatcher._collect_futures(futures, result)

        assert "t1" in result.failed
        assert result.errors["t1"] == "unknown error"


# ---------------------------------------------------------------------------
# TestDrainRemaining
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestDrainRemaining:
    """Tests for DAGDispatcher._drain_remaining."""

    def test_drains_unprocessed_futures(self) -> None:
        graph = TaskGraph(":memory:")
        t1 = _insert_and_start(graph, "t1")
        t2 = _insert_and_start(graph, "t2")
        dispatcher = _make_dispatcher(graph)

        f1: Future = Future()
        f1.set_result((_make_artifact("a1"), None))
        f2: Future = Future()
        f2.set_result((_make_artifact("a2"), None))

        result = DispatchResult()
        # Neither t1 nor t2 is in completed/failed — both should be drained
        futures = {f1: t1, f2: t2}
        dispatcher._drain_remaining(futures, result)

        assert "t1" in result.completed
        assert "t2" in result.completed

    def test_skips_already_completed(self) -> None:
        graph = TaskGraph(":memory:")
        t1 = _insert_and_start(graph, "t1")
        t2 = _insert_and_start(graph, "t2")
        dispatcher = _make_dispatcher(graph)

        f1: Future = Future()
        f1.set_result((_make_artifact("a1"), None))
        f2: Future = Future()
        f2.set_result((_make_artifact("a2"), None))

        result = DispatchResult()
        result.completed.append("t1")
        result.artifacts["t1"] = _make_artifact("a1")

        futures = {f1: t1, f2: t2}
        dispatcher._drain_remaining(futures, result)

        # t1 skipped, t2 processed
        assert result.completed.count("t1") == 1
        assert "t2" in result.completed

    def test_skips_already_failed(self) -> None:
        graph = TaskGraph(":memory:")
        t1 = _insert_and_start(graph, "t1")
        t2 = _insert_and_start(graph, "t2")
        dispatcher = _make_dispatcher(graph)

        f1: Future = Future()
        f2: Future = Future()
        f2.set_result((_make_artifact("a2"), None))

        result = DispatchResult()
        result.failed.append("t1")

        futures = {f1: t1, f2: t2}
        dispatcher._drain_remaining(futures, result)

        # t1 skipped (in failed), t2 processed
        assert result.failed.count("t1") == 1
        assert "t2" in result.completed

    def test_mixed_state(self) -> None:
        graph = TaskGraph(":memory:")
        t1 = _insert_and_start(graph, "t1")
        t2 = _insert_and_start(graph, "t2")
        t3 = _insert_and_start(graph, "t3")
        dispatcher = _make_dispatcher(graph)

        f1: Future = Future()
        f2: Future = Future()
        f3: Future = Future()
        f3.set_result((_make_artifact("a3"), None))

        result = DispatchResult()
        result.completed.append("t1")
        result.failed.append("t2")

        futures = {f1: t1, f2: t2, f3: t3}
        dispatcher._drain_remaining(futures, result)

        assert result.completed.count("t1") == 1
        assert result.failed.count("t2") == 1
        assert "t3" in result.completed


# ---------------------------------------------------------------------------
# TestRunDispatchLoop
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestRunDispatchLoop:
    """Tests for DAGDispatcher._run_dispatch_loop."""

    def test_completes_normally(self) -> None:
        graph = TaskGraph(":memory:")
        t1 = _make_task("t1")
        t2 = _make_task("t2")
        graph.add_tasks([t1, t2])

        dispatcher = _make_dispatcher(graph)
        result = DispatchResult()
        specialist_ids = {"t1", "t2"}

        completed = dispatcher._run_dispatch_loop(
            specialist_ids, "test task", deadline=None, max_iterations=100, result=result,
        )
        assert completed is True
        assert set(result.completed) == {"t1", "t2"}

    def test_returns_false_on_deadline(self) -> None:
        graph = TaskGraph(":memory:")
        t1 = _make_task("t1")
        graph.add_tasks([t1])

        dispatcher = _make_dispatcher(graph)
        result = DispatchResult()

        deadline = time.monotonic() - 1  # already expired
        completed = dispatcher._run_dispatch_loop(
            {"t1"}, "test task", deadline=deadline, max_iterations=100, result=result,
        )
        assert completed is False
        assert result.timed_out is True

    def test_returns_false_on_terminal_result(self) -> None:
        graph = TaskGraph(":memory:")
        t1 = _make_task("t1")
        graph.add_tasks([t1])

        def fail_executor(profile_id: str, task: str, **kw: object) -> Artifact:
            raise RuntimeError("boom")

        dispatcher = DAGDispatcher(graph, fail_executor)  # type: ignore[arg-type]
        result = DispatchResult()

        completed = dispatcher._run_dispatch_loop(
            {"t1"}, "test task", deadline=None, max_iterations=100, result=result,
        )
        assert completed is False
        assert result.is_terminal is True

    def test_respects_max_iterations(self) -> None:
        graph = TaskGraph(":memory:")
        t1 = _make_task("t1")
        graph.add_tasks([t1])

        dispatcher = _make_dispatcher(graph)
        result = DispatchResult()

        completed = dispatcher._run_dispatch_loop(
            {"t1"}, "test task", deadline=None, max_iterations=0, result=result,
        )
        # max_iterations=0 means loop never enters, returns False
        assert completed is False

    def test_empty_graph_returns_true(self) -> None:
        graph = TaskGraph(":memory:")
        dispatcher = _make_dispatcher(graph)
        result = DispatchResult()

        completed = dispatcher._run_dispatch_loop(
            set(), "test task", deadline=None, max_iterations=100, result=result,
        )
        # No specialist_ids → no work → _no_more_work returns True
        assert completed is True

    def test_respects_max_batch_size(self) -> None:
        graph = TaskGraph(":memory:")
        for i in range(5):
            graph.add_tasks([_make_task(f"t{i}")])

        batch_sizes: list[int] = []

        def tracking_executor(profile_id: str, task: str, **kw: object) -> Artifact:
            return _make_artifact(profile_id)

        dispatcher = DAGDispatcher(graph, tracking_executor, max_batch_size=2)  # type: ignore[arg-type]
        result = DispatchResult()

        # Patch _dispatch_batch to record batch sizes
        orig_dispatch_batch = dispatcher._dispatch_batch

        def patched_dispatch_batch(batch, task_description, deadline, result):
            batch_sizes.append(len(batch))
            orig_dispatch_batch(batch, task_description, deadline, result)

        dispatcher._dispatch_batch = patched_dispatch_batch

        dispatcher._run_dispatch_loop(
            {"t0", "t1", "t2", "t3", "t4"},
            "test task",
            deadline=None,
            max_iterations=100,
            result=result,
        )

        # With 5 tasks and max_batch_size=2, batches should be at most 2
        assert all(bs <= 2 for bs in batch_sizes)
        assert set(result.completed) == {"t0", "t1", "t2", "t3", "t4"}
