//! Typed query methods for the evolution store.

use rusqlite::{params, Connection};

use super::StoreError;

// ---------------------------------------------------------------------------
// Data types matching Python schema (wire-compatible)
// ---------------------------------------------------------------------------

/// A row from the `skill_records` table.
#[derive(Debug, Clone)]
pub struct SkillRecord {
    pub id: String,
    pub name: String,
    pub version: String,
    pub lineage_origin: String,
    pub lineage_generation: i64,
    pub lineage_content_diff: Option<String>,
    pub lineage_content_snapshot: Option<String>,
    pub directory: Option<String>,
    pub is_active: bool,
    pub total_selections: i64,
    pub total_applied: i64,
    pub total_completions: i64,
    pub total_fallbacks: i64,
    pub created_at: String,
    pub updated_at: String,
}

/// A row from the `execution_analyses` table.
#[derive(Debug, Clone)]
pub struct ExecutionAnalysis {
    pub id: String,
    pub task_id: String,
    pub agent_name: String,
    pub analysis: String,
    pub evolution_suggestions: Option<String>,
    pub created_at: String,
}

/// A row from the `agent_records` table.
#[derive(Debug, Clone)]
pub struct AgentRecord {
    pub agent_id: String,
    pub name: String,
    pub agent_type: String,
    pub skill_ids: String,
    pub orchestration_toml: Option<String>,
    pub effective_rate: f64,
    pub avg_steps: Option<f64>,
    pub avg_duration_ms: Option<f64>,
    pub is_active: bool,
    pub created_at: String,
    pub updated_at: String,
}

// ---------------------------------------------------------------------------
// Query methods
// ---------------------------------------------------------------------------

/// Insert a skill record into `skill_records`.
pub fn insert_skill(conn: &Connection, skill: &SkillRecord) -> Result<(), StoreError> {
    conn.execute(
        "INSERT INTO skill_records (id, name, version, lineage_origin, lineage_generation,
            lineage_content_diff, lineage_content_snapshot, directory, is_active,
            total_selections, total_applied, total_completions, total_fallbacks,
            created_at, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15)",
        params![
            skill.id,
            skill.name,
            skill.version,
            skill.lineage_origin,
            skill.lineage_generation,
            skill.lineage_content_diff,
            skill.lineage_content_snapshot,
            skill.directory,
            skill.is_active as i32,
            skill.total_selections,
            skill.total_applied,
            skill.total_completions,
            skill.total_fallbacks,
            skill.created_at,
            skill.updated_at,
        ],
    )?;
    Ok(())
}

/// Get a skill record by name (first active match).
pub fn get_skill_by_name(
    conn: &Connection,
    name: &str,
) -> Result<Option<SkillRecord>, StoreError> {
    let mut stmt = conn.prepare(
        "SELECT id, name, version, lineage_origin, lineage_generation,
                lineage_content_diff, lineage_content_snapshot, directory, is_active,
                total_selections, total_applied, total_completions, total_fallbacks,
                created_at, updated_at
         FROM skill_records WHERE name = ?1 AND is_active = 1 LIMIT 1",
    )?;
    let mut rows = stmt.query(params![name])?;
    match rows.next()? {
        Some(row) => Ok(Some(SkillRecord {
            id: row.get(0)?,
            name: row.get(1)?,
            version: row.get(2)?,
            lineage_origin: row.get(3)?,
            lineage_generation: row.get(4)?,
            lineage_content_diff: row.get(5)?,
            lineage_content_snapshot: row.get(6)?,
            directory: row.get(7)?,
            is_active: row.get::<_, i32>(8)? != 0,
            total_selections: row.get(9)?,
            total_applied: row.get(10)?,
            total_completions: row.get(11)?,
            total_fallbacks: row.get(12)?,
            created_at: row.get(13)?,
            updated_at: row.get(14)?,
        })),
        None => Ok(None),
    }
}

/// Get all active skills.
pub fn get_active_skills(conn: &Connection) -> Result<Vec<SkillRecord>, StoreError> {
    let mut stmt = conn.prepare(
        "SELECT id, name, version, lineage_origin, lineage_generation,
                lineage_content_diff, lineage_content_snapshot, directory, is_active,
                total_selections, total_applied, total_completions, total_fallbacks,
                created_at, updated_at
         FROM skill_records WHERE is_active = 1",
    )?;
    let rows = stmt.query_map([], |row| {
        Ok(SkillRecord {
            id: row.get(0)?,
            name: row.get(1)?,
            version: row.get(2)?,
            lineage_origin: row.get(3)?,
            lineage_generation: row.get(4)?,
            lineage_content_diff: row.get(5)?,
            lineage_content_snapshot: row.get(6)?,
            directory: row.get(7)?,
            is_active: row.get::<_, i32>(8)? != 0,
            total_selections: row.get(9)?,
            total_applied: row.get(10)?,
            total_completions: row.get(11)?,
            total_fallbacks: row.get(12)?,
            created_at: row.get(13)?,
            updated_at: row.get(14)?,
        })
    })?;
    let mut skills = Vec::new();
    for skill in rows {
        skills.push(skill?);
    }
    Ok(skills)
}

