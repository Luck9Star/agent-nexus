"""TaskGraph -- SQLite-backed task dependency engine.

Task lifecycle: pending -> in_progress -> completed | failed
Implicit blocked state: task is "blocked" when blocked_by list has non-completed tasks.

SQLite schema:
    CREATE TABLE tasks (
        id TEXT PRIMARY KEY,
        description TEXT NOT NULL,
        agent TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending',
        vars TEXT DEFAULT '{}',  -- JSON
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE task_dependencies (
        task_id TEXT NOT NULL,
        blocked_by_id TEXT NOT NULL,
        PRIMARY KEY (task_id, blocked_by_id),
        FOREIGN KEY (task_id) REFERENCES tasks(id),
        FOREIGN KEY (blocked_by_id) REFERENCES tasks(id)
    );
    CREATE INDEX idx_dep_task ON task_dependencies(task_id);
    CREATE INDEX idx_dep_blocked ON task_dependencies(blocked_by_id);
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from agent_nexus.models.task import TaskGraphSnapshot, TaskItem, TaskState

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    agent TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    vars TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id TEXT NOT NULL,
    blocked_by_id TEXT NOT NULL,
    PRIMARY KEY (task_id, blocked_by_id),
    FOREIGN KEY (task_id) REFERENCES tasks(id),
    FOREIGN KEY (blocked_by_id) REFERENCES tasks(id)
);
CREATE INDEX IF NOT EXISTS idx_dep_task ON task_dependencies(task_id);
CREATE INDEX IF NOT EXISTS idx_dep_blocked ON task_dependencies(blocked_by_id);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskGraph:
    """SQLite-backed task graph with dependency tracking and cycle detection."""

    def __init__(self, db_path: Path) -> None:
        """Initialize or open existing database.

        Uses WAL mode for concurrent read/write access.
        Creates tables on first init.
        """
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if not exist, enable WAL mode."""
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_SCHEMA_SQL)

    @contextmanager
    def _conn(
        self, immediate: bool = False,
    ) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for DB connections.

        Uses check_same_thread=False for async compatibility.
        Connection is NOT shared -- create per-operation.

        Args:
            immediate: If True, use BEGIN IMMEDIATE for write serialization.
                Prevents TOCTOU races in read-then-write mutation methods.
        """
        conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
        )
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            if immediate:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add_task(self, task: TaskItem) -> None:
        """Add a task with its dependencies.

        Validates:
        1. No duplicate task ID
        2. All blocked_by references exist
        3. No cycle would be created (DFS check)

        Raises ValueError on validation failure.
        """
        with self._conn(immediate=True) as conn:
            # 1. Check duplicate
            row = conn.execute(
                "SELECT id FROM tasks WHERE id = ?", (task.id,)
            ).fetchone()
            if row is not None:
                raise ValueError(f"Task '{task.id}' already exists")

            # 2. Validate blocked_by references exist
            if task.blocked_by:
                placeholders = ",".join("?" * len(task.blocked_by))
                existing = conn.execute(
                    f"SELECT id FROM tasks WHERE id IN ({placeholders})",
                    tuple(task.blocked_by),
                ).fetchall()
                existing_ids = {r[0] for r in existing}
                # Also include the task being added (self-referencing blocked_by
                # among a batch of tasks is handled via cycle detection below)
                existing_ids.add(task.id)
                missing = set(task.blocked_by) - existing_ids
                if missing:
                    raise ValueError(
                        f"blocked_by references non-existent tasks: {missing}"
                    )

            # 3. Cycle detection
            if task.blocked_by and self._would_create_cycle(conn, task.id, task.blocked_by):
                raise ValueError(
                    f"Adding dependencies {task.blocked_by} to '{task.id}' would create a cycle"
                )

            # Insert task
            conn.execute(
                "INSERT INTO tasks (id, description, agent, state, vars, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    task.id,
                    task.description,
                    task.agent,
                    task.state.value,
                    json.dumps(task.vars, default=str),
                    task.created_at.isoformat(),
                    task.updated_at.isoformat(),
                ),
            )

            # Insert dependencies
            for dep_id in task.blocked_by:
                conn.execute(
                    "INSERT INTO task_dependencies (task_id, blocked_by_id) VALUES (?, ?)",
                    (task.id, dep_id),
                )

    def start_task(self, task_id: str) -> TaskItem:
        """Transition task to in_progress.

        Only allowed if:
        1. Current state is 'pending'
        2. All blocked_by tasks are 'completed'

        Raises ValueError if preconditions not met.
        """
        with self._conn(immediate=True) as conn:
            task = self._get_task_conn(conn, task_id)
            if task is None:
                raise ValueError(f"Task '{task_id}' not found")

            if task.state != TaskState.PENDING:
                raise ValueError(
                    f"Task '{task_id}' is {task.state.value}, expected pending"
                )

            # Check all blockers are completed
            unresolved = self._get_unresolved_blockers(conn, task_id)
            if unresolved:
                raise ValueError(
                    f"Task '{task_id}' has unresolved dependencies: {unresolved}"
                )

            now = _now_iso()
            conn.execute(
                "UPDATE tasks SET state = ?, updated_at = ? WHERE id = ?",
                (TaskState.IN_PROGRESS.value, now, task_id),
            )

            return self._get_task_conn_required(conn, task_id)

    def complete_task(self, task_id: str) -> TaskItem:
        """Transition task to completed.

        Only allowed from 'in_progress' state.
        Raises ValueError if not in_progress.
        """
        with self._conn(immediate=True) as conn:
            task = self._get_task_conn(conn, task_id)
            if task is None:
                raise ValueError(f"Task '{task_id}' not found")

            if task.state != TaskState.IN_PROGRESS:
                raise ValueError(
                    f"Task '{task_id}' is {task.state.value}, expected in_progress"
                )

            now = _now_iso()
            conn.execute(
                "UPDATE tasks SET state = ?, updated_at = ? WHERE id = ?",
                (TaskState.COMPLETED.value, now, task_id),
            )

            return self._get_task_conn_required(conn, task_id)

    def fail_task(self, task_id: str) -> TaskItem:
        """Transition task to failed.

        Only allowed from 'in_progress' state.
        """
        with self._conn(immediate=True) as conn:
            task = self._get_task_conn(conn, task_id)
            if task is None:
                raise ValueError(f"Task '{task_id}' not found")

            if task.state != TaskState.IN_PROGRESS:
                raise ValueError(
                    f"Task '{task_id}' is {task.state.value}, expected in_progress"
                )

            now = _now_iso()
            conn.execute(
                "UPDATE tasks SET state = ?, updated_at = ? WHERE id = ?",
                (TaskState.FAILED.value, now, task_id),
            )

            return self._get_task_conn_required(conn, task_id)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_task(self, task_id: str) -> TaskItem | None:
        """Get a single task by ID."""
        with self._conn() as conn:
            return self._get_task_conn(conn, task_id)

    def get_ready_tasks(self) -> list[TaskItem]:
        """Get all tasks that are ready to execute.

        A task is ready when:
        1. State is 'pending'
        2. ALL blocked_by tasks are 'completed'
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id FROM tasks WHERE state = ?",
                (TaskState.PENDING.value,),
            ).fetchall()
            ready = []
            for (tid,) in rows:
                unresolved = self._get_unresolved_blockers(conn, tid)
                if not unresolved:
                    task = self._get_task_conn(conn, tid)
                    if task is not None:
                        ready.append(task)
            return ready

    def get_blocked_tasks(self) -> list[TaskItem]:
        """Get all tasks that are pending but have unresolved dependencies."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id FROM tasks WHERE state = ?",
                (TaskState.PENDING.value,),
            ).fetchall()
            blocked = []
            for (tid,) in rows:
                unresolved = self._get_unresolved_blockers(conn, tid)
                if unresolved:
                    task = self._get_task_conn(conn, tid)
                    if task is not None:
                        blocked.append(task)
            return blocked

    def get_parallel_groups(
        self, conn: Any | None = None,
    ) -> list[list[TaskItem]]:
        """Get groups of tasks that can run in parallel.

        Returns groups ordered by dependency depth (no deps first).
        Within each group, all tasks are independent.

        Args:
            conn: Optional existing connection for consistent reads.
                If None, opens a new connection.
        """
        if conn is not None:
            return self._get_parallel_groups_conn(conn)
        with self._conn() as c:
            return self._get_parallel_groups_conn(c)

    def _get_parallel_groups_conn(
        self, conn: Any,
    ) -> list[list[TaskItem]]:
        """Internal: compute parallel groups using an existing connection."""
        # Build adjacency: task -> set of tasks it depends on
        all_rows = conn.execute(
            "SELECT id FROM tasks ORDER BY created_at"
        ).fetchall()
        task_ids = [r[0] for r in all_rows]

        if not task_ids:
            return []

        task_id_set = set(task_ids)
        dep_map: dict[str, set[str]] = {tid: set() for tid in task_ids}
        dep_rows = conn.execute(
            "SELECT task_id, blocked_by_id FROM task_dependencies"
        ).fetchall()
        for task_id, blocked_by_id in dep_rows:
            # Only track dependencies to tasks that exist in the graph.
            # Unknown deps are logged but don't block grouping.
            if blocked_by_id in task_id_set:
                dep_map[task_id].add(blocked_by_id)
            else:
                logger.warning(
                    "Task '%s' depends on non-existent task '%s', ignoring dep",
                    task_id,
                    blocked_by_id,
                )

        # BFS-based topological grouping
        groups: list[list[TaskItem]] = []
        assigned: set[str] = set()

        while len(assigned) < len(task_ids):
            group_ids = [
                tid
                for tid in task_ids
                if tid not in assigned
                and dep_map[tid].issubset(assigned)
            ]
            if not group_ids:
                # Remaining tasks have unresolvable deps (cycle)
                unassigned = task_id_set - assigned
                logger.warning(
                    "Cannot schedule %d task(s) — likely cyclic dependency: %s",
                    len(unassigned),
                    unassigned,
                )
                break
            group_tasks = []
            for tid in group_ids:
                task = self._get_task_conn(conn, tid)
                if task is not None:
                    group_tasks.append(task)
            groups.append(group_tasks)
            assigned.update(group_ids)

        return groups

    def detect_cycles(self) -> list[list[str]]:
        """Detect cycles in the dependency graph.

        Returns list of cycles found, each cycle as a list of task IDs.
        Empty list means no cycles.

        Uses DFS with visiting/visited two-set technique.
        """
        with self._conn() as conn:
            all_rows = conn.execute("SELECT id FROM tasks").fetchall()
            task_ids = [r[0] for r in all_rows]

            dep_map: dict[str, list[str]] = {tid: [] for tid in task_ids}
            dep_rows = conn.execute(
                "SELECT task_id, blocked_by_id FROM task_dependencies"
            ).fetchall()
            for task_id, blocked_by_id in dep_rows:
                dep_map[task_id].append(blocked_by_id)

            cycles: list[list[str]] = []
            visiting: set[str] = set()
            visited: set[str] = set()

            def _dfs(node: str, path: list[str]) -> None:
                if node in visiting:
                    # Found a cycle -- extract it from path
                    cycle_start = path.index(node)
                    cycles.append(path[cycle_start:])
                    return
                if node in visited:
                    return

                visiting.add(node)
                path.append(node)
                for dep in dep_map.get(node, []):
                    _dfs(dep, path)
                path.pop()
                visiting.discard(node)
                visited.add(node)

            for tid in task_ids:
                if tid not in visited:
                    _dfs(tid, [])

            return cycles

    def get_snapshot(self) -> TaskGraphSnapshot:
        """Get a full snapshot of the graph state."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, description, agent, state, vars, created_at, updated_at "
                "FROM tasks ORDER BY created_at"
            ).fetchall()

            tasks: list[TaskItem] = []
            for row in rows:
                task = self._task_from_row(conn, row)
                if task is not None:
                    tasks.append(task)

            groups = self.get_parallel_groups(conn=conn)
            group_ids = [[t.id for t in group] for group in groups]

            return TaskGraphSnapshot(tasks=tasks, parallel_groups=group_ids)

    def clear(self) -> None:
        """Clear all tasks (for testing)."""
        with self._conn() as conn:
            conn.execute("DELETE FROM task_dependencies")
            conn.execute("DELETE FROM tasks")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _task_from_row(
        self,
        conn: sqlite3.Connection,
        row: tuple[Any, ...],
    ) -> TaskItem | None:
        """Convert DB row to TaskItem.

        Returns None only for rows that are genuinely missing (should not
        happen in normal operation).  Data-corruption errors (bad JSON,
        invalid state enum) are logged and re-raised so callers do not
        silently lose data.
        """
        try:
            task_id, description, agent, state_str, vars_json, created_at_str, updated_at_str = row
            blocked_by = self._get_blocked_by_conn(conn, task_id)
            return TaskItem(
                id=task_id,
                description=description,
                agent=agent,
                state=TaskState(state_str),
                vars=json.loads(vars_json) if vars_json else {},
                blocked_by=blocked_by,
                created_at=datetime.fromisoformat(created_at_str),
                updated_at=datetime.fromisoformat(updated_at_str),
            )
        except (ValueError, KeyError, json.JSONDecodeError, TypeError) as exc:
            # Data corruption — log loudly and re-raise so the caller
            # knows a row is damaged rather than merely absent.
            logger.error(
                "Corrupt task row (id=%s): %s", row[0] if row else "?", exc
            )
            raise

    def _get_blocked_by_conn(
        self, conn: sqlite3.Connection, task_id: str
    ) -> list[str]:
        """Get blocked_by list for a task (using existing connection)."""
        rows = conn.execute(
            "SELECT blocked_by_id FROM task_dependencies WHERE task_id = ?",
            (task_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def _get_task_conn(
        self, conn: sqlite3.Connection, task_id: str
    ) -> TaskItem | None:
        """Get task using an existing connection."""
        row = conn.execute(
            "SELECT id, description, agent, state, vars, created_at, updated_at "
            "FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        return self._task_from_row(conn, row)

    def _get_task_conn_required(
        self, conn: sqlite3.Connection, task_id: str
    ) -> TaskItem:
        """Get task using an existing connection, raising on missing."""
        result = self._get_task_conn(conn, task_id)
        if result is None:
            raise ValueError(f"Task '{task_id}' disappeared during update")
        return result

    def _get_unresolved_blockers(
        self, conn: sqlite3.Connection, task_id: str
    ) -> list[str]:
        """Get list of blocked_by IDs that are not completed."""
        rows = conn.execute(
            "SELECT td.blocked_by_id FROM task_dependencies td "
            "JOIN tasks t ON td.blocked_by_id = t.id "
            "WHERE td.task_id = ? AND t.state != ?",
            (task_id, TaskState.COMPLETED.value),
        ).fetchall()
        return [r[0] for r in rows]

    def _would_create_cycle(
        self,
        conn: sqlite3.Connection,
        task_id: str,
        blocked_by_ids: list[str],
    ) -> bool:
        """Check if adding dependencies would create a cycle.

        Walk from each blocked_by_id back through their dependencies.
        If we can reach task_id, there's a cycle.
        """
        # Build current dependency graph from DB
        dep_rows = conn.execute(
            "SELECT task_id, blocked_by_id FROM task_dependencies"
        ).fetchall()
        dep_map: dict[str, list[str]] = {}
        for tid, bid in dep_rows:
            dep_map.setdefault(tid, []).append(bid)

        # Add proposed dependencies
        for bid in blocked_by_ids:
            dep_map.setdefault(task_id, []).append(bid)

        # BFS/DFS from blocked_by_ids to see if we reach task_id
        visited: set[str] = set()

        def _can_reach(node: str, target: str) -> bool:
            if node == target:
                return True
            if node in visited:
                return False
            visited.add(node)
            for dep in dep_map.get(node, []):
                if _can_reach(dep, target):
                    return True
            return False

        for start_id in blocked_by_ids:
            visited.clear()
            # Walk from start_id through its dependencies
            # If start_id is task_id itself, that's a self-loop
            if start_id == task_id:
                return True
            if _can_reach(start_id, task_id):
                return True

        return False
