//! TaskGraph: SQLite-backed DAG of tasks with topological sort and cycle detection.
//!
//! Python source: `src/agent_nexus/platform/orchestration/task_graph.py` (~600 lines)

use rusqlite::{params, Connection};
use std::collections::{HashMap, HashSet, VecDeque};
use std::path::Path;

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
    #[error("Cycle detected in task dependencies")]
    CycleDetected,
}

// ---------------------------------------------------------------------------
// TaskGraph
// ---------------------------------------------------------------------------

pub struct TaskGraph {
    conn: Connection,
}

impl TaskGraph {
    /// Create an in-memory TaskGraph (for testing).
    pub fn new_in_memory() -> Result<Self, TaskGraphError> {
        let conn = Connection::open_in_memory()?;
        let tg = Self { conn };
        tg.init_schema()?;
        Ok(tg)
    }

    /// Create a file-backed TaskGraph with WAL mode.
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

    /// Insert or replace a task into the graph.
    pub fn add_task(&self, task: &TaskItem) -> Result<(), TaskGraphError> {
        let blocked_json = serde_json::to_string(&task.blocked_by)
            .map_err(|e| TaskGraphError::Serialization(e.to_string()))?;
        self.conn.execute(
            "INSERT OR REPLACE INTO tasks
             (task_id, agent_name, description, state, blocked_by, vars, result, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                task.id,
                task.agent,
                task.description,
                Self::state_to_str(task.state),
                blocked_json,
                task.vars.to_string(),
                task.result.as_ref().map(|v| v.to_string()),
                task.created_at.to_rfc3339(),
                task.updated_at.to_rfc3339(),
            ],
        )?;
        Ok(())
    }

    /// Retrieve a task by ID.
    pub fn get_task(&self, task_id: &str) -> Result<Option<TaskItem>, TaskGraphError> {
        let mut stmt = self.conn.prepare(
            "SELECT task_id, agent_name, description, state, blocked_by, vars, result, created_at, updated_at
             FROM tasks WHERE task_id = ?1",
        )?;

        let result = stmt.query_row(params![task_id], |row| {
            let id: String = row.get(0)?;
            let agent: String = row.get(1)?;
            let description: String = row.get(2)?;
            let state_str: String = row.get(3)?;
            let blocked_json: String = row.get(4)?;
            let vars_str: String = row.get::<_, String>(5)?;
            let result_str: Option<String> = row.get(6)?;
            let created_str: String = row.get(7)?;
            let updated_str: String = row.get(8)?;

            let state = Self::str_to_state(&state_str);
            let blocked_by: Vec<String> = serde_json::from_str(&blocked_json).unwrap_or_default();
            let vars: serde_json::Value = serde_json::from_str(&vars_str).unwrap_or(serde_json::Value::Null);
            let result: Option<serde_json::Value> = result_str
                .as_deref()
                .and_then(|s| serde_json::from_str(s).ok());
            let created_at = chrono::DateTime::parse_from_rfc3339(&created_str)
                .map(|dt| dt.to_utc())
                .unwrap_or_else(|_| utc_now());
            let updated_at = chrono::DateTime::parse_from_rfc3339(&updated_str)
                .map(|dt| dt.to_utc())
                .unwrap_or_else(|_| utc_now());

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
        });

        match result {
            Ok(task) => Ok(Some(task)),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(e) => Err(TaskGraphError::Sqlite(e)),
        }
    }

    /// Update a task's state.
    pub fn set_state(&self, task_id: &str, state: TaskState) -> Result<(), TaskGraphError> {
        let rows = self.conn.execute(
            "UPDATE tasks SET state = ?1, updated_at = ?2 WHERE task_id = ?3",
            params![Self::state_to_str(state), chrono::Utc::now().to_rfc3339(), task_id],
        )?;
        if rows == 0 {
            return Err(TaskGraphError::NotFound(task_id.to_string()));
        }
        Ok(())
    }

    /// Detect cycles via DFS with three-color marking.
    pub fn detect_cycle(&self) -> bool {
        // Load all tasks and build adjacency list
        let tasks = match self.load_all_tasks() {
            Ok(t) => t,
            Err(_) => return false,
        };

        let mut name_index: HashMap<String, usize> = HashMap::new();
        for (i, t) in tasks.iter().enumerate() {
            name_index.insert(t.id.clone(), i);
        }

        let mut white: HashSet<String> = tasks.iter().map(|t| t.id.clone()).collect();
        let mut gray: HashSet<String> = HashSet::new();
        let mut black: HashSet<String> = HashSet::new();

        fn dfs(
            name: &str,
            tasks: &[TaskItem],
            name_index: &HashMap<String, usize>,
            white: &mut HashSet<String>,
            gray: &mut HashSet<String>,
            black: &mut HashSet<String>,
        ) -> bool {
            white.remove(name);
            gray.insert(name.to_string());

            let idx = match name_index.get(name) {
                Some(&i) => i,
                None => return false,
            };

            for dep in &tasks[idx].blocked_by {
                if gray.contains(dep) {
                    return true; // cycle found
                }
                if !black.contains(dep)
                    && dfs(dep, tasks, name_index, white, gray, black)
                {
                    return true;
                }
            }

            gray.remove(name);
            black.insert(name.to_string());
            false
        }

        let names: Vec<String> = tasks.iter().map(|t| t.id.clone()).collect();
        for name in names {
            if !black.contains(&name)
                && dfs(&name, &tasks, &name_index, &mut white, &mut gray, &mut black)
            {
                return true;
            }
        }
        false
    }

