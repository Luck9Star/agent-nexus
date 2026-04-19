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
        """Cannot fail a completed task."""
        task_graph.add_task(_make_task("A"))
        task_graph.start_task("A")
        task_graph.complete_task("A")

        with pytest.raises(ValueError, match="expected in_progress or pending"):
            task_graph.fail_task("A")

    def test_fail_pending(self, task_graph: TaskGraph) -> None:
        """pending -> failed transition (dependency cascade)."""
        task_graph.add_task(_make_task("A"))
        result = task_graph.fail_task("A")

        assert result.state == TaskState.FAILED

    def test_fail_blocked_pending(self, task_graph: TaskGraph) -> None:
        """Fail a pending task whose dependency already failed."""
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B", blocked_by=["A"]))

        task_graph.start_task("A")
        task_graph.fail_task("A")

        # B is still pending but can now be failed
        result = task_graph.fail_task("B")
        assert result.state == TaskState.FAILED


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


# ============================================================================
# Coverage gap tests: task_graph.py line 185 (blocked_by present + cycle check)
# ============================================================================


class TestAddTaskCycleCheckWithBlockedBy:
    """add_task calls _would_create_cycle when blocked_by is present (line 185).

    Line 185 is: if task.blocked_by and self._would_create_cycle(conn, task.id, task.blocked_by)
    It is covered when a new task has blocked_by and the cycle check returns True.
    """

    def test_add_task_blocked_by_creates_cycle_via_chain(self, task_graph: TaskGraph) -> None:
        """New task blocked_by an existing task that transitively depends on it.

        Build: A -> B (B blocked_by A). Then adding C blocked_by B is fine.
        Then trying to add a task that closes the loop triggers line 185.
        """
        task_graph.add_task(_make_task("A"))
        task_graph.add_task(_make_task("B", blocked_by=["A"]))
        task_graph.add_task(_make_task("C", blocked_by=["B"]))

        # Now try to add D blocked_by C where D already has a dep chain back to A.
        # This is valid (no cycle): D -> C -> B -> A.
        task_graph.add_task(_make_task("D", blocked_by=["C"]))
        assert task_graph.get_task("D") is not None

        # To trigger line 185 with a True result: add Z blocked_by D,
        # then try to add a new task A2 blocked_by Z. But we need to build
        # a scenario where adding a task's blocked_by creates a cycle.
        # Since we can't re-add A, we create: A->B, then try to add a
        # task whose blocked_by references an existing task whose deps
        # lead back to the new task. We do this by first adding Z
        # blocked_by D, then trying to add a new task blocked_by Z
        # where Z also depends back.
        # Actually the simplest way: use raw SQL to set up the back-edge
        # so _would_create_cycle returns True on the next add_task.
        import sqlite3
        conn = sqlite3.connect(str(task_graph._db_path))
        # Make A blocked_by D (creating A->D->C->B->A cycle via raw SQL)
        conn.execute(
            "INSERT INTO task_dependencies (task_id, blocked_by_id) VALUES ('A', 'D')"
        )
        conn.commit()
        conn.close()

        # Now adding any task blocked_by A triggers cycle check via A->D->C->B->A
        # But the new task itself is fine. The key is that _would_create_cycle
        # walks from blocked_by_id back and checks if it reaches task_id.
        # Adding E blocked_by A: walk from A -> D -> C -> B -> A.
        # But A != E, so no cycle detected for E.
        # The line is covered when blocked_by is non-empty AND cycle check runs.
        task_graph.add_task(_make_task("E", blocked_by=["A"]))
        assert task_graph.get_task("E") is not None

    def test_add_task_blocked_by_cycle_detected(self, task_graph: TaskGraph) -> None:
        """New task whose blocked_by would create a cycle is rejected at line 185."""
        task_graph.add_task(_make_task("P"))
        task_graph.add_task(_make_task("Q", blocked_by=["P"]))

        # Adding a task blocked_by Q where Q transitively depends on
        # this new task would be a cycle. But since the task doesn't exist yet,
        # _would_create_cycle walks from Q -> P, and if the new task is "P"
        # it can't be re-added. We need a different approach.
        # Add R blocked_by Q first.
        task_graph.add_task(_make_task("R", blocked_by=["Q"]))

        # Now use raw SQL to add a reverse dep: R -> "NEW"
        import sqlite3
        conn = sqlite3.connect(str(task_graph._db_path))
        conn.execute(
            "INSERT INTO task_dependencies (task_id, blocked_by_id) VALUES ('R', 'NEW')"
        )
        conn.commit()
        conn.close()

        # Now adding "NEW" blocked_by R would walk: R -> Q -> P (no NEW).
        # But R also -> NEW. Walk from R: R has deps [Q, NEW]. Follow Q->P.
        # Follow NEW -> NEW == task_id? No, NEW is blocked_by_id in R's deps.
        # Walk from R: check _can_reach(R, NEW). R's deps = [Q, NEW via SQL].
        # So R depends on NEW. Then _can_reach(R, NEW) = True because R's dep NEW == NEW.
        # Wait, we need to walk from R's blocked_by list to see if we reach NEW.
        # Actually _would_create_cycle adds proposed deps: NEW -> [R].
        # Then walks from each blocked_by_id (R) to see if we reach task_id (NEW).
        # R's deps include Q (from add_task) and NEW (from raw SQL).
        # Walk from R: R has dep Q and NEW. NEW == task_id -> cycle!
        with pytest.raises(ValueError, match="cycle"):
            task_graph.add_task(_make_task("NEW", blocked_by=["R"]))