/// Delete a skill record by id.
pub fn delete_skill(conn: &Connection, id: &str) -> Result<(), StoreError> {
    conn.execute("DELETE FROM skill_records WHERE id = ?1", params![id])?;
    Ok(())
}

/// Insert an execution analysis.
pub fn insert_execution_analysis(
    conn: &Connection,
    id: &str,
    task_id: &str,
    agent_name: &str,
    analysis: &str,
    evolution_suggestions: Option<&str>,
) -> Result<(), StoreError> {
    let now = chrono::Utc::now().to_rfc3339();
    conn.execute(
        "INSERT INTO execution_analyses (id, task_id, agent_name, analysis, evolution_suggestions, created_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
        params![id, task_id, agent_name, analysis, evolution_suggestions, now],
    )?;
    Ok(())
}

/// Get analyses for a specific task.
pub fn get_analyses_for_task(
    conn: &Connection,
    task_id: &str,
) -> Result<Vec<ExecutionAnalysis>, StoreError> {
    let mut stmt = conn.prepare(
        "SELECT id, task_id, agent_name, analysis, evolution_suggestions, created_at
         FROM execution_analyses WHERE task_id = ?1",
    )?;
    let rows = stmt.query_map(params![task_id], |row| {
        Ok(ExecutionAnalysis {
            id: row.get(0)?,
            task_id: row.get(1)?,
            agent_name: row.get(2)?,
            analysis: row.get(3)?,
            evolution_suggestions: row.get(4)?,
            created_at: row.get(5)?,
        })
    })?;
    let mut analyses = Vec::new();
    for analysis in rows {
        analyses.push(analysis?);
    }
    Ok(analyses)
}

/// Insert a context budget log entry.
pub fn insert_context_budget_log(
    conn: &Connection,
    id: &str,
    agent_name: &str,
    event_type: &str,
    tokens_before: Option<i64>,
    tokens_after: Option<i64>,
    details: Option<&str>,
) -> Result<(), StoreError> {
    let now = chrono::Utc::now().to_rfc3339();
    conn.execute(
        "INSERT INTO context_budget_log (id, agent_name, event_type, tokens_before, tokens_after, details, created_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
        params![id, agent_name, event_type, tokens_before, tokens_after, details, now],
    )?;
    Ok(())
}

/// Upsert an agent record.
///
/// If an agent with the same `agent_id` already exists, it is updated in place.
/// If a **different** `agent_id` already uses the same `name`, the insert is
/// rejected with [`StoreError::DuplicateAgentName`].
pub fn upsert_agent_record(
    conn: &Connection,
    agent_id: &str,
    name: &str,
    agent_type: &str,
    skill_ids: &str,
    orchestration_toml: Option<&str>,
) -> Result<(), StoreError> {
    let now = chrono::Utc::now().to_rfc3339();
    let result = conn.execute(
        "INSERT INTO agent_records (agent_id, name, type, skill_ids, orchestration_toml, is_active, created_at, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?5, 1, ?6, ?7)
         ON CONFLICT(agent_id) DO UPDATE SET
            name = excluded.name,
            type = excluded.type,
            skill_ids = excluded.skill_ids,
            orchestration_toml = excluded.orchestration_toml,
            is_active = 1,
            updated_at = excluded.updated_at",
        params![agent_id, name, agent_type, skill_ids, orchestration_toml, now, now],
    );

    match result {
        Ok(_) => Ok(()),
        Err(rusqlite::Error::SqliteFailure(err, Some(msg))) => {
            // SQLite constraint violation: code 19.
            // If the message mentions the unique index on `name`, surface a
            // dedicated error so callers can distinguish name-clashes from
            // other constraint failures.
            if err.code == rusqlite::ErrorCode::ConstraintViolation
                && msg.contains("agent_records.name")
            {
                // Best-effort: look up the existing agent_id for the name so
                // the error message is actionable.
                let existing_id = conn
                    .query_row(
                        "SELECT agent_id FROM agent_records WHERE name = ?1 LIMIT 1",
                        params![name],
                        |row| row.get::<_, String>(0),
                    )
                    .unwrap_or_else(|_| "<unknown>".to_string());
                Err(StoreError::DuplicateAgentName {
                    name: name.to_string(),
                    existing_id,
                })
            } else {
                Err(StoreError::Sqlite(rusqlite::Error::SqliteFailure(
                    err,
                    Some(msg),
                )))
            }
        }
        Err(e) => Err(StoreError::Sqlite(e)),
    }
}