    /// Return tasks in topological execution order (Kahn's algorithm).
    pub fn topological_sort(&self) -> Result<Vec<String>, TaskGraphError> {
        if self.detect_cycle() {
            return Err(TaskGraphError::CycleDetected);
        }

        let tasks = self.load_all_tasks()?;
        let mut in_degree: HashMap<String, usize> = HashMap::new();
        let mut adjacency: HashMap<String, Vec<String>> = HashMap::new();

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
                    let degree = in_degree.get_mut(dep_name).unwrap();
                    *degree -= 1;
                    if *degree == 0 {
                        queue.push_back(dep_name.clone());
                    }
                }
            }
        }

        Ok(result)
    }

    /// Get tasks with state=Pending and all dependencies completed.
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
                // All dependencies must be completed
                t.blocked_by.iter().all(|dep| {
                    state_map.get(dep).is_some_and(|&s| s == TaskState::Completed)
                })
            })
            .collect();

        Ok(ready)
    }

    /// Check if the graph is empty.
    pub fn is_empty(&self) -> bool {
        self.conn
            .query_row("SELECT COUNT(*) FROM tasks", [], |row| row.get::<_, i64>(0))
            .unwrap_or(1)
            == 0
    }

    // ── Helpers ──────────────────────────────────────────────────────

    fn load_all_tasks(&self) -> Result<Vec<TaskItem>, TaskGraphError> {
        let mut stmt = self.conn.prepare(
            "SELECT task_id, agent_name, description, state, blocked_by, vars, result, created_at, updated_at
             FROM tasks",
        )?;

        let rows = stmt.query_map([], |row| {
            let id: String = row.get(0)?;
            let agent: String = row.get(1)?;
            let description: String = row.get(2)?;
            let state_str: String = row.get(3)?;
            let blocked_json: String = row.get(4)?;
            let vars_str: String = row.get::<_, String>(5)?;
            let result_str: Option<String> = row.get(6)?;
            let created_str: String = row.get(7)?;
            let updated_str: String = row.get(8)?;

            let state = Self::str_to_state(&state_str);
            let blocked_by: Vec<String> =
                serde_json::from_str(&blocked_json).unwrap_or_default();
            let vars: serde_json::Value =
                serde_json::from_str(&vars_str).unwrap_or(serde_json::Value::Null);
            let result: Option<serde_json::Value> =
                result_str.as_deref().and_then(|s| serde_json::from_str(s).ok());
            let created_at = chrono::DateTime::parse_from_rfc3339(&created_str)
                .map(|dt| dt.to_utc())
                .unwrap_or_else(|_| utc_now());
            let updated_at = chrono::DateTime::parse_from_rfc3339(&updated_str)
                .map(|dt| dt.to_utc())
                .unwrap_or_else(|_| utc_now());

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
        })?;

        let mut tasks = Vec::new();
        for task in rows {
            tasks.push(task?);
        }
        Ok(tasks)
    }

    fn state_to_str(state: TaskState) -> &'static str {
        match state {
            TaskState::Pending => "pending",
            TaskState::InProgress => "in_progress",
            TaskState::Completed => "completed",
            TaskState::Failed => "failed",
        }
    }

    fn str_to_state(s: &str) -> TaskState {
        match s {
            "pending" => TaskState::Pending,
            "in_progress" => TaskState::InProgress,
            "completed" => TaskState::Completed,
            "failed" => TaskState::Failed,
            _ => TaskState::Pending,
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
        assert!(tg.is_empty());
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
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &["t2"])).unwrap();
        tg.add_task(&simple_task("t2", "a", &["t3"])).unwrap();
        tg.add_task(&simple_task("t3", "a", &["t1"])).unwrap();
        assert!(tg.detect_cycle());
    }

    #[test]
    fn no_cycle_in_linear_chain() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        tg.add_task(&simple_task("t2", "a", &["t1"])).unwrap();
        tg.add_task(&simple_task("t3", "a", &["t2"])).unwrap();
        assert!(!tg.detect_cycle());
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

    #[test]
    fn get_ready_tasks() {
        let tg = TaskGraph::new_in_memory().unwrap();
        tg.add_task(&simple_task("t1", "a", &[])).unwrap();
        tg.add_task(&simple_task("t2", "b", &["t1"])).unwrap();
        tg.add_task(&simple_task("t3", "c", &[])).unwrap();
        let ready = tg.get_ready_tasks().unwrap();
        assert_eq!(ready.len(), 2); // t1 and t3 have no dependencies
    }
}
