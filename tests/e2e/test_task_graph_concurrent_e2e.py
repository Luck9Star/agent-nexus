"""E2E tests for TaskGraph concurrent and batch operations.

Covers:
- Concurrent add_tasks with dependency chains
- Cycle detection under concurrent-like conditions
- Large DAG performance (100+ tasks)
- Batch add_tasks validation (duplicates, missing deps, cycles)
- Blocked tasks queries
- State transition validation
"""

import asyncio
import time
from pathlib import Path

import pytest

from agent_nexus.models.task import TaskItem, TaskState
from agent_nexus.platform.orchestration.task_graph import TaskGraph


def _make_task(
    task_id: str,
    description: str = "",
    agent: str = "worker",
    blocked_by: list[str] | None = None,
    state: TaskState = TaskState.PENDING,
) -> TaskItem:
    """Create a TaskItem with sensible defaults."""
    return TaskItem(
        id=task_id,
        description=description or f"Task {task_id}",
        agent=agent,
        blocked_by=blocked_by or [],
        state=state,
    )


# ---------------------------------------------------------------------------
# Concurrent add_tasks scenarios
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestTaskGraphConcurrentAdd:
    """Batch add_tasks with complex dependency structures."""

    def test_batch_add_with_dependency_chain(self, tmp_path: Path) -> None:
        """add_tasks correctly handles a chain: a -> b -> c -> d."""
        tg = TaskGraph(str(tmp_path / "chain.db"))

        tasks = [
            _make_task("a"),
            _make_task("b", blocked_by=["a"]),
            _make_task("c", blocked_by=["b"]),
            _make_task("d", blocked_by=["c"]),
        ]

        tg.add_tasks(tasks)

        # Only 'a' should be ready
        ready = tg.get_ready_tasks()
        assert [t.id for t in ready] == ["a"]

        # Walk the chain
        tg.start_task("a")
        tg.complete_task("a")
        ready = tg.get_ready_tasks()
        assert [t.id for t in ready] == ["b"]

        tg.start_task("b")
        tg.complete_task("b")
        ready = tg.get_ready_tasks()
        assert [t.id for t in ready] == ["c"]

        tg.start_task("c")
        tg.complete_task("c")
        ready = tg.get_ready_tasks()
        assert [t.id for t in ready] == ["d"]

    def test_batch_add_with_internal_and_external_deps(self, tmp_path: Path) -> None:
        """add_tasks with mix of batch-internal and pre-existing external deps."""
        tg = TaskGraph(str(tmp_path / "mixed.db"))

        # Pre-existing task
        tg.add_task(_make_task("pre-existing"))

        # Batch: one depends on pre-existing, another depends on batch-internal
        tasks = [
            _make_task("depends-on-pre", blocked_by=["pre-existing"]),
            _make_task("depends-on-batch", blocked_by=["depends-on-pre"]),
        ]

        tg.add_tasks(tasks)

        # 'pre-existing' is ready
        ready = {t.id for t in tg.get_ready_tasks()}
        assert "pre-existing" in ready

    def test_batch_add_empty_list_is_noop(self, tmp_path: Path) -> None:
        """add_tasks([]) does nothing and raises no error."""
        tg = TaskGraph(str(tmp_path / "empty.db"))
        tg.add_tasks([])  # Should not raise
        assert tg.get_snapshot().tasks == []

    def test_batch_add_rejects_cycle_within_batch(self, tmp_path: Path) -> None:
        """add_tasks detects cycles among the batch itself (a->b->c->a)."""
        tg = TaskGraph(str(tmp_path / "cycle.db"))

        tasks = [
            _make_task("a", blocked_by=["c"]),
            _make_task("b", blocked_by=["a"]),
            _make_task("c", blocked_by=["b"]),
        ]

        with pytest.raises(ValueError, match="[Cc]ycle|cycle"):
            tg.add_tasks(tasks)

    def test_batch_add_rejects_duplicate_ids_within_batch(self, tmp_path: Path) -> None:
        """add_tasks detects duplicate IDs within the batch."""
        tg = TaskGraph(str(tmp_path / "dup.db"))

        tasks = [
            _make_task("t1"),
            _make_task("t2"),
            _make_task("t1"),  # duplicate
        ]

        with pytest.raises(ValueError, match="[Dd]uplicate|Duplicate"):
            tg.add_tasks(tasks)

    def test_batch_add_rejects_duplicate_against_existing(self, tmp_path: Path) -> None:
        """add_tasks detects IDs that already exist in the graph."""
        tg = TaskGraph(str(tmp_path / "exist.db"))
        tg.add_task(_make_task("existing"))

        tasks = [_make_task("existing"), _make_task("new")]

        with pytest.raises(ValueError, match="already exist"):
            tg.add_tasks(tasks)


