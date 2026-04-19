"""EvolutionStore -- SQLite persistence for skill records and evolution data.

Architecture mirrors TaskGraph: WAL mode, connection-per-operation, no
shared state between calls.  All write operations are atomic within a
single transaction.

Tables:
    skill_records          -- Skill identity + lineage + quality counters
    skill_lineage_parents  -- DAG edges (many-to-many)
    execution_analyses     -- Post-task analysis (one per task per agent)
    skill_judgments        -- Per-skill assessment within an analysis
    context_budget_log     -- Token usage / compaction observability
    agent_records          -- Composite Agent evolution tracking (Layer 2)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from agent_nexus.models.evolution import (
    EvolutionMetrics,
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_nexus.platform.evolution.evolver import EvolveResult


logger = logging.getLogger(__name__)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS skill_records (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '1.0.0',
    lineage_origin TEXT NOT NULL DEFAULT 'imported',
    lineage_generation INTEGER NOT NULL DEFAULT 0,
    lineage_content_diff TEXT,
    lineage_content_snapshot TEXT,
    directory TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    total_selections INTEGER NOT NULL DEFAULT 0,
    total_applied INTEGER NOT NULL DEFAULT 0,
    total_completions INTEGER NOT NULL DEFAULT 0,
    total_fallbacks INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sr_active ON skill_records(is_active);
CREATE INDEX IF NOT EXISTS idx_sr_name ON skill_records(name);
CREATE INDEX IF NOT EXISTS idx_sr_updated ON skill_records(updated_at);

CREATE TABLE IF NOT EXISTS skill_lineage_parents (
    skill_id TEXT NOT NULL,
    parent_id TEXT NOT NULL,
    PRIMARY KEY (skill_id, parent_id),
    FOREIGN KEY (skill_id) REFERENCES skill_records(id),
    FOREIGN KEY (parent_id) REFERENCES skill_records(id)
);
CREATE INDEX IF NOT EXISTS idx_lp_parent ON skill_lineage_parents(parent_id);

CREATE TABLE IF NOT EXISTS execution_analyses (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    analysis TEXT NOT NULL,
    evolution_suggestions TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ea_task ON execution_analyses(task_id);

CREATE TABLE IF NOT EXISTS skill_judgments (
    id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    selected INTEGER NOT NULL DEFAULT 0,
    applied INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    fell_back INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (analysis_id) REFERENCES execution_analyses(id)
);
CREATE INDEX IF NOT EXISTS idx_sj_skill ON skill_judgments(skill_id);
CREATE INDEX IF NOT EXISTS idx_sj_analysis ON skill_judgments(analysis_id);

CREATE TABLE IF NOT EXISTS context_budget_log (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    tokens_before INTEGER,
    tokens_after INTEGER,
    details TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cbl_agent ON context_budget_log(agent_name);

CREATE TABLE IF NOT EXISTS agent_records (
    agent_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'atomic',
    skill_ids TEXT DEFAULT '[]',
    orchestration_toml TEXT,
    effective_rate REAL DEFAULT 0.0,
    avg_steps REAL,
    avg_duration_ms REAL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ar_active ON agent_records(is_active);
CREATE INDEX IF NOT EXISTS idx_ar_name ON agent_records(name);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class EvolutionStore:
    """SQLite-backed persistence for the Self-Evolution Engine.

    Uses WAL mode for concurrent read/write.  Connection-per-operation
    pattern (same as TaskGraph) for async compatibility.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(_SCHEMA_SQL)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
        )
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            logger.exception("DB commit failed in evolution store context")
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Skill Record CRUD
    # ------------------------------------------------------------------

    def save_skill_record(self, record: SkillRecord) -> None:
        """Insert or replace a skill record."""
        with self._conn() as conn:
            lin = record.lineage
            snapshot_json = json.dumps(
                lin.content_snapshot or {}, ensure_ascii=False
            )
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
                    is_active = excluded.is_active,
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
            for pid in lin.parent_skill_ids:
                conn.execute(
                    "INSERT INTO skill_lineage_parents (skill_id, parent_id) "
                    "VALUES (?, ?)",
                    (record.id, pid),
                )

    def get_skill_record(self, skill_id: str) -> SkillRecord | None:
        """Load a single skill record by ID."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, name, version, lineage_origin, lineage_generation, "
                "lineage_content_diff, lineage_content_snapshot, directory, "
                "is_active, total_selections, total_applied, total_completions, "
                "total_fallbacks, created_at, updated_at "
                "FROM skill_records WHERE id = ?",
                (skill_id,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_record(conn, row)

    def get_active_skills(self) -> list[SkillRecord]:
        """Load all active skill records."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, name, version, lineage_origin, lineage_generation, "
                "lineage_content_diff, lineage_content_snapshot, directory, "
                "is_active, total_selections, total_applied, total_completions, "
                "total_fallbacks, created_at, updated_at "
                "FROM skill_records WHERE is_active = 1"
            ).fetchall()
            return [self._row_to_record(conn, r) for r in rows]

    def get_all_skills(self) -> list[SkillRecord]:
        """Load all skill records (including inactive)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, name, version, lineage_origin, lineage_generation, "
                "lineage_content_diff, lineage_content_snapshot, directory, "
                "is_active, total_selections, total_applied, total_completions, "
                "total_fallbacks, created_at, updated_at "
                "FROM skill_records"
            ).fetchall()
            return [self._row_to_record(conn, r) for r in rows]

    def deactivate_skill(self, skill_id: str) -> bool:
        """Set is_active = False for a skill record."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE skill_records SET is_active = 0, updated_at = ? "
                "WHERE id = ?",
                (_now_iso(), skill_id),
            )
            return cur.rowcount > 0

    def get_versions(self, name: str) -> list[SkillRecord]:
        """Load all versions of a named skill, sorted by generation."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, name, version, lineage_origin, lineage_generation, "
                "lineage_content_diff, lineage_content_snapshot, directory, "
                "is_active, total_selections, total_applied, total_completions, "
                "total_fallbacks, created_at, updated_at "
                "FROM skill_records WHERE name = ? "
                "ORDER BY lineage_generation ASC",
                (name,),
            ).fetchall()
            return [self._row_to_record(conn, r) for r in rows]

    # ------------------------------------------------------------------
    # Atomic counter increments
    # ------------------------------------------------------------------

    def increment_counters(
        self,
        skill_id: str,
        *,
        selected: bool = False,
        applied: bool = False,
        completed: bool = False,
        fell_back: bool = False,
    ) -> None:
        """Atomically increment quality counters for a skill.

        Called within the same transaction as judgment insert.
        """
        # Validate counter invariants: each flag requires its prerequisite
        if fell_back and not applied:
            raise ValueError("fell_back requires applied=True")
        if applied and not selected:
            raise ValueError("applied requires selected=True")
        if completed and not applied:
            raise ValueError("completed requires applied=True")

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

        with self._conn() as conn:
            sets.append("updated_at = ?")
            params.append(_now_iso())
            params.append(skill_id)

            conn.execute(
                f"UPDATE skill_records SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )

    # ------------------------------------------------------------------
    # Analysis + Judgments (atomic)
    # ------------------------------------------------------------------

    def record_analysis(
        self,
        task_id: str,
        agent_name: str,
        analysis_text: str,
        evolution_suggestions: list[dict[str, Any]] | None = None,
        judgments: list[dict[str, Any]] | None = None,
    ) -> str:
        """Insert analysis + judgments + update counters in one transaction.

        Args:
            task_id: The task being analyzed.
            agent_name: Agent that executed the task.
            analysis_text: LLM analysis text.
            evolution_suggestions: List of evolution suggestion dicts.
            judgments: List of judgment dicts with keys:
                skill_id, selected, applied, completed, fell_back.

        Returns:
            The analysis ID.
        """
        analysis_id = str(uuid.uuid4())
        now = _now_iso()
        suggestions_json = json.dumps(
            evolution_suggestions or [], ensure_ascii=False
        )

        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO execution_analyses (
                    id, task_id, agent_name, analysis,
                    evolution_suggestions, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    analysis_id,
                    task_id,
                    agent_name,
                    analysis_text,
                    suggestions_json,
                    now,
                ),
            )

            for j in judgments or []:
                sid = j.get("skill_id")
                if not sid:
                    continue
                j_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO skill_judgments (
                        id, analysis_id, skill_id,
                        selected, applied, completed, fell_back
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        j_id,
                        analysis_id,
                        sid,
                        int(j.get("selected", False)),
                        int(j.get("applied", False)),
                        int(j.get("completed", False)),
                        int(j.get("fell_back", False)),
                    ),
                )

                # Atomically increment counters for this skill
                selected = bool(j.get("selected", False))
                applied = bool(j.get("applied", False))
                completed = bool(j.get("completed", False))
                fell_back = bool(j.get("fell_back", False))

                sets: list[str] = []
                params: list[Any] = []
                if selected:
                    sets.append("total_selections = total_selections + 1")
                if applied:
                    sets.append("total_applied = total_applied + 1")
                if completed:
                    sets.append("total_completions = total_completions + 1")
                if fell_back:
                    sets.append("total_fallbacks = total_fallbacks + 1")
                if sets:
                    sets.append("updated_at = ?")
                    params.append(_now_iso())
                    params.append(sid)
                    conn.execute(
                        f"UPDATE skill_records SET {', '.join(sets)} WHERE id = ?",
                        tuple(params),
                    )

        return analysis_id

    def get_analyses_for_task(
        self, task_id: str
    ) -> list[dict[str, Any]]:
        """Load all analyses for a given task."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, task_id, agent_name, analysis, "
                "evolution_suggestions, created_at "
                "FROM execution_analyses WHERE task_id = ?",
                (task_id,),
            ).fetchall()
            return [self._row_to_analysis_dict(conn, r) for r in rows]

    def get_judgments_for_skill(
        self, skill_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Load recent judgments for a skill."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, analysis_id, skill_id, selected, applied, "
                "completed, fell_back FROM skill_judgments "
                "WHERE skill_id = ? ORDER BY rowid DESC LIMIT ?",
                (skill_id, limit),
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "analysis_id": r[1],
                    "skill_id": r[2],
                    "selected": bool(r[3]),
                    "applied": bool(r[4]),
                    "completed": bool(r[5]),
                    "fell_back": bool(r[6]),
                }
                for r in rows
            ]

    # ------------------------------------------------------------------
    # Context Budget Log
    # ------------------------------------------------------------------

    def log_budget_event(
        self,
        agent_name: str,
        event_type: str,
        tokens_before: int | None = None,
        tokens_after: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> str:
        """Record a context budget event."""
        log_id = str(uuid.uuid4())
        details_json = json.dumps(details or {}, ensure_ascii=False)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO context_budget_log (
                    id, agent_name, event_type,
                    tokens_before, tokens_after, details, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log_id,
                    agent_name,
                    event_type,
                    tokens_before,
                    tokens_after,
                    details_json,
                    _now_iso(),
                ),
            )
        return log_id

    def get_budget_log(
        self, agent_name: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Load recent budget log entries for an agent."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, agent_name, event_type, tokens_before, "
                "tokens_after, details, created_at "
                "FROM context_budget_log WHERE agent_name = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (agent_name, limit),
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "agent_name": r[1],
                    "event_type": r[2],
                    "tokens_before": r[3],
                    "tokens_after": r[4],
                    "details": r[5],
                    "created_at": r[6],
                }
                for r in rows
            ]

    # ------------------------------------------------------------------
    # Evolution (new version = new record, deactivate old)
    # ------------------------------------------------------------------

    def evolve_skill(
        self,
        new_record: SkillRecord,
        parent_skill_ids: list[str],
    ) -> EvolveResult:
        """Atomic evolution: insert new version, deactivate old for FIX.

        For FIX: parent is deactivated (same name, same directory).
        For DERIVED: parent stays active (new name, new directory).
        For CAPTURED: no parents (parent_skill_ids empty).
        """
        from agent_nexus.platform.evolution.evolver import EvolveResult
        with self._conn() as conn:
            # For FIX: deactivate parent(s)
            if new_record.lineage.origin == SkillOrigin.FIXED:
                for pid in parent_skill_ids:
                    conn.execute(
                        "UPDATE skill_records SET is_active = 0, updated_at = ? "
                        "WHERE id = ?",
                        (_now_iso(), pid),
                    )

            # Insert new record — evolved skills always have unique IDs
            # (uuid-suffixed), so plain INSERT is sufficient.
            lin = new_record.lineage
            snapshot_json = json.dumps(
                lin.content_snapshot or {}, ensure_ascii=False
            )
            try:
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
            except sqlite3.IntegrityError:
                return EvolveResult(
                    success=False,
                    error=f"Skill ID collision: {new_record.id}",
                )

            # Insert lineage parents
            for pid in parent_skill_ids:
                conn.execute(
                    "INSERT INTO skill_lineage_parents "
                    "(skill_id, parent_id) VALUES (?, ?)",
                    (new_record.id, pid),
                )

            return EvolveResult(success=True, new_record=new_record)

    # ------------------------------------------------------------------
    # Lineage queries
    # ------------------------------------------------------------------

    def get_ancestry(
        self, skill_id: str, max_depth: int = 10
    ) -> list[SkillRecord]:
        """Walk up the lineage tree, returns ancestors oldest-first."""
        with self._conn() as conn:
            visited: set[str] = set()
            ancestors: list[SkillRecord] = []
            frontier = [skill_id]

            for _ in range(max_depth):
                next_frontier: list[str] = []
                for sid in frontier:
                    rows = conn.execute(
                        "SELECT parent_id FROM skill_lineage_parents "
                        "WHERE skill_id = ?",
                        (sid,),
                    ).fetchall()
                    for (pid,) in rows:
                        if pid in visited:
                            continue
                        visited.add(pid)
                        row = conn.execute(
                            "SELECT id, name, version, lineage_origin, "
                            "lineage_generation, lineage_content_diff, "
                            "lineage_content_snapshot, directory, is_active, "
                            "total_selections, total_applied, total_completions, "
                            "total_fallbacks, created_at, updated_at "
                            "FROM skill_records WHERE id = ?",
                            (pid,),
                        ).fetchone()
                        if row:
                            ancestors.append(
                                self._row_to_record(conn, row)
                            )
                            next_frontier.append(pid)
                frontier = next_frontier
                if not frontier:
                    break

            ancestors.sort(key=lambda r: r.lineage.generation)
            return ancestors

    def get_children(self, parent_id: str) -> list[str]:
        """Find skill IDs derived from the given parent."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT skill_id FROM skill_lineage_parents "
                "WHERE parent_id = ?",
                (parent_id,),
            ).fetchall()
            return [r[0] for r in rows]

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    def get_metrics(self, agent_name: str | None = None) -> EvolutionMetrics:
        """Aggregate quality metrics across active skills.

        If agent_name is given, filter by skills belonging to that agent's
        directory pattern.  Otherwise aggregate all active skills.
        """
        with self._conn() as conn:
            if agent_name:
                # Escape LIKE wildcards to prevent unintended matches
                escaped = agent_name.replace("%", "\\%").replace("_", "\\_")
                rows = conn.execute(
                    "SELECT total_selections, total_applied, "
                    "total_completions, total_fallbacks "
                    "FROM skill_records WHERE is_active = 1 "
                    "AND (directory LIKE ? ESCAPE '\\' OR directory = ?)",
                    (f"agents/{escaped}/%", f"agents/{escaped}"),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT total_selections, total_applied, "
                    "total_completions, total_fallbacks "
                    "FROM skill_records WHERE is_active = 1"
                ).fetchall()

            total_sel = sum(r[0] for r in rows)
            total_app = sum(r[1] for r in rows)
            total_comp = sum(r[2] for r in rows)
            total_fb = sum(r[3] for r in rows)

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
        with self._conn() as conn:
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
                    orchestration_toml = COALESCE(excluded.orchestration_toml, agent_records.orchestration_toml),
                    effective_rate = CASE WHEN agent_records.effective_rate IS NOT NULL THEN agent_records.effective_rate ELSE 0.0 END,
                    avg_steps = agent_records.avg_steps,
                    avg_duration_ms = agent_records.avg_duration_ms,
                    is_active = agent_records.is_active,
                    created_at = agent_records.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    agent_id, name, type, skill_ids_json, orchestration_toml,
                    now, now,
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

    def get_active_agents(self) -> list[dict[str, Any]]:
        """Load all active agent records."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT agent_id, name, type, skill_ids, orchestration_toml, "
                "effective_rate, avg_steps, avg_duration_ms, is_active, "
                "created_at, updated_at "
                "FROM agent_records WHERE is_active = 1"
            ).fetchall()
            return [
                {
                    "agent_id": r[0],
                    "name": r[1],
                    "type": r[2],
                    "skill_ids": json.loads(r[3]) if r[3] else [],
                    "orchestration_toml": r[4],
                    "effective_rate": r[5],
                    "avg_steps": r[6],
                    "avg_duration_ms": r[7],
                    "is_active": bool(r[8]),
                    "created_at": r[9],
                    "updated_at": r[10],
                }
                for r in rows
            ]

    def update_agent_metrics(
        self,
        agent_id: str,
        effective_rate: float,
        avg_steps: float,
        avg_duration_ms: float,
    ) -> bool:
        """Update computed metrics for an agent record."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE agent_records SET effective_rate = ?, "
                "avg_steps = ?, avg_duration_ms = ?, updated_at = ? "
                "WHERE agent_id = ?",
                (effective_rate, avg_steps, avg_duration_ms, _now_iso(), agent_id),
            )
            return cur.rowcount > 0

    def deactivate_agent(self, agent_id: str) -> bool:
        """Set is_active = False for an agent record."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE agent_records SET is_active = 0, updated_at = ? "
                "WHERE agent_id = ?",
                (_now_iso(), agent_id),
            )
            return cur.rowcount > 0

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Delete all data (keeps schema).  For testing."""
        with self._conn() as conn:
            conn.execute("DELETE FROM skill_judgments")
            conn.execute("DELETE FROM execution_analyses")
            conn.execute("DELETE FROM skill_lineage_parents")
            conn.execute("DELETE FROM context_budget_log")
            conn.execute("DELETE FROM agent_records")
            conn.execute("DELETE FROM skill_records")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_record(
        self,
        conn: sqlite3.Connection,
        row: tuple[Any, ...],
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

        # Load lineage parents
        parent_rows = conn.execute(
            "SELECT parent_id FROM skill_lineage_parents WHERE skill_id = ?",
            (skill_id,),
        ).fetchall()
        parent_ids = [r[0] for r in parent_rows]

        # Parse snapshot
        snapshot: dict[str, str] = {}
        if lineage_content_snapshot:
            try:
                loaded = json.loads(lineage_content_snapshot)
                if isinstance(loaded, dict):
                    # Validate all values are strings (Pydantic requirement)
                    if all(isinstance(v, str) for v in loaded.values()):
                        snapshot = loaded
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        lineage = SkillLineage(
            origin=SkillOrigin(lineage_origin),
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

    @staticmethod
    def _row_to_analysis_dict(
        conn: sqlite3.Connection,
        row: tuple[Any, ...],
    ) -> dict[str, Any]:
        """Convert an execution_analyses row to a dict."""
        analysis_id = row[0]
        result: dict[str, Any] = {
            "id": row[0],
            "task_id": row[1],
            "agent_name": row[2],
            "analysis": row[3],
            "evolution_suggestions": json.loads(row[4]) if row[4] else [],
            "created_at": row[5],
        }

        # Load judgments
        j_rows = conn.execute(
            "SELECT id, analysis_id, skill_id, selected, applied, "
            "completed, fell_back FROM skill_judgments "
            "WHERE analysis_id = ?",
            (analysis_id,),
        ).fetchall()
        result["judgments"] = [
            {
                "id": r[0],
                "analysis_id": r[1],
                "skill_id": r[2],
                "selected": bool(r[3]),
                "applied": bool(r[4]),
                "completed": bool(r[5]),
                "fell_back": bool(r[6]),
            }
            for r in j_rows
        ]

        return result
