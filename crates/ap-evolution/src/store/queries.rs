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

/// A row from the `skill_judgments` table.
///
/// Contains multiple boolean flags tracking judgment lifecycle. The bool
/// fields are inherent to the domain model (judgment outcomes) and cannot
/// be meaningfully simplified.
#[derive(Debug, Clone)]
#[allow(clippy::struct_excessive_bools)]
pub struct SkillJudgment {
    pub id: String,
    pub analysis_id: String,
    pub skill_id: String,
    pub selected: bool,
    pub applied: bool,
    pub completed: bool,
    pub fell_back: bool,
}

// ---------------------------------------------------------------------------
// Query methods
// ---------------------------------------------------------------------------

/// Insert a skill record into `skill_records`.
pub(crate) fn insert_skill(conn: &Connection, skill: &SkillRecord) -> Result<(), StoreError> {
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
            i32::from(skill.is_active),
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
pub(crate) fn get_skill_by_name(
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
        Some(row) => Ok(Some(skill_record_from_row(row)?)),
        None => Ok(None),
    }
}

/// Get a skill record by ID.
pub(crate) fn get_skill_by_id(
    conn: &Connection,
    id: &str,
) -> Result<Option<SkillRecord>, StoreError> {
    let mut stmt = conn.prepare(
        "SELECT id, name, version, lineage_origin, lineage_generation,
                lineage_content_diff, lineage_content_snapshot, directory, is_active,
                total_selections, total_applied, total_completions, total_fallbacks,
                created_at, updated_at
         FROM skill_records WHERE id = ?1",
    )?;
    let mut rows = stmt.query(params![id])?;
    match rows.next()? {
        Some(row) => Ok(Some(skill_record_from_row(row)?)),
        None => Ok(None),
    }
}

/// Get all active skills.
pub(crate) fn get_active_skills(conn: &Connection) -> Result<Vec<SkillRecord>, StoreError> {
    let mut stmt = conn.prepare(
        "SELECT id, name, version, lineage_origin, lineage_generation,
                lineage_content_diff, lineage_content_snapshot, directory, is_active,
                total_selections, total_applied, total_completions, total_fallbacks,
                created_at, updated_at
         FROM skill_records WHERE is_active = 1",
    )?;
    let rows = stmt.query_map([], skill_record_from_row)?;
    let mut skills = Vec::with_capacity(16);
    for skill in rows {
        skills.push(skill?);
    }
    Ok(skills)
}

/// Get all skills (active and inactive).
pub(crate) fn get_all_skills(conn: &Connection) -> Result<Vec<SkillRecord>, StoreError> {
    let mut stmt = conn.prepare(
        "SELECT id, name, version, lineage_origin, lineage_generation,
                lineage_content_diff, lineage_content_snapshot, directory, is_active,
                total_selections, total_applied, total_completions, total_fallbacks,
                created_at, updated_at
         FROM skill_records",
    )?;
    let rows = stmt.query_map([], skill_record_from_row)?;
    let mut skills = Vec::with_capacity(16);
    for skill in rows {
        skills.push(skill?);
    }
    Ok(skills)
}

/// Delete a skill record by id.
///
/// Returns `Ok(true)` if a row was deleted, `Ok(false)` if no matching row was found.
pub(crate) fn delete_skill(conn: &Connection, id: &str) -> Result<bool, StoreError> {
    let rows = conn.execute("DELETE FROM skill_records WHERE id = ?1", params![id])?;
    Ok(rows > 0)
}

// ---------------------------------------------------------------------------
// Counter increment methods
// ---------------------------------------------------------------------------

/// Increment `total_selections` for a skill by 1.
///
/// Returns `Ok(true)` if a row was updated, `Ok(false)` if the skill was not found.
pub(crate) fn increment_selections(conn: &Connection, id: &str) -> Result<bool, StoreError> {
    let now = chrono::Utc::now().to_rfc3339();
    let rows = conn.execute(
        "UPDATE skill_records SET total_selections = total_selections + 1, updated_at = ?1 WHERE id = ?2",
        params![now, id],
    )?;
    Ok(rows > 0)
}