# ---------------------------------------------------------------------------
# Concurrent-style async operations
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestTaskGraphAsyncOps:
    """Async wrappers behave correctly under concurrent access."""

    def test_async_get_task(self, tmp_path: Path) -> None:
        """aget_task returns correct task."""
        tg = TaskGraph(":memory:")
        tg.add_task(_make_task("async-1", description="Async test"))

        result = asyncio.run(tg.aget_task("async-1"))
        assert result is not None
        assert result.id == "async-1"
        assert result.description == "Async test"

    def test_async_get_ready_tasks(self, tmp_path: Path) -> None:
        """aget_ready_tasks returns unblocked pending tasks."""
        tg = TaskGraph(":memory:")
        tg.add_tasks([
            _make_task("ready-1"),
            _make_task("blocked-1", blocked_by=["ready-1"]),
        ])

        result = asyncio.run(tg.aget_ready_tasks())
        assert len(result) == 1
        assert result[0].id == "ready-1"

    def test_async_get_blocked_tasks(self, tmp_path: Path) -> None:
        """aget_blocked_tasks returns tasks with unresolved deps."""
        tg = TaskGraph(":memory:")
        tg.add_tasks([
            _make_task("root"),
            _make_task("blocked", blocked_by=["root"]),
        ])

        result = asyncio.run(tg.aget_blocked_tasks())
        assert len(result) == 1
        assert result[0].id == "blocked"

    def test_async_snapshot(self, tmp_path: Path) -> None:
        """aget_snapshot returns complete graph state."""
        tg = TaskGraph(":memory:")
        tg.add_tasks([
            _make_task("s1"),
            _make_task("s2", blocked_by=["s1"]),
        ])

        snapshot = asyncio.run(tg.aget_snapshot())
        assert len(snapshot.tasks) == 2
        assert len(snapshot.parallel_groups) >= 1

    def test_async_close(self, tmp_path: Path) -> None:
        """aclose properly shuts down the graph."""
        tg = TaskGraph(":memory:")
        tg.add_task(_make_task("closing"))

        asyncio.run(tg.aclose())

        with pytest.raises(RuntimeError, match="closed"):
            tg.get_task("closing")

    def test_concurrent_async_reads(self, tmp_path: Path) -> None:
        """Multiple concurrent async reads don't corrupt state."""
        tg = TaskGraph(":memory:")
        for i in range(20):
            tg.add_task(_make_task(f"concurrent-{i}"))

        async def _read_all():
            results = await asyncio.gather(
                tg.aget_task("concurrent-0"),
                tg.aget_ready_tasks(),
                tg.aget_snapshot(),
                tg.aget_parallel_groups(),
                tg.aget_blocked_tasks(),
            )
            return results

        results = asyncio.run(_read_all())
        assert results[0] is not None  # get_task
        assert len(results[1]) == 20  # get_ready_tasks
        assert len(results[2].tasks) == 20  # snapshot


