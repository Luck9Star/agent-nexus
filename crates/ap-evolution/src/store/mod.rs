//! `EvolutionStore`: SQLite-backed persistence facade for the Self-Evolution Engine.
//!
//! Thread-safe via `r2d2` connection pool.
//! Uses WAL mode for file-backed databases.

pub mod error;
pub mod queries;
pub mod schema;
pub mod traits;

use r2d2::Pool;
use r2d2_sqlite::SqliteConnectionManager;
use std::path::Path;

pub use error::{Result, StoreError};
pub use traits::Store;

// Re-export query types for convenience
pub use queries::{
    AgentRecord, ExecutionAnalysis, SkillJudgment, SkillRecord,
};

/// Known table names used in tests to verify whitelist correctness.
#[cfg(test)]
const VALID_TABLES: &[&str] = &[
    "skill_records",
    "skill_lineage_parents",
    "execution_analyses",
    "skill_judgments",
    "context_budget_log",
    "agent_records",
    "_meta",
];

// ---------------------------------------------------------------------------
// EvolutionStore facade
// ---------------------------------------------------------------------------

/// SQLite-backed store for skill records and evolution data.
///
/// Thread-safe via an `r2d2` connection pool.  Each method acquires a
/// connection from the pool, performs the operation, and returns the
/// connection.  Read-heavy workloads benefit from multiple concurrent
/// readers under WAL mode.
///
/// **Transaction semantics**: Each public method acquires its own connection from the pool.
/// Multi-step operations are NOT atomic across method calls. For atomic multi-step
/// operations, use the `queries::` module directly with a single connection.
pub struct EvolutionStore {
    pool: Pool<SqliteConnectionManager>,
}

