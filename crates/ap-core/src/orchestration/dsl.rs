//! `OrchestrationDSL`: TOML DAG parser with cycle detection and topological execution order.
//!
//! Python source: `src/agent_nexus/platform/orchestration/dsl.py` (~350 lines)

use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet, VecDeque};

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

#[derive(Debug, thiserror::Error)]
pub enum DslError {
    #[error("TOML parse error: {0}")]
    Toml(#[from] toml::de::Error),
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    #[error("DAG has no tasks")]
    EmptyDag,
    #[error("Duplicate task name: {0}")]
    DuplicateTask(String),
    #[error("Missing dependency: task '{task}' depends on '{dep}' which does not exist")]
    MissingDependency { task: String, dep: String },
    #[error("Cycle detected: {0:?}")]
    CycleDetected(Vec<String>),
}

// ---------------------------------------------------------------------------
// DslTask
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DslTask {
    pub name: String,
    pub agent: String,
    #[serde(default)]
    pub phase: u32,
    #[serde(default)]
    pub depends_on: Vec<String>,
    #[serde(default)]
    pub variables: HashMap<String, serde_json::Value>,
}

// ---------------------------------------------------------------------------
// OrchestrationDsl
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub struct OrchestrationDsl {
    pub tasks: Vec<DslTask>,
    /// Index: task name -> position in tasks vec
    name_index: HashMap<String, usize>,
}

/// TOML wire format -- `[[tasks]]` array.
#[derive(Debug, Deserialize)]
struct DslToml {
    #[serde(default)]
    tasks: Vec<DslTask>,
}

impl OrchestrationDsl {
    /// Parse a TOML string into a validated DAG.
    /// Rejects cycles, missing dependencies, and duplicate task names.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn parse(toml: &str) -> Result<Self, DslError> {
        let wrapper: DslToml = toml::from_str(toml)?;
        Self::from_tasks(wrapper.tasks)
    }

    /// Build from a task list (shared logic for parse and `from_toml`).
    fn from_tasks(tasks: Vec<DslTask>) -> Result<Self, DslError> {
        if tasks.is_empty() {
            return Err(DslError::EmptyDag);
        }

        // Build name index, check for duplicates
        let mut name_index = HashMap::with_capacity(tasks.len());
        for (i, task) in tasks.iter().enumerate() {
            if name_index.contains_key(&task.name) {
                return Err(DslError::DuplicateTask(task.name.clone()));
            }
            name_index.insert(task.name.clone(), i);
        }

        // Validate dependencies exist
        let all_names: HashSet<&str> = tasks.iter().map(|t| t.name.as_str()).collect();
        for task in &tasks {
            for dep in &task.depends_on {
                if !all_names.contains(dep.as_str()) {
                    return Err(DslError::MissingDependency {
                        task: task.name.clone(),
                        dep: dep.clone(),
                    });
                }
            }
        }

        // Cycle detection
        if let Some(cycle) = Self::detect_cycle(&tasks, &name_index) {
            return Err(DslError::CycleDetected(cycle));
        }

        Ok(Self { tasks, name_index })
    }