/// Get an agent record by name.
pub fn get_agent_record(
    conn: &Connection,
    name: &str,
) -> Result<Option<AgentRecord>, StoreError> {
    let mut stmt = conn.prepare(
        "SELECT agent_id, name, type, skill_ids, orchestration_toml,
                effective_rate, avg_steps, avg_duration_ms, is_active,
                created_at, updated_at
         FROM agent_records WHERE name = ?1 AND is_active = 1 LIMIT 1",
    )?;
    let mut rows = stmt.query(params![name])?;
    match rows.next()? {
        Some(row) => Ok(Some(AgentRecord {
            agent_id: row.get(0)?,
            name: row.get(1)?,
            agent_type: row.get(2)?,
            skill_ids: row.get(3)?,
            orchestration_toml: row.get(4)?,
            effective_rate: row.get(5)?,
            avg_steps: row.get(6)?,
            avg_duration_ms: row.get(7)?,
            is_active: row.get::<_, i32>(8)? != 0,
            created_at: row.get(9)?,
            updated_at: row.get(10)?,
        })),
        None => Ok(None),
    }
}

/// List all table names in the database (for testing schema).
pub(crate) fn list_tables(conn: &Connection) -> Vec<String> {
    let mut stmt = conn
        .prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        .expect("prepare list_tables");
    let rows = stmt
        .query_map([], |row| row.get::<_, String>(0))
        .expect("query_map list_tables");
    let mut tables = Vec::new();
    for row in rows {
        tables.push(row.expect("row in list_tables"));
    }
    tables
}

