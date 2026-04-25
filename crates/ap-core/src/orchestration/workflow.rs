//! Workflow phase types for the PlatformRouter's 4-phase composite agent orchestration.
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
    pub fn ordered() -> [WorkflowPhase; 4] {
        [
            WorkflowPhase::Research,
            WorkflowPhase::Synthesis,
            WorkflowPhase::Implementation,
            WorkflowPhase::Verification,
        ]
    }

    /// Whether this phase supports parallel agent execution.
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
    pub fn close(self) -> std::time::Duration {
        self.started_at.elapsed()
    }
}
