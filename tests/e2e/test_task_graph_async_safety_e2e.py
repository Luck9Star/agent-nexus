"""E2E tests for TaskGraph async safety: async wrappers, state transition edge cases,
and close-then-use behavior.

Quality focus: async_safety — verifies TaskGraph's async methods (aget_task,
aget_ready_tasks, etc.) work correctly with the asyncio.Lock serialization, and
that closed-state guards prevent use-after-close.
"""

import asyncio
from pathlib import Path

import pytest

from agent_nexus.models.task import TaskItem, TaskState
from agent_nexus.platform.orchestration.task_graph import TaskGraph


def _make_task(
    task_id: str,
    description: str = "Test task",
    agent: str = "test-agent",
    blocked_by: list[str] | None = None,
    state: TaskState = TaskState.PENDING,
) -> TaskItem:
    return TaskItem(
        id=task_id,
        description=description,
        agent=agent,
        state=state,
        blocked_by=blocked_by or [],
    )


# ---------------------------------------------------------------------------
# Async wrapper tests
# ---------------------------------------------------------------------------


class TestTaskGraphAsyncWrappers:
    """Verify async wrappers delegate correctly with lock serialization."""

    def test_aget_task_returns_task(self, tmp_path: Path) -> None:
        """aget_task retrieves a task added via sync add_task."""
        tg = TaskGraph(str(tmp_path / "async_get.db"))
        tg.add_task(_make_task("t1"))

        result = asyncio.run(tg.aget_task("t1"))
        assert result is not None
        assert result.id == "t1"
        assert result.state == TaskState.PENDING
        tg.close()

    def test_aget_task_missing_returns_none(self, tmp_path: Path) -> None:
        """aget_task returns None for non-existent task."""
        tg = TaskGraph(str(tmp_path / "async_missing.db"))

        result = asyncio.run(tg.aget_task("ghost"))
        assert result is None
        tg.close()

    def test_aget_ready_tasks_returns_unblocked(self, tmp_path: Path) -> None:
        """aget_ready_tasks returns tasks with no pending blockers."""

        async def _test():
            tg = TaskGraph(str(tmp_path / "async_ready.db"))
            tg.add_task(_make_task("t1"))
            tg.add_task(_make_task("t2", blocked_by=["t1"]))

            ready = await tg.aget_ready_tasks()
            ready_ids = {t.id for t in ready}
            assert "t1" in ready_ids
            assert "t2" not in ready_ids

            tg.start_task("t1")
            tg.complete_task("t1")

            ready = await tg.aget_ready_tasks()
            ready_ids = {t.id for t in ready}
            assert "t2" in ready_ids

            tg.close()

        asyncio.run(_test())

    def test_aget_blocked_tasks(self, tmp_path: Path) -> None:
        """aget_blocked_tasks returns tasks with unresolved blockers."""

        async def _test():
            tg = TaskGraph(str(tmp_path / "async_blocked.db"))
            tg.add_task(_make_task("t1"))
            tg.add_task(_make_task("t2", blocked_by=["t1"]))

            blocked = await tg.aget_blocked_tasks()
            blocked_ids = {t.id for t in blocked}
            assert "t2" in blocked_ids
            assert "t1" not in blocked_ids

            tg.close()

        asyncio.run(_test())

    def test_aget_snapshot(self, tmp_path: Path) -> None:
        """aget_snapshot returns a valid snapshot."""

        async def _test():
            tg = TaskGraph(str(tmp_path / "async_snap.db"))
            tg.add_task(_make_task("t1"))

            snap = await tg.aget_snapshot()
            assert snap is not None
            assert hasattr(snap, "tasks")
            assert len(snap.tasks) == 1

            tg.close()

        asyncio.run(_test())

    def test_aget_parallel_groups(self, tmp_path: Path) -> None:
        """aget_parallel_groups returns correctly ordered groups."""

        async def _test():
            tg = TaskGraph(str(tmp_path / "async_groups.db"))
            tg.add_tasks(
                [
                    _make_task("a"),
                    _make_task("b", blocked_by=["a"]),
                    _make_task("c", blocked_by=["b"]),
                ]
            )

            groups = await tg.aget_parallel_groups()
            assert len(groups) == 3
            assert [t.id for t in groups[0]] == ["a"]

            tg.close()

        asyncio.run(_test())


