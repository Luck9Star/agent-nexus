"""SkillStore -- skill CRUD, lineage, evolution, metrics, and agent records.

Extracted from EvolutionStore to improve cohesion.  Each method operates on
skill_records, skill_lineage_parents, or agent_records tables.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_nexus.models.evolution import (
    EvolutionMetrics,
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)
from agent_nexus.platform.evolution._shared import (
    _SKILL_COLUMNS,
    _chunked_in_fetchall,
)
from agent_nexus.platform.utils import (
    now_iso as _now_iso,
)

if TYPE_CHECKING:
    from agent_nexus.platform.evolution.evolver import EvolveResult

logger = logging.getLogger(__name__)

# Type alias for the connection factory callable used by sub-stores.
# When created standalone, the sub-store builds its own connections.
# When created by EvolutionStore, the facade injects its own _conn.
ConnFactory = Callable[..., AbstractContextManager[sqlite3.Connection]]


class SkillStore:
    """SQLite-backed persistence for skill records, lineage, and agent records.

    Uses WAL mode for concurrent read/write.  Connection-per-operation
    pattern for async compatibility.

    Accepts an optional ``conn_factory`` so the EvolutionStore facade
    can inject its own ``_conn`` method.  This ensures test mocks that
    patch ``EvolutionStore._conn`` propagate to sub-store calls.
    """

    def __init__(
        self,
        db_path: Path,
        conn_factory: ConnFactory | None = None,
    ) -> None:
        self._db_path = db_path
        self._is_memory = str(db_path) == ":memory:"
        self._memory_conn: sqlite3.Connection | None = None
        self._conn_factory: ConnFactory | None = conn_factory

    @contextmanager
    def _conn(self, *, immediate: bool = False) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for DB connections.

        Delegates to the injected ``conn_factory`` when available (facade
        mode), otherwise creates its own connection (standalone mode).
        """
        if self._conn_factory is not None:
            with self._conn_factory(immediate=immediate) as conn:
                yield conn
            return

        # Standalone mode: manage connections internally.
        from agent_nexus.platform.utils import sqlite_connection

        if self._is_memory and self._memory_conn is None:
            self._memory_conn = sqlite3.connect(":memory:")
            self._memory_conn.execute("PRAGMA foreign_keys=ON")

        with sqlite_connection(
            self._db_path,
            immediate=immediate,
            persistent_conn=self._memory_conn,
        ) as conn:
            yield conn

    # ------------------------------------------------------------------
    # Skill Record CRUD
    # ------------------------------------------------------------------

    def save_skill_record(self, record: SkillRecord) -> None:
        """Insert a skill record, or update its metadata on ID conflict.

        On conflict (same ``id``), metadata fields (name, version,
        lineage, directory, is_active) are updated from the new record,
        but **quality counters** (total_selections, total_applied,
        total_completions, total_fallbacks) are **preserved** — they
        are managed atomically via :meth:`increment_counters` and
        :meth:`record_analysis`.
        """
        with self._conn(immediate=True) as conn:
            lin = record.lineage
            snapshot_json = json.dumps(lin.content_snapshot or {}, ensure_ascii=False)
            diff_json = lin.content_diff or ""
            conn.execute(
                """
                INSERT INTO skill_records (
                    id, name, version,
                    lineage_origin, lineage_generation,
                    lineage_content_diff, lineage_content_snapshot,
                    directory, is_active,
                    total_selections, total_applied,
                    total_completions, total_fallbacks,
                    created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    version = excluded.version,
                    lineage_origin = excluded.lineage_origin,
                    lineage_generation = excluded.lineage_generation,
                    lineage_content_diff = excluded.lineage_content_diff,
                    lineage_content_snapshot = excluded.lineage_content_snapshot,
                    directory = excluded.directory,
                    is_active = is_active,
                    total_selections = total_selections,
                    total_applied = total_applied,
                    total_completions = total_completions,
                    total_fallbacks = total_fallbacks,
                    updated_at = excluded.updated_at
                """,
                (
                    record.id,
                    record.name,
                    record.version,
                    lin.origin.value,
                    lin.generation,
                    diff_json,
                    snapshot_json,
                    record.directory,
                    int(record.is_active),
                    record.total_selections,
                    record.total_applied,
                    record.total_completions,
                    record.total_fallbacks,
                    record.first_seen.isoformat(),
                    record.last_updated.isoformat(),
                ),
            )
            # Sync lineage parents
            conn.execute(
                "DELETE FROM skill_lineage_parents WHERE skill_id = ?",
                (record.id,),
            )
            if lin.parent_skill_ids:
                conn.executemany(
                    "INSERT INTO skill_lineage_parents (skill_id, parent_id) VALUES (?, ?)",
                    [(record.id, pid) for pid in lin.parent_skill_ids],
                )

    def get_skill_record(self, skill_id: str) -> SkillRecord | None:
        """Load a single skill record by ID."""
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT {_SKILL_COLUMNS} FROM skill_records WHERE id = ?",
                (skill_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_record(conn, row)

    def get_skill_records_batch(self, skill_ids: list[str]) -> dict[str, SkillRecord]:
        """Load multiple skill records by ID in a single query."""
        if not skill_ids:
            return {}
        with self._conn() as conn:
            rows = _chunked_in_fetchall(
                conn,
                f"SELECT {_SKILL_COLUMNS} FROM skill_records WHERE id IN ({{IN}})",
                skill_ids,
            )
            found_ids = {row[0] for row in rows}
            parents = self._batch_load_parents(conn, found_ids)
            result: dict[str, SkillRecord] = {}
            for row in rows:
                record = self._row_to_record(conn, row, parents)
                result[record.id] = record
            return result

    def get_active_skills(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[SkillRecord]:
        """Load active skill records, optionally paginated."""
        with self._conn() as conn:
            sql = f"SELECT {_SKILL_COLUMNS} FROM skill_records WHERE is_active = 1"
            params: list[Any] = []
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])
            rows = conn.execute(sql, params if params else ()).fetchall()
            active_ids = {row[0] for row in rows}
            parents = self._batch_load_parents(conn, active_ids)
            return self._rows_to_records(conn, rows, parents)

    def get_all_skills(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[SkillRecord]:
        """Load all skill records (including inactive), optionally paginated."""
        with self._conn() as conn:
            sql = f"SELECT {_SKILL_COLUMNS} FROM skill_records"
            params: list[Any] = []
            if limit is not None:
                sql += " LIMIT ? OFFSET ?"
                params.extend([limit, offset])
            rows = conn.execute(sql, params if params else ()).fetchall()
            all_ids = {row[0] for row in rows}
            parents = self._batch_load_parents(conn, all_ids)
            return self._rows_to_records(conn, rows, parents)

    def deactivate_skill(self, skill_id: str) -> bool:
        """Set is_active = False for a skill record."""
        with self._conn(immediate=True) as conn:
            cur = conn.execute(
                "UPDATE skill_records SET is_active = 0, updated_at = ? WHERE id = ?",
                (_now_iso(), skill_id),
            )
            return cur.rowcount > 0

    def get_versions(self, name: str) -> list[SkillRecord]:
        """Load all versions of a named skill, sorted by generation."""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT {_SKILL_COLUMNS} FROM skill_records WHERE name = ? "
                "ORDER BY lineage_generation ASC",
                (name,),
            ).fetchall()
            version_ids = {row[0] for row in rows}
            parents = self._batch_load_parents(conn, version_ids)
            return self._rows_to_records(conn, rows, parents)

    # ------------------------------------------------------------------
    # Atomic counter increments
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_counter_invariants(
        *,
        selected: bool,
        applied: bool,
        completed: bool,
        fell_back: bool,
    ) -> None:
        """Validate counter prerequisite invariants."""
        if applied and not selected:
            raise ValueError("applied requires selected=True")
        if completed and not applied:
            raise ValueError("completed requires applied=True")
        if fell_back and not selected:
            raise ValueError("fell_back requires selected=True")

    def increment_counters(
        self,
        skill_id: str,
        *,
        selected: bool = False,
        applied: bool = False,
        completed: bool = False,
        fell_back: bool = False,
    ) -> None:
        """Atomically increment quality counters for a skill."""
        self._validate_counter_invariants(
            selected=selected,
            applied=applied,
            completed=completed,
            fell_back=fell_back,
        )

        sets: list[str] = []
        params: list[str] = []
        if selected:
            sets.append("total_selections = total_selections + 1")
        if applied:
            sets.append("total_applied = total_applied + 1")
        if completed:
            sets.append("total_completions = total_completions + 1")
        if fell_back:
            sets.append("total_fallbacks = total_fallbacks + 1")

        if not sets:
            return

        with self._conn(immediate=True) as conn:
            sets.append("updated_at = ?")
            params.append(_now_iso())
            params.append(skill_id)

            cur = conn.execute(
                f"UPDATE skill_records SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )
            if cur.rowcount == 0:
                logger.warning(
                    "increment_counters: skill_id %s not found — counters not updated",
                    skill_id,
                )

    # ------------------------------------------------------------------
    # Evolution (new version = new record, deactivate old)
    # ------------------------------------------------------------------

    def evolve_skill(
        self,
        new_record: SkillRecord,
        parent_skill_ids: list[str],
    ) -> EvolveResult:
        """Atomic evolution: insert new version, deactivate old for FIX."""
        from agent_nexus.platform.evolution._shared import _SQL_CHUNK_SIZE
        from agent_nexus.platform.evolution.evolver import EvolveResult

        try:
            with self._conn(immediate=True) as conn:
                if new_record.lineage.origin == SkillOrigin.FIXED:
                    if parent_skill_ids:
                        found = {
                            r[0]
                            for r in _chunked_in_fetchall(
                                conn,
                                "SELECT id FROM skill_records WHERE id IN ({IN})",
                                parent_skill_ids,
                            )
                        }
                        missing = set(parent_skill_ids) - found
                        if missing:
                            raise ValueError(
                                f"Parent skill_id(s) not found: {missing} — "
                                f"cannot deactivate for FIX evolution"
                            )

                    if parent_skill_ids:
                        now = _now_iso()
                        for ci in range(0, len(parent_skill_ids), _SQL_CHUNK_SIZE):
                            chunk = parent_skill_ids[ci : ci + _SQL_CHUNK_SIZE]
                            ph = ",".join("?" * len(chunk))
                            conn.execute(
                                f"UPDATE skill_records SET is_active = 0, updated_at = ? "
                                f"WHERE id IN ({ph})",
                                (now, *chunk),
                            )

                    dup = conn.execute(
                        "SELECT id FROM skill_records WHERE name = ? AND is_active = 1 AND id != ?",
                        (new_record.name, new_record.id),
                    ).fetchone()
                    if dup is not None:
                        raise ValueError(
                            f"Duplicate active skill: '{new_record.name}' "
                            f"(id={dup[0]}) already active"
                        )

                lin = new_record.lineage
                snapshot_json = json.dumps(lin.content_snapshot or {}, ensure_ascii=False)
                conn.execute(
                    """
                    INSERT INTO skill_records (
                        id, name, version,
                        lineage_origin, lineage_generation,
                        lineage_content_diff, lineage_content_snapshot,
                        directory, is_active,
                        total_selections, total_applied,
                        total_completions, total_fallbacks,
                        created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        new_record.id,
                        new_record.name,
                        new_record.version,
                        lin.origin.value,
                        lin.generation,
                        lin.content_diff or "",
                        snapshot_json,
                        new_record.directory,
                        int(new_record.is_active),
                        new_record.total_selections,
                        new_record.total_applied,
                        new_record.total_completions,
                        new_record.total_fallbacks,
                        new_record.first_seen.isoformat(),
                        new_record.last_updated.isoformat(),
                    ),
                )

                if parent_skill_ids:
                    conn.executemany(
                        "INSERT INTO skill_lineage_parents (skill_id, parent_id) VALUES (?, ?)",
                        [(new_record.id, pid) for pid in parent_skill_ids],
                    )

        except sqlite3.IntegrityError:
            logger.warning("Skill ID collision during evolution: %s", new_record.id)
            return EvolveResult(
                success=False,
                error=f"Skill ID collision: {new_record.id}",
            )
        except ValueError as exc:
            logger.warning("evolve_skill validation failed: %s", exc)
            return EvolveResult(success=False, error=str(exc))
        except sqlite3.Error as exc:
            logger.error("Database error during skill evolution: %s", exc, exc_info=True)
            return EvolveResult(
                success=False,
                error=f"Database error during evolution: {exc}",
            )

        return EvolveResult(success=True, new_record=new_record)

    # ------------------------------------------------------------------
    # Lineage queries
    # ------------------------------------------------------------------

    def get_ancestry_batch(
        self, skill_ids: list[str], max_depth: int = 10
    ) -> dict[str, list[SkillRecord]]:
        """Walk up lineage trees for multiple skills in a single connection."""
        if not skill_ids:
            return {}
        with self._conn() as conn:
            visited_per_skill: dict[str, set[str]] = {sid: set() for sid in skill_ids}
            frontiers: dict[str, list[str]] = {sid: [sid] for sid in skill_ids}

            for _ in range(max_depth):
                all_frontier_ids: set[str] = set()
                for sid in skill_ids:
                    all_frontier_ids.update(frontiers[sid])

                if not all_frontier_ids:
                    break

                round_parents = self._batch_load_parents(conn, all_frontier_ids)

                next_frontiers: dict[str, list[str]] = {sid: [] for sid in skill_ids}
                any_progress = False
                for sid in skill_ids:
                    for fid in frontiers[sid]:
                        for pid in round_parents.get(fid, []):
                            if pid not in visited_per_skill[sid]:
                                visited_per_skill[sid].add(pid)
                                next_frontiers[sid].append(pid)
                                any_progress = True
                frontiers = next_frontiers
                if not any_progress:
                    break

            all_ancestor_ids: set[str] = set()
            for s in visited_per_skill.values():
                all_ancestor_ids.update(s)

            if not all_ancestor_ids:
                return {sid: [] for sid in skill_ids}

            rows = _chunked_in_fetchall(
                conn,
                f"SELECT {_SKILL_COLUMNS} FROM skill_records WHERE id IN ({{IN}})",
                list(all_ancestor_ids),
            )

            ancestors_ids = {r[0] for r in rows}
            parents = self._batch_load_parents(conn, ancestors_ids)
            records_by_id: dict[str, SkillRecord] = {}
            for row in rows:
                record = self._row_to_record(conn, row, parents)
                records_by_id[record.id] = record

            result: dict[str, list[SkillRecord]] = {}
            for sid in skill_ids:
                ancestors = [
                    records_by_id[aid] for aid in visited_per_skill[sid] if aid in records_by_id
                ]
                ancestors.sort(key=lambda r: r.lineage.generation)
                result[sid] = ancestors
            return result

    def get_ancestry(self, skill_id: str, max_depth: int = 10) -> list[SkillRecord]:
        """Walk up the lineage tree, returns ancestors oldest-first."""
        with self._conn() as conn:
            visited: set[str] = set()
            frontier = [skill_id]

            for _ in range(max_depth):
                if not frontier:
                    break
                round_parents = self._batch_load_parents(conn, set(frontier))
                next_frontier: list[str] = []
                for sid in frontier:
                    for pid in round_parents.get(sid, []):
                        if pid in visited:
                            continue
                        visited.add(pid)
                        next_frontier.append(pid)
                frontier = next_frontier

            if not visited:
                return []

            rows = _chunked_in_fetchall(
                conn,
                f"SELECT {_SKILL_COLUMNS} FROM skill_records WHERE id IN ({{IN}})",
                list(visited),
            )

            ancestor_ids = {r[0] for r in rows}
            parents = self._batch_load_parents(conn, ancestor_ids)
            ancestors: list[SkillRecord] = []
            for row in rows:
                ancestors.append(self._row_to_record(conn, row, parents))

            ancestors.sort(key=lambda r: r.lineage.generation)
            return ancestors

    def get_children(self, parent_id: str) -> list[str]:
        """Find skill IDs derived from the given parent."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT skill_id FROM skill_lineage_parents WHERE parent_id = ?",
                (parent_id,),
            ).fetchall()
            return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def get_metrics(self, agent_name: str | None = None) -> EvolutionMetrics:
        """Aggregate quality metrics across active skills."""
        with self._conn() as conn:
            if agent_name:
                escaped = agent_name.replace("%", "\\%").replace("_", "\\_")
                row = conn.execute(
                    "SELECT COALESCE(SUM(total_selections), 0), "
                    "COALESCE(SUM(total_applied), 0), "
                    "COALESCE(SUM(total_completions), 0), "
                    "COALESCE(SUM(total_fallbacks), 0) "
                    "FROM skill_records WHERE is_active = 1 "
                    "AND (directory LIKE ? ESCAPE '\\' OR directory = ?)",
                    (f"agents/{escaped}/%", f"agents/{escaped}"),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COALESCE(SUM(total_selections), 0), "
                    "COALESCE(SUM(total_applied), 0), "
                    "COALESCE(SUM(total_completions), 0), "
                    "COALESCE(SUM(total_fallbacks), 0) "
                    "FROM skill_records WHERE is_active = 1"
                ).fetchone()

            total_sel, total_app, total_comp, total_fb = row

            return EvolutionMetrics(
                total_selections=total_sel,
                total_applied=total_app,
                total_completions=total_comp,
                total_fallbacks=total_fb,
            )

    # ------------------------------------------------------------------
    # Agent Records (Composite Agent evolution, Layer 2)
    # ------------------------------------------------------------------

    def save_agent_record(
        self,
        agent_id: str,
        name: str,
        type: str,
        skill_ids: list[str],
        orchestration_toml: str | None = None,
    ) -> None:
        """Insert or update an agent record."""
        skill_ids_json = json.dumps(skill_ids, ensure_ascii=False)
        now = _now_iso()
        with self._conn(immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO agent_records (
                    agent_id, name, type, skill_ids, orchestration_toml,
                    effective_rate, avg_steps, avg_duration_ms,
                    is_active, created_at, updated_at
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    0.0, NULL, NULL,
                    1, ?, ?
                )
                ON CONFLICT(agent_id) DO UPDATE SET
                    name = excluded.name,
                    type = excluded.type,
                    skill_ids = excluded.skill_ids,
                    orchestration_toml = COALESCE(
                        excluded.orchestration_toml, agent_records.orchestration_toml
                    ),
                    effective_rate = CASE
                        WHEN agent_records.effective_rate IS NOT NULL
                        THEN agent_records.effective_rate ELSE 0.0
                    END,
                    avg_steps = agent_records.avg_steps,
                    avg_duration_ms = agent_records.avg_duration_ms,
                    is_active = agent_records.is_active,
                    created_at = agent_records.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    agent_id,
                    name,
                    type,
                    skill_ids_json,
                    orchestration_toml,
                    now,
                    now,
                ),
            )

    def get_agent_record(self, agent_id: str) -> dict[str, Any] | None:
        """Load an agent record by ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT agent_id, name, type, skill_ids, orchestration_toml, "
                "effective_rate, avg_steps, avg_duration_ms, is_active, "
                "created_at, updated_at "
                "FROM agent_records WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
            if row is None:
                return None
            return self._agent_row_to_dict(row)

    def get_active_agents(self) -> list[dict[str, Any]]:
        """Load all active agent records."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT agent_id, name, type, skill_ids, orchestration_toml, "
                "effective_rate, avg_steps, avg_duration_ms, is_active, "
                "created_at, updated_at "
                "FROM agent_records WHERE is_active = 1"
            ).fetchall()
            return [self._agent_row_to_dict(r) for r in rows]

    def update_agent_metrics(
        self,
        agent_id: str,
        effective_rate: float,
        avg_steps: float,
        avg_duration_ms: float,
    ) -> bool:
        """Update computed metrics for an agent record."""
        with self._conn(immediate=True) as conn:
            cur = conn.execute(
                "UPDATE agent_records SET effective_rate = ?, "
                "avg_steps = ?, avg_duration_ms = ?, updated_at = ? "
                "WHERE agent_id = ?",
                (effective_rate, avg_steps, avg_duration_ms, _now_iso(), agent_id),
            )
            return cur.rowcount > 0

    def deactivate_agent(self, agent_id: str) -> bool:
        """Set is_active = False for an agent record."""
        with self._conn(immediate=True) as conn:
            cur = conn.execute(
                "UPDATE agent_records SET is_active = 0, updated_at = ? WHERE agent_id = ?",
                (_now_iso(), agent_id),
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Row-to-dict helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _agent_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
        """Convert an 11-column agent_records row to a dict."""
        return {
            "agent_id": row[0],
            "name": row[1],
            "type": row[2],
            "skill_ids": json.loads(row[3]) if row[3] else [],
            "orchestration_toml": row[4],
            "effective_rate": row[5],
            "avg_steps": row[6],
            "avg_duration_ms": row[7],
            "is_active": bool(row[8]),
            "created_at": row[9],
            "updated_at": row[10],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _batch_load_parents(
        conn: sqlite3.Connection,
        skill_ids: set[str] | None = None,
    ) -> dict[str, list[str]]:
        """Load skill_lineage_parents, optionally filtered by skill_ids."""
        parents: dict[str, list[str]] = {}
        if skill_ids:
            rows = _chunked_in_fetchall(
                conn,
                "SELECT skill_id, parent_id FROM skill_lineage_parents WHERE skill_id IN ({IN})",
                list(skill_ids),
            )
        else:
            rows = conn.execute("SELECT skill_id, parent_id FROM skill_lineage_parents").fetchall()
        for skill_id, parent_id in rows:
            parents.setdefault(skill_id, []).append(parent_id)
        return parents

    def _row_to_record(
        self,
        conn: sqlite3.Connection,
        row: tuple[Any, ...],
        parents_by_id: dict[str, list[str]] | None = None,
    ) -> SkillRecord:
        """Convert a skill_records row to a SkillRecord model."""
        (
            skill_id,
            name,
            version,
            lineage_origin,
            lineage_generation,
            lineage_content_diff,
            lineage_content_snapshot,
            directory,
            is_active,
            total_selections,
            total_applied,
            total_completions,
            total_fallbacks,
            created_at,
            updated_at,
        ) = row

        if parents_by_id is not None:
            parent_ids = parents_by_id.get(skill_id, [])
        else:
            parent_rows = conn.execute(
                "SELECT parent_id FROM skill_lineage_parents WHERE skill_id = ?",
                (skill_id,),
            ).fetchall()
            parent_ids = [r[0] for r in parent_rows]

        snapshot: dict[str, str] = {}
        if lineage_content_snapshot and lineage_content_snapshot not in ('""', "{}", "null"):
            try:
                loaded = json.loads(lineage_content_snapshot)
                if isinstance(loaded, dict) and loaded:
                    if all(isinstance(v, str) for v in loaded.values()):
                        snapshot = loaded
                    else:
                        non_str = [k for k, v in loaded.items() if not isinstance(v, str)]
                        logger.warning(
                            "content_snapshot for skill '%s' has non-string "
                            "values in keys %s, discarding snapshot",
                            skill_id,
                            non_str,
                        )
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                logger.warning(
                    "Corrupted content_snapshot for skill '%s': %s",
                    skill_id,
                    exc,
                )

        try:
            origin = SkillOrigin(lineage_origin)
        except ValueError:
            logger.warning(
                "Invalid lineage_origin '%s' for skill '%s', defaulting to CAPTURED",
                lineage_origin,
                skill_id,
            )
            origin = SkillOrigin.CAPTURED

        lineage = SkillLineage(
            origin=origin,
            generation=lineage_generation,
            parent_skill_ids=parent_ids,
            content_diff=lineage_content_diff,
            content_snapshot=snapshot or None,
        )

        return SkillRecord(
            id=skill_id,
            name=name,
            version=version,
            lineage=lineage,
            directory=directory or "",
            is_active=bool(is_active),
            total_selections=total_selections,
            total_applied=total_applied,
            total_completions=total_completions,
            total_fallbacks=total_fallbacks,
            first_seen=datetime.fromisoformat(created_at),
            last_updated=datetime.fromisoformat(updated_at),
        )

    def _rows_to_records(
        self,
        conn: sqlite3.Connection,
        rows: list[tuple[Any, ...]],
        parents_by_id: dict[str, list[str]] | None = None,
    ) -> list[SkillRecord]:
        """Convert multiple rows to SkillRecords, skipping corrupt rows."""
        records: list[SkillRecord] = []
        for row in rows:
            try:
                records.append(self._row_to_record(conn, row, parents_by_id))
            except Exception as exc:
                row_id = row[0] if row else "<empty>"
                logger.warning(
                    "Skipping corrupt skill_records row '%s': %s",
                    row_id,
                    exc,
                )
        return records