# ---------------------------------------------------------------------------
# Large DAG performance
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestTaskGraphLargeDAG:
    """Performance tests for large DAG operations."""

    def test_large_dag_add_and_query(self, tmp_path: Path) -> None:
        """Adding 100 tasks with dependencies is fast."""
        tg = TaskGraph(str(tmp_path / "large.db"))

        tasks = [_make_task("t0")]
        for i in range(1, 100):
            tasks.append(_make_task(f"t{i}", blocked_by=[f"t{i-1}"]))

        start = time.monotonic()
        tg.add_tasks(tasks)
        add_time = time.monotonic() - start

        assert add_time < 5.0, f"add_tasks took {add_time:.2f}s for 100 tasks"

        # Query operations should also be fast
        start = time.monotonic()
        ready = tg.get_ready_tasks()
        query_time = time.monotonic() - start
        assert len(ready) == 1
        assert ready[0].id == "t0"
        assert query_time < 1.0

    def test_large_dag_parallel_groups(self, tmp_path: Path) -> None:
        """Parallel groups computation for wide DAG is correct."""
        tg = TaskGraph(str(tmp_path / "wide.db"))

        # Wide: 1 root -> 50 parallel tasks -> 1 join
        tasks = [_make_task("root")]
        for i in range(50):
            tasks.append(_make_task(f"mid-{i}", blocked_by=["root"]))
        tasks.append(_make_task("join", blocked_by=[f"mid-{i}" for i in range(50)]))

        tg.add_tasks(tasks)

        groups = tg.get_parallel_groups()
        assert len(groups) == 3  # root | mid-0..49 | join
        assert len(groups[0]) == 1  # root
        assert len(groups[1]) == 50  # all mid tasks
        assert len(groups[2]) == 1  # join

    def test_large_dag_detect_cycles_performance(self, tmp_path: Path) -> None:
        """Cycle detection on 100-task DAG completes quickly."""
        tg = TaskGraph(str(tmp_path / "cycle-perf.db"))

        tasks = [_make_task(f"t{i}", blocked_by=[f"t{i-1}"] if i > 0 else []) for i in range(100)]
        tg.add_tasks(tasks)

        start = time.monotonic()
        cycles = tg.detect_cycles()
        elapsed = time.monotonic() - start

        assert cycles == []
        assert elapsed < 2.0, f"Cycle detection took {elapsed:.2f}s for 100 tasks"


# ---------------------------------------------------------------------------
# State transition edge cases
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestTaskGraphStateTransitions:
    """Edge cases in task state transitions."""

    def test_start_blocked_task_raises(self, tmp_path: Path) -> None:
        """Starting a task with unresolved deps raises ValueError."""
        tg = TaskGraph(str(tmp_path / "blocked.db"))
        tg.add_tasks([
            _make_task("blocker"),
            _make_task("blocked", blocked_by=["blocker"]),
        ])

        with pytest.raises(ValueError, match="unresolved dependencies"):
            tg.start_task("blocked")

    def test_complete_pending_task_raises(self, tmp_path: Path) -> None:
        """Completing a task that's still pending raises ValueError."""
        tg = TaskGraph(str(tmp_path / "state.db"))
        tg.add_task(_make_task("pending"))

        with pytest.raises(ValueError, match="expected in_progress"):
            tg.complete_task("pending")

    def test_fail_pending_task_succeeds(self, tmp_path: Path) -> None:
        """Failing a pending task is allowed (dependency failure propagation)."""
        tg = TaskGraph(str(tmp_path / "fail-pending.db"))
        tg.add_task(_make_task("to-fail"))

        failed = tg.fail_task("to-fail")
        assert failed.state == TaskState.FAILED

    def test_fail_completed_task_raises(self, tmp_path: Path) -> None:
        """Failing a completed task raises ValueError."""
        tg = TaskGraph(str(tmp_path / "fail-comp.db"))
        tg.add_task(_make_task("done"))
        tg.start_task("done")
        tg.complete_task("done")

        with pytest.raises(ValueError, match="expected in_progress or pending"):
            tg.fail_task("done")

    def test_start_nonexistent_task_raises(self, tmp_path: Path) -> None:
        """Starting a task that doesn't exist raises ValueError."""
        tg = TaskGraph(str(tmp_path / "noexist.db"))

        with pytest.raises(ValueError, match="not found"):
            tg.start_task("ghost")

    def test_self_referencing_task_rejected(self, tmp_path: Path) -> None:
        """TaskItem validation rejects self-referencing blocked_by."""
        with pytest.raises(ValueError, match="cannot block itself"):
            _make_task("self-loop", blocked_by=["self-loop"])

    def test_add_task_duplicate_rejected(self, tmp_path: Path) -> None:
        """add_task rejects duplicate task ID."""
        tg = TaskGraph(str(tmp_path / "dup-single.db"))
        tg.add_task(_make_task("t1"))

        with pytest.raises(ValueError, match="already exists"):
            tg.add_task(_make_task("t1"))

    def test_clear_resets_graph(self, tmp_path: Path) -> None:
        """clear() removes all tasks and dependencies."""
        tg = TaskGraph(str(tmp_path / "clear.db"))
        tg.add_tasks([
            _make_task("a"),
            _make_task("b", blocked_by=["a"]),
            _make_task("c", blocked_by=["b"]),
        ])

        assert len(tg.get_snapshot().tasks) == 3

        tg.clear()

        assert len(tg.get_snapshot().tasks) == 0
        assert tg.get_ready_tasks() == []
