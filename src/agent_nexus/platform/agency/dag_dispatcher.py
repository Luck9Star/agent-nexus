"""DAGDispatcher — bridges CompositionDAG to TaskGraph + runtime execution.

Converts the agency pipeline's CompositionDAG (in-memory) into TaskGraph
entries (SQLite-backed) and dispatches execution via either:
- ``ProcessManager`` for real subprocess IPC, or
- ``ExpertExecutor`` protocol for in-process execution (testing/dev).

This is the G4 bridge connecting agency DAG generation to the orchestration
runtime (TaskGraph + ProcessManager).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

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

    def __call__(
        self,
        profile_id: str,
        task: str,
        *,
        upstream_artifacts: list[Any] | None = None,
    ) -> Artifact: ...


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
        max_parallel: int | None = None,
        timeout_seconds: float | None = None,
        concurrent: bool = False,
        *,
        max_batch_size: int | None = None,
    ) -> None:
        # Backward compat: max_batch_size is the old name for max_parallel.
        # max_parallel takes precedence when both are provided.
        if max_parallel is not None:
            effective = max_parallel
        elif max_batch_size is not None:
            effective = max_batch_size
        else:
            effective = 3
        self._graph = graph
        self._executor = executor
        self._max_batch_size = max(1, effective)
        self._timeout_seconds = timeout_seconds
        self._concurrent = concurrent
        # Persistent thread pool for concurrent execution (reused across batches)
        self._pool: concurrent.futures.ThreadPoolExecutor | None = None

    def __del__(self) -> None:
        """Best-effort cleanup of the thread pool on garbage collection."""
        if self._pool is not None:
            self._pool.shutdown(wait=False)
            self._pool = None

    def _get_pool(self) -> concurrent.futures.ThreadPoolExecutor:
        """Lazy-init the persistent thread pool."""
        if self._pool is None:
            self._pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=self._max_batch_size,
            )
        return self._pool

    def _collect_upstream_artifacts(
        self,
        task_item: TaskItem,
        artifacts: dict[str, Artifact],
    ) -> list[Artifact] | None:
        """Collect artifacts from upstream tasks (via blocked_by edges).

        Returns ``None`` when the task has no upstream dependencies (backward
        compatible — signals the executor to use default behaviour).
        """
        if not task_item.blocked_by:
            return None

        upstream = [artifacts[dep] for dep in task_item.blocked_by if dep in artifacts]
        return upstream if upstream else None

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
        upstream_artifacts: list[Artifact] | None = None,
    ) -> tuple[Artifact | None, str | None]:
        """Execute a single task and return (artifact, error). Thread-safe.

        Does NOT touch the graph — state mutations are the caller's
        responsibility so they always happen on the main thread.
        """
        try:
            artifact = self._executor(
                task_item.agent,
                task_description,
                upstream_artifacts=upstream_artifacts,
            )
            return artifact, None
        except TypeError as exc:
            # Backward compatibility: if the executor doesn't accept
            # upstream_artifacts (old-style two-arg callable), retry
            # without the keyword argument.
            if "upstream_artifacts" in str(exc):
                logger.debug(
                    "Executor for task '%s' does not accept upstream_artifacts, "
                    "falling back to two-arg call",
                    task_item.id,
                )
                try:
                    artifact = self._executor(task_item.agent, task_description)  # type: ignore[call-arg]
                    return artifact, None
                except Exception as inner_exc:
                    logger.exception(
                        "Executor failed for task '%s' (agent '%s')",
                        task_item.id,
                        task_item.agent,
                    )
                    return None, str(inner_exc)
            logger.exception(
                "Executor failed for task '%s' (agent '%s')",
                task_item.id,
                task_item.agent,
            )
            return None, str(exc)
        except Exception as exc:
            logger.exception(
                "Executor failed for task '%s' (agent '%s')",
                task_item.id,
                task_item.agent,
            )
            return None, str(exc)

    def _no_more_work(self, specialist_ids: set[str], result: DispatchResult) -> bool:
        """Handle the "no ready specialists" state inside the dispatch loop.

        Returns True if the dispatch loop should break, False if it should
        continue (e.g. after failing stale tasks).
        """
        all_specialists = [self._graph.get_task(tid) for tid in specialist_ids]
        pending_or_running = [
            t for t in all_specialists
            if t is not None and t.state in (TaskState.PENDING, TaskState.IN_PROGRESS)
        ]

        if not pending_or_running:
            return True  # All done

        # Stale IN_PROGRESS tasks → fail them and stop
        in_progress = [t for t in pending_or_running if t.state == TaskState.IN_PROGRESS]
        if in_progress:
            for t in in_progress:
                _safe_fail(self._graph, t.id)
                result.failed.append(t.id)
            return True

        # Only PENDING tasks remain but none are ready → blocked by failed deps
        for t in pending_or_running:
            _safe_fail(self._graph, t.id)
            result.failed.append(t.id)
        return True

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
        load_dag_into_graph(dag, task_description, self._graph)

        result = DispatchResult()
        deadline = (
            time.monotonic() + self._timeout_seconds if self._timeout_seconds is not None else None
        )

        specialist_ids = {t.id for t in dag.specialist_tasks}
        max_iterations = max(len(dag.tasks) * 3, 1)

        completed_normally = False
        iteration = 0
        while iteration < max_iterations:
            iteration += 1

            if deadline is not None and time.monotonic() > deadline:
                result.timed_out = True
                logger.warning("DAGDispatch timed out after %ss", self._timeout_seconds)
                break

            ready_specialists = [
                t for t in self._graph.get_ready_tasks() if t.id in specialist_ids
            ]

            if not ready_specialists:
                if self._no_more_work(specialist_ids, result):
                    completed_normally = True
                    break

            else:
                batch = ready_specialists[: self._max_batch_size]
                if self._concurrent and len(batch) > 1:
                    self._dispatch_parallel(batch, task_description, deadline, result)
                else:
                    self._dispatch_sequential(batch, task_description, deadline, result)

                if result.failed or result.cancelled:
                    break

        if not completed_normally and iteration >= max_iterations:
            result.hit_iteration_limit = True
            result.timed_out = True
            logger.warning(
                "DAGDispatch exceeded max_iterations (%d) for %d tasks — "
                "possible state machine bug",
                max_iterations,
                len(dag.tasks),
            )

        self._cleanup_stale_tasks(specialist_ids, result)

        return result

    async def adispatch(self, dag: CompositionDAG, task_description: str) -> DispatchResult:
        """Async wrapper for :meth:`dispatch` — prevents event loop blocking.

        When the agency pipeline runs inside an ``asyncio`` event loop, calling
        the synchronous :meth:`dispatch` would block the loop because it uses
        ``ThreadPoolExecutor`` internally.  This wrapper offloads the work to a
        thread via ``asyncio.to_thread`` so the event loop stays responsive.

        Parameters are identical to :meth:`dispatch`.
        """
        return await asyncio.to_thread(self.dispatch, dag, task_description)

    def _dispatch_parallel(
        self,
        batch: list[TaskItem],
        task_description: str,
        deadline: float | None,
        result: DispatchResult,
    ) -> None:
        """Execute a batch of tasks concurrently using the thread pool."""
        per_task_timeout = self._timeout_seconds if self._timeout_seconds is not None else None
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
            upstream = self._collect_upstream_artifacts(task_item, result.artifacts)
            future = pool.submit(self._run_executor, task_item, task_description, upstream)
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

    def _dispatch_sequential(
        self,
        batch: list[TaskItem],
        task_description: str,
        deadline: float | None,
        result: DispatchResult,
    ) -> None:
        """Execute a batch of tasks sequentially (backward compatible)."""
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
            upstream = self._collect_upstream_artifacts(task_item, result.artifacts)
            artifact, error = self._run_executor(task_item, task_description, upstream)
            if error is None and artifact is not None:
                self._graph.complete_task(task_item.id)
                result.artifacts[task_item.id] = artifact
                result.completed.append(task_item.id)
            else:
                _safe_fail(self._graph, task_item.id)
                result.errors[task_item.id] = error or "unknown error"
                result.failed.append(task_item.id)
                break  # Fail-fast: don't start more tasks in batch

    def _cleanup_stale_tasks(
        self,
        specialist_ids: set[str],
        result: DispatchResult,
    ) -> None:
        """Clean up IN_PROGRESS and orphaned PENDING tasks after the main loop."""
        failed_set = set(result.failed)
        self._fail_in_progress(specialist_ids, result, failed_set)
        self._fail_orphaned_pending(specialist_ids, result, failed_set)

    def _fail_in_progress(
        self,
        specialist_ids: set[str],
        result: DispatchResult,
        failed_set: set[str],
    ) -> None:
        """Fail any tasks left IN_PROGRESS after loop exit (e.g. mid-batch timeout)."""
        for tid in specialist_ids:
            task = self._graph.get_task(tid)
            if task is not None and task.state == TaskState.IN_PROGRESS:
                _safe_fail(self._graph, tid)
                if tid not in failed_set:
                    result.failed.append(tid)
                    failed_set.add(tid)

    def _fail_orphaned_pending(
        self,
        specialist_ids: set[str],
        result: DispatchResult,
        failed_set: set[str],
    ) -> None:
        """Fail orphaned PENDING tasks, looping until stable for transitive chains."""
        changed = True
        while changed:
            changed = False
            for tid in specialist_ids:
                task = self._graph.get_task(tid)
                if task is None or task.state != TaskState.PENDING:
                    continue
                if self._should_fail_orphan(task):
                    _safe_fail(self._graph, tid)
                    if tid not in failed_set:
                        result.failed.append(tid)
                        failed_set.add(tid)
                    changed = True

    def _should_fail_orphan(self, task: Any) -> bool:
        """Return True if a PENDING task should be failed (orphaned)."""
        if not task.blocked_by:
            return True  # Independent task never started
        dep_tasks = [self._graph.get_task(d) for d in task.blocked_by]
        return all(
            t is not None and t.state in (TaskState.COMPLETED, TaskState.FAILED)
            for t in dep_tasks
        )