/// Count rows in a table.
pub(crate) fn count_rows(conn: &Connection, table: &str) -> Result<i64, StoreError> {
    // Only allow known table names to prevent SQL injection
    let allowed = [
        "skill_records",
        "skill_lineage_parents",
        "execution_analyses",
        "skill_judgments",
        "context_budget_log",
        "agent_records",
    ];
    if !allowed.contains(&table) {
        return Err(StoreError::Sqlite(rusqlite::Error::InvalidParameterName(
            format!("Unknown table: {table}"),
        )));
    }
    let sql = format!("SELECT COUNT(*) FROM {table}");
    let count: i64 = conn.query_row(&sql, [], |row| row.get(0))?;
    Ok(count)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::store::schema::SCHEMA_SQL;

    fn test_conn() -> Connection {
        let conn = Connection::open_in_memory().unwrap();
        conn.execute_batch(SCHEMA_SQL).unwrap();
        conn
    }

    fn make_skill() -> SkillRecord {
        SkillRecord {
            id: "skill-001".to_string(),
            name: "test-skill".to_string(),
            version: "1.0.0".to_string(),
            lineage_origin: "imported".to_string(),
            lineage_generation: 0,
            lineage_content_diff: None,
            lineage_content_snapshot: None,
            directory: Some("/skills/test".to_string()),
            is_active: true,
            total_selections: 0,
            total_applied: 0,
            total_completions: 0,
            total_fallbacks: 0,
            created_at: chrono::Utc::now().to_rfc3339(),
            updated_at: chrono::Utc::now().to_rfc3339(),
        }
    }

    #[test]
    fn insert_and_get_skill() {
        let conn = test_conn();
        let skill = make_skill();
        insert_skill(&conn, &skill).unwrap();

        let found = get_skill_by_name(&conn, "test-skill").unwrap();
        assert!(found.is_some());
        let found = found.unwrap();
        assert_eq!(found.id, "skill-001");
        assert_eq!(found.name, "test-skill");
        assert_eq!(found.version, "1.0.0");
        assert_eq!(found.lineage_origin, "imported");
        assert_eq!(found.lineage_generation, 0);
        assert!(found.is_active);
    }

    #[test]
    fn get_skill_by_name_not_found() {
        let conn = test_conn();
        let found = get_skill_by_name(&conn, "nonexistent").unwrap();
        assert!(found.is_none());
    }

    #[test]
    fn remove_skill() {
        let conn = test_conn();
        let skill = make_skill();
        insert_skill(&conn, &skill).unwrap();
        delete_skill(&conn, "skill-001").unwrap();

        let found = get_skill_by_name(&conn, "test-skill").unwrap();
        assert!(found.is_none());
    }

    #[test]
    fn insert_and_get_execution_analysis() {
        let conn = test_conn();
        insert_execution_analysis(
            &conn,
            "analysis-001",
            "task-001",
            "test-agent",
            "Analysis text here",
            Some(r#"[{"type":"FIX"}]"#),
        )
        .unwrap();

        let analyses = get_analyses_for_task(&conn, "task-001").unwrap();
        assert_eq!(analyses.len(), 1);
        assert_eq!(analyses[0].id, "analysis-001");
        assert_eq!(analyses[0].agent_name, "test-agent");
        assert_eq!(analyses[0].analysis, "Analysis text here");
    }

    #[test]
    fn insert_execution_analysis_no_suggestions() {
        let conn = test_conn();
        insert_execution_analysis(
            &conn,
            "analysis-002",
            "task-002",
            "agent-2",
            "No suggestions",
            None,
        )
        .unwrap();

        let analyses = get_analyses_for_task(&conn, "task-002").unwrap();
        assert_eq!(analyses.len(), 1);
        assert!(analyses[0].evolution_suggestions.is_none());
    }

    #[test]
    fn log_context_budget_event() {
        let conn = test_conn();
        insert_context_budget_log(
            &conn,
            "log-001",
            "agent-1",
            "compaction",
            Some(5000),
            Some(2000),
            Some(r#"{"consecutive":1}"#),
        )
        .unwrap();

        let count = count_rows(&conn, "context_budget_log").unwrap();
        assert_eq!(count, 1);
    }

    #[test]
    fn upsert_and_get_agent_record() {
        let conn = test_conn();
        upsert_agent_record(
            &conn,
            "agent-001",
            "my-agent",
            "atomic",
            r#"["skill-1","skill-2"]"#,
            Some("some toml"),
        )
        .unwrap();

        let found = get_agent_record(&conn, "my-agent").unwrap();
        assert!(found.is_some());
        let found = found.unwrap();
        assert_eq!(found.agent_id, "agent-001");
        assert_eq!(found.agent_type, "atomic");
        assert_eq!(found.skill_ids, r#"["skill-1","skill-2"]"#);
    }

    #[test]
    fn upsert_agent_record_updates_existing() {
        let conn = test_conn();
        upsert_agent_record(&conn, "a-1", "agent-x", "atomic", "[]", None).unwrap();
        upsert_agent_record(&conn, "a-1", "agent-x-updated", "composite", r#"["s1"]"#, None)
            .unwrap();

        let found = get_agent_record(&conn, "agent-x-updated").unwrap();
        assert!(found.is_some());
        let found = found.unwrap();
        assert_eq!(found.agent_type, "composite");
    }

    #[test]
    fn get_agent_record_not_found() {
        let conn = test_conn();
        let found = get_agent_record(&conn, "nonexistent").unwrap();
        assert!(found.is_none());
    }

    #[test]
    fn upsert_agent_record_rejects_duplicate_name() {
        let conn = test_conn();

        // Insert first agent
        upsert_agent_record(
            &conn,
            "agent-001",
            "shared-name",
            "atomic",
            "[]",
            None,
        )
        .unwrap();

        // Attempt to insert a second agent with the same name but different agent_id
        let result = upsert_agent_record(
            &conn,
            "agent-002",
            "shared-name",
            "composite",
            r#"["s1"]"#,
            None,
        );

        // Should fail with DuplicateAgentName
        let err = result.expect_err("expected error for duplicate agent name");
        match err {
            StoreError::DuplicateAgentName { name, existing_id } => {
                assert_eq!(name, "shared-name");
                assert_eq!(existing_id, "agent-001");
            }
            other => panic!("expected DuplicateAgentName, got: {other}"),
        }

        // Verify only one record exists
        let count = count_rows(&conn, "agent_records").unwrap();
        assert_eq!(count, 1, "should still have exactly one agent record");
    }

    #[test]
    fn list_tables_returns_all_six() {
        let conn = test_conn();
        let tables = list_tables(&conn);
        // Should contain all 6 tables (sqlite_master internal table excluded by type filter)
        assert!(tables.contains(&"skill_records".to_string()));
        assert!(tables.contains(&"skill_lineage_parents".to_string()));
        assert!(tables.contains(&"execution_analyses".to_string()));
        assert!(tables.contains(&"skill_judgments".to_string()));
        assert!(tables.contains(&"context_budget_log".to_string()));
        assert!(tables.contains(&"agent_records".to_string()));
    }

    #[test]
    fn list_active_skills() {
        let conn = test_conn();
        let mut skill1 = make_skill();
        skill1.id = "s1".to_string();
        skill1.name = "skill-a".to_string();
        insert_skill(&conn, &skill1).unwrap();

        let mut skill2 = make_skill();
        skill2.id = "s2".to_string();
        skill2.name = "skill-b".to_string();
        insert_skill(&conn, &skill2).unwrap();

        let active = get_active_skills(&conn).unwrap();
        assert_eq!(active.len(), 2);
    }

    #[test]
    fn count_rows_rejects_unknown_table() {
        let conn = test_conn();
        let result = count_rows(&conn, "droptable_skill_records");
        assert!(result.is_err());
    }
}
