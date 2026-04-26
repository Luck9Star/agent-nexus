"""DAGDispatcher — bridges CompositionDAG to TaskGraph + runtime execution.

Converts the agency pipeline's CompositionDAG (in-memory) into TaskGraph
entries (SQLite-backed) and dispatches execution via either:
- ``ProcessManager`` for real subprocess IPC, or
- ``ExpertExecutor`` protocol for in-process execution (testing/dev).

This is the G4 bridge connecting agency DAG generation to the orchestration
runtime (TaskGraph + ProcessManager).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Protocol

from agent_nexus.models.task import TaskItem, TaskState
from agent_nexus.platform.orchestration.task_graph import TaskGraph

from .integrator import Artifact
from .planner import CompositionDAG, DAGTask

logger = logging.getLogger(__name__)


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

    timed_out: bool = False
    """Whether the dispatch hit its timeout limit."""


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
    for topological ordering with configurable parallelism.
    """

    def __init__(
        self,
        graph: TaskGraph,
        executor: ExpertExecutor,
        max_parallel: int = 3,
        timeout_seconds: float | None = None,
    ) -> None:
        self._graph = graph
        self._executor = executor
        self._max_parallel = max(1, max_parallel)
        self._timeout_seconds = timeout_seconds

    @property
    def graph(self) -> TaskGraph:
        """The underlying TaskGraph for inspection."""
        return self._graph

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
            time.monotonic() + self._timeout_seconds
            if self._timeout_seconds is not None
            else None
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
                logger.warning(
                    "DAGDispatch timed out after %ss", self._timeout_seconds
                )
                break

            ready = self._graph.get_ready_tasks()
            # Filter to specialist tasks only (skip integrate/validate)
            ready_specialists = [t for t in ready if t.id in specialist_ids]

            if not ready_specialists:
                # Check if there are still pending/blocked specialist tasks
                all_specialists_in_graph = [
                    self._graph.get_task(tid) for tid in specialist_ids
                ]
                pending_or_in_progress = [
                    t
                    for t in all_specialists_in_graph
                    if t is not None and t.state in (TaskState.PENDING, TaskState.IN_PROGRESS)
                ]
                if not pending_or_in_progress:
                    break  # All done

                # Distinguish: tasks still IN_PROGRESS vs truly stuck PENDING
                in_progress = [
                    t for t in pending_or_in_progress
                    if t.state == TaskState.IN_PROGRESS
                ]
                if in_progress:
                    # In synchronous mode, stale IN_PROGRESS tasks from a prior
                    # crash would cause an infinite loop. Fail them instead.
                    for t in in_progress:
                        self._graph.fail_task(t.id)
                        result.failed.append(t.id)
                    break

                # Only PENDING tasks remain but none are ready → blocked by failed deps
                for t in pending_or_in_progress:
                    self._graph.fail_task(t.id)
                    result.failed.append(t.id)
                break

            # Dispatch up to max_parallel
            batch = ready_specialists[: self._max_parallel]
            for task_item in batch:
                if deadline is not None and time.monotonic() > deadline:
                    result.timed_out = True
                    break

                self._graph.start_task(task_item.id)
                try:
                    artifact = self._executor(task_item.agent, task_description)
                    self._graph.complete_task(task_item.id)
                    result.artifacts[task_item.id] = artifact
                    result.completed.append(task_item.id)
                except Exception:
                    logger.exception(
                        "Executor failed for task '%s' (agent '%s')",
                        task_item.id,
                        task_item.agent,
                    )
                    self._graph.fail_task(task_item.id)
                    result.failed.append(task_item.id)

        # If the loop was terminated by the max_iterations guard (not a normal
        # break), mark the result as timed_out so callers know it didn't finish.
        if iteration >= max_iterations:
            result.timed_out = True
            logger.warning(
                "DAGDispatch exceeded max_iterations (%d) for %d tasks — "
                "possible state machine bug",
                max_iterations,
                len(dag.tasks),
            )

        return result
