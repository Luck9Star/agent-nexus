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

    @property
    def is_terminal(self) -> bool:
        """True if any task has failed or been cancelled (dispatch should stop)."""
        return bool(self.failed) or bool(self.cancelled)


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


def _build_specialist_items(
    dag: CompositionDAG,
    task_description: str,
    specialist_ids: set[str],
) -> list[TaskItem]:
    """Build TaskItems for specialist tasks, filtering deps to specialist-only."""
    items: list[TaskItem] = []
    for dag_task in dag.tasks:
        if dag_task.id not in specialist_ids:
            continue
        filtered_deps = [dep for dep in dag_task.blocked_by if dep in specialist_ids]
        items.append(
            TaskItem(
                id=dag_task.id,
                description=task_description,
                agent=dag_task.agent,
                blocked_by=filtered_deps,
                vars={"output_contract": dag_task.output},
            )
        )
    return items


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
    items = _build_specialist_items(dag, task_description, specialist_ids)

    new_items: list[TaskItem] = []
    if items:
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
        use_concurrency: bool = False,
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
        self._concurrent = use_concurrency
        # Persistent thread pool for concurrent execution (reused across batches)
        self._pool: concurrent.futures.ThreadPoolExecutor | None = None

    def __enter__(self) -> DAGDispatcher:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __del__(self) -> None:
        """Best-effort cleanup of the thread pool on garbage collection."""
        with contextlib.suppress(Exception):
            self.close()

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
        pending_or_running = [
            t
            for tid in specialist_ids
            if (t := self._graph.get_task(tid)) is not None
            and t.state in (TaskState.PENDING, TaskState.IN_PROGRESS)
        ]

        if not pending_or_running:
            return True  # All done

        # Remaining tasks are stuck (in-progress or blocked pending) → fail and stop
        for t in pending_or_running:
            _safe_fail(self._graph, t.id)
            result.failed.append(t.id)
        return True

    def _check_deadline(self, deadline: float | None, result: DispatchResult) -> bool:
        """Return True if deadline exceeded (sets result.timed_out)."""
        if deadline is not None and time.monotonic() > deadline:
            result.timed_out = True
            logger.warning("DAGDispatch timed out after %ss", self._timeout_seconds)
            return True
        return False

    def _dispatch_batch(
        self,
        batch: list[TaskItem],
        task_description: str,
        deadline: float | None,
        result: DispatchResult,
    ) -> None:
        """Dispatch a batch via parallel or sequential strategy."""
        if self._concurrent and len(batch) > 1:
            self._dispatch_parallel(batch, task_description, deadline, result)
        else:
            self._dispatch_sequential(batch, task_description, deadline, result)

    def _run_dispatch_loop(
        self,
        specialist_ids: set[str],
        task_description: str,
        deadline: float | None,
        max_iterations: int,
        result: DispatchResult,
    ) -> bool:
        """Run the main dispatch loop. Returns True if completed normally."""
        iteration = 0
        while iteration < max_iterations:
            iteration += 1

            if self._check_deadline(deadline, result):
                return False

            ready_specialists = [t for t in self._graph.get_ready_tasks() if t.id in specialist_ids]

            if not ready_specialists:
                if self._no_more_work(specialist_ids, result):
                    return True

            else:
                batch = ready_specialists[: self._max_batch_size]
                self._dispatch_batch(batch, task_description, deadline, result)

                if result.is_terminal:
                    return False

        return False

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

        completed_normally = self._run_dispatch_loop(
            specialist_ids,
            task_description,
            deadline,
            max_iterations,
            result,
        )

        has_no_errors = (
            not completed_normally
            and not result.failed
            and not result.cancelled
            and not result.timed_out
        )
        if has_no_errors:
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

    def _submit_batch(
        self,
        batch: list[TaskItem],
        task_description: str,
        deadline: float | None,
        result: DispatchResult,
    ) -> dict[concurrent.futures.Future[tuple[Artifact | None, str | None]], TaskItem] | None:
        """Submit all tasks in batch to the thread pool. Returns None on deadline."""
        pool = self._get_pool()
        futures: dict[
            concurrent.futures.Future[tuple[Artifact | None, str | None]],
            TaskItem,
        ] = {}
        started_in_batch: list[str] = []
        for task_item in batch:
            if deadline is not None and time.monotonic() > deadline:
                result.timed_out = True
                for tid in started_in_batch:
                    _safe_fail(self._graph, tid)
                    result.cancelled.append(tid)
                    result.errors[tid] = "cancelled (deadline reached before submit)"
                return None
            self._graph.start_task(task_item.id)
            started_in_batch.append(task_item.id)
            upstream = self._collect_upstream_artifacts(task_item, result.artifacts)
            future = pool.submit(self._run_executor, task_item, task_description, upstream)
            futures[future] = task_item
        return futures

    def _collect_futures(
        self,
        futures: dict[concurrent.futures.Future[tuple[Artifact | None, str | None]], TaskItem],
        result: DispatchResult,
    ) -> None:
        """Collect results from submitted futures with fail-fast on first error.

        The *batch_timeout* applies to the ``as_completed`` iterator as a
        whole — it caps the total time spent waiting for the entire batch,
        not per-future.  If any individual future has not completed by the
        time the batch timeout fires, a ``TimeoutError`` is raised and all
        remaining futures are cancelled.
        """
        batch_timeout = self._timeout_seconds
        try:
            for future in concurrent.futures.as_completed(futures, timeout=batch_timeout):
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
                    for f in futures:
                        f.cancel()
                    break
        except TimeoutError:
            result.timed_out = True
            for f in futures:
                f.cancel()

    def _drain_single_future(
        self,
        _f: concurrent.futures.Future[tuple[Artifact | None, str | None]],
        ti: TaskItem,
        result: DispatchResult,
    ) -> bool:
        """Process a single remaining future. Returns True if task completed."""
        if _f.done() and not _f.cancelled():
            try:
                artifact, error = _f.result()
            except Exception as exc:
                artifact, error = None, f"executor error: {exc}"
            if error is None and artifact is not None:
                self._graph.complete_task(ti.id)
                result.artifacts[ti.id] = artifact
                result.completed.append(ti.id)
                return True
        _safe_fail(self._graph, ti.id)
        result.cancelled.append(ti.id)
        result.errors[ti.id] = "cancelled (sibling task failed)"
        return False

    def _drain_remaining(
        self,
        futures: dict[concurrent.futures.Future[tuple[Artifact | None, str | None]], TaskItem],
        result: DispatchResult,
    ) -> None:
        """Drain any futures that completed before fail-fast break."""
        for _f, ti in futures.items():
            if ti.id in result.completed or ti.id in result.failed:
                continue
            self._drain_single_future(_f, ti, result)

    def _dispatch_parallel(
        self,
        batch: list[TaskItem],
        task_description: str,
        deadline: float | None,
        result: DispatchResult,
    ) -> None:
        """Execute a batch of tasks concurrently using the thread pool."""
        futures = self._submit_batch(batch, task_description, deadline, result)
        if futures is None:
            return
        self._collect_futures(futures, result)
        self._drain_remaining(futures, result)

    def _fail_started_in_batch(
        self,
        started_ids: list[str],
        result: DispatchResult,
    ) -> None:
        """Fail tasks started in a batch but not completed (deadline hit)."""
        for tid in started_ids:
            task = self._graph.get_task(tid)
            if task is not None and task.state == TaskState.IN_PROGRESS:
                _safe_fail(self._graph, tid)
                result.failed.append(tid)

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
                self._fail_started_in_batch(started_in_batch, result)
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

    def _should_fail_orphan(self, task: TaskItem) -> bool:
        """Return True if a PENDING task should be failed (orphaned).

        After the dispatch loop exits, any remaining PENDING task will never
        be scheduled.  Mark it as failed so the caller sees a complete set
        of terminal states (completed / failed / cancelled).
        """
        if not task.blocked_by:
            return True  # Independent task never started
        dep_tasks = [self._graph.get_task(d) for d in task.blocked_by]
        return all(
            t is not None and t.state in (TaskState.COMPLETED, TaskState.FAILED) for t in dep_tasks
        )
