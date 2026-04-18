"""Unit tests for TaskGraph — SQLite-backed task dependency engine.

Uses the task_graph fixture from conftest.py (backed by tmp_path SQLite).
Tests add_task, state transitions, queries, cycle detection, and snapshots.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_nexus.models.task import TaskGraphSnapshot, TaskItem, TaskState
from agent_nexus.platform.orchestration.task_graph import TaskGraph


def _make_task(
    task_id: str,
    description: str = "",
    agent: str = "test-agent",
    blocked_by: list[str] | None = None,
    state: TaskState = TaskState.PENDING,
) -> TaskItem:
    """Create a TaskItem with UTC timestamps."""
    now = datetime.now(timezone.utc)
    return TaskItem(
        id=task_id,
        description=description or f"Task {task_id}",
        agent=agent,
        blocked_by=blocked_by or [],
        state=state,
        created_at=now,
        updated_at=now,
    )


# ============================================================================
# add_task()
# ============================================================================


class TestAddTask:
    def test_add_single_task(self, task_graph: TaskGraph) -> None:
        """Add a task and retrieve it."""
        task = _make_task("A", description="First task")
        task_graph.add_task(task)

        result = task_graph.get_task("A")
        assert result is not None
        assert result.id == "A"
        assert result.description == "First task"
        assert result.agent == "test-agent"
        assert result.state == TaskState.PENDING
        assert result.blocked_by == []

    def test_add_duplicate_raises(self, task_graph: TaskGraph) -> None:
        """Adding the same task ID twice raises ValueError."""
        task_graph.add_task(_make_task("A"))
        with pytest.raises(ValueError, match="already exists"):
            task_graph.add_task(_make_task("A"))

    def test_add_with_blocked_by(self, task_graph: TaskGraph) -> None:
        """Add task B blocked_by task A, verify dependency stored."""
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B", blocked_by=["A"]))

        result = task_graph.get_task("B")
        assert result is not None
        assert result.blocked_by == ["A"]

    def test_add_unknown_blocked_by_raises(self, task_graph: TaskGraph) -> None:
        """blocked_by referencing nonexistent task raises ValueError."""
        with pytest.raises(ValueError, match="non-existent"):
            task_graph.add_task(_make_task("B", blocked_by=["nonexistent"]))

    def test_add_self_loop_raises(self, task_graph: TaskGraph) -> None:
        """Task blocked_by itself raises ValueError (cycle)."""
        with pytest.raises(ValueError, match="cycle"):
            task_graph.add_task(_make_task("A", blocked_by=["A"]))

    def test_add_multiple_deps(self, task_graph: TaskGraph) -> None:
        """Task with multiple blocked_by deps stores them all."""
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B"))
        task_graph.add_task(_make_task("C", blocked_by=["A", "B"]))

        result = task_graph.get_task("C")
        assert result is not None
        assert set(result.blocked_by) == {"A", "B"}

    def test_add_no_deps_chain(self, task_graph: TaskGraph) -> None:
        """Build a valid chain A->B->C->D with no cycle."""
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B", blocked_by=["A"]))
        task_graph.add_task(_make_task("C", blocked_by=["B"]))
        task_graph.add_task(_make_task("D", blocked_by=["C"]))

        result = task_graph.get_task("D")
        assert result is not None
        assert result.blocked_by == ["C"]


# ============================================================================
# State transitions
# ============================================================================


class TestStartTask:
    def test_start_pending(self, task_graph: TaskGraph) -> None:
        """pending -> in_progress transition."""
        task_graph.add_task(_make_task("A"))
        result = task_graph.start_task("A")

        assert result.state == TaskState.IN_PROGRESS

    def test_start_wrong_state(self, task_graph: TaskGraph) -> None:
        """Cannot start a task already in_progress."""
        task_graph.add_task(_make_task("A"))
        task_graph.start_task("A")

        with pytest.raises(ValueError, match="expected pending"):
            task_graph.start_task("A")

    def test_start_blocked(self, task_graph: TaskGraph) -> None:
        """Cannot start when blocked_by not completed."""
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B", blocked_by=["A"]))

        with pytest.raises(ValueError, match="unresolved dependencies"):
            task_graph.start_task("B")

    def test_start_not_found(self, task_graph: TaskGraph) -> None:
        """Starting unknown task raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            task_graph.start_task("nonexistent")

    def test_start_after_dep_completed(self, task_graph: TaskGraph) -> None:
        """Can start after dependency is completed."""
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B", blocked_by=["A"]))

        task_graph.start_task("A")
        task_graph.complete_task("A")
        result = task_graph.start_task("B")

        assert result.state == TaskState.IN_PROGRESS