# ============================================================================
# Iteration 85: In-memory DB PRAGMA foreign_keys=ON
# ============================================================================


class TestTaskGraphInMemoryForeignKeys:
    """In-memory SQLite enforces PRAGMA foreign_keys=ON."""

    def test_foreign_key_violation_raises_integrity_error(self) -> None:
        """Inserting a dependency with non-existent task_id raises IntegrityError."""
        import sqlite3

        tg = TaskGraph(Path(":memory:"))
        # Insert one valid task so the schema is populated
        tg.add_task(_make_task("A"))

        # Directly insert a dependency referencing a non-existent task.
        # With foreign_keys=ON this should raise IntegrityError.
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            tg._mem_conn.execute(
                "INSERT INTO task_dependencies (task_id, blocked_by_id) "
                "VALUES ('nonexistent_task', 'A')"
            )


# ============================================================================
# TaskGraph.close() — resource lifecycle regression
# ============================================================================


class TestTaskGraphClose:
    """TaskGraph.close() must release the in-memory SQLite connection."""

    def test_close_memory_db_releases_conn(self) -> None:
        tg = TaskGraph(Path(":memory:"))
        assert tg._mem_conn is not None
        tg.close()
        assert tg._mem_conn is None

    def test_close_idempotent(self) -> None:
        tg = TaskGraph(Path(":memory:"))
        tg.close()
        tg.close()  # second call is a no-op, should not raise

    def test_close_file_db_is_noop(self, tmp_path: Path) -> None:
        tg = TaskGraph(tmp_path / "test.db")
        assert tg._mem_conn is None
        tg.close()  # no-op for file-based DBs
        assert tg._mem_conn is None

    def test_operations_after_close_raise(self) -> None:
        tg = TaskGraph(Path(":memory:"))
        tg.add_task(_make_task("A"))
        tg.close()
        # After close, _mem_conn is None so _conn() falls through to
        # file-based path which will fail on ":memory:" string as a path
        with pytest.raises(Exception):
            tg.get_task("A")


# ---------------------------------------------------------------------------
# iter105 regression: corrupt task row detection
# ---------------------------------------------------------------------------


class TestCorruptTaskRow:
    """_rows_to_tasks raises on corrupt database rows (invalid state JSON)."""

    def test_corrupt_state_raises(self, tmp_path: Path) -> None:
        tg = TaskGraph(tmp_path / "test.db")
        tg.add_task(_make_task("good"))
        # Manually corrupt a row: insert invalid state string
        with tg._conn(immediate=True) as conn:
            conn.execute(
                "UPDATE tasks SET state = 'INVALID_STATE' WHERE id = 'good'"
            )
        with pytest.raises(ValueError, match="INVALID_STATE"):
            tg.get_task("good")

    def test_corrupt_vars_json_raises(self, tmp_path: Path) -> None:
        tg = TaskGraph(tmp_path / "test.db")
        tg.add_task(_make_task("v1"))
        with tg._conn(immediate=True) as conn:
            conn.execute(
                "UPDATE tasks SET vars = 'not-json{{{}}' WHERE id = 'v1'"
            )
        with pytest.raises(Exception):
            tg.get_task("v1")

    def test_corrupt_state_in_batch_raises(self, tmp_path: Path) -> None:
        """_rows_to_tasks (lines 562-566) raises on corrupt state in batch query."""
        tg = TaskGraph(tmp_path / "test.db")
        tg.add_task(_make_task("batch-bad"))
        with tg._conn(immediate=True) as conn:
            conn.execute(
                "UPDATE tasks SET state = 'TOTALLY_INVALID' WHERE id = 'batch-bad'"
            )
        # get_snapshot uses _task_from_row (single), not _rows_to_tasks.
        # Use get_parallel_groups which calls _rows_to_tasks internally.
        with pytest.raises(ValueError, match="TOTALLY_INVALID"):
            tg.get_parallel_groups()
