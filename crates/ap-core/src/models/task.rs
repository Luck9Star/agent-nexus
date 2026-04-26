//! Task graph models: `TaskItem`, `TaskState`.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::models::common::utc_now;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum TaskState {
    #[default]
    Pending,
    InProgress,
    Completed,
    Failed,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TaskItem {
    pub id: String,
    pub description: String,
    pub agent: String,
    #[serde(default)]
    pub blocked_by: Vec<String>,
    #[serde(default)]
    pub vars: serde_json::Value,
    #[serde(default)]
    pub state: TaskState,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<serde_json::Value>,
    #[serde(default = "utc_now")]
    pub created_at: DateTime<Utc>,
    #[serde(default = "utc_now")]
    pub updated_at: DateTime<Utc>,
}

impl TaskItem {
    /// Validate that a task does not block itself.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn validate_no_self_reference(&self) -> Result<(), String> {
        if self.blocked_by.contains(&self.id) {
            return Err(format!("Task '{}' cannot block itself", self.id));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TaskGraphSnapshot {
    #[serde(default)]
    pub tasks: Vec<TaskItem>,
    #[serde(default)]
    pub parallel_groups: Vec<Vec<String>>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn task_default_state_is_pending() {
        let task = TaskItem {
            id: "t1".into(),
            description: "test".into(),
            agent: "agent1".into(),
            blocked_by: vec![],
            vars: serde_json::Value::Null,
            state: TaskState::default(),
            result: None,
            created_at: utc_now(),
            updated_at: utc_now(),
        };
        assert_eq!(task.state, TaskState::Pending);
    }

    #[test]
    fn task_self_reference_detected() {
        let task = TaskItem {
            id: "t1".into(),
            description: "test".into(),
            agent: "agent1".into(),
            blocked_by: vec!["t1".into()],
            vars: serde_json::Value::Null,
            state: TaskState::default(),
            result: None,
            created_at: utc_now(),
            updated_at: utc_now(),
        };
        assert!(task.validate_no_self_reference().is_err());
    }

    #[test]
    fn task_valid_no_self_block() {
        let task = TaskItem {
            id: "t1".into(),
            description: "test".into(),
            agent: "agent1".into(),
            blocked_by: vec!["t0".into()],
            vars: serde_json::Value::Null,
            state: TaskState::default(),
            result: None,
            created_at: utc_now(),
            updated_at: utc_now(),
        };
        assert!(task.validate_no_self_reference().is_ok());
    }

    #[test]
    fn roundtrip_task_state_json() {
        let states = vec![TaskState::Pending, TaskState::InProgress, TaskState::Completed, TaskState::Failed];
        for state in states {
            let json = serde_json::to_string(&state).unwrap();
            let de: TaskState = serde_json::from_str(&json).unwrap();
            assert_eq!(state, de);
        }
    }
}
