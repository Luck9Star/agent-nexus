"""EvolutionStore -- facade for SQLite persistence of evolution data.

Delegates to three focused sub-stores:
    - SkillStore      -- skill CRUD, lineage, evolution, metrics, agent records
    - AnalysisStore   -- analysis logging and judgment queries
    - BudgetStore     -- context budget events and maintenance

The public API is identical to the pre-refactor monolith.  All callers
continue to import ``EvolutionStore`` from this module without changes.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_nexus.platform.evolution._shared import _SCHEMA_SQL
from agent_nexus.platform.evolution.analysis_store import AnalysisStore
from agent_nexus.platform.evolution.budget_store import BudgetStore
from agent_nexus.platform.evolution.skill_store import SkillStore
from agent_nexus.platform.utils import (
    sqlite_connection,
)

if TYPE_CHECKING:
    from agent_nexus.models.evolution import (
        EvolutionMetrics,
        SkillRecord,
    )
    from agent_nexus.platform.evolution.evolver import EvolveResult

logger = logging.getLogger(__name__)


class EvolutionStore:
    """SQLite-backed persistence for the Self-Evolution Engine.

    Uses WAL mode for concurrent read/write.  Connection-per-operation
    pattern (same as TaskGraph) for async compatibility.

    For ``:memory:`` databases, a single persistent connection is kept
    alive because ``sqlite3.connect(":memory:")`` creates a separate
    database each time.

    This class acts as a **facade** that delegates to three focused
    sub-stores (SkillStore, AnalysisStore, BudgetStore).  The public
    API is unchanged from the monolith version.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._is_memory = str(db_path) == ":memory:"
        self._memory_conn: sqlite3.Connection | None = None

        # Initialise schema before creating sub-stores
        self._init_db()

        # Create sub-stores, injecting a lazy conn_factory that always
        # resolves from the current EvolutionStore._conn.  This ensures
        # test mocks that patch EvolutionStore._conn propagate correctly.
        self._skill_store = SkillStore(
            db_path, conn_factory=lambda *, immediate=False: self._conn(immediate=immediate)
        )
        self._analysis_store = AnalysisStore(
            db_path, conn_factory=lambda *, immediate=False: self._conn(immediate=immediate)
        )
        self._budget_store = BudgetStore(
            db_path, conn_factory=lambda *, immediate=False: self._conn(immediate=immediate)
        )

    def close(self) -> None:
        """Release persistent resources.

        Closes the in-memory connection if one is held.  File-based
        connections are already closed per-operation by ``_conn()``.
        """
        if self._memory_conn is not None:
            try:
                self._memory_conn.close()
            except Exception:
                logger.warning("Failed to close evolution store connection", exc_info=True)
            self._memory_conn = None

    def __enter__(self) -> EvolutionStore:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __del__(self) -> None:
        with suppress(Exception):
            self.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            for stmt in _SCHEMA_SQL.split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(stmt)

    @contextmanager
    def _conn(self, *, immediate: bool = False) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for DB connections."""
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
    # Skill Record CRUD — delegated to SkillStore
    # ------------------------------------------------------------------

    def save_skill_record(self, record: SkillRecord) -> None:
        """Insert a skill record, or update its metadata on ID conflict."""
        return self._skill_store.save_skill_record(record)

    def get_skill_record(self, skill_id: str) -> SkillRecord | None:
        """Load a single skill record by ID."""
        return self._skill_store.get_skill_record(skill_id)

    def get_skill_records_batch(self, skill_ids: list[str]) -> dict[str, SkillRecord]:
        """Load multiple skill records by ID in a single query."""
        return self._skill_store.get_skill_records_batch(skill_ids)

    def get_active_skills(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[SkillRecord]:
        """Load active skill records, optionally paginated."""
        return self._skill_store.get_active_skills(limit=limit, offset=offset)

    def get_all_skills(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[SkillRecord]:
        """Load all skill records (including inactive), optionally paginated."""
        return self._skill_store.get_all_skills(limit=limit, offset=offset)

    def deactivate_skill(self, skill_id: str) -> bool:
        """Set is_active = False for a skill record."""
        return self._skill_store.deactivate_skill(skill_id)

    def get_versions(self, name: str) -> list[SkillRecord]:
        """Load all versions of a named skill, sorted by generation."""
        return self._skill_store.get_versions(name)

    # ------------------------------------------------------------------
    # Atomic counter increments — delegated to SkillStore
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_counter_invariants(
        *,
        selected: bool,
        applied: bool,
        completed: bool,
        fell_back: bool,
    ) -> None:
        """Delegate to SkillStore. Edit validation logic in SkillStore, not here."""
        return SkillStore._validate_counter_invariants(
            selected=selected,
            applied=applied,
            completed=completed,
            fell_back=fell_back,
        )

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
        return self._skill_store.increment_counters(
            skill_id,
            selected=selected,
            applied=applied,
            completed=completed,
            fell_back=fell_back,
        )

    # ------------------------------------------------------------------
    # Analysis + Judgments — delegated to AnalysisStore
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
        return self._analysis_store.record_analysis(
            task_id,
            agent_name,
            analysis_text,
            evolution_suggestions,
            judgments,
        )

    def get_analyses_for_task(self, task_id: str) -> list[dict[str, Any]]:
        """Load all analyses for a given task."""
        return self._analysis_store.get_analyses_for_task(task_id)

    def get_judgments_for_skill(self, skill_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Load recent judgments for a skill."""
        return self._analysis_store.get_judgments_for_skill(skill_id, limit)

    def get_judgments_batch(
        self, skill_ids: set[str], limit_per_skill: int = 50
    ) -> dict[str, list[dict[str, Any]]]:
        """Load recent judgments for multiple skills in one query."""
        return self._analysis_store.get_judgments_batch(skill_ids, limit_per_skill)

    # ------------------------------------------------------------------
    # Context Budget Log — delegated to BudgetStore
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
        return self._budget_store.log_budget_event(
            agent_name,
            event_type,
            tokens_before,
            tokens_after,
            details,
        )

    def get_budget_log(self, agent_name: str, limit: int = 50) -> list[dict[str, Any]]:
        """Load recent budget log entries for an agent."""
        return self._budget_store.get_budget_log(agent_name, limit)

    # ------------------------------------------------------------------
    # Evolution — delegated to SkillStore
    # ------------------------------------------------------------------

    def evolve_skill(
        self,
        new_record: SkillRecord,
        parent_skill_ids: list[str],
    ) -> EvolveResult:
        """Atomic evolution: insert new version, deactivate old for FIX."""
        return self._skill_store.evolve_skill(new_record, parent_skill_ids)

    # ------------------------------------------------------------------
    # Lineage queries — delegated to SkillStore
    # ------------------------------------------------------------------

    def get_ancestry_batch(
        self, skill_ids: list[str], max_depth: int = 10
    ) -> dict[str, list[SkillRecord]]:
        """Walk up lineage trees for multiple skills in a single connection."""
        return self._skill_store.get_ancestry_batch(skill_ids, max_depth)

    def get_ancestry(self, skill_id: str, max_depth: int = 10) -> list[SkillRecord]:
        """Walk up the lineage tree, returns ancestors oldest-first."""
        return self._skill_store.get_ancestry(skill_id, max_depth)

    def get_children(self, parent_id: str) -> list[str]:
        """Find skill IDs derived from the given parent."""
        return self._skill_store.get_children(parent_id)

    # ------------------------------------------------------------------
    # Aggregation — delegated to SkillStore
    # ------------------------------------------------------------------

    def get_metrics(self, agent_name: str | None = None) -> EvolutionMetrics:
        """Aggregate quality metrics across active skills."""
        return self._skill_store.get_metrics(agent_name)

    # ------------------------------------------------------------------
    # Agent Records — delegated to SkillStore
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
        return self._skill_store.save_agent_record(
            agent_id, name, type, skill_ids, orchestration_toml
        )

    def get_agent_record(self, agent_id: str) -> dict[str, Any] | None:
        """Load an agent record by ID."""
        return self._skill_store.get_agent_record(agent_id)

    def get_active_agents(self) -> list[dict[str, Any]]:
        """Load all active agent records."""
        return self._skill_store.get_active_agents()

    def update_agent_metrics(
        self,
        agent_id: str,
        effective_rate: float,
        avg_steps: float,
        avg_duration_ms: float,
    ) -> bool:
        """Update computed metrics for an agent record."""
        return self._skill_store.update_agent_metrics(
            agent_id, effective_rate, avg_steps, avg_duration_ms
        )

    def deactivate_agent(self, agent_id: str) -> bool:
        """Set is_active = False for an agent record."""
        return self._skill_store.deactivate_agent(agent_id)

    # ------------------------------------------------------------------
    # Row-to-dict helpers (static — delegate to sub-store static methods)
    # ------------------------------------------------------------------

    @staticmethod
    def _judgment_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
        """Convert a 7-column skill_judgments row to a dict."""
        return AnalysisStore._judgment_row_to_dict(row)

    @staticmethod
    def _agent_row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
        """Convert an 11-column agent_records row to a dict."""
        return SkillStore._agent_row_to_dict(row)

    # ------------------------------------------------------------------
    # Maintenance — delegated to BudgetStore
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Delete all data (keeps schema).  For testing."""
        return self._budget_store.clear()

    def prune_budget_log(
        self,
        max_age_days: int = 30,
        max_rows: int = 10_000,
    ) -> int:
        """Prune old or excess rows from ``context_budget_log``."""
        return self._budget_store.prune_budget_log(max_age_days, max_rows)

    # ------------------------------------------------------------------
    # Internal helpers — delegated to SkillStore
    # ------------------------------------------------------------------

    @staticmethod
    def _batch_load_parents(
        conn: sqlite3.Connection,
        skill_ids: set[str] | None = None,
    ) -> dict[str, list[str]]:
        """Load skill_lineage_parents, optionally filtered by skill_ids."""
        return SkillStore._batch_load_parents(conn, skill_ids)

    def _row_to_record(
        self,
        conn: sqlite3.Connection,
        row: tuple[Any, ...],
        parents_by_id: dict[str, list[str]] | None = None,
    ) -> SkillRecord:
        """Convert a skill_records row to a SkillRecord model."""
        return self._skill_store._row_to_record(conn, row, parents_by_id)

    def _rows_to_records(
        self,
        conn: sqlite3.Connection,
        rows: list[tuple[Any, ...]],
        parents_by_id: dict[str, list[str]] | None = None,
    ) -> list[SkillRecord]:
        """Convert multiple rows to SkillRecords, skipping corrupt rows."""
        return self._skill_store._rows_to_records(conn, rows, parents_by_id)
