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
    // A2A delivery types — Platform relays these to target agents
    ReceiveMessage,
    ReceiveRequest,
    ReceiveBroadcast,
    ReceiveReply,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AgentToPlatformType {
    Result,
    Progress,
    Error,
    // A2A origination types — Agent asks Platform to relay
    SendMessage,
    SendRequest,
    Broadcast,
    Reply,
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

// ── Payload union (matches Python's tagged union with model_validator) ─

/// Typed IPC payload that validates at deserialization time.
///
/// Python source: `models/ipc.py:98-125` — `IPCMessage.payload: PlatformToAgent | AgentToPlatform`
/// with a `model_validator(mode="before")` that resolves the union based on `direction`.
///
/// Uses untagged representation: the payload itself has no direction field.
/// Direction discrimination is handled by the `IPCMessage` envelope via
/// `#[serde(deserialize_with)]`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(untagged)]
pub enum IpcPayload {
    PlatformToAgent(PlatformToAgent),
    AgentToPlatform(AgentToPlatform),
}

// ── Envelope ───────────────────────────────────────────────────────

/// Envelope for any IPC message, with direction tagging.
///
/// Python source: models/ipc.py:98-125
/// Used for deserialization of raw JSON-lines from stdin/stdout pipes.
///
/// Wire format matches Python exactly:
/// ```json
/// {"direction": "platform_to_agent", "payload": {"type": "chat", "content": "..."}}
/// ```
#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct IPCMessage {
    pub direction: MessageDirection,
    pub payload: IpcPayload,
}

impl<'de> Deserialize<'de> for IPCMessage {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        let raw: std::collections::HashMap<String, serde_json::Value> =
            std::collections::HashMap::deserialize(deserializer)?;
        let direction_str = raw
            .get("direction")
            .and_then(|v| v.as_str())
            .ok_or_else(|| serde::de::Error::missing_field("direction"))?;
        let direction: MessageDirection = serde_json::from_value(serde_json::json!(direction_str))
            .map_err(serde::de::Error::custom)?;
        let payload_val = raw
            .get("payload")
            .ok_or_else(|| serde::de::Error::missing_field("payload"))?
            .clone();
        let payload = match direction {
            MessageDirection::PlatformToAgent => {
                let typed: PlatformToAgent =
                    serde_json::from_value(payload_val).map_err(serde::de::Error::custom)?;
                IpcPayload::PlatformToAgent(typed)
            }
            MessageDirection::AgentToPlatform => {
                let typed: AgentToPlatform =
                    serde_json::from_value(payload_val).map_err(serde::de::Error::custom)?;
                IpcPayload::AgentToPlatform(typed)
            }
        };
        Ok(IPCMessage { direction, payload })
    }
}

impl IPCMessage {
    /// Create a PlatformToAgent message.
    pub fn platform_to_agent(payload: PlatformToAgent) -> Self {
        Self {
            direction: MessageDirection::PlatformToAgent,
            payload: IpcPayload::PlatformToAgent(payload),
        }
    }

    /// Create an AgentToPlatform message.
    pub fn agent_to_platform(payload: AgentToPlatform) -> Self {
        Self {
            direction: MessageDirection::AgentToPlatform,
            payload: IpcPayload::AgentToPlatform(payload),
        }
    }
}

// ── A2A (Agent-to-Agent) messaging models ───────────────────────────

/// Network-layer address for an agent in A2A communication.
///
/// Python source: models/ipc.py:152-161
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AgentAddress {
    pub agent_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub role: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub composition: Option<String>,
}

