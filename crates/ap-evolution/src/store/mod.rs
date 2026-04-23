//! EvolutionStore: SQLite-backed persistence facade for the Self-Evolution Engine.
//!
//! Thread-safe via `std::sync::Mutex<rusqlite::Connection>`.
//! Uses WAL mode for file-backed databases.

pub mod error;
pub mod queries;
pub mod schema;

use rusqlite::Connection;
use std::path::Path;
use std::sync::Mutex;

pub use error::{Result, StoreError};

// Re-export query types for convenience
pub use queries::{
    AgentRecord, ExecutionAnalysis, SkillJudgment, SkillRecord,
};

// ---------------------------------------------------------------------------
// EvolutionStore facade
// ---------------------------------------------------------------------------

/// SQLite-backed store for skill records and evolution data.
///
/// Thread-safe via `std::sync::Mutex<Connection>`.  All methods acquire
/// the lock, perform the operation synchronously, and release the lock.
/// This is correct because rusqlite operations are CPU-bound and never
/// need to be held across `.await` points.
pub struct EvolutionStore {
    conn: Mutex<Connection>,
}

impl EvolutionStore {
    /// Create a file-backed EvolutionStore with WAL mode.
    pub fn new(path: &Path) -> Result<Self> {
        let conn = Connection::open(path)?;
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;")?;
        conn.execute_batch(schema::SCHEMA_SQL)?;
        Ok(Self {
            conn: Mutex::new(conn),
        })
    }

    /// Create an in-memory EvolutionStore (for testing).
    pub fn new_in_memory() -> Result<Self> {
        let conn = Connection::open_in_memory()?;
        conn.execute_batch(schema::SCHEMA_SQL)?;
        Ok(Self {
            conn: Mutex::new(conn),
        })
    }

