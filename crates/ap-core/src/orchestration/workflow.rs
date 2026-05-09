//! Workflow phase types for the `PlatformRouter`'s 4-phase composite agent orchestration.
//!
//! The router executes composite agents through a fixed sequence:
//! **Research → Synthesis → Implementation → Verification**
//!
//! Each phase maps to an `AgentRole` that determines which agents participate.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

/// The four phases of a composite agent workflow.
///
/// Order: Research → Synthesis → Implementation → Verification.
/// Research and Implementation can run multiple agents in parallel;
/// Synthesis and Verification run a single agent.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkflowPhase {
    Research,
    Synthesis,
    Implementation,
    Verification,
}

impl WorkflowPhase {
    /// Canonical execution order.
    #[must_use] 
    pub fn ordered() -> [WorkflowPhase; 4] {
        [
            WorkflowPhase::Research,
            WorkflowPhase::Synthesis,
            WorkflowPhase::Implementation,
            WorkflowPhase::Verification,
        ]
    }

    /// Whether this phase supports parallel agent execution.
    #[must_use] 
    pub fn is_parallel(&self) -> bool {
        matches!(self, WorkflowPhase::Research | WorkflowPhase::Implementation)
    }
}

impl std::fmt::Display for WorkflowPhase {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            WorkflowPhase::Research => write!(f, "research"),
            WorkflowPhase::Synthesis => write!(f, "synthesis"),
            WorkflowPhase::Implementation => write!(f, "implementation"),
            WorkflowPhase::Verification => write!(f, "verification"),
        }
    }
}

/// Result of executing a single phase within a composite workflow.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PhaseResult {
    pub phase: WorkflowPhase,
    pub success: bool,
    pub output: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

/// Full result of a composite agent workflow execution.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CompositeWorkflowResult {
    pub success: bool,
    pub final_output: String,
    pub phase_results: HashMap<WorkflowPhase, PhaseResult>,
    pub total_phases: u32,
    pub completed_phases: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_type: Option<String>,
}

/// Context object tracking state across a composite agent workflow.
///
/// Python source: `src/agent_nexus/platform/router/workflow.py` — `WorkflowContext`
pub struct WorkflowContext {
    /// Unique identifier for the conversation.
    pub conversation_id: String,
    /// Original message that initiated the workflow.
    pub message: String,
    /// Name of the composite agent being executed.
    pub agent_name: String,
    /// Results accumulated from completed phases.
    pub phase_results: HashMap<WorkflowPhase, PhaseResult>,
    /// Current phase being executed.
    pub current_phase: Option<WorkflowPhase>,
    /// Optional task graph ID for topological ordering.
    pub task_graph_id: Option<String>,
    /// Accumulated context to pass to the next phase.
    pub phase_context: String,
    /// When the workflow started.
    pub started_at: std::time::Instant,
}

impl WorkflowContext {
    /// Create a new workflow context for a composite agent run.
    #[must_use] 
    pub fn new(conversation_id: String, message: String, agent_name: String) -> Self {
        let phase_context = message.clone();
        Self {
            conversation_id,
            message,
            agent_name,
            phase_results: HashMap::new(),
            current_phase: None,
            task_graph_id: None,
            phase_context,
            started_at: std::time::Instant::now(),
        }
    }

    /// Consume the context and return elapsed time since creation.
    #[must_use] 
    pub fn close(self) -> std::time::Duration {
        self.started_at.elapsed()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn workflow_phase_ordered_returns_correct_sequence() {
        let order = WorkflowPhase::ordered();
        assert_eq!(order[0], WorkflowPhase::Research);
        assert_eq!(order[1], WorkflowPhase::Synthesis);
        assert_eq!(order[2], WorkflowPhase::Implementation);
        assert_eq!(order[3], WorkflowPhase::Verification);
    }

    #[test]
    fn workflow_phase_is_parallel() {
        assert!(WorkflowPhase::Research.is_parallel());
        assert!(!WorkflowPhase::Synthesis.is_parallel());
        assert!(WorkflowPhase::Implementation.is_parallel());
        assert!(!WorkflowPhase::Verification.is_parallel());
    }

    #[test]
    fn workflow_phase_display() {
        assert_eq!(WorkflowPhase::Research.to_string(), "research");
        assert_eq!(WorkflowPhase::Synthesis.to_string(), "synthesis");
        assert_eq!(WorkflowPhase::Implementation.to_string(), "implementation");
        assert_eq!(WorkflowPhase::Verification.to_string(), "verification");
    }

    #[test]
    fn workflow_phase_serde_roundtrip() {
        for phase in WorkflowPhase::ordered() {
            let json = serde_json::to_string(&phase).unwrap();
            let de: WorkflowPhase = serde_json::from_str(&json).unwrap();
            assert_eq!(phase, de);
        }
    }

    #[test]
    fn workflow_phase_serde_renames_to_snake_case() {
        let json = serde_json::to_string(&WorkflowPhase::Research).unwrap();
        assert_eq!(json, "\"research\"");
    }

    #[test]
    fn phase_result_serialization() {
        let result = PhaseResult {
            phase: WorkflowPhase::Research,
            success: true,
            output: "found 3 references".to_string(),
            error: None,
        };
        let json = serde_json::to_string(&result).unwrap();
        assert!(json.contains("\"phase\":\"research\""));
        assert!(json.contains("\"success\":true"));
    }

    #[test]
    fn phase_result_with_error_serializes_error() {
        let result = PhaseResult {
            phase: WorkflowPhase::Synthesis,
            success: false,
            output: String::new(),
            error: Some("timeout exceeded".to_string()),
        };
        let json = serde_json::to_string(&result).unwrap();
        assert!(json.contains("\"error\":\"timeout exceeded\""));
    }

    #[test]
    fn composite_workflow_result_serialization() {
        let mut phase_results = HashMap::new();
        phase_results.insert(
            WorkflowPhase::Research,
            PhaseResult {
                phase: WorkflowPhase::Research,
                success: true,
                output: "ok".to_string(),
                error: None,
            },
        );

        let result = CompositeWorkflowResult {
            success: true,
            final_output: "done".to_string(),
            phase_results,
            total_phases: 4,
            completed_phases: 1,
            error: None,
            error_type: None,
        };

        let json = serde_json::to_string(&result).unwrap();
        let de: CompositeWorkflowResult = serde_json::from_str(&json).unwrap();
        assert!(de.success);
        assert_eq!(de.total_phases, 4);
    }

    #[test]
    fn workflow_context_new_initializes_correctly() {
        let ctx = WorkflowContext::new(
            "conv-1".to_string(),
            "build feature".to_string(),
            "composer".to_string(),
        );
        assert_eq!(ctx.conversation_id, "conv-1");
        assert_eq!(ctx.message, "build feature");
        assert_eq!(ctx.agent_name, "composer");
        assert_eq!(ctx.phase_context, "build feature");
        assert!(ctx.phase_results.is_empty());
        assert!(ctx.current_phase.is_none());
        assert!(ctx.task_graph_id.is_none());
    }

    #[test]
    fn workflow_context_close_returns_elapsed() {
        let ctx = WorkflowContext::new(
            "conv-1".to_string(),
            "msg".to_string(),
            "agent".to_string(),
        );
        let elapsed = ctx.close();
        // Elapsed should be very small since we just created it
        assert!(elapsed.as_millis() < 1000);
    }
}