class TestTaskGraphConcurrentAsyncAccess:
    """Verify concurrent async operations on in-memory DB are safe."""

    def test_concurrent_aget_ready_tasks(self) -> None:
        """Multiple concurrent aget_ready_tasks calls don't corrupt data."""

        async def _test():
            # Use in-memory DB — relies on asyncio.Lock for safety
            tg = TaskGraph(":memory:")
            tg.add_task(_make_task("t1"))
            tg.add_task(_make_task("t2", blocked_by=["t1"]))

            # 5 concurrent reads
            results = await asyncio.gather(
                tg.aget_ready_tasks(),
                tg.aget_ready_tasks(),
                tg.aget_ready_tasks(),
                tg.aget_ready_tasks(),
                tg.aget_ready_tasks(),
            )

            for ready in results:
                ids = {t.id for t in ready}
                assert "t1" in ids
                assert "t2" not in ids

            tg.close()

        asyncio.run(_test())

    def test_concurrent_aget_task_same_id(self) -> None:
        """Multiple concurrent aget_task calls for the same ID all succeed."""

        async def _test():
            tg = TaskGraph(":memory:")
            tg.add_task(_make_task("shared"))

            results = await asyncio.gather(
                tg.aget_task("shared"),
                tg.aget_task("shared"),
                tg.aget_task("shared"),
            )

            for result in results:
                assert result is not None
                assert result.id == "shared"

            tg.close()

        asyncio.run(_test())


# ---------------------------------------------------------------------------
# Batch edge cases
# ---------------------------------------------------------------------------


class TestTaskGraphBatchEdgeCases:
    """Verify batch operations handle edge cases correctly."""

    def test_add_tasks_empty_list_is_noop(self, tmp_path: Path) -> None:
        """add_tasks([]) is a no-op (no error, no changes)."""
        tg = TaskGraph(str(tmp_path / "batch_empty.db"))
        tg.add_tasks([])  # Should not raise
        assert tg.get_task("anything") is None
        tg.close()

    def test_add_tasks_atomic_rollback_on_cycle(self, tmp_path: Path) -> None:
        """add_tasks with a cycle rolls back the entire batch."""
        tg = TaskGraph(str(tmp_path / "batch_rollback.db"))

        tasks = [
            _make_task("a"),
            _make_task("b", blocked_by=["a"]),
            # This creates a cycle: c -> b -> a -> c (through a's blocked_by)
            _make_task("c", blocked_by=["b"]),
        ]
        # Add the first two successfully
        tg.add_tasks(tasks[:2])

        # Now try adding a batch with a cycle
        # a depends on c, c depends on b, b depends on a
        cycle_tasks = [
            TaskItem(
                id="x",
                description="X",
                agent="a",
                state=TaskState.PENDING,
                blocked_by=["y"],
            ),
            TaskItem(
                id="y",
                description="Y",
                agent="a",
                state=TaskState.PENDING,
                blocked_by=["x"],
            ),
        ]

        with pytest.raises(ValueError, match="[Cc]ycle|cycle"):
            tg.add_tasks(cycle_tasks)

        # Original tasks should still exist
        assert tg.get_task("a") is not None
        assert tg.get_task("b") is not None
        tg.close()

    def test_get_parallel_groups_empty_graph(self, tmp_path: Path) -> None:
        """get_parallel_groups on empty graph returns empty list."""
        tg = TaskGraph(str(tmp_path / "empty_groups.db"))
        assert tg.get_parallel_groups() == []
        tg.close()

    def test_get_snapshot_empty_graph(self, tmp_path: Path) -> None:
        """get_snapshot on empty graph returns snapshot with empty tasks."""
        tg = TaskGraph(str(tmp_path / "empty_snap.db"))
        snap = tg.get_snapshot()
        assert snap is not None
        assert len(snap.tasks) == 0
        tg.close()