    /// Acquire the connection lock.
    fn conn(&self) -> Result<std::sync::MutexGuard<'_, Connection>> {
        self.conn.lock().map_err(|e| {
            StoreError::Io(std::io::Error::other(format!("lock poisoned: {e}")))
        })
    }

    // -----------------------------------------------------------------------
    // Skill record operations
    // -----------------------------------------------------------------------

    /// Insert a skill record.
    pub fn insert_skill(&self, skill: &SkillRecord) -> Result<()> {
        let conn = self.conn()?;
        queries::insert_skill(&conn, skill)
    }

    /// Get an active skill by name.
    pub fn get_skill_by_name(&self, name: &str) -> Result<Option<SkillRecord>> {
        let conn = self.conn()?;
        queries::get_skill_by_name(&conn, name)
    }

    /// Get a skill by ID (regardless of active status).
    pub fn get_skill_by_id(&self, id: &str) -> Result<Option<SkillRecord>> {
        let conn = self.conn()?;
        queries::get_skill_by_id(&conn, id)
    }

    /// Get all active skills.
    pub fn get_active_skills(&self) -> Result<Vec<SkillRecord>> {
        let conn = self.conn()?;
        queries::get_active_skills(&conn)
    }

    /// Delete a skill record by id.
    ///
    /// Returns `Ok(true)` if a row was deleted, `Ok(false)` if no matching row was found.
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
    pub fn increment_selections(&self, id: &str) -> Result<bool> {
        let conn = self.conn()?;
        queries::increment_selections(&conn, id)
    }

    /// Increment `total_applied` for a skill.
    ///
    /// Returns `Ok(true)` if updated, `Ok(false)` if skill not found.
    pub fn increment_applied(&self, id: &str) -> Result<bool> {
        let conn = self.conn()?;
        queries::increment_applied(&conn, id)
    }

    /// Increment `total_completions` for a skill.
    ///
    /// Returns `Ok(true)` if updated, `Ok(false)` if skill not found.
    pub fn increment_completions(&self, id: &str) -> Result<bool> {
        let conn = self.conn()?;
        queries::increment_completions(&conn, id)
    }

    /// Increment `total_fallbacks` for a skill.
    ///
    /// Returns `Ok(true)` if updated, `Ok(false)` if skill not found.
    pub fn increment_fallbacks(&self, id: &str) -> Result<bool> {
        let conn = self.conn()?;
        queries::increment_fallbacks(&conn, id)
    }

    // -----------------------------------------------------------------------
    // Skill lifecycle
    // -----------------------------------------------------------------------

    /// Deactivate a skill by setting `is_active = 0`.
    ///
    /// Returns `Ok(true)` if updated, `Ok(false)` if skill not found.
    pub fn deactivate_skill(&self, id: &str) -> Result<bool> {
        let conn = self.conn()?;
        queries::deactivate_skill(&conn, id)
    }

    /// Evolve a skill: insert a new version with lineage parents.
    ///
    /// If `deactivate_parents` is true (FIX evolution), parents are deactivated.
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
    pub fn save_judgment(&self, judgment: &SkillJudgment) -> Result<()> {
        let conn = self.conn()?;
        queries::save_judgment(&conn, judgment)
    }

    /// Get judgments for a skill, most recent first.
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
    pub fn get_ancestry(
        &self,
        skill_id: &str,
        max_depth: usize,
    ) -> Result<Vec<SkillRecord>> {
        let conn = self.conn()?;
        queries::get_ancestry(&conn, skill_id, max_depth)
    }

    /// Get child skill IDs derived from the given parent.
    pub fn get_children(&self, parent_id: &str) -> Result<Vec<String>> {
        let conn = self.conn()?;
        queries::get_children(&conn, parent_id)
    }

    // -----------------------------------------------------------------------
    // Execution analysis operations
    // -----------------------------------------------------------------------

    /// Record a post-task analysis.
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
    pub fn get_analyses_for_task(&self, task_id: &str) -> Result<Vec<ExecutionAnalysis>> {
        let conn = self.conn()?;
        queries::get_analyses_for_task(&conn, task_id)
    }

    // -----------------------------------------------------------------------
    // Context budget log
    // -----------------------------------------------------------------------

    /// Log a budget event (compaction, budget_check, etc.).
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
    pub fn get_agent_record(&self, name: &str) -> Result<Option<AgentRecord>> {
        let conn = self.conn()?;
        queries::get_agent_record(&conn, name)
    }

    // -----------------------------------------------------------------------
    // Convenience methods
    // -----------------------------------------------------------------------

    /// List all active skills (alias for `get_active_skills`).
    pub fn list_skills(&self) -> Result<Vec<SkillRecord>> {
        self.get_active_skills()
    }

    /// Count active skills in the store.
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
    // Testing / introspection
    // -----------------------------------------------------------------------

    /// List all table names (for testing schema).
    pub fn list_tables(&self) -> Result<Vec<String>> {
        let conn = self.conn()?;
        queries::list_tables(&conn)
    }

    /// Count rows in a table (for testing).
    pub fn count_rows(&self, table: &str) -> Result<i64> {
        let conn = self.conn()?;
        queries::count_rows(&conn, table)
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
        let store = EvolutionStore::new_in_memory().unwrap();
        let store = std::sync::Arc::new(store);

        let mut handles = vec![];
        for i in 0..4 {
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
        assert_eq!(active.len(), 4);
    }

    // --- New facade tests for missing methods ---

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
        let store = EvolutionStore::new_in_memory().unwrap();

        let gp = make_facade_skill("anc-gp", "anc-gp");
        store.insert_skill(&gp).unwrap();

        let mut p = make_facade_skill("anc-p", "anc-p");
        p.lineage_generation = 1;
        store.insert_skill(&p).unwrap();
        // Need to insert lineage parent manually via internal queries
        {
            let conn = store.conn().unwrap();
            conn.execute(
                "INSERT INTO skill_lineage_parents (skill_id, parent_id) VALUES ('anc-p', 'anc-gp')",
                [],
            ).unwrap();
        }

        let mut c = make_facade_skill("anc-c", "anc-c");
        c.lineage_generation = 2;
        store.insert_skill(&c).unwrap();
        {
            let conn = store.conn().unwrap();
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
        let store = EvolutionStore::new_in_memory().unwrap();

        let parent = make_facade_skill("ch-parent", "ch-parent");
        store.insert_skill(&parent).unwrap();

        let child = make_facade_skill("ch-child", "ch-child");
        store.insert_skill(&child).unwrap();
        {
            let conn = store.conn().unwrap();
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
}