    /// Load from a TOML file on disk.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn from_toml(path: &std::path::Path) -> Result<Self, DslError> {
        let content = std::fs::read_to_string(path)?;
        Self::parse(&content)
    }

    /// Return tasks with no dependencies (entry points).
    #[must_use] 
    pub fn get_root_tasks(&self) -> Vec<&DslTask> {
        self.tasks
            .iter()
            .filter(|t| t.depends_on.is_empty())
            .collect()
    }

    /// Return tasks that depend on the given task.
    #[must_use] 
    pub fn get_dependents(&self, task_name: &str) -> Vec<&DslTask> {
        self.tasks
            .iter()
            .filter(|t| t.depends_on.iter().any(|d| d == task_name))
            .collect()
    }

    /// Topological execution order (BFS/Kahn's algorithm).
    /// Respects phase ordering for ties.
    ///
    /// # Panics
    /// May panic if internal invariants are violated.
    #[must_use]
    pub fn get_execution_order(&self) -> Vec<&DslTask> {
        let task_count = self.tasks.len();
        let mut in_degree: HashMap<&str, usize> = HashMap::with_capacity(task_count);
        let mut adjacency: HashMap<&str, Vec<&str>> = HashMap::with_capacity(task_count);

        for task in &self.tasks {
            in_degree.entry(&task.name).or_insert(0);
            for dep in &task.depends_on {
                adjacency.entry(dep.as_str()).or_default().push(&task.name);
                *in_degree.entry(&task.name).or_insert(0) += 1;
            }
        }

        // Start with root tasks, sorted by phase
        let mut queue: VecDeque<&DslTask> = self
            .get_root_tasks()
            .into_iter()
            .collect();
        // Sort by phase for deterministic order
        queue.make_contiguous().sort_by_key(|t| t.phase);

        let mut result = Vec::with_capacity(self.tasks.len());
        while let Some(task) = queue.pop_front() {
            result.push(task);
            if let Some(deps) = adjacency.get(task.name.as_str()) {
                for &dep_name in deps {
                    let degree = in_degree.get_mut(dep_name)
                        .expect("invariant violation: node not found in in_degree map during topo sort");
                    *degree -= 1;
                    if *degree == 0 {
                        if let Some(t) = self.get_task(dep_name) {
                            queue.push_back(t);
                        }
                    }
                }
            }
        }

        result
    }

    fn get_task(&self, name: &str) -> Option<&DslTask> {
        self.name_index.get(name).map(|&i| &self.tasks[i])
    }

    /// Detect cycles using DFS. Returns a cycle path if found.
    fn detect_cycle(
        tasks: &[DslTask],
        name_index: &HashMap<String, usize>,
    ) -> Option<Vec<String>> {
        // Inner DFS function must be defined before any let-statements.
        fn dfs<'a>(
            name: &'a str,
            tasks: &'a [DslTask],
            name_index: &HashMap<String, usize>,
            white: &mut HashSet<&'a str>,
            gray: &mut HashSet<&'a str>,
            black: &mut HashSet<&'a str>,
            path: &mut Vec<String>,
        ) -> Option<Vec<String>> {
            white.remove(name);
            gray.insert(name);
            path.push(name.to_string());

            let idx = *name_index.get(name)?;
            for dep in &tasks[idx].depends_on {
                if gray.contains(dep.as_str()) {
                    // Found cycle — extract the cycle segment from the current DFS path
                    let cycle_start = path.iter().position(|p| p == dep).unwrap_or(0);
                    let mut cycle: Vec<String> = path[cycle_start..].to_vec();
                    cycle.push(dep.clone());
                    return Some(cycle);
                }
                if !black.contains(dep.as_str()) {
                    if let Some(cycle) =
                        dfs(dep.as_str(), tasks, name_index, white, gray, black, path)
                    {
                        return Some(cycle);
                    }
                }
            }

            gray.remove(name);
            black.insert(name);
            path.pop();
            None
        }

        let mut white: HashSet<&str> = tasks.iter().map(|t| t.name.as_str()).collect();
        let mut gray: HashSet<&str> = HashSet::new();
        let mut black: HashSet<&str> = HashSet::new();
        let mut path: Vec<String> = Vec::new();

        let names: Vec<&str> = tasks.iter().map(|t| t.name.as_str()).collect();
        for name in names {
            if !black.contains(name) {
                if let Some(cycle) =
                    dfs(name, tasks, name_index, &mut white, &mut gray, &mut black, &mut path)
                {
                    return Some(cycle);
                }
            }
        }
        None
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_simple_dag() {
        let toml = r#"
[[tasks]]
name = "research"
agent = "code-reviewer"
phase = 1

[[tasks]]
name = "implement"
agent = "code-writer"
phase = 2
depends_on = ["research"]
"#;
        let dag = OrchestrationDsl::parse(toml).unwrap();
        assert_eq!(dag.tasks.len(), 2);
        assert_eq!(dag.tasks[1].depends_on, vec!["research"]);
    }

    #[test]
    fn reject_cycle_in_dag() {
        let toml = r#"
[[tasks]]
name = "a"
agent = "x"
depends_on = ["b"]

[[tasks]]
name = "b"
agent = "y"
depends_on = ["a"]
"#;
        let result = OrchestrationDsl::parse(toml);
        assert!(result.is_err());
    }

    #[test]
    fn reject_missing_dependency() {
        let toml = r#"
[[tasks]]
name = "a"
agent = "x"
depends_on = ["nonexistent"]
"#;
        let result = OrchestrationDsl::parse(toml);
        assert!(result.is_err());
    }

    #[test]
    fn get_root_tasks_returns_entry_points() {
        let toml = r#"
[[tasks]]
name = "a"
agent = "x"
phase = 1

[[tasks]]
name = "b"
agent = "y"
phase = 2
depends_on = ["a"]
"#;
        let dag = OrchestrationDsl::parse(toml).unwrap();
        let roots = dag.get_root_tasks();
        assert_eq!(roots.len(), 1);
        assert_eq!(roots[0].name, "a");
    }

    #[test]
    fn get_dependents_finds_downstream() {
        let toml = r#"
[[tasks]]
name = "a"
agent = "x"

[[tasks]]
name = "b"
agent = "y"
depends_on = ["a"]

[[tasks]]
name = "c"
agent = "z"
depends_on = ["a"]
"#;
        let dag = OrchestrationDsl::parse(toml).unwrap();
        let deps = dag.get_dependents("a");
        assert_eq!(deps.len(), 2);
    }

    #[test]
    fn execution_order_is_topological() {
        let toml = r#"
[[tasks]]
name = "a"
agent = "x"
phase = 1

[[tasks]]
name = "b"
agent = "y"
phase = 1

[[tasks]]
name = "c"
agent = "z"
phase = 2
depends_on = ["a", "b"]
"#;
        let dag = OrchestrationDsl::parse(toml).unwrap();
        let order: Vec<&str> = dag
            .get_execution_order()
            .iter()
            .map(|t| t.name.as_str())
            .collect();
        assert!(
            order.iter().position(|&n| n == "a").unwrap()
                < order.iter().position(|&n| n == "c").unwrap()
        );
        assert!(
            order.iter().position(|&n| n == "b").unwrap()
                < order.iter().position(|&n| n == "c").unwrap()
        );
    }

    #[test]
    fn from_toml_reads_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("pipeline.toml");
        std::fs::write(&path, "[[tasks]]\nname = \"a\"\nagent = \"x\"\n").unwrap();
        let dag = OrchestrationDsl::from_toml(&path).unwrap();
        assert_eq!(dag.tasks.len(), 1);
    }

    #[test]
    fn reject_empty_dag() {
        let toml = "";
        let result = OrchestrationDsl::parse(toml);
        assert!(matches!(result, Err(DslError::EmptyDag)));
    }

    #[test]
    fn reject_duplicate_task_name() {
        let toml = r#"
[[tasks]]
name = "a"
agent = "x"

[[tasks]]
name = "a"
agent = "y"
"#;
        let result = OrchestrationDsl::parse(toml);
        assert!(matches!(result, Err(DslError::DuplicateTask(_))));
    }
}
