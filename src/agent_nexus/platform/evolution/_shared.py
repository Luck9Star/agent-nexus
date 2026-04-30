"""Shared constants and helpers for evolution sub-stores.

Extracted from store.py to avoid circular imports between sub-store modules.
"""

from __future__ import annotations

import sqlite3
from typing import Any

_SQL_CHUNK_SIZE = 500
"""Max variables per IN clause — stays well below SQLite's SQLITE_MAX_VARIABLE_NUMBER (999)."""

_SKILL_COLUMNS = (
    "id, name, version, lineage_origin, lineage_generation, "
    "lineage_content_diff, lineage_content_snapshot, directory, "
    "is_active, total_selections, total_applied, total_completions, "
    "total_fallbacks, created_at, updated_at"
)
"""Column list for skill_records SELECT queries — single source of truth."""

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
    FOREIGN KEY (analysis_id) REFERENCES execution_analyses(id),
    FOREIGN KEY (skill_id) REFERENCES skill_records(id)
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
CREATE INDEX IF NOT EXISTS idx_cbl_agent_created
    ON context_budget_log(agent_name, created_at DESC);

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


def _chunked_in_fetchall(
    conn: sqlite3.Connection,
    sql_template: str,
    values: list[str] | tuple[str, ...],
    extra_params: tuple[Any, ...] = (),
) -> list[tuple[Any, ...]]:
    """Execute *sql_template* in chunks, bypassing the SQLite variable limit."""
    vals = list(values)
    if not vals:
        return []
    all_rows: list[tuple[Any, ...]] = []
    for i in range(0, len(vals), _SQL_CHUNK_SIZE):
        chunk = vals[i : i + _SQL_CHUNK_SIZE]
        ph = ",".join("?" * len(chunk))
        sql = sql_template.replace("{IN}", ph)
        all_rows.extend(conn.execute(sql, tuple(chunk) + extra_params).fetchall())
    return all_rows
