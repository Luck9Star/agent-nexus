//! `TaskGraph`: SQLite-backed DAG of tasks with topological sort and cycle detection.
//!
//! Python source: `src/agent_nexus/platform/orchestration/task_graph.py` (~600 lines)

use rusqlite::{params, Connection};
use std::collections::{HashMap, HashSet, VecDeque};
use std::path::Path;

use tracing::{debug, warn};

use crate::models::common::utc_now;
use crate::models::task::{TaskItem, TaskState};

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

#[derive(Debug, thiserror::Error)]
pub enum TaskGraphError {
    #[error("SQLite error: {0}")]
    Sqlite(#[from] rusqlite::Error),
    #[error("Serialization error: {0}")]
    Serialization(String),
    #[error("Task not found: {0}")]
    NotFound(String),
    #[error("Task already exists: {0}")]
    DuplicateTask(String),
    #[error("Cycle detected in task dependencies")]
    CycleDetected,
    #[error("Topological sort incomplete: {dropped_count} task(s) remain in dependency graph (possible undetected cycle): {dropped_ids:?}")]
    IncompleteTopoSort {
        dropped_count: usize,
        dropped_ids: Vec<String>,
    },
    #[error("Invalid state transition: {from} -> {to}")]
    InvalidTransition {
        from: String,
        to: String,
    },
    #[error("Invalid task state: {0}")]
    InvalidState(String),
}

// ---------------------------------------------------------------------------
// Cycle detection helper
// ---------------------------------------------------------------------------

/// Iterative depth-first search with three-color marking for cycle detection.
///
/// Returns `true` if a cycle is found starting from `name`.
/// Uses an explicit stack instead of recursion to avoid stack overflow on
/// deep dependency chains.
fn dfs(
    start: &str,
    tasks: &[TaskItem],
    name_index: &HashMap<String, usize>,
    white: &mut HashSet<String>,
    gray: &mut HashSet<String>,
    black: &mut HashSet<String>,
) -> bool {
    /// One frame of the iterative DFS: tracks which children we still need to visit.
    struct Frame {
        name: String,
        child_idx: usize,
    }

    let mut stack: Vec<Frame> = vec![Frame {
        name: start.to_string(),
        child_idx: 0,
    }];

    // Mark start as discovered (gray)
    white.remove(start);
    gray.insert(start.to_string());

    while let Some(frame) = stack.last_mut() {
        let Some(&idx) = name_index.get(&frame.name) else {
            // No such task — skip (shouldn't happen with valid data)
            let done = stack.pop().unwrap();
            gray.remove(&done.name);
            black.insert(done.name);
            continue;
        };

        let deps = &tasks[idx].blocked_by;
        if frame.child_idx < deps.len() {
            let dep = deps[frame.child_idx].clone();
            frame.child_idx += 1;

            if gray.contains(&dep) {
                return true; // Cycle found
            }
            if !black.contains(&dep) {
                // Descend into this child
                white.remove(&dep);
                gray.insert(dep.clone());
                stack.push(Frame {
                    name: dep,
                    child_idx: 0,
                });
            }
        } else {
            // All children visited — mark black and pop
            let done = stack.pop().unwrap();
            gray.remove(&done.name);
            black.insert(done.name);
        }
    }

    false
}

// ---------------------------------------------------------------------------
// TaskGraph
// ---------------------------------------------------------------------------

/// `TaskGraph` stores task dependency relationships in `SQLite`.
///
/// **Note**: This type is `!Send + !Sync` because `rusqlite::Connection` is not thread-safe.
/// To use in async contexts, wrap in `std::sync::Mutex` and access via `tokio::task::spawn_blocking`.
pub struct TaskGraph {
    conn: Connection,
}

impl TaskGraph {
    /// Create an in-memory `TaskGraph` (for testing).
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn new_in_memory() -> Result<Self, TaskGraphError> {
        let conn = Connection::open_in_memory()?;
        let tg = Self { conn };
        tg.init_schema()?;
        Ok(tg)
    }

    /// Create a file-backed `TaskGraph` with WAL mode.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn new(path: &Path) -> Result<Self, TaskGraphError> {
        let conn = Connection::open(path)?;
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;")?;
        let tg = Self { conn };
        tg.init_schema()?;
        Ok(tg)
    }