/// Increment `total_applied` for a skill by 1.
///
/// Returns `Ok(true)` if a row was updated, `Ok(false)` if the skill was not found.
pub(crate) fn increment_applied(conn: &Connection, id: &str) -> Result<bool, StoreError> {
    let now = chrono::Utc::now().to_rfc3339();
    let rows = conn.execute(
        "UPDATE skill_records SET total_applied = total_applied + 1, updated_at = ?1 WHERE id = ?2",
        params![now, id],
    )?;
    Ok(rows > 0)
}

/// Increment `total_completions` for a skill by 1.
///
/// Returns `Ok(true)` if a row was updated, `Ok(false)` if the skill was not found.
pub(crate) fn increment_completions(conn: &Connection, id: &str) -> Result<bool, StoreError> {
    let now = chrono::Utc::now().to_rfc3339();
    let rows = conn.execute(
        "UPDATE skill_records SET total_completions = total_completions + 1, updated_at = ?1 WHERE id = ?2",
        params![now, id],
    )?;
    Ok(rows > 0)
}

/// Increment `total_fallbacks` for a skill by 1.
///
/// Returns `Ok(true)` if a row was updated, `Ok(false)` if the skill was not found.
pub(crate) fn increment_fallbacks(conn: &Connection, id: &str) -> Result<bool, StoreError> {
    let now = chrono::Utc::now().to_rfc3339();
    let rows = conn.execute(
        "UPDATE skill_records SET total_fallbacks = total_fallbacks + 1, updated_at = ?1 WHERE id = ?2",
        params![now, id],
    )?;
    Ok(rows > 0)
}

/// Batch-increment multiple counters for a single skill in one statement.
///
/// Each delta can be 0 (no change). This is more efficient than calling
/// individual `increment_*` functions when multiple counters need updating.
///
/// Returns `Ok(true)` if a row was updated, `Ok(false)` if the skill was not found.
pub(crate) fn batch_increment(
    conn: &Connection,
    id: &str,
    selections: u32,
    applied: u32,
    completions: u32,
    fallbacks: u32,
) -> Result<bool, StoreError> {
    let now = chrono::Utc::now().to_rfc3339();
    let rows = conn.execute(
        "UPDATE skill_records SET
            total_selections = total_selections + ?1,
            total_applied = total_applied + ?2,
            total_completions = total_completions + ?3,
            total_fallbacks = total_fallbacks + ?4,
            updated_at = ?5
         WHERE id = ?6",
        params![selections, applied, completions, fallbacks, now, id],
    )?;
    Ok(rows > 0)
}

// ---------------------------------------------------------------------------
// Skill lifecycle
// ---------------------------------------------------------------------------

/// Deactivate a skill by setting `is_active = 0`.
///
/// Returns `Ok(true)` if a row was updated, `Ok(false)` if the skill was not found.
pub(crate) fn deactivate_skill(conn: &Connection, id: &str) -> Result<bool, StoreError> {
    let now = chrono::Utc::now().to_rfc3339();
    let rows = conn.execute(
        "UPDATE skill_records SET is_active = 0, updated_at = ?1 WHERE id = ?2",
        params![now, id],
    )?;
    Ok(rows > 0)
}

