"""AnalysisStore -- analysis logging and judgment queries.

Extracted from EvolutionStore to improve cohesion.  Each method operates on
execution_analyses and skill_judgments tables.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from typing import Any

from agent_nexus.platform.evolution._shared import _chunked_in_fetchall
from agent_nexus.platform.utils import (
    now_iso as _now_iso,
)

logger = logging.getLogger(__name__)

# Type alias for the connection factory callable used by sub-stores.
ConnFactory = Callable[..., AbstractContextManager[sqlite3.Connection]]


def _build_judgment_rows(
    analysis_id: str,
    judgments: list[dict[str, Any]] | None,
) -> list[tuple]:
    """Convert judgment dicts into SQL insert rows."""
    return [
        (
            str(uuid.uuid4()),
            analysis_id,
            j.get("skill_id"),
            int(j.get("selected", False)),
            int(j.get("applied", False)),
            int(j.get("completed", False)),
            int(j.get("fell_back", False)),
        )
        for j in (judgments or [])
        if j.get("skill_id")
    ]


def _validate_counter_invariant(judgment: dict[str, Any]) -> tuple[bool, bool, bool, bool]:
    """Validate counter invariants and return extracted boolean flags."""
    selected = bool(judgment.get("selected", False))
    applied = bool(judgment.get("applied", False))
    completed = bool(judgment.get("completed", False))
    fell_back = bool(judgment.get("fell_back", False))

    if applied and not selected:
        raise ValueError("applied requires selected=True")
    if completed and not applied:
        raise ValueError("completed requires applied=True")
    if fell_back and not selected:
        raise ValueError("fell_back requires selected=True")

    return selected, applied, completed, fell_back


def _compute_deltas(
    judgments: list[dict[str, Any]] | None,
) -> dict[str, dict[str, int]]:
    """Aggregate counter deltas per skill from validated judgments."""
    deltas: dict[str, dict[str, int]] = {}
    for j in judgments or []:
        sid = j.get("skill_id")
        if not sid:
            continue
        selected, applied, completed, fell_back = _validate_counter_invariant(j)

        d = deltas.setdefault(sid, {"sel": 0, "app": 0, "comp": 0, "fb": 0})
        if selected:
            d["sel"] += 1
        if applied:
            d["app"] += 1
        if completed:
            d["comp"] += 1
        if fell_back:
            d["fb"] += 1
    return deltas


class AnalysisStore:
    """SQLite-backed persistence for execution analyses and skill judgments.

    Uses WAL mode for concurrent read/write.  Connection-per-operation
    pattern for async compatibility.

    Accepts an optional ``conn_factory`` so the EvolutionStore facade
    can inject its own ``_conn`` method.
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
        """Insert analysis + judgments + update counters in one transaction."""
        analysis_id = str(uuid.uuid4())
        now = _now_iso()
        suggestions_json = json.dumps(evolution_suggestions or [], ensure_ascii=False)

        with self._conn(immediate=True) as conn:
            conn.execute(
                """
                INSERT INTO execution_analyses (
                    id, task_id, agent_name, analysis,
                    evolution_suggestions, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (analysis_id, task_id, agent_name, analysis_text, suggestions_json, now),
            )

            judgment_rows = _build_judgment_rows(analysis_id, judgments)
            if judgment_rows:
                conn.executemany(
                    "INSERT INTO skill_judgments (id, analysis_id, skill_id, "
                    "selected, applied, completed, fell_back) VALUES (?,?,?,?,?,?,?)",
                    judgment_rows,
                )

            deltas = _compute_deltas(judgments)
            for sid, d in deltas.items():
                conn.execute(
                    "UPDATE skill_records SET "
                    "total_selections = total_selections + ?, "
                    "total_applied = total_applied + ?, "
                    "total_completions = total_completions + ?, "
                    "total_fallbacks = total_fallbacks + ?, "
                    "updated_at = ? WHERE id = ?",
                    (d["sel"], d["app"], d["comp"], d["fb"], now, sid),
                )

        return analysis_id

    def get_analyses_for_task(self, task_id: str) -> list[dict[str, Any]]:
        """Load all analyses for a given task."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, task_id, agent_name, analysis, "
                "evolution_suggestions, created_at "
                "FROM execution_analyses WHERE task_id = ?",
                (task_id,),
            ).fetchall()
            if not rows:
                return []

            analysis_ids = [r[0] for r in rows]

            j_rows = _chunked_in_fetchall(
                conn,
                "SELECT id, analysis_id, skill_id, selected, applied, "
                "completed, fell_back FROM skill_judgments "
                "WHERE analysis_id IN ({IN})",
                analysis_ids,
            )

            judgments_by_analysis: dict[str, list[dict[str, Any]]] = {}
            for r in j_rows:
                aid = r[1]
                judgments_by_analysis.setdefault(aid, []).append(self._judgment_row_to_dict(r))

            results: list[dict[str, Any]] = []
            for r in rows:
                results.append(
                    {
                        "id": r[0],
                        "task_id": r[1],
                        "agent_name": r[2],
                        "analysis": r[3],
                        "evolution_suggestions": json.loads(r[4]) if r[4] else [],
                        "created_at": r[5],
                        "judgments": judgments_by_analysis.get(r[0], []),
                    }
                )
            return results

    def get_judgments_for_skill(self, skill_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Load recent judgments for a skill."""
        if limit < 1:
            limit = 1
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, analysis_id, skill_id, selected, applied, "
                "completed, fell_back FROM skill_judgments "
                "WHERE skill_id = ? ORDER BY rowid DESC LIMIT ?",
                (skill_id, limit),
            ).fetchall()
            return [self._judgment_row_to_dict(r) for r in rows]

    def get_judgments_batch(
        self, skill_ids: set[str], limit_per_skill: int = 50
    ) -> dict[str, list[dict[str, Any]]]:
        """Load recent judgments for multiple skills in one query."""
        if not skill_ids:
            return {}
        if limit_per_skill < 1:
            limit_per_skill = 1
        with self._conn() as conn:
            rows = _chunked_in_fetchall(
                conn,
                "SELECT id, analysis_id, skill_id, selected, applied, "
                "completed, fell_back FROM ("
                "SELECT *, ROW_NUMBER() OVER ("
                "PARTITION BY skill_id ORDER BY rowid DESC"
                ") AS rn FROM skill_judgments "
                "WHERE skill_id IN ({IN})"
                ") WHERE rn <= ?",
                list(skill_ids),
                extra_params=(limit_per_skill,),
            )
        result: dict[str, list[dict[str, Any]]] = {sid: [] for sid in skill_ids}
        for r in rows:
            sid = r[2]
            result[sid].append(self._judgment_row_to_dict(r))
        return result

    # ------------------------------------------------------------------
    # Row-to-dict helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _judgment_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
        """Convert a 7-column skill_judgments row to a dict."""
        return {
            "id": row[0],
            "analysis_id": row[1],
            "skill_id": row[2],
            "selected": bool(row[3]),
            "applied": bool(row[4]),
            "completed": bool(row[5]),
            "fell_back": bool(row[6]),
        }
