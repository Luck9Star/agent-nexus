"""Unit tests for TaskGraph — SQLite-backed task dependency engine.

Uses the task_graph fixture from conftest.py (backed by tmp_path SQLite).
Tests add_task, state transitions, queries, cycle detection, and snapshots.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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
        """Task blocked_by itself raises error (model-level self-reference check)."""
        with pytest.raises(Exception, match="cannot block itself"):
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


# ============================================================================
# Regression: Corrupt row handling in _task_from_row
# ============================================================================


class TestTaskGraphCorruptRow:
    """_task_from_row must raise on data corruption, not silently return None."""

    def test_invalid_state_in_db_raises(self, tmp_path: Path) -> None:
        """A row with an invalid state value causes an error, not silent drop."""
        import json
        import sqlite3

        db_path = tmp_path / "corrupt.db"
        tg = TaskGraph(db_path)

        # Insert a task normally
        task = _make_task("T1")
        tg.add_task(task)

        # Corrupt the state field directly in SQLite
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE tasks SET state = 'bogus_state' WHERE id = 'T1'")
        conn.commit()
        conn.close()

        # get_task should raise ValueError (bad state enum) instead of
        # silently returning None
        with pytest.raises(ValueError, match="bogus_state"):
            tg.get_task("T1")

    def test_invalid_json_vars_in_db_raises(self, tmp_path: Path) -> None:
        """A row with corrupt JSON in vars column causes an error."""
        import sqlite3

        db_path = tmp_path / "corrupt_json.db"
        tg = TaskGraph(db_path)

        task = _make_task("T1")
        tg.add_task(task)

        # Corrupt the vars field with invalid JSON
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE tasks SET vars = '{not valid json' WHERE id = 'T1'")
        conn.commit()
        conn.close()

        with pytest.raises(Exception):
            tg.get_task("T1")

    def test_valid_row_still_works(self, task_graph: TaskGraph) -> None:
        """Normal rows are unaffected by the tighter error handling."""
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B", blocked_by=["A"]))

        result = task_graph.get_task("B")
        assert result is not None
        assert result.blocked_by == ["A"]
        assert result.state == TaskState.PENDING


# ============================================================================
# Regression: :memory: SQLite shares a single persistent connection
# ============================================================================


class TestTaskGraphInMemory:
    """TaskGraph with Path(':memory:') must persist data across operations.

    sqlite3.connect(':memory:') creates a brand-new empty database on each
    call.  TaskGraph must detect this and keep a single persistent connection
    alive so that _init_db() tables survive into subsequent operations.
    """

    def test_add_and_get_task(self) -> None:
        """add_task then get_task must return the same task."""
        tg = TaskGraph(Path(":memory:"))
        task = _make_task("M1", description="memory task")
        tg.add_task(task)

        result = tg.get_task("M1")
        assert result is not None
        assert result.id == "M1"
        assert result.description == "memory task"

    def test_full_lifecycle(self) -> None:
        """Full lifecycle: add -> start -> complete on in-memory DB."""
        tg = TaskGraph(Path(":memory:"))
        tg.add_task(_make_task("A"))
        tg.add_task(_make_task("B", blocked_by=["A"]))

        # Start and complete A
        tg.start_task("A")
        tg.complete_task("A")

        # B should now be ready
        ready = tg.get_ready_tasks()
        assert any(t.id == "B" for t in ready)

        # Start and complete B
        tg.start_task("B")
        result = tg.complete_task("B")
        assert result.state == TaskState.COMPLETED

    def test_snapshot_and_clear(self) -> None:
        """get_snapshot and clear work correctly on in-memory DB."""
        tg = TaskGraph(Path(":memory:"))
        tg.add_task(_make_task("X"))
        tg.add_task(_make_task("Y", blocked_by=["X"]))

        snapshot = tg.get_snapshot()
        assert len(snapshot.tasks) == 2

        tg.clear()
        assert tg.get_snapshot().tasks == []

    def test_detect_cycles(self) -> None:
        """detect_cycles works on in-memory DB."""
        tg = TaskGraph(Path(":memory:"))
        tg.add_task(_make_task("A"))
        tg.add_task(_make_task("B"))

        # No cycles initially
        assert tg.detect_cycles() == []



# ============================================================================
# Coverage gap tests: fail_task not-found, parallel groups non-existent dep
# warning, cyclic break branch, _get_task_conn_required raise,
# in-memory rollback, _would_create_cycle DFS internals
# ============================================================================


class TestFailTaskNotFound:
    """fail_task raises ValueError for non-existent task."""

    def test_fail_not_found(self, task_graph: TaskGraph) -> None:
        with pytest.raises(ValueError, match="not found"):
            task_graph.fail_task("nonexistent_task")


class TestParallelGroupsNonExistentDep:
    """Parallel groups with a dependency on a non-existent task logs warning."""

    def test_nonexistent_dep_ignored(self, task_graph: TaskGraph) -> None:
        """Task depending on a non-existent task ID is still grouped."""
        import sqlite3

        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B", blocked_by=["A"]))

        # Manually insert a dep pointing to a non-existent task
        conn = sqlite3.connect(str(task_graph._db_path))
        conn.execute(
            "INSERT INTO task_dependencies (task_id, blocked_by_id) "
            "VALUES ('B', 'ghost_task')"
        )
        conn.commit()
        conn.close()

        # B depends on A (real) and ghost_task (fake).
        # ghost_task is not in task_id_set so it is logged but ignored.
        # A has no deps -> group 0. B depends on A only -> group 1.
        groups = task_graph.get_parallel_groups()
        assert len(groups) == 2
        assert {t.id for t in groups[0]} == {"A"}
        assert {t.id for t in groups[1]} == {"B"}


class TestParallelGroupsCyclicBreak:
    """Parallel groups with unresolvable deps triggers break branch."""

    def test_cyclic_deps_break(self, task_graph: TaskGraph) -> None:
        """Cyclic deps cause _get_parallel_groups_conn to break early."""
        import sqlite3

        task_graph.add_task(_make_task("X"))
        task_graph.add_task(_make_task("Y"))

        # Create cycle: X->Y->X via raw SQL
        conn = sqlite3.connect(str(task_graph._db_path))
        conn.execute(
            "INSERT INTO task_dependencies (task_id, blocked_by_id) "
            "VALUES ('X', 'Y')"
        )
        conn.execute(
            "INSERT INTO task_dependencies (task_id, blocked_by_id) "
            "VALUES ('Y', 'X')"
        )
        conn.commit()
        conn.close()

        # Neither X nor Y can be scheduled -- both have unresolvable deps
        groups = task_graph.get_parallel_groups()
        assert len(groups) == 0



class TestGetTaskConnRequiredRaises:
    """_get_task_conn_required raises when task row is absent."""

    def test_direct_call_on_missing_task(self) -> None:
        """Calling _get_task_conn_required with a missing ID raises ValueError."""
        tg = TaskGraph(Path(":memory:"))

        with pytest.raises(ValueError, match="disappeared"):
            tg._get_task_conn_required(tg._mem_conn, "nonexistent_id")

    def test_raised_after_concurrent_delete(self) -> None:
        """Task exists at start of mutation but is deleted before re-read.

        Uses a patched _get_task_conn to bypass the initial existence check,
        allowing the code to reach _get_task_conn_required after UPDATE.
        """
        import json
        from unittest.mock import patch

        tg = TaskGraph(Path(":memory:"))
        tg.add_task(_make_task("V1"))
        tg.start_task("V1")

        # Patch _get_task_conn to return a fake task for the initial check
        # but delete the real row so _get_task_conn_required fails
        original_get = tg._get_task_conn

        def fake_get(conn, task_id):
            result = original_get(conn, task_id)
            if result is not None and task_id == "V1":
                # Delete the row so _get_task_conn_required can't find it
                conn.execute("DELETE FROM tasks WHERE id = 'V1'")
            return result

        with patch.object(tg, "_get_task_conn", side_effect=fake_get):
            with pytest.raises(ValueError, match="disappeared"):
                tg.complete_task("V1")


class TestInMemoryRollback:
    """In-memory DB rollback path when commit fails."""

    def test_rollback_on_corrupt_data(self) -> None:
        """Invalid state causes rollback in _conn, not unhandled crash."""
        tg = TaskGraph(Path(":memory:"))
        tg.add_task(_make_task("R1"))
        tg.start_task("R1")

        # Corrupt the state so the read-back in _get_task_conn_required fails
        tg._mem_conn.execute(
            "UPDATE tasks SET state = 'invalid_state' WHERE id = 'R1'"
        )
        tg._mem_conn.commit()

        with pytest.raises(ValueError, match="invalid_state"):
            tg.complete_task("R1")

        # After rollback the UPDATE to 'completed' was rolled back
        row = tg._mem_conn.execute(
            "SELECT state FROM tasks WHERE id = 'R1'"
        ).fetchone()
        assert row is not None
        assert row[0] == "invalid_state"


class TestWouldCreateCycleDFS:
    """_would_create_cycle DFS internals: visited set, _can_reach, self-loop."""

    def test_self_loop_detected_directly(self, task_graph: TaskGraph) -> None:
        """_would_create_cycle catches start_id == task_id (self-loop branch)."""
        import sqlite3

        task_graph.add_task(_make_task("A"))

        conn = sqlite3.connect(str(task_graph._db_path))
        # new_id "X" blocked_by "X" -> start_id == task_id -> True
        result = task_graph._would_create_cycle(conn, "X", ["X"])
        assert result is True
        conn.close()

    def test_can_reach_target_through_chain(
        self, task_graph: TaskGraph,
    ) -> None:
        """_can_reach follows dep chain and returns True when target found."""
        import sqlite3

        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B", blocked_by=["A"]))
        task_graph.add_task(_make_task("C", blocked_by=["B"]))

        conn = sqlite3.connect(str(task_graph._db_path))
        # Add dep from A to "fake_target" manually
        conn.execute(
            "INSERT INTO task_dependencies (task_id, blocked_by_id) "
            "VALUES ('A', 'fake_target')"
        )
        conn.commit()

        # Would adding "fake_target" blocked_by C create a cycle?
        # Walk from C: C->B->A->fake_target. fake_target == task_id -> True!
        result = task_graph._would_create_cycle(conn, "fake_target", ["C"])
        assert result is True
        conn.close()

    def test_visited_set_prevents_revisit(self, task_graph: TaskGraph) -> None:
        """Diamond pattern: visited set prevents revisiting nodes."""
        import sqlite3

        # Diamond: A->B, A->C, B->D, C->D
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B", blocked_by=["A"]))
        task_graph.add_task(_make_task("C", blocked_by=["A"]))
        task_graph.add_task(_make_task("D", blocked_by=["B", "C"]))

        conn = sqlite3.connect(str(task_graph._db_path))
        # E blocked_by D: walk from D -> B -> A, D -> C -> A. No E -> False.
        result = task_graph._would_create_cycle(conn, "E", ["D"])
        assert result is False
        conn.close()