/// Agent-to-Agent message carried over the Platform-as-Broker relay.
///
/// Python source: models/ipc.py:164-188
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct A2AMessage {
    pub message_id: String,
    pub from_agent: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub to_agent: Option<String>,
    pub msg_type: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub in_reply_to: Option<String>,
    pub content: String,
    #[serde(default, skip_serializing_if = "std::collections::HashMap::is_empty")]
    pub metadata: std::collections::HashMap<String, serde_json::Value>,
    pub timestamp: f64,
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

    #[test]
    fn ipc_message_roundtrip_platform_to_agent() {
        let payload = PlatformToAgent {
            msg_type: PlatformToAgentType::Chat,
            content: "hello".to_string(),
            task_id: None,
            conversation_id: Some("c1".to_string()),
            ref_id: None,
            summary: None,
        };
        let msg = IPCMessage::platform_to_agent(payload.clone());
        let json = serde_json::to_string(&msg).unwrap();
        // Verify wire format matches Python: {"direction":"platform_to_agent","payload":{...}}
        assert!(json.contains(r#""direction":"platform_to_agent""#));
        assert!(json.contains(r#""payload":{"type":"chat""#));
        let de: IPCMessage = serde_json::from_str(&json).unwrap();
        assert_eq!(de.direction, MessageDirection::PlatformToAgent);
        match de.payload {
            IpcPayload::PlatformToAgent(p) => assert_eq!(p, payload),
            _ => panic!("Expected PlatformToAgent variant"),
        }
    }

    #[test]
    fn ipc_message_roundtrip_agent_to_platform() {
        let payload = AgentToPlatform {
            msg_type: AgentToPlatformType::Result,
            content: "done".to_string(),
            task_id: Some("t-1".to_string()),
            message: None,
            progress_pct: None,
            error: None,
            status: Some("completed".to_string()),
            output: None,
        };
        let msg = IPCMessage::agent_to_platform(payload.clone());
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""direction":"agent_to_platform""#));
        assert!(json.contains(r#""payload":{"type":"result""#));
        let de: IPCMessage = serde_json::from_str(&json).unwrap();
        assert_eq!(de.direction, MessageDirection::AgentToPlatform);
        match de.payload {
            IpcPayload::AgentToPlatform(p) => assert_eq!(p, payload),
            _ => panic!("Expected AgentToPlatform variant"),
        }
    }

    #[test]
    fn deserialize_python_ipc_message() {
        // This matches what Python would serialize for an IPCMessage
        let json = r#"{"direction":"platform_to_agent","payload":{"type":"chat","content":"hello","conversation_id":"c1"}}"#;
        let msg: IPCMessage = serde_json::from_str(json).unwrap();
        assert_eq!(msg.direction, MessageDirection::PlatformToAgent);
        match &msg.payload {
            IpcPayload::PlatformToAgent(p) => {
                assert_eq!(p.msg_type, PlatformToAgentType::Chat);
                assert_eq!(p.content, "hello");
            }
            _ => panic!("Expected PlatformToAgent variant"),
        }
    }

    // -- A2A variant tests --------------------------------------------------

    #[test]
    fn deserialize_a2a_receive_message() {
        let json = r#"{"type":"receive_message","content":"hello from peer","task_id":"t-1"}"#;
        let msg: PlatformToAgent = serde_json::from_str(json).unwrap();
        assert_eq!(msg.msg_type, PlatformToAgentType::ReceiveMessage);
        assert_eq!(msg.content, "hello from peer");
    }

    #[test]
    fn deserialize_a2a_receive_request() {
        let json = r#"{"type":"receive_request","content":"review this code","task_id":"t-2"}"#;
        let msg: PlatformToAgent = serde_json::from_str(json).unwrap();
        assert_eq!(msg.msg_type, PlatformToAgentType::ReceiveRequest);
    }

    #[test]
    fn deserialize_a2a_receive_broadcast() {
        let json = r#"{"type":"receive_broadcast","content":"status update","task_id":"t-3"}"#;
        let msg: PlatformToAgent = serde_json::from_str(json).unwrap();
        assert_eq!(msg.msg_type, PlatformToAgentType::ReceiveBroadcast);
    }

    #[test]
    fn deserialize_a2a_receive_reply() {
        let json = r#"{"type":"receive_reply","content":"here is my response","task_id":"t-4"}"#;
        let msg: PlatformToAgent = serde_json::from_str(json).unwrap();
        assert_eq!(msg.msg_type, PlatformToAgentType::ReceiveReply);
    }

    #[test]
    fn deserialize_a2a_send_message() {
        let json = r#"{"type":"send_message","content":"hello peer","task_id":"t-5","output":{"to_agent":"agent-b"}}"#;
        let msg: AgentToPlatform = serde_json::from_str(json).unwrap();
        assert_eq!(msg.msg_type, AgentToPlatformType::SendMessage);
    }

    #[test]
    fn deserialize_a2a_broadcast() {
        let json = r#"{"type":"broadcast","content":"announcement","task_id":"t-6"}"#;
        let msg: AgentToPlatform = serde_json::from_str(json).unwrap();
        assert_eq!(msg.msg_type, AgentToPlatformType::Broadcast);
    }

    #[test]
    fn deserialize_a2a_reply() {
        let json = r#"{"type":"reply","content":"response to request","task_id":"t-7"}"#;
        let msg: AgentToPlatform = serde_json::from_str(json).unwrap();
        assert_eq!(msg.msg_type, AgentToPlatformType::Reply);
    }

    #[test]
    fn a2a_agent_address_roundtrip() {
        let addr = AgentAddress {
            agent_id: "agent-a".to_string(),
            role: Some("coordinator".to_string()),
            composition: None,
        };
        let json = serde_json::to_string(&addr).unwrap();
        let de: AgentAddress = serde_json::from_str(&json).unwrap();
        assert_eq!(addr, de);
    }

    #[test]
    fn a2a_message_roundtrip() {
        let msg = A2AMessage {
            message_id: "msg-1".to_string(),
            from_agent: "agent-a".to_string(),
            to_agent: Some("agent-b".to_string()),
            msg_type: "chat".to_string(),
            in_reply_to: None,
            content: "hello".to_string(),
            metadata: std::collections::HashMap::new(),
            timestamp: 1715600000.0,
        };
        let json = serde_json::to_string(&msg).unwrap();
        let de: A2AMessage = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, de);
    }
}
