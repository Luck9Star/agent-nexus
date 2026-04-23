//! `SQLite` schema for the evolution store.
//!
//! Wire-compatible with the Python implementation -- exact same table/column names.
//! 6 tables + 11 indexes.

/// Complete DDL for the evolution database.
pub const SCHEMA_SQL: &str = r"
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_sr_unique_active_name ON skill_records(name) WHERE is_active = 1;

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
    name TEXT NOT NULL UNIQUE,
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
";

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn schema_sql_is_non_empty() {
        assert!(!SCHEMA_SQL.is_empty());
        assert!(SCHEMA_SQL.contains("skill_records"));
        assert!(SCHEMA_SQL.contains("skill_lineage_parents"));
        assert!(SCHEMA_SQL.contains("execution_analyses"));
        assert!(SCHEMA_SQL.contains("skill_judgments"));
        assert!(SCHEMA_SQL.contains("context_budget_log"));
        assert!(SCHEMA_SQL.contains("agent_records"));
    }

    #[test]
    fn schema_has_all_indexes() {
        let expected_indexes = [
            "idx_sr_active",
            "idx_sr_name",
            "idx_sr_updated",
            "idx_lp_parent",
            "idx_ea_task",
            "idx_sj_skill",
            "idx_sj_analysis",
            "idx_cbl_agent",
            "idx_cbl_agent_created",
            "idx_ar_active",
            "idx_ar_name",
        ];
        for idx in &expected_indexes {
            assert!(
                SCHEMA_SQL.contains(idx),
                "Missing index: {idx}"
            );
        }
    }

    #[test]
    fn skill_records_has_14_columns() {
        // Extract the skill_records CREATE TABLE block
        let start = SCHEMA_SQL.find("CREATE TABLE IF NOT EXISTS skill_records").unwrap();
        let end = SCHEMA_SQL[start..].find(';').unwrap();
        let block = &SCHEMA_SQL[start..start + end];
        // Count column definitions (lines with column name + type)
        let columns: Vec<&str> = block
            .lines()
            .skip(1) // skip CREATE TABLE line
            .filter(|l| !l.contains("PRIMARY KEY") && !l.contains("CREATE INDEX"))
            .filter(|l| l.trim().starts_with(|c: char| c.is_alphabetic()))
            .collect();
        assert_eq!(columns.len(), 14, "Expected 14 columns, got {}: {:?}", columns.len(), columns);
    }
}