/// Evolve a skill: insert a new version and link it to its parents.
///
/// If `deactivate_parents` is true (FIX evolution), all parent skills are
/// deactivated atomically. The caller is responsible for ensuring the new
/// skill record has a unique ID.
///
/// **Ordering contract**: Parent deactivation MUST happen BEFORE the new skill insert.
/// The `idx_sr_unique_active_name` partial unique index enforces that only one active
/// skill with a given name can exist. If this ordering is inverted, the insert will
/// violate the unique constraint and fail.
pub(crate) fn evolve_skill(
    conn: &Connection,
    new_skill: &SkillRecord,
    parent_ids: &[&str],
    deactivate_parents: bool,
) -> Result<(), StoreError> {
    let tx = conn.unchecked_transaction()?;

    let now = chrono::Utc::now().to_rfc3339();

    // Step 1: Deactivate parents if requested (FIX evolution)
    if deactivate_parents {
        for pid in parent_ids {
            tx.execute(
                "UPDATE skill_records SET is_active = 0, updated_at = ?1 WHERE id = ?2",
                params![now, pid],
            )?;
        }
    }

    // Step 2: Insert the new skill record
    insert_skill(&tx, new_skill)?;

    // Step 3: Insert lineage parent edges
    for pid in parent_ids {
        tx.execute(
            "INSERT INTO skill_lineage_parents (skill_id, parent_id) VALUES (?1, ?2)",
            params![new_skill.id, pid],
        )?;
    }

    tx.commit()?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Judgment system
// ---------------------------------------------------------------------------

/// Insert a skill judgment.
pub(crate) fn save_judgment(conn: &Connection, judgment: &SkillJudgment) -> Result<(), StoreError> {
    conn.execute(
        "INSERT INTO skill_judgments (id, analysis_id, skill_id, selected, applied, completed, fell_back)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
        params![
            judgment.id,
            judgment.analysis_id,
            judgment.skill_id,
            i32::from(judgment.selected),
            i32::from(judgment.applied),
            i32::from(judgment.completed),
            i32::from(judgment.fell_back),
        ],
    )?;
    Ok(())
}

/// Get judgments for a skill, ordered by rowid descending (most recent first).
pub(crate) fn get_judgments_for_skill(
    conn: &Connection,
    skill_id: &str,
    limit: i64,
) -> Result<Vec<SkillJudgment>, StoreError> {
    let limit = limit.max(1);
    let mut stmt = conn.prepare(
        "SELECT id, analysis_id, skill_id, selected, applied, completed, fell_back
         FROM skill_judgments WHERE skill_id = ?1 ORDER BY rowid DESC LIMIT ?2",
    )?;
    let rows = stmt.query_map(params![skill_id, limit], |row| {
        Ok(SkillJudgment {
            id: row.get(0)?,
            analysis_id: row.get(1)?,
            skill_id: row.get(2)?,
            selected: row.get::<_, i32>(3)? != 0,
            applied: row.get::<_, i32>(4)? != 0,
            completed: row.get::<_, i32>(5)? != 0,
            fell_back: row.get::<_, i32>(6)? != 0,
        })
    })?;
    let mut judgments = Vec::with_capacity(8);
    for j in rows {
        judgments.push(j?);
    }
    Ok(judgments)
}

// ---------------------------------------------------------------------------
// Lineage queries
// ---------------------------------------------------------------------------

/// Walk up the lineage tree via BFS, returning ancestor records oldest-first.
///
/// The starting `skill_id` itself is NOT included in the result.
/// Iteration stops after `max_depth` hops or when no more parents are found.
pub(crate) fn get_ancestry(
    conn: &Connection,
    skill_id: &str,
    max_depth: usize,
) -> Result<Vec<SkillRecord>, StoreError> {
    let mut visited = std::collections::HashSet::<String>::new();
    let mut frontier = vec![skill_id.to_string()];

    // Phase 1: BFS through lineage_parents table
    for _ in 0..max_depth {
        if frontier.is_empty() {
            break;
        }
        let mut next_frontier: Vec<String> = Vec::new();
        for sid in &frontier {
            let mut stmt = conn.prepare(
                "SELECT parent_id FROM skill_lineage_parents WHERE skill_id = ?1",
            )?;
            let parent_rows = stmt.query_map(params![sid], |row| row.get::<_, String>(0))?;
            for pid in parent_rows {
                let pid = pid?;
                if visited.insert(pid.clone()) {
                    next_frontier.push(pid);
                }
            }
        }
        frontier = next_frontier;
    }

    if visited.is_empty() {
        return Ok(Vec::new());
    }

    // Phase 2: Batch-load all ancestor records in a single query
    let mut ancestors: Vec<SkillRecord> = Vec::with_capacity(visited.len());
    let placeholders: Vec<String> = visited.iter().enumerate().map(|(i, _)| format!("?{}", i + 1)).collect();
    let sql = format!(
        "SELECT id, name, version, lineage_origin, lineage_generation,
                lineage_content_diff, lineage_content_snapshot, directory, is_active,
                total_selections, total_applied, total_completions, total_fallbacks,
                created_at, updated_at
         FROM skill_records WHERE id IN ({})",
        placeholders.join(",")
    );
    let mut stmt = conn.prepare(&sql)?;
    let params: Vec<&str> = visited.iter().map(std::string::String::as_str).collect();
    let rows = stmt.query_map(rusqlite::params_from_iter(params.iter().copied()), skill_record_from_row)?;
    for skill in rows {
        ancestors.push(skill?);
    }

    // Sort by generation ascending (oldest first), matching Python behavior
    ancestors.sort_by_key(|r| r.lineage_generation);
    Ok(ancestors)
}

/// Get child skill IDs derived from the given parent.
pub(crate) fn get_children(conn: &Connection, parent_id: &str) -> Result<Vec<String>, StoreError> {
    let mut stmt = conn.prepare(
        "SELECT skill_id FROM skill_lineage_parents WHERE parent_id = ?1",
    )?;
    let rows = stmt.query_map(params![parent_id], |row| row.get::<_, String>(0))?;
    let mut children = Vec::with_capacity(4);
    for row in rows {
        children.push(row?);
    }
    Ok(children)
}

// ---------------------------------------------------------------------------
// Execution analysis
// ---------------------------------------------------------------------------

/// Insert an execution analysis.
pub(crate) fn insert_execution_analysis(
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
pub(crate) fn get_analyses_for_task(
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
    let mut analyses = Vec::with_capacity(4);
    for analysis in rows {
        analyses.push(analysis?);
    }
    Ok(analyses)
}

/// Insert a context budget log entry.
pub(crate) fn insert_context_budget_log(
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
pub(crate) fn upsert_agent_record(
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
pub(crate) fn get_agent_record(
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
pub(crate) fn list_tables(conn: &Connection) -> Result<Vec<String>, StoreError> {
    let mut stmt = conn.prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")?;
    let rows = stmt.query_map([], |row| row.get::<_, String>(0))?;
    let mut tables = Vec::with_capacity(6);
    for row in rows {
        tables.push(row?);
    }
    Ok(tables)
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
        "_meta",
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

// ---------------------------------------------------------------------------
// Row mapping helper (DRY)
// ---------------------------------------------------------------------------

/// Map a single row to a `SkillRecord`.
///
/// Expects the row to contain all 15 columns in schema order:
/// `id, name, version, lineage_origin, lineage_generation, lineage_content_diff,
///  lineage_content_snapshot, directory, is_active, total_selections, total_applied,
///  total_completions, total_fallbacks, created_at, updated_at`
fn skill_record_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<SkillRecord> {
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
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

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

    #[allow(dead_code)]
    fn make_judgment(skill_id: &str, analysis_id: &str) -> SkillJudgment {
        SkillJudgment {
            id: format!("j-{}", uuid::Uuid::new_v4()),
            analysis_id: analysis_id.to_string(),
            skill_id: skill_id.to_string(),
            selected: true,
            applied: true,
            completed: false,
            fell_back: false,
        }
    }

    // --- Existing tests (preserved) ---

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
        let deleted = delete_skill(&conn, "skill-001").unwrap();
        assert!(deleted, "delete_skill should return true when a row is deleted");

        let found = get_skill_by_name(&conn, "test-skill").unwrap();
        assert!(found.is_none());
    }

    #[test]
    fn delete_skill_returns_false_for_missing() {
        let conn = test_conn();
        let deleted = delete_skill(&conn, "nonexistent").unwrap();
        assert!(!deleted, "delete_skill should return false when no row matches");
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
        let tables = list_tables(&conn).unwrap();
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

    // --- New tests for missing methods ---

    #[test]
    fn get_skill_by_id_found() {
        let conn = test_conn();
        let skill = make_skill();
        insert_skill(&conn, &skill).unwrap();

        let found = get_skill_by_id(&conn, "skill-001").unwrap();
        assert!(found.is_some());
        assert_eq!(found.unwrap().name, "test-skill");
    }

    #[test]
    fn get_skill_by_id_not_found() {
        let conn = test_conn();
        let found = get_skill_by_id(&conn, "nonexistent").unwrap();
        assert!(found.is_none());
    }

    #[test]
    fn increment_selections_updates_counter() {
        let conn = test_conn();
        let skill = make_skill();
        insert_skill(&conn, &skill).unwrap();

        let updated = increment_selections(&conn, "skill-001").unwrap();
        assert!(updated, "increment_selections should return true for existing skill");

        let found = get_skill_by_id(&conn, "skill-001").unwrap().unwrap();
        assert_eq!(found.total_selections, 1);
        assert_eq!(found.total_applied, 0);
    }

    #[test]
    fn increment_selections_missing_skill() {
        let conn = test_conn();
        let updated = increment_selections(&conn, "nonexistent").unwrap();
        assert!(!updated, "increment_selections should return false for missing skill");
    }

    #[test]
    fn increment_applied_updates_counter() {
        let conn = test_conn();
        let skill = make_skill();
        insert_skill(&conn, &skill).unwrap();

        increment_applied(&conn, "skill-001").unwrap();

        let found = get_skill_by_id(&conn, "skill-001").unwrap().unwrap();
        assert_eq!(found.total_applied, 1);
        assert_eq!(found.total_selections, 0);
    }

    #[test]
    fn increment_completions_updates_counter() {
        let conn = test_conn();
        let skill = make_skill();
        insert_skill(&conn, &skill).unwrap();

        increment_completions(&conn, "skill-001").unwrap();

        let found = get_skill_by_id(&conn, "skill-001").unwrap().unwrap();
        assert_eq!(found.total_completions, 1);
    }

    #[test]
    fn increment_fallbacks_updates_counter() {
        let conn = test_conn();
        let skill = make_skill();
        insert_skill(&conn, &skill).unwrap();

        increment_fallbacks(&conn, "skill-001").unwrap();

        let found = get_skill_by_id(&conn, "skill-001").unwrap().unwrap();
        assert_eq!(found.total_fallbacks, 1);
    }

    #[test]
    fn increment_multiple_counters_accumulates() {
        let conn = test_conn();
        let skill = make_skill();
        insert_skill(&conn, &skill).unwrap();

        increment_selections(&conn, "skill-001").unwrap();
        increment_selections(&conn, "skill-001").unwrap();
        increment_applied(&conn, "skill-001").unwrap();

        let found = get_skill_by_id(&conn, "skill-001").unwrap().unwrap();
        assert_eq!(found.total_selections, 2);
        assert_eq!(found.total_applied, 1);
    }

    #[test]
    fn deactivate_skill_sets_inactive() {
        let conn = test_conn();
        let skill = make_skill();
        insert_skill(&conn, &skill).unwrap();

        let deactivated = deactivate_skill(&conn, "skill-001").unwrap();
        assert!(deactivated, "deactivate_skill should return true for existing skill");

        // get_skill_by_name filters is_active=1, so it should return None now
        let found = get_skill_by_name(&conn, "test-skill").unwrap();
        assert!(found.is_none(), "deactivated skill should not appear in active query");

        // But get_skill_by_id does NOT filter by is_active, so it should still work
        let found = get_skill_by_id(&conn, "skill-001").unwrap();
        assert!(found.is_some());
        assert!(!found.unwrap().is_active);
    }

    #[test]
    fn deactivate_skill_missing_returns_false() {
        let conn = test_conn();
        let result = deactivate_skill(&conn, "nonexistent").unwrap();
        assert!(!result);
    }

    #[test]
    fn evolve_skill_creates_new_version() {
        let conn = test_conn();

        // Create parent
        let mut parent = make_skill();
        parent.id = "parent-1".to_string();
        parent.name = "my-skill".to_string();
        insert_skill(&conn, &parent).unwrap();

        // Build evolved version (not yet inserted — evolve_skill does the insert)
        let mut child = make_skill();
        child.id = "child-1".to_string();
        child.name = "my-skill".to_string();
        child.lineage_origin = "fixed".to_string();
        child.lineage_generation = 1;

        // Evolve: deactivate parent, insert child, link lineage
        evolve_skill(&conn, &child, &["parent-1"], true).unwrap();

        // Parent should be deactivated
        let parent_found = get_skill_by_id(&conn, "parent-1").unwrap().unwrap();
        assert!(!parent_found.is_active);

        // Child should be active
        let child_found = get_skill_by_id(&conn, "child-1").unwrap().unwrap();
        assert!(child_found.is_active);

        // Lineage edge should exist
        let children = get_children(&conn, "parent-1").unwrap();
        assert_eq!(children, vec!["child-1"]);
    }

    #[test]
    fn evolve_skill_without_deactivation() {
        let conn = test_conn();

        let mut parent = make_skill();
        parent.id = "parent-2".to_string();
        parent.name = "derived-parent".to_string();
        insert_skill(&conn, &parent).unwrap();

        let mut child = make_skill();
        child.id = "child-2".to_string();
        child.name = "derived-child".to_string();
        child.lineage_origin = "derived".to_string();
        child.lineage_generation = 1;

        // DERIVED evolution: parent stays active, evolve_skill inserts child
        evolve_skill(&conn, &child, &["parent-2"], false).unwrap();

        let parent_found = get_skill_by_id(&conn, "parent-2").unwrap().unwrap();
        assert!(parent_found.is_active, "parent should remain active for DERIVED evolution");
    }

    #[test]
    fn save_and_get_judgment() {
        let conn = test_conn();

        // Need a skill and analysis for foreign keys
        let skill = make_skill();
        insert_skill(&conn, &skill).unwrap();

        insert_execution_analysis(
            &conn, "analysis-001", "task-001", "agent-1", "test", None,
        ).unwrap();

        let judgment = SkillJudgment {
            id: "j-001".to_string(),
            analysis_id: "analysis-001".to_string(),
            skill_id: "skill-001".to_string(),
            selected: true,
            applied: true,
            completed: false,
            fell_back: false,
        };
        save_judgment(&conn, &judgment).unwrap();

        let judgments = get_judgments_for_skill(&conn, "skill-001", 10).unwrap();
        assert_eq!(judgments.len(), 1);
        assert_eq!(judgments[0].id, "j-001");
        assert!(judgments[0].selected);
        assert!(judgments[0].applied);
        assert!(!judgments[0].completed);
        assert!(!judgments[0].fell_back);
    }

    #[test]
    fn get_judgments_for_skill_empty() {
        let conn = test_conn();
        let skill = make_skill();
        insert_skill(&conn, &skill).unwrap();

        let judgments = get_judgments_for_skill(&conn, "skill-001", 10).unwrap();
        assert!(judgments.is_empty());
    }

    #[test]
    fn get_judgments_respects_limit() {
        let conn = test_conn();
        let skill = make_skill();
        insert_skill(&conn, &skill).unwrap();

        // Insert an analysis
        insert_execution_analysis(
            &conn, "analysis-100", "task-100", "agent-1", "test", None,
        ).unwrap();

        // Insert 5 judgments
        for i in 0..5 {
            let j = SkillJudgment {
                id: format!("j-limit-{i}"),
                analysis_id: "analysis-100".to_string(),
                skill_id: "skill-001".to_string(),
                selected: true,
                applied: false,
                completed: false,
                fell_back: false,
            };
            save_judgment(&conn, &j).unwrap();
        }

        let judgments = get_judgments_for_skill(&conn, "skill-001", 3).unwrap();
        assert_eq!(judgments.len(), 3, "should return at most limit judgments");
    }

    #[test]
    fn get_judgments_limit_minimum_is_one() {
        let conn = test_conn();
        let skill = make_skill();
        insert_skill(&conn, &skill).unwrap();

        insert_execution_analysis(
            &conn, "analysis-min", "task-min", "agent-1", "test", None,
        ).unwrap();

        let j = SkillJudgment {
            id: "j-min-1".to_string(),
            analysis_id: "analysis-min".to_string(),
            skill_id: "skill-001".to_string(),
            selected: true,
            applied: false,
            completed: false,
            fell_back: false,
        };
        save_judgment(&conn, &j).unwrap();

        // limit=0 should be clamped to 1
        let judgments = get_judgments_for_skill(&conn, "skill-001", 0).unwrap();
        assert_eq!(judgments.len(), 1);
    }

    #[test]
    fn get_ancestry_linear_chain() {
        let conn = test_conn();

        // grandparent -> parent -> child
        let mut gp = make_skill();
        gp.id = "gp".to_string();
        gp.name = "grandparent".to_string();
        gp.lineage_generation = 0;
        insert_skill(&conn, &gp).unwrap();

        let mut parent = make_skill();
        parent.id = "p".to_string();
        parent.name = "parent".to_string();
        parent.lineage_generation = 1;
        insert_skill(&conn, &parent).unwrap();
        conn.execute(
            "INSERT INTO skill_lineage_parents (skill_id, parent_id) VALUES ('p', 'gp')",
            [],
        ).unwrap();

        let mut child = make_skill();
        child.id = "c".to_string();
        child.name = "child".to_string();
        child.lineage_generation = 2;
        insert_skill(&conn, &child).unwrap();
        conn.execute(
            "INSERT INTO skill_lineage_parents (skill_id, parent_id) VALUES ('c', 'p')",
            [],
        ).unwrap();

        let ancestry = get_ancestry(&conn, "c", 10).unwrap();
        assert_eq!(ancestry.len(), 2);
        // Sorted by generation ascending (oldest first)
        assert_eq!(ancestry[0].id, "gp");
        assert_eq!(ancestry[1].id, "p");
    }

    #[test]
    fn get_ancestry_no_parents() {
        let conn = test_conn();
        let skill = make_skill();
        insert_skill(&conn, &skill).unwrap();

        let ancestry = get_ancestry(&conn, "skill-001", 10).unwrap();
        assert!(ancestry.is_empty(), "skill with no parents should return empty ancestry");
    }

    #[test]
    fn get_ancestry_respects_max_depth() {
        let conn = test_conn();

        // Create a chain: s0 <- s1 <- s2 <- s3
        for i in 0..4 {
            let mut s = make_skill();
            s.id = format!("s{i}");
            s.name = format!("skill-{i}");
            s.lineage_generation = i as i64;
            insert_skill(&conn, &s).unwrap();
            if i > 0 {
                conn.execute(
                    &format!("INSERT INTO skill_lineage_parents (skill_id, parent_id) VALUES ('s{i}', 's{}')", i - 1),
                    [],
                ).unwrap();
            }
        }

        // max_depth=1 should only find s3's direct parent (s2)
        let ancestry = get_ancestry(&conn, "s3", 1).unwrap();
        assert_eq!(ancestry.len(), 1, "max_depth=1 should find only the direct parent");
        assert_eq!(ancestry[0].id, "s2");
    }

    #[test]
    fn get_children_returns_derived_ids() {
        let conn = test_conn();

        let mut parent = make_skill();
        parent.id = "parent-c".to_string();
        parent.name = "parent-c".to_string();
        insert_skill(&conn, &parent).unwrap();

        let mut child1 = make_skill();
        child1.id = "child-c1".to_string();
        child1.name = "child-c1".to_string();
        insert_skill(&conn, &child1).unwrap();
        conn.execute(
            "INSERT INTO skill_lineage_parents (skill_id, parent_id) VALUES ('child-c1', 'parent-c')",
            [],
        ).unwrap();

        let mut child2 = make_skill();
        child2.id = "child-c2".to_string();
        child2.name = "child-c2".to_string();
        insert_skill(&conn, &child2).unwrap();
        conn.execute(
            "INSERT INTO skill_lineage_parents (skill_id, parent_id) VALUES ('child-c2', 'parent-c')",
            [],
        ).unwrap();

        let children = get_children(&conn, "parent-c").unwrap();
        assert_eq!(children.len(), 2);
        assert!(children.contains(&"child-c1".to_string()));
        assert!(children.contains(&"child-c2".to_string()));
    }

    #[test]
    fn get_children_no_children() {
        let conn = test_conn();
        let skill = make_skill();
        insert_skill(&conn, &skill).unwrap();

        let children = get_children(&conn, "skill-001").unwrap();
        assert!(children.is_empty());
    }
}
