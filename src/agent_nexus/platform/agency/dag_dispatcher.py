"""DAGDispatcher — bridges CompositionDAG to TaskGraph + runtime execution.

Converts the agency pipeline's CompositionDAG (in-memory) into TaskGraph
entries (SQLite-backed) and dispatches execution via either:
- ``ProcessManager`` for real subprocess IPC, or
- ``ExpertExecutor`` protocol for in-process execution (testing/dev).

This is the G4 bridge connecting agency DAG generation to the orchestration
runtime (TaskGraph + ProcessManager).
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Protocol

from agent_nexus.models.task import TaskItem, TaskState
from agent_nexus.platform.orchestration.task_graph import TaskGraph

from .integrator import Artifact
from .planner import CompositionDAG, DAGTask

logger = logging.getLogger(__name__)


def _safe_fail(graph: TaskGraph, task_id: str) -> None:
    """Mark a task as failed, suppressing invalid-state errors."""
    with contextlib.suppress(ValueError, RuntimeError):
        graph.fail_task(task_id)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class ExpertExecutor(Protocol):
    """Callable that produces an Artifact for a given agent + task."""

    def __call__(self, profile_id: str, task: str) -> Artifact: ...


class ArtifactSink(Protocol):
    """Callable that receives artifacts as they complete."""

    def __call__(self, task_id: str, artifact: Artifact) -> None: ...


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class DispatchResult:
    """Outcome of a DAG dispatch run."""

    artifacts: dict[str, Artifact] = field(default_factory=dict)
    """task_id → Artifact for completed specialist tasks."""

    completed: list[str] = field(default_factory=list)
    """IDs of tasks that completed successfully."""

    failed: list[str] = field(default_factory=list)
    """IDs of tasks that failed."""

    cancelled: list[str] = field(default_factory=list)
    """IDs of tasks cancelled before execution (e.g. sibling fail-fast)."""

    errors: dict[str, str] = field(default_factory=dict)
    """task_id → error message for failed tasks."""

    timed_out: bool = False
    """Whether the dispatch hit its timeout limit."""

    hit_iteration_limit: bool = False
    """Whether the dispatch exceeded its iteration guard (indicates a possible bug)."""


# ---------------------------------------------------------------------------
# DAGTask → TaskItem conversion
# ---------------------------------------------------------------------------


def dag_task_to_task_item(
    dag_task: DAGTask,
    task_description: str,
) -> TaskItem:
    """Convert a DAGTask from the agency planner to an orchestration TaskItem.

    Parameters
    ----------
    dag_task:
        The planner's task definition.
    task_description:
        The human-readable task goal (copied from the original user request).
    """
    return TaskItem(
        id=dag_task.id,
        description=task_description,
        agent=dag_task.agent,
        blocked_by=list(dag_task.blocked_by),
        vars={"output_contract": dag_task.output},
    )


def load_dag_into_graph(
    dag: CompositionDAG,
    task_description: str,
    graph: TaskGraph,
) -> list[TaskItem]:
    """Load all specialist tasks from a CompositionDAG into a TaskGraph.

    Only loads specialist tasks (skips ``integrate`` and ``validate`` — those
    are handled by the Integrator and QAGate outside the dispatcher).

    Returns the list of created TaskItems for inspection.
    """
    specialist_ids = {t.id for t in dag.specialist_tasks}
    items: list[TaskItem] = []
    for dag_task in dag.tasks:
        if dag_task.id not in specialist_ids:
            continue
        # Strip blocked_by refs to non-specialist tasks (they're handled
        # externally by Integrator / QAGate outside the dispatcher).
        # Filter without mutating the shared DAGTask dataclass.
        filtered_deps = [dep for dep in dag_task.blocked_by if dep in specialist_ids]
        item = TaskItem(
            id=dag_task.id,
            description=task_description,
            agent=dag_task.agent,
            blocked_by=filtered_deps,
            vars={"output_contract": dag_task.output},
        )
        items.append(item)

    new_items: list[TaskItem] = []
    if items:
        # Skip tasks that already exist in the graph (idempotent for re-dispatch)
        new_items = [item for item in items if graph.get_task(item.id) is None]
        if new_items:
            graph.add_tasks(new_items)
    return new_items


# ---------------------------------------------------------------------------
# DAGDispatcher
# ---------------------------------------------------------------------------


class DAGDispatcher:
    """Dispatches a CompositionDAG through the orchestration runtime.

    Execution modes:
    1. **In-process** (default): Uses an ``ExpertExecutor`` callable to
       produce artifacts. Suitable for testing and when agents aren't
       running as subprocesses.
    2. **IPC** (via ProcessManager): Dispatches to real agent subprocesses
       via stdin/stdout JSON-lines. Not yet wired — placeholder for when
       ProcessManager integration is needed.

    The dispatcher uses ``TaskGraph`` for state tracking (pending →
    in_progress → completed/failed) and respects ``blocked_by`` edges
    for topological ordering with configurable batch size.
    """

    def __init__(
        self,
        graph: TaskGraph,
        executor: ExpertExecutor,
        max_batch_size: int = 3,
        timeout_seconds: float | None = None,
        concurrent: bool = False,
    ) -> None:
        self._graph = graph
        self._executor = executor
        self._max_batch_size = max(1, max_batch_size)
        self._timeout_seconds = timeout_seconds
        self._concurrent = concurrent
        # Persistent thread pool for concurrent execution (reused across batches)
        self._pool: concurrent.futures.ThreadPoolExecutor | None = None

    def _get_pool(self) -> concurrent.futures.ThreadPoolExecutor:
        """Lazy-init the persistent thread pool."""
        if self._pool is None:
            self._pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=self._max_batch_size,
            )
        return self._pool

    def close(self) -> None:
        """Shut down the thread pool, waiting for in-flight work."""
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    @property
    def graph(self) -> TaskGraph:
        """The underlying TaskGraph for inspection."""
        return self._graph

    def _run_executor(
        self,
        task_item: TaskItem,
        task_description: str,
    ) -> tuple[Artifact | None, str | None]:
        """Execute a single task and return (artifact, error). Thread-safe.

        Does NOT touch the graph — state mutations are the caller's
        responsibility so they always happen on the main thread.
        """
        try:
            artifact = self._executor(task_item.agent, task_description)
            return artifact, None
        except Exception as exc:
            logger.exception(
                "Executor failed for task '%s' (agent '%s')",
                task_item.id,
                task_item.agent,
            )
            return None, str(exc)

    def dispatch(self, dag: CompositionDAG, task_description: str) -> DispatchResult:
        """Execute specialist tasks from *dag* in topological order.

        Parameters
        ----------
        dag:
            The composition DAG to execute.
        task_description:
            The original task description (passed to each executor call).

        Returns
        -------
        DispatchResult
            Artifacts and completion status for each specialist task.
        """
        # Load tasks into graph (idempotent — skips already-existing tasks)
        load_dag_into_graph(dag, task_description, self._graph)

        result = DispatchResult()
        deadline = (
            time.monotonic() + self._timeout_seconds if self._timeout_seconds is not None else None
        )

        specialist_ids = {t.id for t in dag.specialist_tasks}

        # Safety guard: each iteration processes at least one task, so
        # len(tasks)*3 is a generous upper bound.  If exceeded something
        # has gone wrong (e.g. a state machine bug causing a livelock).
        max_iterations = max(len(dag.tasks) * 3, 1)
        iteration = 0

        # Execute in rounds: pick ready tasks, dispatch up to max_parallel,
        # mark complete, repeat until done or stuck.
        while iteration < max_iterations:
            iteration += 1

            if deadline is not None and time.monotonic() > deadline:
                result.timed_out = True
                logger.warning("DAGDispatch timed out after %ss", self._timeout_seconds)
                break

            ready = self._graph.get_ready_tasks()
            # Filter to specialist tasks only (skip integrate/validate)
            ready_specialists = [t for t in ready if t.id in specialist_ids]

            if not ready_specialists:
                # Check if there are still pending/blocked specialist tasks
                all_specialists_in_graph = [self._graph.get_task(tid) for tid in specialist_ids]
                pending_or_in_progress = [
                    t
                    for t in all_specialists_in_graph
                    if t is not None and t.state in (TaskState.PENDING, TaskState.IN_PROGRESS)
                ]
                if not pending_or_in_progress:
                    break  # All done

                # Distinguish: tasks still IN_PROGRESS vs truly stuck PENDING
                in_progress = [
                    t for t in pending_or_in_progress if t.state == TaskState.IN_PROGRESS
                ]
                if in_progress:
                    # In synchronous mode, stale IN_PROGRESS tasks from a prior
                    # crash would cause an infinite loop. Fail them instead.
                    for t in in_progress:
                        _safe_fail(self._graph, t.id)
                        result.failed.append(t.id)
                    break

                # Only PENDING tasks remain but none are ready → blocked by failed deps
                for t in pending_or_in_progress:
                    _safe_fail(self._graph, t.id)
                    result.failed.append(t.id)
                break

            # Dispatch up to max_batch_size tasks per round
            batch = ready_specialists[: self._max_batch_size]

            if self._concurrent and len(batch) > 1:
                # Concurrent execution within batch using persistent pool
                per_task_timeout = (
                    self._timeout_seconds if self._timeout_seconds is not None else None
                )
                pool = self._get_pool()
                futures: dict[
                    concurrent.futures.Future[tuple[Artifact | None, str | None]],
                    TaskItem,
                ] = {}
                for task_item in batch:
                    if deadline is not None and time.monotonic() > deadline:
                        result.timed_out = True
                        break
                    self._graph.start_task(task_item.id)
                    future = pool.submit(self._run_executor, task_item, task_description)
                    futures[future] = task_item

                # Collect results — fail-fast on first error
                for future in concurrent.futures.as_completed(futures, timeout=per_task_timeout):
                    task_item = futures[future]
                    try:
                        artifact, error = future.result()
                    except Exception as exc:
                        error = str(exc)
                        artifact = None

                    if error is None and artifact is not None:
                        self._graph.complete_task(task_item.id)
                        result.artifacts[task_item.id] = artifact
                        result.completed.append(task_item.id)
                    else:
                        _safe_fail(self._graph, task_item.id)
                        result.errors[task_item.id] = error or "unknown error"
                        result.failed.append(task_item.id)
                        # Cancel remaining futures — fail fast
                        for f in futures:
                            f.cancel()
                        break

                # Mark cancelled tasks
                for _f, ti in futures.items():
                    if ti.id not in result.completed and ti.id not in result.failed:
                        _safe_fail(self._graph, ti.id)
                        result.cancelled.append(ti.id)
                        result.errors[ti.id] = "cancelled (sibling task failed)"
            else:
                # Sequential execution (backward compatible)
                started_in_batch: list[str] = []
                for task_item in batch:
                    if deadline is not None and time.monotonic() > deadline:
                        result.timed_out = True
                        # Fail any tasks we started in this batch but didn't complete
                        for tid in started_in_batch:
                            task = self._graph.get_task(tid)
                            if task is not None and task.state == TaskState.IN_PROGRESS:
                                _safe_fail(self._graph, tid)
                                result.failed.append(tid)
                        break

                    self._graph.start_task(task_item.id)
                    started_in_batch.append(task_item.id)
                    artifact, error = self._run_executor(task_item, task_description)
                    if error is None and artifact is not None:
                        self._graph.complete_task(task_item.id)
                        result.artifacts[task_item.id] = artifact
                        result.completed.append(task_item.id)
                    else:
                        _safe_fail(self._graph, task_item.id)
                        result.errors[task_item.id] = error or "unknown error"
                        result.failed.append(task_item.id)
                        break  # Fail-fast: don't start more tasks in batch

            # Fail-fast: stop dispatching after any task failure or cancellation
            if result.failed or result.cancelled:
                break

        # If the loop was terminated by the max_iterations guard (not a normal
        # break), mark the result as timed_out so callers know it didn't finish.
        if iteration >= max_iterations:
            result.hit_iteration_limit = True
            result.timed_out = True
            logger.warning(
                "DAGDispatch exceeded max_iterations (%d) for %d tasks — "
                "possible state machine bug",
                max_iterations,
                len(dag.tasks),
            )

        # Clean up any tasks left in IN_PROGRESS after loop exit (e.g. mid-batch timeout)
        failed_set = set(result.failed)
        for tid in specialist_ids:
            task = self._graph.get_task(tid)
            if task is not None and task.state == TaskState.IN_PROGRESS:
                _safe_fail(self._graph, tid)
                if tid not in failed_set:
                    result.failed.append(tid)
                    failed_set.add(tid)

        # Clean up orphaned PENDING tasks. Loop until stable to handle
        # transitive failure chains (e.g. A→B→C where failing B must also fail C).
        changed = True
        while changed:
            changed = False
            for tid in specialist_ids:
                task = self._graph.get_task(tid)
                if task is None or task.state != TaskState.PENDING:
                    continue
                deps = task.blocked_by
                if not deps:
                    # Independent task never started (e.g. after fail-fast)
                    _safe_fail(self._graph, tid)
                    if tid not in failed_set:
                        result.failed.append(tid)
                        failed_set.add(tid)
                    changed = True
                    continue
                dep_tasks = [self._graph.get_task(d) for d in deps]
                all_done = all(
                    t is not None and t.state in (TaskState.COMPLETED, TaskState.FAILED)
                    for t in dep_tasks
                )
                if all_done:
                    _safe_fail(self._graph, tid)
                    if tid not in failed_set:
                        result.failed.append(tid)
                        failed_set.add(tid)
                    changed = True

        return result