class TestCompleteTask:
    def test_complete_in_progress(self, task_graph: TaskGraph) -> None:
        """in_progress -> completed transition."""
        task_graph.add_task(_make_task("A"))
        task_graph.start_task("A")
        result = task_graph.complete_task("A")

        assert result.state == TaskState.COMPLETED

    def test_complete_wrong_state(self, task_graph: TaskGraph) -> None:
        """Cannot complete a pending task."""
        task_graph.add_task(_make_task("A"))

        with pytest.raises(ValueError, match="expected in_progress"):
            task_graph.complete_task("A")

    def test_complete_not_found(self, task_graph: TaskGraph) -> None:
        """Completing unknown task raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            task_graph.complete_task("nonexistent")


class TestFailTask:
    def test_fail_in_progress(self, task_graph: TaskGraph) -> None:
        """in_progress -> failed transition."""
        task_graph.add_task(_make_task("A"))
        task_graph.start_task("A")
        result = task_graph.fail_task("A")

        assert result.state == TaskState.FAILED

    def test_fail_wrong_state(self, task_graph: TaskGraph) -> None:
        """Cannot fail a pending task."""
        task_graph.add_task(_make_task("A"))

        with pytest.raises(ValueError, match="expected in_progress"):
            task_graph.fail_task("A")


# ============================================================================
# Queries
# ============================================================================


class TestGetReadyTasks:
    def test_ready_no_deps(self, task_graph: TaskGraph) -> None:
        """Tasks with no deps are ready."""
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B"))

        ready = task_graph.get_ready_tasks()
        ids = {t.id for t in ready}
        assert ids == {"A", "B"}

    def test_ready_with_completed_deps(self, task_graph: TaskGraph) -> None:
        """Task becomes ready when deps are completed."""
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B", blocked_by=["A"]))

        # B should not be ready yet
        assert all(t.id != "B" for t in task_graph.get_ready_tasks())

        # Complete A
        task_graph.start_task("A")
        task_graph.complete_task("A")

        ready = task_graph.get_ready_tasks()
        ids = {t.id for t in ready}
        assert "B" in ids

    def test_ready_excludes_in_progress(self, task_graph: TaskGraph) -> None:
        """in_progress tasks are not in ready list."""
        task_graph.add_task(_make_task("A"))
        task_graph.start_task("A")

        ready = task_graph.get_ready_tasks()
        assert all(t.id != "A" for t in ready)


class TestGetBlockedTasks:
    def test_blocked_with_unresolved_deps(self, task_graph: TaskGraph) -> None:
        """Tasks with unresolved deps appear in blocked list."""
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B", blocked_by=["A"]))

        blocked = task_graph.get_blocked_tasks()
        ids = {t.id for t in blocked}
        assert "B" in ids
        assert "A" not in ids

    def test_no_blocked_when_all_ready(self, task_graph: TaskGraph) -> None:
        """No blocked tasks when all deps are satisfied."""
        task_graph.add_task(_make_task("A"))

        blocked = task_graph.get_blocked_tasks()
        assert len(blocked) == 0


class TestGetParallelGroups:
    def test_diamond_pattern(self, task_graph: TaskGraph) -> None:
        """BFS topological grouping for diamond: A -> B,C -> D."""
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B", blocked_by=["A"]))
        task_graph.add_task(_make_task("C", blocked_by=["A"]))
        task_graph.add_task(_make_task("D", blocked_by=["B", "C"]))

        groups = task_graph.get_parallel_groups()
        assert len(groups) == 3

        group0_ids = {t.id for t in groups[0]}
        group1_ids = {t.id for t in groups[1]}
        group2_ids = {t.id for t in groups[2]}

        assert group0_ids == {"A"}
        assert group1_ids == {"B", "C"}
        assert group2_ids == {"D"}

    def test_empty_graph(self, task_graph: TaskGraph) -> None:
        """Empty graph returns empty groups."""
        assert task_graph.get_parallel_groups() == []

    def test_linear_chain(self, task_graph: TaskGraph) -> None:
        """Linear chain: A -> B -> C, each in its own group."""
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B", blocked_by=["A"]))
        task_graph.add_task(_make_task("C", blocked_by=["B"]))

        groups = task_graph.get_parallel_groups()
        assert len(groups) == 3
        for group in groups:
            assert len(group) == 1


# ============================================================================
# Cycle detection
# ============================================================================


class TestDetectCycles:
    def test_no_cycles_in_dag(self, task_graph: TaskGraph) -> None:
        """Valid DAG has no cycles."""
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B", blocked_by=["A"]))
        task_graph.add_task(_make_task("C", blocked_by=["B"]))

        cycles = task_graph.detect_cycles()
        assert len(cycles) == 0

    def test_cycles_detected(self, task_graph: TaskGraph) -> None:
        """Cycles are detected when present in the graph.

        Since add_task prevents cycle creation, we use a two-step approach:
        add tasks without deps, then manually insert cycle deps via raw SQL.
        """
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B"))
        task_graph.add_task(_make_task("C"))

        # Manually insert cyclic dependencies: A->B->C->A
        import sqlite3

        conn = sqlite3.connect(str(task_graph._db_path))
        conn.execute("INSERT INTO task_dependencies (task_id, blocked_by_id) VALUES ('A', 'B')")
        conn.execute("INSERT INTO task_dependencies (task_id, blocked_by_id) VALUES ('B', 'C')")
        conn.execute("INSERT INTO task_dependencies (task_id, blocked_by_id) VALUES ('C', 'A')")
        conn.commit()
        conn.close()

        cycles = task_graph.detect_cycles()
        assert len(cycles) > 0


# ============================================================================
# Snapshot & clear
# ============================================================================


class TestGetSnapshot:
    def test_snapshot_captures_all(self, task_graph: TaskGraph) -> None:
        """Snapshot includes all tasks and parallel groups."""
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B", blocked_by=["A"]))

        snapshot = task_graph.get_snapshot()

        assert isinstance(snapshot, TaskGraphSnapshot)
        assert len(snapshot.tasks) == 2
        assert len(snapshot.parallel_groups) == 2

    def test_snapshot_empty(self, task_graph: TaskGraph) -> None:
        """Snapshot of empty graph."""
        snapshot = task_graph.get_snapshot()
        assert len(snapshot.tasks) == 0
        assert len(snapshot.parallel_groups) == 0


class TestClear:
    def test_clear_removes_all(self, task_graph: TaskGraph) -> None:
        """clear() removes all tasks."""
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B", blocked_by=["A"]))

        task_graph.clear()

        assert task_graph.get_task("A") is None
        assert task_graph.get_task("B") is None
        assert task_graph.get_snapshot().tasks == []


# ============================================================================
# TaskGraph uses IMMEDIATE transactions for mutations (from iter14)
# ============================================================================


class TestTaskGraphImmediate:
    """TaskGraph mutation methods use BEGIN IMMEDIATE."""

    def test_start_task_uses_immediate(self, tmp_path: Path) -> None:
        tg = TaskGraph(tmp_path / "tg.db")
        task = TaskItem(id="t1", description="test", agent="test-agent")
        tg.add_task(task)
        # Should succeed -- basic smoke test that IMMEDIATE doesn't break
        result = tg.start_task("t1")
        assert result.state == TaskState.IN_PROGRESS

    def test_complete_task_uses_immediate(self, tmp_path: Path) -> None:
        tg = TaskGraph(tmp_path / "tg.db")
        task = TaskItem(id="t1", description="test", agent="test-agent")
        tg.add_task(task)
        tg.start_task("t1")
        result = tg.complete_task("t1")
        assert result.state == TaskState.COMPLETED

    def test_fail_task_uses_immediate(self, tmp_path: Path) -> None:
        tg = TaskGraph(tmp_path / "tg.db")
        task = TaskItem(id="t1", description="test", agent="test-agent")
        tg.add_task(task)
        tg.start_task("t1")
        result = tg.fail_task("t1")
        assert result.state == TaskState.FAILED
