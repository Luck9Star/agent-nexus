//! IPC message models: Platform <-> Agent communication via stdin/stdout JSON-lines.
//!
//! Wire format: FLAT STRUCTS with `type` discriminator.
//! Python uses single classes with all optional fields, not tagged unions.
//! See models/ipc.py for the source of truth.

use serde::{Deserialize, Serialize};

// ── Direction ──────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MessageDirection {
    PlatformToAgent,
    AgentToPlatform,
}

// ── Type discriminators ────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PlatformToAgentType {
    Chat,
    Task,
    DataReference,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AgentToPlatformType {
    Result,
    Progress,
    Error,
}

// ── Flat message structs (matches Python exactly) ──────────────────

/// Message from Platform Router to Agent subprocess (stdin).
///
/// Python source: models/ipc.py:37-51
/// Wire examples:
///   Chat:  {"`type":"chat","content":"...","conversation_id"`:"..."}
///   Task:  {"`type":"task","content":"...","task_id"`:"..."}
///   Data:  {"`type":"data_reference","ref_id":"var://...","summary"`:"..."}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PlatformToAgent {
    #[serde(rename = "type")]
    pub msg_type: PlatformToAgentType,
    #[serde(default)]
    pub content: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub conversation_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ref_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub summary: Option<String>,
}

/// Message from Agent subprocess to Platform Router (stdout).
///
/// Python source: models/ipc.py:54-95
/// Wire examples:
///   Result:   {"`type":"result","task_id":"...","output":"...","status":"completed`"}
///   Progress: {"`type":"progress","task_id":"...","message":"...","progress_pct":50.0`}
///   Error:    {"`type":"error","task_id":"...","error"`:"..."}
///
/// NOTE: `is_success` is a computed property in Python, not a wire field.
/// NOTE: All fields are optional; `type` is the only required discriminator.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AgentToPlatform {
    #[serde(rename = "type")]
    pub msg_type: AgentToPlatformType,
    #[serde(default)]
    pub content: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub progress_pct: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output: Option<serde_json::Value>,
}

impl AgentToPlatform {
    /// Check if this response indicates successful completion.
    /// Mirrors Python's `is_success` computed property:
    /// `self.status is None or self.status.lower() == "completed"`
    /// None status = success (agent completed without explicit status).
    #[must_use]
    pub fn is_success(&self) -> bool {
        if self.msg_type == AgentToPlatformType::Error {
            return false;
        }
        self.status
            .as_ref()
            .is_none_or(|s| s.to_lowercase() == "completed")
    }
}

// ── Envelope ───────────────────────────────────────────────────────

