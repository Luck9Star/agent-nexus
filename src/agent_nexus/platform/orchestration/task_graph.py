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

import asyncio
import json
import logging
import sqlite3
from collections import deque
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_nexus.models.task import TaskGraphSnapshot, TaskItem, TaskState
from agent_nexus.platform.utils import (
    detect_cycles_dfs,
    sqlite_connection,
)
from agent_nexus.platform.utils import (
    now_iso as _now_iso,
)

logger = logging.getLogger(__name__)

_TASK_COLUMNS = "id, description, agent, state, vars, created_at, updated_at"
"""Column list for tasks SELECT queries — single source of truth."""

_SQL_CHUNK_SIZE = 500
"""Max variables per SQLite IN clause — matches evolution/store.py."""

_TASK_COLUMNS_T = "t.id, t.description, t.agent, t.state, t.vars, t.created_at, t.updated_at"
"""Aliased column list for tasks SELECT queries using table alias 't'."""

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
CREATE INDEX IF NOT EXISTS idx_task_state ON tasks(state);
CREATE INDEX IF NOT EXISTS idx_task_agent ON tasks(agent);
CREATE INDEX IF NOT EXISTS idx_task_state_created ON tasks(state, created_at);
"""


class TaskGraph:
    """SQLite-backed task graph with dependency tracking and cycle detection."""

    def __init__(self, db_path: str | Path) -> None:
        """Initialize or open existing database.

        Uses WAL mode for concurrent read/write access.
        Creates tables on first init.

        For ``:memory:`` databases, a persistent connection is kept alive
        so that all operations share the same in-memory store.  File-based
        databases open a new connection per operation (original behaviour).
        """
        self._db_path = db_path
        self._mem_conn: sqlite3.Connection | None = None
        if str(self._db_path) == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:")
        self._init_db()

    def _init_db(self) -> None:
        """Create tables if not exist, enable WAL mode."""
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            for stmt in _SCHEMA_SQL.split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(stmt)

    def close(self) -> None:
        """Release persistent resources.

        Closes the in-memory SQLite connection (if any).  File-based
        databases are already closed per-operation by ``_conn()`` so
        this is a no-op for them.
        """
        if self._mem_conn is not None:
            self._mem_conn.close()
            self._mem_conn = None

    def __del__(self) -> None:
        if hasattr(self, "_mem_conn"):
            self.close()

    @contextmanager
    def _conn(
        self, immediate: bool = False,
    ) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for DB connections.

        Delegates to :func:`sqlite_connection` for standardised setup,
        teardown, and transaction handling.
        """
        with sqlite_connection(
            self._db_path,
            immediate=immediate,
            persistent_conn=self._mem_conn,
        ) as conn:
            yield conn

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

            # Insert dependencies (deduplicate to avoid PRIMARY KEY violation)
            deps = [(task.id, dep_id) for dep_id in dict.fromkeys(task.blocked_by)]
            if deps:
                conn.executemany(
                    "INSERT INTO task_dependencies (task_id, blocked_by_id) VALUES (?, ?)",
                    deps,
                )

    def add_tasks(self, tasks: list[TaskItem]) -> None:
        """Batch-add multiple tasks in a single transaction.

        Replaces the O(N^2) pattern of calling ``add_task`` N times where
        each call opens its own transaction and runs full-table cycle
        detection.  This method does:
        1. One ``BEGIN IMMEDIATE`` transaction
        2. Batch duplicate check: single SELECT for all task IDs
        3. Batch insert all tasks
        4. Batch insert all dependencies with ``executemany``
        5. Single cycle-detection pass after all inserts
        6. Commit once

        Raises ValueError on validation failure (duplicates, missing deps, cycles).
        """
        if not tasks:
            return

        with self._conn(immediate=True) as conn:
            # 1. Batch duplicate check
            task_ids = [t.id for t in tasks]
            id_set = set(task_ids)
            if len(id_set) < len(task_ids):
                seen: set[str] = set()
                dupes: list[str] = []
                for tid in task_ids:
                    if tid in seen and tid not in dupes:
                        dupes.append(tid)
                    seen.add(tid)
                raise ValueError(f"Duplicate task IDs in batch: {dupes}")

            existing_ids: set[str] = set()
            for i in range(0, len(task_ids), _SQL_CHUNK_SIZE):
                chunk = task_ids[i : i + _SQL_CHUNK_SIZE]
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT id FROM tasks WHERE id IN ({placeholders})",
                    tuple(chunk),
                ).fetchall()
                existing_ids.update(r[0] for r in rows)
            if existing_ids:
                raise ValueError(
                    f"Tasks already exist: {existing_ids}"
                )

            # 2. Validate all blocked_by references (must exist in DB or in this batch)
            #    Collect external deps, then validate in a single query.
            all_deps: list[tuple[str, str]] = []
            external_deps: set[str] = set()
            for task in tasks:
                for dep_id in dict.fromkeys(task.blocked_by):
                    # Self-dependency will be caught by cycle detection
                    if dep_id not in id_set and dep_id != task.id:
                        external_deps.add(dep_id)
                    all_deps.append((task.id, dep_id))
            if external_deps:
                ext_list = list(external_deps)
                found: set[str] = set()
                for i in range(0, len(ext_list), _SQL_CHUNK_SIZE):
                    chunk = ext_list[i : i + _SQL_CHUNK_SIZE]
                    ph = ",".join("?" * len(chunk))
                    found.update(
                        r[0] for r in conn.execute(
                            f"SELECT id FROM tasks WHERE id IN ({ph})",
                            tuple(chunk),
                        ).fetchall()
                    )
                missing = external_deps - found
                if missing:
                    raise ValueError(
                        f"blocked_by references non-existent tasks: {missing}"
                    )

            # 3. Batch insert all tasks
            task_rows = [
                (
                    t.id,
                    t.description,
                    t.agent,
                    t.state.value,
                    json.dumps(t.vars, default=str),
                    t.created_at.isoformat(),
                    t.updated_at.isoformat(),
                )
                for t in tasks
            ]
            conn.executemany(
                "INSERT INTO tasks (id, description, agent, state, vars, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                task_rows,
            )

            # 4. Batch insert dependencies
            if all_deps:
                conn.executemany(
                    "INSERT INTO task_dependencies (task_id, blocked_by_id) VALUES (?, ?)",
                    all_deps,
                )

            # 5. Single cycle-detection pass
            cycles = self._detect_cycles_conn(conn)
            if cycles:
                raise ValueError(
                    f"Adding batch tasks would create cycles: {cycles}"
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

            # Return updated copy instead of re-querying the DB
            return task.model_copy(
                update={"state": TaskState.IN_PROGRESS, "updated_at": now}
            )

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

            # Return updated copy instead of re-querying the DB
            return task.model_copy(
                update={"state": TaskState.COMPLETED, "updated_at": now}
            )

    def fail_task(self, task_id: str) -> TaskItem:
        """Transition task to failed.

        Allowed from 'in_progress' or 'pending' state.  Pending tasks
        may be failed when a dependency they are blocked on has already
        failed, preventing them from ever becoming ready.
        """
        with self._conn(immediate=True) as conn:
            task = self._get_task_conn(conn, task_id)
            if task is None:
                raise ValueError(f"Task '{task_id}' not found")

            if task.state not in (TaskState.IN_PROGRESS, TaskState.PENDING):
                raise ValueError(
                    f"Task '{task_id}' is {task.state.value}, "
                    f"expected in_progress or pending"
                )

            now = _now_iso()
            conn.execute(
                "UPDATE tasks SET state = ?, updated_at = ? WHERE id = ?",
                (TaskState.FAILED.value, now, task_id),
            )

            # Return updated copy instead of re-querying the DB
            return task.model_copy(
                update={"state": TaskState.FAILED, "updated_at": now}
            )

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
            # Single query: pending tasks with zero unresolved blockers
            rows = conn.execute(
                f"SELECT {_TASK_COLUMNS_T} "
                "FROM tasks t "
                "WHERE t.state = ? "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM task_dependencies td "
                "  JOIN tasks bt ON td.blocked_by_id = bt.id "
                "  WHERE td.task_id = t.id AND bt.state != ?"
                ") ORDER BY t.created_at",
                (TaskState.PENDING.value, TaskState.COMPLETED.value),
            ).fetchall()
            return self._rows_to_tasks(conn, rows)

    def get_blocked_tasks(self) -> list[TaskItem]:
        """Get all tasks that are pending but have unresolved dependencies."""
        with self._conn() as conn:
            # Single query: pending tasks with at least one unresolved blocker
            rows = conn.execute(
                f"SELECT DISTINCT {_TASK_COLUMNS_T} "
                "FROM tasks t "
                "JOIN task_dependencies td ON td.task_id = t.id "
                "JOIN tasks bt ON td.blocked_by_id = bt.id "
                "WHERE t.state = ? AND bt.state != ? "
                "ORDER BY t.created_at",
                (TaskState.PENDING.value, TaskState.COMPLETED.value),
            ).fetchall()
            return self._rows_to_tasks(conn, rows)

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

        # In-degree based topological grouping (Kahn's algorithm) — O(V+E)
        in_degree: dict[str, int] = {tid: len(deps) for tid, deps in dep_map.items()}
        reverse_map: dict[str, set[str]] = {tid: set() for tid in task_ids}
        for tid, deps in dep_map.items():
            for dep in deps:
                reverse_map[dep].add(tid)

        available: set[str] = {tid for tid, deg in in_degree.items() if deg == 0}
        assigned: set[str] = set()

        # Pre-build position map for O(1) lookup instead of list.index O(n)
        position = {tid: idx for idx, tid in enumerate(task_ids)}

        groups: list[list[TaskItem]] = []
        while available:
            # Preserve creation order within each group
            group_ids = sorted(available, key=lambda t: position[t])
            # Batch-load group tasks in one pass instead of per-task queries
            # (chunked to stay under SQLITE_MAX_VARIABLE_NUMBER)
            group_rows: list[tuple[Any, ...]] = []
            for gi in range(0, len(group_ids), _SQL_CHUNK_SIZE):
                g_chunk = group_ids[gi : gi + _SQL_CHUNK_SIZE]
                placeholders = ",".join("?" for _ in g_chunk)
                group_rows.extend(conn.execute(
                    f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id IN ({placeholders})",
                    g_chunk,
                ).fetchall())
            group_tasks = self._rows_to_tasks(conn, group_rows)
            groups.append(group_tasks)
            assigned.update(group_ids)

            next_available: set[str] = set()
            for tid in group_ids:
                for dependent in reverse_map[tid]:
                    in_degree[dependent] -= 1
                    if in_degree[dependent] == 0:
                        next_available.add(dependent)
            available = next_available

        if len(assigned) < len(task_ids):
            unassigned = task_id_set - assigned
            logger.warning(
                "Cannot schedule %d task(s) — likely cyclic dependency: %s",
                len(unassigned),
                unassigned,
            )

        return groups

    def detect_cycles(self) -> list[list[str]]:
        """Detect cycles in the dependency graph.

        Returns list of cycles found, each cycle as a list of task IDs.
        Empty list means no cycles.

        Uses DFS with visiting/visited two-set technique.
        """
        with self._conn() as conn:
            return self._detect_cycles_conn(conn)

    def _detect_cycles_conn(
        self, conn: sqlite3.Connection,
    ) -> list[list[str]]:
        """Detect cycles using preloaded dependency map (single query)."""
        dep_rows = conn.execute(
            "SELECT task_id, blocked_by_id FROM task_dependencies"
        ).fetchall()
        dep_map: dict[str, list[str]] = {}
        for task_id, blocked_by_id in dep_rows:
            dep_map.setdefault(task_id, []).append(blocked_by_id)
        return detect_cycles_dfs(
            nodes=[row[0] for row in conn.execute("SELECT id FROM tasks")],
            get_deps=lambda name: dep_map.get(name, []),
        )

    def get_snapshot(self) -> TaskGraphSnapshot:
        """Get a full snapshot of the graph state."""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT {_TASK_COLUMNS} FROM tasks ORDER BY created_at"
            ).fetchall()

            tasks = self._rows_to_tasks(conn, rows)

            groups = self.get_parallel_groups(conn=conn)
            group_ids = [[t.id for t in group] for group in groups]

            return TaskGraphSnapshot(tasks=tasks, parallel_groups=group_ids)

    # ------------------------------------------------------------------
    # Async wrappers — offload sync SQLite calls to a worker thread
    # ------------------------------------------------------------------

    async def aget_task(self, task_id: str) -> TaskItem | None:
        """Async wrapper — avoids blocking the event loop."""
        return await asyncio.to_thread(self.get_task, task_id)

    async def aget_ready_tasks(self) -> list[TaskItem]:
        """Async wrapper — avoids blocking the event loop."""
        return await asyncio.to_thread(self.get_ready_tasks)

    async def aget_blocked_tasks(self) -> list[TaskItem]:
        """Async wrapper — avoids blocking the event loop."""
        return await asyncio.to_thread(self.get_blocked_tasks)

    async def aget_parallel_groups(self) -> list[list[TaskItem]]:
        """Async wrapper — avoids blocking the event loop."""
        return await asyncio.to_thread(self.get_parallel_groups)

    async def aget_snapshot(self) -> TaskGraphSnapshot:
        """Async wrapper — avoids blocking the event loop."""
        return await asyncio.to_thread(self.get_snapshot)

    def clear(self) -> None:
        """Clear all tasks (for testing).

        Uses ``immediate=True`` to serialize concurrent writers under
        WAL mode and prevent stale snapshots during the delete.
        """
        with self._conn(immediate=True) as conn:
            conn.execute("DELETE FROM task_dependencies")
            conn.execute("DELETE FROM tasks")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _batch_load_blocked_by(
        conn: sqlite3.Connection,
        task_ids: set[str] | None = None,
    ) -> dict[str, list[str]]:
        """Load task_dependencies in one query, keyed by task_id.

        When *task_ids* is provided, only load dependencies for those tasks.
        """
        if task_ids is not None and not task_ids:
            return {}
        deps: dict[str, list[str]] = {}
        if task_ids:
            tid_list = list(task_ids)
            rows: list[tuple[Any, ...]] = []
            for i in range(0, len(tid_list), _SQL_CHUNK_SIZE):
                chunk = tid_list[i : i + _SQL_CHUNK_SIZE]
                placeholders = ",".join("?" * len(chunk))
                rows.extend(conn.execute(
                    f"SELECT task_id, blocked_by_id FROM task_dependencies "
                    f"WHERE task_id IN ({placeholders})",
                    tuple(chunk),
                ).fetchall())
        else:
            rows = conn.execute(
                "SELECT task_id, blocked_by_id FROM task_dependencies"
            ).fetchall()
        for task_id, blocked_by_id in rows:
            deps.setdefault(task_id, []).append(blocked_by_id)
        return deps

    def _rows_to_tasks(
        self,
        conn: sqlite3.Connection,
        rows: list[tuple[Any, ...]],
    ) -> list[TaskItem]:
        """Convert multiple task rows to TaskItems with batch-loaded deps.

        Uses a single query for ALL blocked_by lists instead of one per row.
        Filters dependencies to only the task IDs present in *rows*.
        """
        row_task_ids = {row[0] for row in rows}
        blocked_map = self._batch_load_blocked_by(conn, task_ids=row_task_ids)
        results: list[TaskItem] = []
        for row in rows:
            try:
                task_id, description, agent, state_str, vars_json, created_at_str, updated_at_str = row
                results.append(TaskItem(
                    id=task_id,
                    description=description,
                    agent=agent,
                    state=TaskState(state_str),
                    vars=json.loads(vars_json) if vars_json else {},
                    blocked_by=blocked_map.get(task_id, []),
                    created_at=datetime.fromisoformat(created_at_str),
                    updated_at=datetime.fromisoformat(updated_at_str),
                ))
            except (ValueError, KeyError, json.JSONDecodeError, TypeError) as exc:
                logger.error(
                    "Corrupt task row (id=%s): %s", row[0] if row else "?", exc
                )
                raise
        return results

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
            f"SELECT {_TASK_COLUMNS} FROM tasks WHERE id = ?",
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

        Preloads all dependencies in a single query so the BFS loop
        does not issue per-node SQL.
        """
        dep_rows = conn.execute(
            "SELECT task_id, blocked_by_id FROM task_dependencies"
        ).fetchall()
        dep_map: dict[str, list[str]] = {}
        for tid, bid in dep_rows:
            dep_map.setdefault(tid, []).append(bid)

        visited: set[str] = set()

        # BFS from blocked_by_ids backwards through their dependencies
        queue = deque(blocked_by_ids)
        while queue:
            node = queue.popleft()
            if node == task_id:
                return True
            if node in visited:
                continue
            visited.add(node)

            for dep_id in dep_map.get(node, []):
                if dep_id not in visited:
                    queue.append(dep_id)

        return False