impl EvolutionStore {
    /// Create a file-backed `EvolutionStore` with WAL mode.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn new(path: &Path) -> Result<Self> {
        let manager = SqliteConnectionManager::file(path)
            .with_init(|conn| {
                conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA synchronous=NORMAL; PRAGMA busy_timeout=5000;")?;
                // Only run schema creation + migrations on the first connection.
                // Subsequent connections from the pool inherit the same WAL-mode DB
                // and tables are already present — skip the overhead.
                let needs_init: bool = conn
                    .query_row(
                        "SELECT COUNT(*) = 0 FROM sqlite_master WHERE type='table' AND name='_meta'",
                        [],
                        |row| row.get::<_, bool>(0),
                    )
                    .unwrap_or(true);
                if needs_init {
                    conn.execute_batch(schema::SCHEMA_SQL)?;
                    Self::run_migrations(conn)?;
                }
                Ok(())
            });
        let pool = Pool::builder()
            .max_size(4)
            .build(manager)?;
        Ok(Self { pool })
    }

    /// Create an in-memory `EvolutionStore` (for testing).
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn new_in_memory() -> Result<Self> {
        let manager = SqliteConnectionManager::memory()
            .with_init(|conn| {
                conn.execute_batch(schema::SCHEMA_SQL)?;
                Self::run_migrations(conn)?;
                Ok(())
            });
        let pool = Pool::builder()
            .max_size(1) // in-memory DB is per-connection, so limit to 1
            .build(manager)?;
        Ok(Self { pool })
    }

    /// Check the stored schema version and apply migrations if needed.
    fn run_migrations(conn: &rusqlite::Connection) -> std::result::Result<(), rusqlite::Error> {
        let current: Option<String> = conn
            .query_row(
                schema::GET_SCHEMA_VERSION_SQL,
                [],
                |row| row.get(0),
            )
            .ok();

        // Fresh DB (no _meta row): schema was just created from SCHEMA_SQL,
        // so all tables are at the latest version. Just stamp and return.
        let current = match current {
            Some(v) if v == schema::SCHEMA_VERSION => return Ok(()),
            Some(v) => v,
            None => {
                conn.execute(schema::SET_SCHEMA_VERSION_SQL, [schema::SCHEMA_VERSION])?;
                return Ok(());
            }
        };

        // Existing DB at older version — apply migration chain inside a transaction
        // so that a crash mid-migration does not leave the schema in a partial state.
        conn.execute_batch("BEGIN TRANSACTION")?;
        let migration_result: std::result::Result<(), rusqlite::Error> = (|| {
            let mut current = current;
            while current != schema::SCHEMA_VERSION {
                let mut applied = false;
                for (from, to, sql) in schema::MIGRATIONS {
                    if current == *from {
                        conn.execute_batch(sql)?;
                        current = to.to_string();
                        applied = true;
                        break;
                    }
                }
                if !applied {
                    return Err(rusqlite::Error::ToSqlConversionFailure(
                        Box::from(format!(
                            "migration stuck at version {current}: no migration from {current} to {target}",
                            target = schema::SCHEMA_VERSION
                        )),
                    ));
                }
            }

            // All migrations applied — stamp the target version.
            conn.execute(schema::SET_SCHEMA_VERSION_SQL, [schema::SCHEMA_VERSION])?;
            Ok(())
        })();

        match migration_result {
            Ok(()) => {
                conn.execute_batch("COMMIT")?;
                Ok(())
            }
            Err(e) => {
                // Best-effort rollback; ignore error if the rollback itself fails
                // (the connection may be in a broken state regardless).
                let _ = conn.execute_batch("ROLLBACK");
                Err(e)
            }
        }
    }

    /// Acquire a connection from the pool.
    fn conn(&self) -> Result<r2d2::PooledConnection<SqliteConnectionManager>> {
        self.pool.get().map_err(StoreError::from)
    }

    /// Acquire a raw connection for direct SQL execution (e.g. in tests).
    /// Prefer using typed methods over this.
    #[cfg(test)]
    fn raw_conn(&self) -> Result<r2d2::PooledConnection<SqliteConnectionManager>> {
        self.conn()
    }

    /// Execute a closure inside a transaction.
    ///
    /// Automatically handles BEGIN / COMMIT / ROLLBACK.
    pub fn with_transaction<T, E: From<StoreError>>(
        &self,
        f: impl FnOnce(&rusqlite::Connection) -> std::result::Result<T, E>,
    ) -> std::result::Result<T, E> {
        let conn = self.pool.get().map_err(StoreError::from)?;
        let tx = conn.unchecked_transaction().map_err(StoreError::from)?;
        match f(&tx) {
            Ok(v) => {
                tx.commit().map_err(StoreError::from)?;
                Ok(v)
            }
            Err(e) => {
                let _ = tx.rollback();
                Err(e)
            }
        }
    }

    // -----------------------------------------------------------------------
    // Skill record operations
    // -----------------------------------------------------------------------

    /// Insert a skill record.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn insert_skill(&self, skill: &SkillRecord) -> Result<()> {
        let conn = self.conn()?;
        queries::insert_skill(&conn, skill)
    }

    /// Get an active skill by name.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn get_skill_by_name(&self, name: &str) -> Result<Option<SkillRecord>> {
        let conn = self.conn()?;
        queries::get_skill_by_name(&conn, name)
    }

    /// Get a skill by ID (regardless of active status).
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn get_skill_by_id(&self, id: &str) -> Result<Option<SkillRecord>> {
        let conn = self.conn()?;
        queries::get_skill_by_id(&conn, id)
    }

    /// Get all active skills.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn get_active_skills(&self) -> Result<Vec<SkillRecord>> {
        let conn = self.conn()?;
        queries::get_active_skills(&conn)
    }

    /// Get all skills (both active and inactive).
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn get_all_skills(&self) -> Result<Vec<SkillRecord>> {
        let conn = self.conn()?;
        queries::get_all_skills(&conn)
    }

    /// Delete a skill record by id.
    ///
    /// Returns `Ok(true)` if a row was deleted, `Ok(false)` if no matching row was found.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn delete_skill(&self, id: &str) -> Result<bool> {
        let conn = self.conn()?;
        queries::delete_skill(&conn, id)
    }

    // -----------------------------------------------------------------------
    // Counter increment operations
    // -----------------------------------------------------------------------

    /// Increment `total_selections` for a skill.
    ///
    /// Returns `Ok(true)` if updated, `Ok(false)` if skill not found.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn increment_selections(&self, id: &str) -> Result<bool> {
        let conn = self.conn()?;
        queries::increment_selections(&conn, id)
    }

    /// Increment `total_applied` for a skill.
    ///
    /// Returns `Ok(true)` if updated, `Ok(false)` if skill not found.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn increment_applied(&self, id: &str) -> Result<bool> {
        let conn = self.conn()?;
        queries::increment_applied(&conn, id)
    }

    /// Increment `total_completions` for a skill.
    ///
    /// Returns `Ok(true)` if updated, `Ok(false)` if skill not found.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn increment_completions(&self, id: &str) -> Result<bool> {
        let conn = self.conn()?;
        queries::increment_completions(&conn, id)
    }

    /// Increment `total_fallbacks` for a skill.
    ///
    /// Returns `Ok(true)` if updated, `Ok(false)` if skill not found.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn increment_fallbacks(&self, id: &str) -> Result<bool> {
        let conn = self.conn()?;
        queries::increment_fallbacks(&conn, id)
    }

    /// Batch-increment multiple counters for a single skill in one SQL statement.
    ///
    /// Each delta can be 0 (no change). More efficient than calling individual
    /// `increment_*` methods when multiple counters need updating.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn batch_increment(
        &self,
        id: &str,
        selections: u32,
        applied: u32,
        completions: u32,
        fallbacks: u32,
    ) -> Result<bool> {
        let conn = self.conn()?;
        queries::batch_increment(&conn, id, selections, applied, completions, fallbacks)
    }

    // -----------------------------------------------------------------------
    // Skill lifecycle
    // -----------------------------------------------------------------------

    /// Deactivate a skill by setting `is_active = 0`.
    ///
    /// Returns `Ok(true)` if updated, `Ok(false)` if skill not found.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn deactivate_skill(&self, id: &str) -> Result<bool> {
        let conn = self.conn()?;
        queries::deactivate_skill(&conn, id)
    }

    /// Evolve a skill: insert a new version with lineage parents.
    ///
    /// If `deactivate_parents` is true (FIX evolution), parents are deactivated.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn evolve_skill(
        &self,
        new_skill: &SkillRecord,
        parent_ids: &[&str],
        deactivate_parents: bool,
    ) -> Result<()> {
        let conn = self.conn()?;
        queries::evolve_skill(&conn, new_skill, parent_ids, deactivate_parents)
    }

    // -----------------------------------------------------------------------
    // Judgment operations
    // -----------------------------------------------------------------------

    /// Insert a skill judgment.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn save_judgment(&self, judgment: &SkillJudgment) -> Result<()> {
        let conn = self.conn()?;
        queries::save_judgment(&conn, judgment)
    }

    /// Get judgments for a skill, most recent first.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn get_judgments_for_skill(
        &self,
        skill_id: &str,
        limit: i64,
    ) -> Result<Vec<SkillJudgment>> {
        let conn = self.conn()?;
        queries::get_judgments_for_skill(&conn, skill_id, limit)
    }

    // -----------------------------------------------------------------------
    // Lineage queries
    // -----------------------------------------------------------------------

    /// Walk up the lineage tree via BFS, returning ancestors oldest-first.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn get_ancestry(
        &self,
        skill_id: &str,
        max_depth: usize,
    ) -> Result<Vec<SkillRecord>> {
        let conn = self.conn()?;
        queries::get_ancestry(&conn, skill_id, max_depth)
    }

    /// Get child skill IDs derived from the given parent.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn get_children(&self, parent_id: &str) -> Result<Vec<String>> {
        let conn = self.conn()?;
        queries::get_children(&conn, parent_id)
    }

    // -----------------------------------------------------------------------
    // Execution analysis operations
    // -----------------------------------------------------------------------

    /// Record a post-task analysis.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn record_analysis(
        &self,
        task_id: &str,
        agent_name: &str,
        analysis_text: &str,
        evolution_suggestions: Option<&str>,
    ) -> Result<String> {
        let id = uuid::Uuid::new_v4().to_string();
        let conn = self.conn()?;
        queries::insert_execution_analysis(
            &conn,
            &id,
            task_id,
            agent_name,
            analysis_text,
            evolution_suggestions,
        )?;
        Ok(id)
    }

    /// Get analyses for a specific task.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn get_analyses_for_task(&self, task_id: &str) -> Result<Vec<ExecutionAnalysis>> {
        let conn = self.conn()?;
        queries::get_analyses_for_task(&conn, task_id)
    }

    // -----------------------------------------------------------------------
    // Context budget log
    // -----------------------------------------------------------------------

    /// Log a budget event (compaction, `budget_check`, etc.).
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn log_budget_event(
        &self,
        agent_name: &str,
        event_type: &str,
        tokens_before: Option<i64>,
        tokens_after: Option<i64>,
        details: Option<&str>,
    ) -> Result<()> {
        let id = uuid::Uuid::new_v4().to_string();
        let conn = self.conn()?;
        queries::insert_context_budget_log(
            &conn,
            &id,
            agent_name,
            event_type,
            tokens_before,
            tokens_after,
            details,
        )
    }

    // -----------------------------------------------------------------------
    // Agent record operations
    // -----------------------------------------------------------------------

    /// Upsert an agent record.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn upsert_agent_record(
        &self,
        agent_id: &str,
        name: &str,
        agent_type: &str,
        skill_ids: &str,
        orchestration_toml: Option<&str>,
    ) -> Result<()> {
        let conn = self.conn()?;
        queries::upsert_agent_record(&conn, agent_id, name, agent_type, skill_ids, orchestration_toml)
    }

    /// Get an agent record by name.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn get_agent_record(&self, name: &str) -> Result<Option<AgentRecord>> {
        let conn = self.conn()?;
        queries::get_agent_record(&conn, name)
    }

    // -----------------------------------------------------------------------
    // Convenience methods
    // -----------------------------------------------------------------------

    /// List all active skills (alias for `get_active_skills`).
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn list_skills(&self) -> Result<Vec<SkillRecord>> {
        self.get_active_skills()
    }

    /// Count active skills in the store.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn count_active_skills(&self) -> Result<i64> {
        let conn = self.conn()?;
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM skill_records WHERE is_active = 1",
            [],
            |row| row.get(0),
        )?;
        Ok(count)
    }

    // -----------------------------------------------------------------------
    // Health score persistence (_meta table)
    // -----------------------------------------------------------------------

    /// Load the persisted health score and total count from `_meta`.
    ///
    /// Returns `(health_score, total_count)` or `(1.0, 0)` if no persisted state.
    pub fn load_health_state(&self) -> Result<(f64, u64)> {
        let conn = self.conn()?;
        let score: Option<String> = conn
            .query_row(
                "SELECT value FROM _meta WHERE key = 'health_score'",
                [],
                |row| row.get(0),
            )
            .ok();
        let total: Option<String> = conn
            .query_row(
                "SELECT value FROM _meta WHERE key = 'health_total'",
                [],
                |row| row.get(0),
            )
            .ok();

        let score = score
            .and_then(|s| s.parse::<f64>().ok())
            .unwrap_or(1.0);
        #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
        let total = total
            .and_then(|s| s.parse::<u64>().ok())
            .unwrap_or(0);

        Ok((score, total))
    }

    /// Persist the health score and total count to `_meta`.
    pub fn save_health_state(&self, score: f64, total: u64) -> Result<()> {
        let conn = self.conn()?;
        let tx = conn.unchecked_transaction()?;
        tx.execute(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('health_score', ?1)",
            [score.to_string()],
        )?;
        tx.execute(
            "INSERT OR REPLACE INTO _meta (key, value) VALUES ('health_total', ?1)",
            [total.to_string()],
        )?;
        tx.commit()?;
        Ok(())
    }

    // -----------------------------------------------------------------------
    // Testing / introspection
    // -----------------------------------------------------------------------

    /// List all table names (for testing schema).
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn list_tables(&self) -> Result<Vec<String>> {
        let conn = self.conn()?;
        queries::list_tables(&conn)
    }

    /// Count rows in a table (for testing).
    ///
    /// Delegates to `queries::count_rows` which validates the table name
    /// against a whitelist to prevent SQL injection.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn count_rows(&self, table: &str) -> Result<i64> {
        let conn = self.conn()?;
        queries::count_rows(&conn, table)
    }
}

