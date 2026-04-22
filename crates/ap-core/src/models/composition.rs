//! Composition models: CompositionTask, Composition.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CompositionTask {
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub agent: String,
    #[serde(default)]
    pub blocked_by: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Composition {
    pub name: String,
    #[serde(default)]
    pub description: String,
    pub tasks: std::collections::HashMap<String, CompositionTask>,
}

/// F-09 fix: Renamed from `WorkflowPhase` to `WorkflowPhaseEntry` to avoid
/// collision with the router's `WorkflowPhase` enum in Phase 04.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct WorkflowPhaseEntry {
    pub phase: String,
    #[serde(default)]
    pub tasks: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct WorkflowResult {
    pub success: bool,
    pub final_output: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_type: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct WorkflowContext {
    pub conversation_id: String,
    pub phases_completed: Vec<String>,
    pub phase_results: std::collections::HashMap<String, serde_json::Value>,
}