/// Envelope for any IPC message, with direction tagging.
///
/// Python source: models/ipc.py:98-125
/// Used for deserialization of raw JSON-lines from stdin/stdout pipes.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct IPCMessage {
    pub direction: MessageDirection,
    pub payload: serde_json::Value,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_chat_message() {
        let msg = PlatformToAgent {
            msg_type: PlatformToAgentType::Chat,
            content: "hello agent".to_string(),
            task_id: None,
            conversation_id: Some("conv-123".to_string()),
            ref_id: None,
            summary: None,
        };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""type":"chat""#));
        let de: PlatformToAgent = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, de);
    }

    #[test]
    fn roundtrip_task_message() {
        let msg = PlatformToAgent {
            msg_type: PlatformToAgentType::Task,
            content: "do something".to_string(),
            task_id: Some("t-1".to_string()),
            conversation_id: None,
            ref_id: None,
            summary: None,
        };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""type":"task""#));
        let de: PlatformToAgent = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, de);
    }

    #[test]
    fn roundtrip_data_reference_message() {
        let msg = PlatformToAgent {
            msg_type: PlatformToAgentType::DataReference,
            content: String::new(),
            task_id: None,
            conversation_id: None,
            ref_id: Some("var://x".to_string()),
            summary: Some("variable x".to_string()),
        };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""type":"data_reference""#));
        let de: PlatformToAgent = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, de);
    }

    #[test]
    fn roundtrip_result_message() {
        let msg = AgentToPlatform {
            msg_type: AgentToPlatformType::Result,
            content: "done".to_string(),
            task_id: Some("t-1".to_string()),
            message: None,
            progress_pct: None,
            error: None,
            status: Some("completed".to_string()),
            output: Some(serde_json::json!("result text")),
        };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""type":"result""#));
        assert!(!json.contains("success"));
        let de: AgentToPlatform = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, de);
    }

    #[test]
    fn roundtrip_progress_message() {
        let msg = AgentToPlatform {
            msg_type: AgentToPlatformType::Progress,
            content: String::new(),
            task_id: Some("t-1".to_string()),
            message: Some("halfway".to_string()),
            progress_pct: Some(50.0),
            error: None,
            status: None,
            output: None,
        };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""type":"progress""#));
        let de: AgentToPlatform = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, de);
    }

    #[test]
    fn roundtrip_error_message() {
        let msg = AgentToPlatform {
            msg_type: AgentToPlatformType::Error,
            content: String::new(),
            task_id: Some("t-1".to_string()),
            message: None,
            progress_pct: None,
            error: Some("something broke".to_string()),
            status: None,
            output: None,
        };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""type":"error""#));
        assert!(!json.contains("error_type"));
        let de: AgentToPlatform = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, de);
    }

    #[test]
    fn deserialize_python_chat() {
        let json = r#"{"type":"chat","content":"hello","conversation_id":"c1"}"#;
        let msg: PlatformToAgent = serde_json::from_str(json).unwrap();
        assert_eq!(msg.msg_type, PlatformToAgentType::Chat);
        assert_eq!(msg.content, "hello");
        assert_eq!(msg.conversation_id.as_deref(), Some("c1"));
    }

    #[test]
    fn deserialize_python_task() {
        let json = r#"{"type":"task","content":"review code","task_id":"t-1"}"#;
        let msg: PlatformToAgent = serde_json::from_str(json).unwrap();
        assert_eq!(msg.msg_type, PlatformToAgentType::Task);
        assert_eq!(msg.task_id.as_deref(), Some("t-1"));
    }

    #[test]
    fn deserialize_python_result() {
        let json = r#"{"type":"result","content":"","task_id":"t-1","output":"ok","status":"completed"}"#;
        let msg: AgentToPlatform = serde_json::from_str(json).unwrap();
        assert_eq!(msg.msg_type, AgentToPlatformType::Result);
        assert_eq!(msg.status.as_deref(), Some("completed"));
        assert!(msg.is_success());
    }

    #[test]
    fn deserialize_python_error() {
        let json = r#"{"type":"error","content":"","task_id":"t-1","error":"ImportError: module not found"}"#;
        let msg: AgentToPlatform = serde_json::from_str(json).unwrap();
        assert_eq!(msg.msg_type, AgentToPlatformType::Error);
        assert!(!msg.is_success());
    }

    #[test]
    fn deserialize_python_progress() {
        let json = r#"{"type":"progress","content":"","task_id":"t-1","message":"50% done","progress_pct":50.0}"#;
        let msg: AgentToPlatform = serde_json::from_str(json).unwrap();
        assert_eq!(msg.msg_type, AgentToPlatformType::Progress);
        assert_eq!(msg.progress_pct, Some(50.0));
        assert_eq!(msg.message.as_deref(), Some("50% done"));
    }

    #[test]
    fn is_success_returns_false_for_error() {
        let msg = AgentToPlatform {
            msg_type: AgentToPlatformType::Error,
            content: String::new(),
            task_id: None,
            message: None,
            progress_pct: None,
            error: Some("fail".to_string()),
            status: None,
            output: None,
        };
        assert!(!msg.is_success());
    }

    #[test]
    fn is_success_returns_true_when_status_completed() {
        let msg = AgentToPlatform {
            msg_type: AgentToPlatformType::Result,
            content: String::new(),
            task_id: None,
            message: None,
            progress_pct: None,
            error: None,
            status: Some("completed".to_string()),
            output: None,
        };
        assert!(msg.is_success());
    }

    #[test]
    fn is_success_returns_true_when_status_none_on_result() {
        // Python: self.status is None → True. Rust must mirror this.
        let msg = AgentToPlatform {
            msg_type: AgentToPlatformType::Result,
            content: String::new(),
            task_id: None,
            message: None,
            progress_pct: None,
            error: None,
            status: None,
            output: None,
        };
        assert!(msg.is_success());
    }
}