// ---------------------------------------------------------------------------
// Trait implementation
// ---------------------------------------------------------------------------

impl traits::Store for EvolutionStore {
    fn insert_skill(&self, skill: &SkillRecord) -> Result<()> {
        self.insert_skill(skill)
    }

    fn get_skill_by_name(&self, name: &str) -> Result<Option<SkillRecord>> {
        self.get_skill_by_name(name)
    }

    fn get_skill_by_id(&self, id: &str) -> Result<Option<SkillRecord>> {
        self.get_skill_by_id(id)
    }

    fn get_active_skills(&self) -> Result<Vec<SkillRecord>> {
        self.get_active_skills()
    }

    fn get_children(&self, parent_id: &str) -> Result<Vec<String>> {
        self.get_children(parent_id)
    }

    fn evolve_skill(
        &self,
        new_skill: &SkillRecord,
        parent_ids: &[&str],
        deactivate_parents: bool,
    ) -> Result<()> {
        self.evolve_skill(new_skill, parent_ids, deactivate_parents)
    }

    fn load_health_state(&self) -> Result<(f64, u64)> {
        self.load_health_state()
    }

    fn save_health_state(&self, score: f64, total: u64) -> Result<()> {
        self.save_health_state(score, total)
    }

    fn count_active_skills(&self) -> Result<i64> {
        self.count_active_skills()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_in_memory_creates_all_tables() {
        let store = EvolutionStore::new_in_memory().unwrap();
        let tables = store.list_tables().unwrap();
        assert!(tables.contains(&"skill_records".to_string()));
        assert!(tables.contains(&"skill_lineage_parents".to_string()));
        assert!(tables.contains(&"execution_analyses".to_string()));
        assert!(tables.contains(&"skill_judgments".to_string()));
        assert!(tables.contains(&"context_budget_log".to_string()));
        assert!(tables.contains(&"agent_records".to_string()));
    }

    #[test]
    fn insert_and_retrieve_skill() {
        let store = EvolutionStore::new_in_memory().unwrap();
        let skill = SkillRecord {
            id: "s-1".to_string(),
            name: "my-skill".to_string(),
            version: "1.0.0".to_string(),
            lineage_origin: "imported".to_string(),
            lineage_generation: 0,
            lineage_content_diff: None,
            lineage_content_snapshot: None,
            directory: Some("/skills".to_string()),
            is_active: true,
            total_selections: 10,
            total_applied: 8,
            total_completions: 7,
            total_fallbacks: 1,
            created_at: chrono::Utc::now().to_rfc3339(),
            updated_at: chrono::Utc::now().to_rfc3339(),
        };

        store.insert_skill(&skill).unwrap();
        let found = store.get_skill_by_name("my-skill").unwrap().unwrap();
        assert_eq!(found.id, "s-1");
        assert_eq!(found.total_selections, 10);
        assert_eq!(found.total_applied, 8);
        assert_eq!(found.total_completions, 7);
        assert_eq!(found.total_fallbacks, 1);
    }

    #[test]
    fn delete_skill_removes_it() {
        let store = EvolutionStore::new_in_memory().unwrap();
        let skill = SkillRecord {
            id: "s-del".to_string(),
            name: "to-delete".to_string(),
            version: "1.0.0".to_string(),
            lineage_origin: "imported".to_string(),
            lineage_generation: 0,
            lineage_content_diff: None,
            lineage_content_snapshot: None,
            directory: None,
            is_active: true,
            total_selections: 0,
            total_applied: 0,
            total_completions: 0,
            total_fallbacks: 0,
            created_at: chrono::Utc::now().to_rfc3339(),
            updated_at: chrono::Utc::now().to_rfc3339(),
        };
        store.insert_skill(&skill).unwrap();
        assert!(store.get_skill_by_name("to-delete").unwrap().is_some());
        let deleted = store.delete_skill("s-del").unwrap();
        assert!(deleted, "delete_skill should return true when row exists");
        assert!(store.get_skill_by_name("to-delete").unwrap().is_none());
    }

    #[test]
    fn record_analysis_returns_id() {
        let store = EvolutionStore::new_in_memory().unwrap();
        let id = store
            .record_analysis("task-1", "agent-1", "some analysis", Some(r#"[{"type":"FIX"}]"#))
            .unwrap();
        assert!(!id.is_empty());

        let analyses = store.get_analyses_for_task("task-1").unwrap();
        assert_eq!(analyses.len(), 1);
        assert_eq!(analyses[0].analysis, "some analysis");
    }

    #[test]
    fn log_budget_event_creates_row() {
        let store = EvolutionStore::new_in_memory().unwrap();
        store
            .log_budget_event("agent-1", "compaction", Some(5000), Some(2000), None)
            .unwrap();
        assert_eq!(store.count_rows("context_budget_log").unwrap(), 1);
    }

    #[test]
    fn upsert_and_get_agent() {
        let store = EvolutionStore::new_in_memory().unwrap();
        store
            .upsert_agent_record("a-1", "test-agent", "atomic", "[]", None)
            .unwrap();

        let agent = store.get_agent_record("test-agent").unwrap().unwrap();
        assert_eq!(agent.agent_id, "a-1");
        assert_eq!(agent.agent_type, "atomic");
    }

    #[test]
    fn file_backed_store_works() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("test.db");
        let store = EvolutionStore::new(&db_path).unwrap();
        let tables = store.list_tables().unwrap();
        assert!(tables.contains(&"skill_records".to_string()));
    }

    #[test]
    fn concurrent_access_does_not_deadlock() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("concurrent_test.db");
        let store = EvolutionStore::new(&db_path).unwrap();
        let store = std::sync::Arc::new(store);

        let mut handles = vec![];
        for i in 0..8 {
            let s = store.clone();
            handles.push(std::thread::spawn(move || {
                let skill = SkillRecord {
                    id: format!("s-{i}"),
                    name: format!("skill-{i}"),
                    version: "1.0.0".to_string(),
                    lineage_origin: "imported".to_string(),
                    lineage_generation: 0,
                    lineage_content_diff: None,
                    lineage_content_snapshot: None,
                    directory: None,
                    is_active: true,
                    total_selections: 0,
                    total_applied: 0,
                    total_completions: 0,
                    total_fallbacks: 0,
                    created_at: chrono::Utc::now().to_rfc3339(),
                    updated_at: chrono::Utc::now().to_rfc3339(),
                };
                s.insert_skill(&skill).unwrap();
            }));
        }
        for h in handles {
            h.join().unwrap();
        }
        let active = store.get_active_skills().unwrap();
        assert_eq!(active.len(), 8);
    }

    #[test]
    fn count_rows_rejects_invalid_table() {
        let store = EvolutionStore::new_in_memory().unwrap();
        let result = store.count_rows("users; DROP TABLE skill_records; --");
        assert!(result.is_err());
        let err = result.unwrap_err().to_string();
        assert!(err.contains("Unknown table"), "Expected whitelist error, got: {err}");
    }

    #[test]
    fn count_rows_accepts_valid_tables() {
        let store = EvolutionStore::new_in_memory().unwrap();
        for table in VALID_TABLES {
            let result = store.count_rows(table);
            assert!(result.is_ok(), "count_rows should accept '{table}'");
        }
    }

    // --- Facade tests for missing methods ---

    fn make_facade_skill(id: &str, name: &str) -> SkillRecord {
        SkillRecord {
            id: id.to_string(),
            name: name.to_string(),
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
    fn facade_get_skill_by_id() {
        let store = EvolutionStore::new_in_memory().unwrap();
        let skill = make_facade_skill("facade-1", "facade-skill");
        store.insert_skill(&skill).unwrap();

        let found = store.get_skill_by_id("facade-1").unwrap();
        assert!(found.is_some());
        assert_eq!(found.unwrap().name, "facade-skill");

        let not_found = store.get_skill_by_id("nonexistent").unwrap();
        assert!(not_found.is_none());
    }

    #[test]
    fn facade_increment_counters() {
        let store = EvolutionStore::new_in_memory().unwrap();
        let skill = make_facade_skill("ctr-1", "ctr-skill");
        store.insert_skill(&skill).unwrap();

        assert!(store.increment_selections("ctr-1").unwrap());
        assert!(store.increment_selections("ctr-1").unwrap());
        assert!(store.increment_applied("ctr-1").unwrap());
        assert!(store.increment_completions("ctr-1").unwrap());
        assert!(store.increment_fallbacks("ctr-1").unwrap());

        let found = store.get_skill_by_id("ctr-1").unwrap().unwrap();
        assert_eq!(found.total_selections, 2);
        assert_eq!(found.total_applied, 1);
        assert_eq!(found.total_completions, 1);
        assert_eq!(found.total_fallbacks, 1);

        // Missing skill returns false
        assert!(!store.increment_selections("nonexistent").unwrap());
    }

    #[test]
    fn facade_deactivate_skill() {
        let store = EvolutionStore::new_in_memory().unwrap();
        let skill = make_facade_skill("deact-1", "deact-skill");
        store.insert_skill(&skill).unwrap();

        assert!(store.deactivate_skill("deact-1").unwrap());
        assert!(store.get_skill_by_name("deact-skill").unwrap().is_none());
        let found = store.get_skill_by_id("deact-1").unwrap().unwrap();
        assert!(!found.is_active);

        assert!(!store.deactivate_skill("nonexistent").unwrap());
    }

    #[test]
    fn facade_evolve_skill() {
        let store = EvolutionStore::new_in_memory().unwrap();

        let parent = make_facade_skill("ev-parent", "ev-skill");
        store.insert_skill(&parent).unwrap();

        let mut child = make_facade_skill("ev-child", "ev-skill");
        child.lineage_generation = 1;
        child.lineage_origin = "fixed".to_string();

        // evolve_skill inserts the child record itself
        store.evolve_skill(&child, &["ev-parent"], true).unwrap();

        // Parent deactivated
        let p = store.get_skill_by_id("ev-parent").unwrap().unwrap();
        assert!(!p.is_active);

        // Child active
        let c = store.get_skill_by_id("ev-child").unwrap().unwrap();
        assert!(c.is_active);

        // Lineage link
        let children = store.get_children("ev-parent").unwrap();
        assert_eq!(children, vec!["ev-child"]);
    }

    #[test]
    fn facade_save_and_get_judgments() {
        let store = EvolutionStore::new_in_memory().unwrap();

        let skill = make_facade_skill("j-skill", "j-skill-name");
        store.insert_skill(&skill).unwrap();

        store.record_analysis("j-task", "j-agent", "analysis", None).unwrap();
        let analyses = store.get_analyses_for_task("j-task").unwrap();
        let analysis_id = &analyses[0].id;

        let judgment = SkillJudgment {
            id: "j-001".to_string(),
            analysis_id: analysis_id.clone(),
            skill_id: "j-skill".to_string(),
            selected: true,
            applied: true,
            completed: false,
            fell_back: false,
        };
        store.save_judgment(&judgment).unwrap();

        let judgments = store.get_judgments_for_skill("j-skill", 10).unwrap();
        assert_eq!(judgments.len(), 1);
        assert!(judgments[0].selected);
    }

    #[test]
    fn facade_get_ancestry() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("ancestry_test.db");
        let store = EvolutionStore::new(&db_path).unwrap();

        let gp = make_facade_skill("anc-gp", "anc-gp");
        store.insert_skill(&gp).unwrap();

        let mut p = make_facade_skill("anc-p", "anc-p");
        p.lineage_generation = 1;
        store.insert_skill(&p).unwrap();
        // Insert lineage parent via raw SQL
        {
            let conn = store.raw_conn().unwrap();
            conn.execute(
                "INSERT INTO skill_lineage_parents (skill_id, parent_id) VALUES ('anc-p', 'anc-gp')",
                [],
            ).unwrap();
        }

        let mut c = make_facade_skill("anc-c", "anc-c");
        c.lineage_generation = 2;
        store.insert_skill(&c).unwrap();
        {
            let conn = store.raw_conn().unwrap();
            conn.execute(
                "INSERT INTO skill_lineage_parents (skill_id, parent_id) VALUES ('anc-c', 'anc-p')",
                [],
            ).unwrap();
        }

        let ancestry = store.get_ancestry("anc-c", 10).unwrap();
        assert_eq!(ancestry.len(), 2);
        assert_eq!(ancestry[0].id, "anc-gp");
        assert_eq!(ancestry[1].id, "anc-p");
    }

    #[test]
    fn facade_get_children() {
        let dir = tempfile::tempdir().unwrap();
        let db_path = dir.path().join("children_test.db");
        let store = EvolutionStore::new(&db_path).unwrap();

        let parent = make_facade_skill("ch-parent", "ch-parent");
        store.insert_skill(&parent).unwrap();

        let child = make_facade_skill("ch-child", "ch-child");
        store.insert_skill(&child).unwrap();
        {
            let conn = store.raw_conn().unwrap();
            conn.execute(
                "INSERT INTO skill_lineage_parents (skill_id, parent_id) VALUES ('ch-child', 'ch-parent')",
                [],
            ).unwrap();
        }

        let children = store.get_children("ch-parent").unwrap();
        assert_eq!(children, vec!["ch-child"]);

        let empty = store.get_children("nonexistent").unwrap();
        assert!(empty.is_empty());
    }

    // --- with_transaction tests ---

    #[test]
    fn with_transaction_commits_on_success() {
        let store = EvolutionStore::new_in_memory().unwrap();

        // Insert a skill inside a transaction
        let skill = make_facade_skill("tx-commit", "tx-skill");
        store.with_transaction::<(), StoreError>(|conn| {
            queries::insert_skill(conn, &skill)?;
            Ok(())
        }).unwrap();

        // Data should be visible after commit
        let found = store.get_skill_by_name("tx-skill").unwrap();
        assert!(found.is_some(), "Transaction should have committed the skill");
    }

    #[test]
    fn with_transaction_rolls_back_on_error() {
        let store = EvolutionStore::new_in_memory().unwrap();

        // Insert a skill normally first
        let skill = make_facade_skill("tx-rollback", "tx-skill-pre");
        store.insert_skill(&skill).unwrap();

        // Attempt a transaction that inserts then fails — the new insert should be rolled back
        let new_skill = make_facade_skill("tx-rollback-2", "tx-skill-should-not-exist");
        let result: std::result::Result<(), StoreError> = store.with_transaction(|conn| {
            queries::insert_skill(conn, &new_skill)?;
            Err(StoreError::Sqlite(rusqlite::Error::InvalidParameterName("force_error".to_string())))
        });

        assert!(result.is_err(), "Transaction should have returned error");
        // The second skill should NOT exist — rollback should have discarded it
        let found = store.get_skill_by_name("tx-skill-should-not-exist").unwrap();
        assert!(found.is_none(), "Rolled-back insert should not be visible");
    }

    #[test]
    fn with_transaction_handles_nested_error_types() {
        let store = EvolutionStore::new_in_memory().unwrap();

        // Use a custom error type that can be created from StoreError
        #[derive(Debug)]
        enum AppError {
            Store(StoreError),
            Custom(String),
        }

        impl std::fmt::Display for AppError {
            fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                match self {
                    AppError::Store(e) => write!(f, "{e}"),
                    AppError::Custom(msg) => write!(f, "{msg}"),
                }
            }
        }

        impl std::error::Error for AppError {
            fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
                match self {
                    AppError::Store(e) => Some(e),
                    AppError::Custom(_) => None,
                }
            }
        }

        impl From<StoreError> for AppError {
            fn from(e: StoreError) -> Self {
                AppError::Store(e)
            }
        }

        // Insert in transaction, then return a custom error
        let skill = make_facade_skill("tx-nested", "tx-nested-skill");
        let result: std::result::Result<(), AppError> = store.with_transaction(|conn| {
            queries::insert_skill(conn, &skill)?;
            Err(AppError::Custom("application-level failure".to_string()))
        });

        match result {
            Err(AppError::Custom(msg)) => assert_eq!(msg, "application-level failure"),
            other => panic!("expected Custom error, got: {other:?}"),
        }

        // Skill should NOT have been committed due to rollback
        let found = store.get_skill_by_name("tx-nested-skill").unwrap();
        assert!(found.is_none(), "Transaction should have rolled back on custom error");
    }
}