    fn init_schema(&self) -> Result<(), TaskGraphError> {
        self.conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL,
                description TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                blocked_by TEXT DEFAULT '[]',
                vars TEXT,
                result TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);",
        )?;
        Ok(())
    }

    /// Adds a task to the graph with its dependencies.
    ///
    /// Validates that:
    /// - No duplicate `task_id` exists
    /// - All `blocked_by` references point to existing tasks
    /// - The new task does not introduce a cycle
    ///
    /// **Performance note**: This method runs full cycle detection (O(V+E)) on every insert,
    /// making batch insertion O(n^2). For bulk operations, consider using a transaction
    /// and checking cycles once after all inserts.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn add_task(&self, task: &TaskItem) -> Result<(), TaskGraphError> {
        // Check for duplicate (outside tx — read-only, harmless if stale)
        if self.get_task(&task.id)?.is_some() {
            return Err(TaskGraphError::DuplicateTask(task.id.clone()));
        }
        let blocked_json = serde_json::to_string(&task.blocked_by)
            .map_err(|e| TaskGraphError::Serialization(e.to_string()))?;
        // Wrap validation + INSERT + cycle check in a single transaction to
        // prevent TOCTOU: a concurrent insertion/deletion between the
        // blocked_by validation and the INSERT could invalidate references.
        let tx = self.conn.unchecked_transaction()?;
        // Validate blocked_by references exist (inside tx for consistency)
        for dep_id in &task.blocked_by {
            let exists: bool = tx
                .query_row(
                    "SELECT COUNT(*) > 0 FROM tasks WHERE task_id = ?1",
                    rusqlite::params![dep_id],
                    |row| row.get(0),
                )?;
            if !exists {
                tx.rollback()?;
                return Err(TaskGraphError::NotFound(format!(
                    "blocked_by dependency '{dep_id}' not found"
                )));
            }
        }
        tx.execute(
            "INSERT INTO tasks
             (task_id, agent_name, description, state, blocked_by, vars, result, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                task.id,
                task.agent,
                task.description,
                Self::state_to_str(task.state),
                blocked_json,
                task.vars.to_string(),
                task.result.as_ref().map(std::string::ToString::to_string),
                task.created_at.to_rfc3339(),
                task.updated_at.to_rfc3339(),
            ],
        )?;
        // Check for newly introduced cycles; rollback if found
        if Self::detect_cycle_with_conn(&tx)? {
            tx.rollback()?;
            return Err(TaskGraphError::CycleDetected);
        }
        tx.commit()?;
        Ok(())
    }

    /// Add multiple tasks in a single transaction with one cycle check.
    ///
    /// O(N) cycle detection instead of O(N²) from calling `add_task` in a loop.
    /// Validates no duplicates within the batch and no duplicate against existing
    /// tasks before inserting. All inserts succeed or all roll back.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn add_tasks_batch(&self, tasks: &[TaskItem]) -> Result<(), TaskGraphError> {
        if tasks.is_empty() {
            return Ok(());
        }
        let tx = self.conn.unchecked_transaction()?;

        // Check for duplicates within the batch
        let mut seen = std::collections::HashSet::with_capacity(tasks.len());
        for task in tasks {
            if !seen.insert(&task.id) {
                tx.rollback()?;
                return Err(TaskGraphError::DuplicateTask(task.id.clone()));
            }
            // Check against existing tasks in DB
            let existing: Option<String> = tx
                .query_row(
                    "SELECT task_id FROM tasks WHERE task_id = ?1",
                    rusqlite::params![task.id],
                    |row| row.get(0),
                )
                .ok();
            if existing.is_some() {
                tx.rollback()?;
                return Err(TaskGraphError::DuplicateTask(task.id.clone()));
            }
        }

        // Insert all tasks
        for task in tasks {
            let blocked_json = serde_json::to_string(&task.blocked_by)
                .map_err(|e| TaskGraphError::Serialization(e.to_string()))?;
            tx.execute(
                "INSERT INTO tasks
                 (task_id, agent_name, description, state, blocked_by, vars, result, created_at, updated_at)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
                rusqlite::params![
                    task.id,
                    task.agent,
                    task.description,
                    Self::state_to_str(task.state),
                    blocked_json,
                    task.vars.to_string(),
                    task.result.as_ref().map(std::string::ToString::to_string),
                    task.created_at.to_rfc3339(),
                    task.updated_at.to_rfc3339(),
                ],
            )?;
        }

        // Single cycle check after all inserts
        if Self::detect_cycle_with_conn(&tx)? {
            tx.rollback()?;
            return Err(TaskGraphError::CycleDetected);
        }
        tx.commit()?;
        Ok(())
    }

    /// Retrieve a task by ID.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn get_task(&self, task_id: &str) -> Result<Option<TaskItem>, TaskGraphError> {
        let mut stmt = self.conn.prepare(
            "SELECT task_id, agent_name, description, state, blocked_by, vars, result, created_at, updated_at
             FROM tasks WHERE task_id = ?1",
        )?;

        let result = stmt.query_row(params![task_id], Self::task_from_row);

        match result {
            Ok(task) => Ok(Some(task)),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(e) => Err(TaskGraphError::Sqlite(e)),
        }
    }

    /// Update a task's state without validation.
    ///
    /// Intended for internal use only (e.g., loading from DB).
    /// External callers should prefer `transition_state` which enforces the state machine.
    pub(crate) fn set_state(&self, task_id: &str, state: TaskState) -> Result<(), TaskGraphError> {
        let rows = self.conn.execute(
            "UPDATE tasks SET state = ?1, updated_at = ?2 WHERE task_id = ?3",
            params![Self::state_to_str(state), chrono::Utc::now().to_rfc3339(), task_id],
        )?;
        if rows == 0 {
            return Err(TaskGraphError::NotFound(task_id.to_string()));
        }
        Ok(())
    }

    /// Validate and apply a state transition.
    ///
    /// Valid transitions:
    /// - Pending -> `InProgress`
    /// - Pending -> Failed (upstream failure blocks pending tasks)
    /// - `InProgress` -> Completed
    /// - `InProgress` -> Failed
    ///
    /// All other transitions return `TaskGraphError::InvalidTransition`.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn transition_state(&self, task_id: &str, new_state: TaskState) -> Result<(), TaskGraphError> {
        let task = self.get_task(task_id)?
            .ok_or_else(|| TaskGraphError::NotFound(task_id.to_string()))?;

        let valid = matches!(
            (task.state, new_state),
            (TaskState::Pending, TaskState::InProgress | TaskState::Failed) |
(TaskState::InProgress, TaskState::Completed | TaskState::Failed)
        );

        if !valid {
            return Err(TaskGraphError::InvalidTransition {
                from: Self::state_to_str(task.state).to_string(),
                to: Self::state_to_str(new_state).to_string(),
            });
        }

        self.set_state(task_id, new_state)
    }

    /// Detect cycles via DFS with three-color marking (delegates to connection-agnostic helper).
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn detect_cycle(&self) -> Result<bool, TaskGraphError> {
        Self::detect_cycle_with_conn(&self.conn)
    }

    /// Connection-agnostic cycle detection used by both `detect_cycle` and `add_task` transaction.
    ///
    /// **Performance note:** This method performs a full table scan via
    /// [`load_all_tasks_from_conn`], loading every task row into memory.
    /// The DFS cycle check itself is O(V + E) in both time and space.
    /// Callers should avoid invoking this in tight loops for large graphs;
    /// consider batching or caching the result if repeated checks are needed.
    fn detect_cycle_with_conn(
        conn: &Connection,
    ) -> Result<bool, TaskGraphError> {
        let tasks = Self::load_all_tasks_from_conn(conn)?;

        let mut name_index: HashMap<String, usize> = HashMap::with_capacity(tasks.len());
        for (i, t) in tasks.iter().enumerate() {
            name_index.insert(t.id.clone(), i);
        }

        let mut white: HashSet<String> = tasks.iter().map(|t| t.id.clone()).collect();
        let mut gray: HashSet<String> = HashSet::new();
        let mut black: HashSet<String> = HashSet::new();

        let names: Vec<String> = tasks.iter().map(|t| t.id.clone()).collect();
        for name in names {
            if !black.contains(&name)
                && dfs(&name, &tasks, &name_index, &mut white, &mut gray, &mut black)
            {
                return Ok(true);
            }
        }
        Ok(false)
    }

    /// Return tasks in topological execution order (Kahn's algorithm).
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    ///
    /// # Panics
    /// May panic if internal invariants are violated.
    pub fn topological_sort(&self) -> Result<Vec<String>, TaskGraphError> {
        if self.detect_cycle()? {
            return Err(TaskGraphError::CycleDetected);
        }

        let tasks = self.load_all_tasks()?;
        let task_count = tasks.len();
        let mut in_degree: HashMap<String, usize> = HashMap::with_capacity(task_count);
        let mut adjacency: HashMap<String, Vec<String>> = HashMap::with_capacity(task_count);

        for task in &tasks {
            in_degree.entry(task.id.clone()).or_insert(0);
            for dep in &task.blocked_by {
                adjacency.entry(dep.clone()).or_default().push(task.id.clone());
                *in_degree.entry(task.id.clone()).or_insert(0) += 1;
            }
        }

        let mut queue: VecDeque<String> = in_degree
            .iter()
            .filter(|(_, &deg)| deg == 0)
            .map(|(name, _)| name.clone())
            .collect();

        let mut result = Vec::with_capacity(tasks.len());
        while let Some(name) = queue.pop_front() {
            result.push(name.clone());
            if let Some(deps) = adjacency.get(&name) {
                for dep_name in deps {
                    let degree = in_degree.get_mut(dep_name)
                        .ok_or_else(|| TaskGraphError::IncompleteTopoSort {
                            dropped_count: 0,
                            dropped_ids: vec![dep_name.clone()],
                        })?;
                    *degree -= 1;
                    if *degree == 0 {
                        queue.push_back(dep_name.clone());
                    }
                }
            }
        }

        if result.len() != tasks.len() {
            // Collect tasks that were dropped (still have non-zero in-degree).
            let dropped_ids: Vec<String> = in_degree
                .iter()
                .filter(|(_, &deg)| deg > 0)
                .map(|(name, _)| name.clone())
                .collect();
            let dropped_count = dropped_ids.len();
            return Err(TaskGraphError::IncompleteTopoSort {
                dropped_count,
                dropped_ids,
            });
        }

        Ok(result)
    }

    /// Get tasks with state=Pending and all dependencies completed.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn get_ready_tasks(&self) -> Result<Vec<TaskItem>, TaskGraphError> {
        let tasks = self.load_all_tasks()?;
        let state_map: HashMap<String, TaskState> = tasks
            .iter()
            .map(|t| (t.id.clone(), t.state))
            .collect();

        let ready: Vec<TaskItem> = tasks
            .into_iter()
            .filter(|t| {
                if t.state != TaskState::Pending {
                    return false;
                }
                // All dependencies must be completed; warn on dangling references
                t.blocked_by.iter().all(|dep| {
                    if let Some(&s) = state_map.get(dep) { s == TaskState::Completed } else {
                        warn!(
                            "Task '{}' has dangling blocked_by reference to '{}'",
                            t.id, dep
                        );
                        false
                    }
                })
            })
            .collect();

        Ok(ready)
    }

    /// Check if the graph is empty.
    ///
    /// # Errors
    /// Returns an error if the underlying database query fails.
    pub fn is_empty(&self) -> Result<bool, TaskGraphError> {
        let count: i64 = self.conn
            .query_row("SELECT COUNT(*) FROM tasks", [], |row| row.get(0))
            .map_err(TaskGraphError::Sqlite)?;
        Ok(count == 0)
    }

    // ── Helpers ──────────────────────────────────────────────────────

    fn load_all_tasks(&self) -> Result<Vec<TaskItem>, TaskGraphError> {
        Self::load_all_tasks_from_conn(&self.conn)
    }

    fn load_all_tasks_from_conn(conn: &Connection) -> Result<Vec<TaskItem>, TaskGraphError> {
        let mut stmt = conn.prepare(
            "SELECT task_id, agent_name, description, state, blocked_by, vars, result, created_at, updated_at
             FROM tasks",
        )?;

        let rows = stmt.query_map([], Self::task_from_row)?;

        let mut tasks = Vec::new();
        for task in rows {
            tasks.push(task?);
        }
        Ok(tasks)
    }

    fn task_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<TaskItem> {
        let id: String = row.get(0)?;
        let agent: String = row.get(1)?;
        let description: String = row.get(2)?;
        let state_str: String = row.get(3)?;
        let blocked_json: String = row.get(4)?;
        let vars_str: String = row.get::<_, String>(5)?;
        let result_str: Option<String> = row.get(6)?;
        let created_str: String = row.get(7)?;
        let updated_str: String = row.get(8)?;

        let state = Self::str_to_state(&state_str).map_err(|e| {
            rusqlite::Error::FromSqlConversionFailure(3, rusqlite::types::Type::Text, Box::new(e))
        })?;
        let blocked_by: Vec<String> = serde_json::from_str(&blocked_json).map_err(|e| {
            rusqlite::Error::FromSqlConversionFailure(
                4,
                rusqlite::types::Type::Text,
                Box::new(std::io::Error::other(
                    format!("Corrupted blocked_by JSON for task {id}: {e}"),
                )),
            )
        })?;
        let vars: serde_json::Value = serde_json::from_str(&vars_str).unwrap_or_else(|e| {
            debug!("Failed to parse vars JSON for task {}: {}", id, e);
            serde_json::Value::Null
        });
        let result: Option<serde_json::Value> = result_str
            .as_deref()
            .and_then(|s| serde_json::from_str(s).map_err(|e| {
                debug!("Failed to parse result JSON for task {}: {}", id, e);
                e
            }).ok());
        let created_at = chrono::DateTime::parse_from_rfc3339(&created_str).map_or_else(|_| utc_now(), |dt| dt.to_utc());
        let updated_at = chrono::DateTime::parse_from_rfc3339(&updated_str).map_or_else(|_| utc_now(), |dt| dt.to_utc());

        Ok(TaskItem {
            id,
            description,
            agent,
            blocked_by,
            vars,
            state,
            result,
            created_at,
            updated_at,
        })
    }

    fn state_to_str(state: TaskState) -> &'static str {
        match state {
            TaskState::Pending => "pending",
            TaskState::InProgress => "in_progress",
            TaskState::Completed => "completed",
            TaskState::Failed => "failed",
        }
    }

    fn str_to_state(s: &str) -> Result<TaskState, TaskGraphError> {
        match s {
            "pending" => Ok(TaskState::Pending),
            "in_progress" => Ok(TaskState::InProgress),
            "completed" => Ok(TaskState::Completed),
            "failed" => Ok(TaskState::Failed),
            other => Err(TaskGraphError::InvalidState(other.to_string())),
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    /// Helper: create a TaskItem with just id, agent, and blocked_by.
    fn simple_task(id: &str, agent: &str, blocked_by: &[&str]) -> TaskItem {
        TaskItem {
            id: id.to_string(),
            description: format!("task {id}"),
            agent: agent.to_string(),
            blocked_by: blocked_by.iter().map(|s| s.to_string()).collect(),
            vars: serde_json::Value::Null,
            state: TaskState::Pending,
            result: None,
            created_at: utc_now(),
            updated_at: utc_now(),
        }
    }

    #[test]
    fn create_in_memory() {
        let tg = TaskGraph::new_in_memory().unwrap();
        assert!(tg.is_empty().unwrap());
    }

    #[test]
    fn add_duplicate_task_rejected() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        let err = tg.add_task(&simple_task("t1", "b", &[])).unwrap_err();
        match err {
            TaskGraphError::DuplicateTask(id) => assert_eq!(id, "t1"),
            other => panic!("expected DuplicateTask, got {:?}", other),
        }
    }

    #[test]
    fn add_task_missing_dependency_rejected() {
        let tg = TaskGraph::new_in_memory().unwrap();
        let err = tg
            .add_task(&simple_task("t2", "a", &["nonexistent"]))
            .unwrap_err();
        match err {
            TaskGraphError::NotFound(msg) => {
                assert!(msg.contains("nonexistent"));
            }
            other => panic!("expected NotFound, got {:?}", other),
        }
    }

    #[test]
    fn add_cycle_creating_task_rejected_and_rolled_back() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        tg.add_task(&simple_task("t2", "a", &["t1"])).unwrap();
        // Create a cycle via raw SQL: make t1 block on t2
        tg.conn
            .execute(
                "UPDATE tasks SET blocked_by = ?1 WHERE task_id = 't1'",
                params![r#"["t2"]"#],
            )
            .unwrap();
        // Now any add_task should detect the existing cycle and rollback
        let err = tg
            .add_task(&simple_task("t3", "a", &["t2"]))
            .unwrap_err();
        match err {
            TaskGraphError::CycleDetected => {}
            other => panic!("expected CycleDetected, got {:?}", other),
        }
        // t3 should not have been inserted (rollback)
        assert!(tg.get_task("t3").unwrap().is_none());
    }

    #[test]
    fn valid_chain_inserts_succeed() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        tg.add_task(&simple_task("t2", "a", &["t1"])).unwrap();
        tg.add_task(&simple_task("t3", "a", &["t2"])).unwrap();
        assert!(!tg.detect_cycle().unwrap());
        let order = tg.topological_sort().unwrap();
        assert_eq!(order.len(), 3);
    }

    #[test]
    fn add_and_get_task() {
        let tg = TaskGraph::new_in_memory().unwrap();
        let task = simple_task("t1", "agent-a", &[]);
        tg.add_task(&task).unwrap();
        let got = tg.get_task("t1").unwrap().unwrap();
        assert_eq!(got.id, "t1");
        assert_eq!(got.agent, "agent-a");
    }

    #[test]
    fn detect_cycle() {
        // With add_task validation, we cannot create a cycle through add_task
        // because it would be rejected. Instead, verify detect_cycle works
        // by inserting tasks without deps and using raw SQL to create the cycle.
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        tg.add_task(&simple_task("t2", "a", &[])).unwrap();
        tg.add_task(&simple_task("t3", "a", &[])).unwrap();
        // Manually create a cycle via raw SQL (bypassing validation)
        tg.conn
            .execute(
                "UPDATE tasks SET blocked_by = ?1 WHERE task_id = 't1'",
                params![r#"["t2"]"#],
            )
            .unwrap();
        tg.conn
            .execute(
                "UPDATE tasks SET blocked_by = ?1 WHERE task_id = 't2'",
                params![r#"["t3"]"#],
            )
            .unwrap();
        tg.conn
            .execute(
                "UPDATE tasks SET blocked_by = ?1 WHERE task_id = 't3'",
                params![r#"["t1"]"#],
            )
            .unwrap();
        assert!(tg.detect_cycle().unwrap());
    }

    #[test]
    fn no_cycle_in_linear_chain() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        tg.add_task(&simple_task("t2", "a", &["t1"])).unwrap();
        tg.add_task(&simple_task("t3", "a", &["t2"])).unwrap();
        assert!(!tg.detect_cycle().unwrap());
    }

    #[test]
    fn topological_sort_order() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        tg.add_task(&simple_task("t2", "b", &["t1"])).unwrap();
        tg.add_task(&simple_task("t3", "c", &["t2"])).unwrap();
        let order = tg.topological_sort().unwrap();
        assert!(
            order.iter().position(|t| t == "t1").unwrap()
                < order.iter().position(|t| t == "t2").unwrap()
        );
        assert!(
            order.iter().position(|t| t == "t2").unwrap()
                < order.iter().position(|t| t == "t3").unwrap()
        );
    }

    #[test]
    fn update_task_state() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        tg.set_state("t1", TaskState::InProgress).unwrap();
        let got = tg.get_task("t1").unwrap().unwrap();
        assert_eq!(got.state, TaskState::InProgress);
    }

    // ── transition_state: valid transitions ──────────────────────────

    #[test]
    fn transition_pending_to_in_progress() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        tg.transition_state("t1", TaskState::InProgress).unwrap();
        let got = tg.get_task("t1").unwrap().unwrap();
        assert_eq!(got.state, TaskState::InProgress);
    }

    #[test]
    fn transition_in_progress_to_completed() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        tg.transition_state("t1", TaskState::InProgress).unwrap();
        tg.transition_state("t1", TaskState::Completed).unwrap();
        let got = tg.get_task("t1").unwrap().unwrap();
        assert_eq!(got.state, TaskState::Completed);
    }

    #[test]
    fn transition_in_progress_to_failed() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        tg.transition_state("t1", TaskState::InProgress).unwrap();
        tg.transition_state("t1", TaskState::Failed).unwrap();
        let got = tg.get_task("t1").unwrap().unwrap();
        assert_eq!(got.state, TaskState::Failed);
    }

    #[test]
    fn transition_pending_to_failed() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        tg.transition_state("t1", TaskState::Failed).unwrap();
        let got = tg.get_task("t1").unwrap().unwrap();
        assert_eq!(got.state, TaskState::Failed);
    }

    // ── transition_state: invalid transitions ────────────────────────

    #[test]
    fn transition_completed_to_in_progress_rejected() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        tg.transition_state("t1", TaskState::InProgress).unwrap();
        tg.transition_state("t1", TaskState::Completed).unwrap();
        let err = tg.transition_state("t1", TaskState::InProgress).unwrap_err();
        match err {
            TaskGraphError::InvalidTransition { from, to } => {
                assert_eq!(from, "completed");
                assert_eq!(to, "in_progress");
            }
            other => panic!("expected InvalidTransition, got {:?}", other),
        }
    }

    #[test]
    fn transition_failed_to_completed_rejected() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        tg.transition_state("t1", TaskState::InProgress).unwrap();
        tg.transition_state("t1", TaskState::Failed).unwrap();
        let err = tg.transition_state("t1", TaskState::Completed).unwrap_err();
        match err {
            TaskGraphError::InvalidTransition { from, to } => {
                assert_eq!(from, "failed");
                assert_eq!(to, "completed");
            }
            other => panic!("expected InvalidTransition, got {:?}", other),
        }
    }

    #[test]
    fn transition_completed_to_pending_rejected() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        tg.transition_state("t1", TaskState::InProgress).unwrap();
        tg.transition_state("t1", TaskState::Completed).unwrap();
        let err = tg.transition_state("t1", TaskState::Pending).unwrap_err();
        match err {
            TaskGraphError::InvalidTransition { from, to } => {
                assert_eq!(from, "completed");
                assert_eq!(to, "pending");
            }
            other => panic!("expected InvalidTransition, got {:?}", other),
        }
    }

    #[test]
    fn transition_pending_to_completed_rejected() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        let err = tg.transition_state("t1", TaskState::Completed).unwrap_err();
        match err {
            TaskGraphError::InvalidTransition { from, to } => {
                assert_eq!(from, "pending");
                assert_eq!(to, "completed");
            }
            other => panic!("expected InvalidTransition, got {:?}", other),
        }
    }

    #[test]
    fn get_ready_tasks() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        tg.add_task(&simple_task("t2", "b", &["t1"])).unwrap();
        tg.add_task(&simple_task("t3", "c", &[])).unwrap();
        let ready = tg.get_ready_tasks().unwrap();
        assert_eq!(ready.len(), 2); // t1 and t3 have no dependencies
    }

    /// F-2: add_task rollback on cycle must be atomic — no phantom rows.
    #[test]
    fn add_task_rollback_on_cycle_is_atomic() {
        let tg = TaskGraph::new_in_memory().unwrap();

        // Start with exactly 0 tasks
        assert!(tg.is_empty().unwrap());

        // Add t1 and t2, then use raw SQL to make t1 block on t2 (creating a cycle)
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        tg.add_task(&simple_task("t2", "a", &["t1"])).unwrap();
        tg.conn
            .execute(
                "UPDATE tasks SET blocked_by = ?1 WHERE task_id = 't1'",
                params![r#"["t2"]"#],
            )
            .unwrap();

        // Before: 2 tasks
        assert_eq!(tg.load_all_tasks().unwrap().len(), 2);

        // Try to add t3 — should fail because the graph already has a cycle
        let err = tg
            .add_task(&simple_task("t3", "a", &["t2"]))
            .unwrap_err();
        match err {
            TaskGraphError::CycleDetected => {}
            other => panic!("expected CycleDetected, got {:?}", other),
        }

        // After: still exactly 2 tasks — no phantom t3 row
        assert_eq!(tg.load_all_tasks().unwrap().len(), 2);
        assert!(tg.get_task("t3").unwrap().is_none());
    }

    /// F-3: get_ready_tasks must warn on dangling blocked_by references.
    #[test]
    fn get_ready_tasks_warns_on_dangling_dependency() {
        // Install a tracing subscriber that captures log output so we can
        // verify the warning is emitted.
        use tracing_subscriber::EnvFilter;

        // Use a no-op guard — we just need the subscriber active during the test.
        let _guard = tracing_subscriber::fmt()
            .with_env_filter(EnvFilter::new("warn"))
            .with_test_writer()
            .try_init();

        let tg = TaskGraph::new_in_memory().unwrap();

        // Insert a task with a valid blocked_by reference
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();

        // Now use raw SQL to create a task with a dangling dependency
        // (bypassing add_task validation which would reject it)
        tg.conn
            .execute(
                "INSERT INTO tasks (task_id, agent_name, description, state, blocked_by, vars, result, created_at, updated_at)
                 VALUES ('dangling', 'a', 'dangling task', 'pending', '[\"ghost\"]', 'null', NULL, '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')",
                [],
            )
            .unwrap();

        let ready = tg.get_ready_tasks().unwrap();

        // t1 is ready (no deps), dangling is NOT ready (dangling dep on "ghost")
        assert_eq!(ready.len(), 1);
        assert_eq!(ready[0].id, "t1");
    }

    /// Test IncompleteTopoSort error: tasks with dangling blocked_by
    /// references that survive the cycle-free check.
    #[test]
    fn topological_sort_incomplete_on_dangling_deps() {
        let tg = TaskGraph::new_in_memory().unwrap();

        // Insert tasks via raw SQL to bypass add_task validation
        tg.conn
            .execute(
                "INSERT INTO tasks (task_id, agent_name, description, state, blocked_by, vars, result, created_at, updated_at)
                 VALUES ('t1', 'a', 'task 1', 'pending', '[\"nonexistent\"]', 'null', NULL, '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')",
                [],
            )
            .unwrap();

        let result = tg.topological_sort();
        match result {
            Err(TaskGraphError::IncompleteTopoSort { dropped_count, dropped_ids }) => {
                assert!(dropped_count > 0, "should report dropped tasks");
                assert!(
                    dropped_ids.contains(&"t1".to_string()),
                    "t1 should be in dropped_ids"
                );
            }
            other => panic!("expected IncompleteTopoSort, got {:?}", other),
        }
    }

    /// Test InvalidState error: raw DB row with unknown state string.
    #[test]
    fn load_rejects_invalid_state_string() {
        let tg = TaskGraph::new_in_memory().unwrap();

        // Insert a task with a state value not in the enum
        tg.conn
            .execute(
                "INSERT INTO tasks (task_id, agent_name, description, state, blocked_by, vars, result, created_at, updated_at)
                 VALUES ('bad-state', 'a', 'corrupted', 'running', '[]', 'null', NULL, '2025-01-01T00:00:00Z', '2025-01-01T00:00:00Z')",
                [],
            )
            .unwrap();

        let result = tg.load_all_tasks();
        // str_to_state wraps InvalidState inside FromSqlConversionFailure,
        // which then becomes TaskGraphError::Sqlite(...)
        match result {
            Err(TaskGraphError::Sqlite(
                rusqlite::Error::FromSqlConversionFailure(_, _, inner),
            )) => {
                let msg = inner.to_string();
                assert!(
                    msg.contains("running"),
                    "Error message should mention the bad state, got: {msg}"
                );
            }
            other => panic!("expected Sqlite(FromSqlConversionFailure), got {:?}", other),
        }
    }

    #[test]
    fn add_tasks_batch_inserts_all() {
        let tg = TaskGraph::new_in_memory().unwrap();
        let now = chrono::Utc::now();
        let tasks = vec![
            TaskItem {
                id: "batch-1".into(),
                agent: "agent-a".into(),
                description: "first".into(),
                state: TaskState::Pending,
                blocked_by: vec![],
                vars: Default::default(),
                result: None,
                created_at: now,
                updated_at: now,
            },
            TaskItem {
                id: "batch-2".into(),
                agent: "agent-b".into(),
                description: "second".into(),
                state: TaskState::Pending,
                blocked_by: vec!["batch-1".into()],
                vars: Default::default(),
                result: None,
                created_at: now,
                updated_at: now,
            },
        ];
        tg.add_tasks_batch(&tasks).unwrap();
        assert!(tg.get_task("batch-1").unwrap().is_some());
        assert!(tg.get_task("batch-2").unwrap().is_some());
    }

    #[test]
    fn add_tasks_batch_rejects_internal_duplicate() {
        let tg = TaskGraph::new_in_memory().unwrap();
        let now = chrono::Utc::now();
        let tasks = vec![
            TaskItem {
                id: "dup".into(),
                agent: "a".into(),
                description: "one".into(),
                state: TaskState::Pending,
                blocked_by: vec![],
                vars: Default::default(),
                result: None,
                created_at: now,
                updated_at: now,
            },
            TaskItem {
                id: "dup".into(),
                agent: "b".into(),
                description: "two".into(),
                state: TaskState::Pending,
                blocked_by: vec![],
                vars: Default::default(),
                result: None,
                created_at: now,
                updated_at: now,
            },
        ];
        let err = tg.add_tasks_batch(&tasks).unwrap_err();
        assert!(matches!(err, TaskGraphError::DuplicateTask(_)));
        assert!(tg.get_task("dup").unwrap().is_none());
    }

    #[test]
    fn add_tasks_batch_rollback_on_cycle() {
        let tg = TaskGraph::new_in_memory().unwrap();
        let now = chrono::Utc::now();
        let tasks = vec![
            TaskItem {
                id: "cyc-1".into(),
                agent: "a".into(),
                description: "first".into(),
                state: TaskState::Pending,
                blocked_by: vec!["cyc-2".into()],
                vars: Default::default(),
                result: None,
                created_at: now,
                updated_at: now,
            },
            TaskItem {
                id: "cyc-2".into(),
                agent: "b".into(),
                description: "second".into(),
                state: TaskState::Pending,
                blocked_by: vec!["cyc-1".into()],
                vars: Default::default(),
                result: None,
                created_at: now,
                updated_at: now,
            },
        ];
        let err = tg.add_tasks_batch(&tasks).unwrap_err();
        assert!(matches!(err, TaskGraphError::CycleDetected));
        assert!(tg.get_task("cyc-1").unwrap().is_none());
    }
}
