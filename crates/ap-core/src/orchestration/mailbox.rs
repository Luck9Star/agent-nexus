//! `Mailbox`: Cross-agent data reference passing.
//!
//! Instead of sending full payloads between agents (expensive in token cost),
//! agents store data in the mailbox and pass a short reference URI (~50 chars).
//! The receiving agent resolves the reference on demand.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::models::common::utc_now;

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

#[derive(Debug, thiserror::Error)]
pub enum MailboxError {
    #[error("Reference not found: {0}")]
    NotFound(String),
    #[error("Invalid reference URI: {0}")]
    InvalidUri(String),
}

// ---------------------------------------------------------------------------
// Data structures
// ---------------------------------------------------------------------------

/// A reference that can be passed between agents instead of full data.
///
/// Format: `ref://mailbox/{agent_id}/{message_id}/{field}`
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct DataRef {
    /// The reference URI: ref://mailbox/{agent}/{msg_id}/{field}
    pub uri: String,
    /// Content type hint (e.g., "application/json", "text/plain")
    pub content_type: String,
    /// Approximate size of referenced data in bytes
    pub size_hint: usize,
}

/// A message stored in the mailbox.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct MailboxMessage {
    pub id: String,
    pub from_agent: String,
    pub to_agent: String,
    pub field: String,
    pub content: String,
    pub created_at: i64,
}

/// In-memory mailbox store for cross-agent data references.
#[derive(Debug, Default)]
pub struct Mailbox {
    messages: HashMap<String, MailboxMessage>,
}

// ---------------------------------------------------------------------------
// Counter for generating unique message IDs
// ---------------------------------------------------------------------------

/// Global atomic counter for unique message IDs within a process.
static MSG_COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

fn next_message_id() -> String {
    let count = MSG_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let ts = utc_now().timestamp_millis();
    format!("msg-{ts}-{count}")
}

fn build_uri(agent_id: &str, msg_id: &str, field: &str) -> String {
    format!("ref://mailbox/{agent_id}/{msg_id}/{field}")
}

// ---------------------------------------------------------------------------
// Mailbox operations
// ---------------------------------------------------------------------------

impl Mailbox {
    /// Create a new empty mailbox.
    pub fn new() -> Self {
        Self::default()
    }

    /// Store data and return a reference.
    pub fn store(
        &mut self,
        from_agent: &str,
        to_agent: &str,
        field: &str,
        content: String,
        content_type: &str,
    ) -> DataRef {
        let id = next_message_id();
        let uri = build_uri(from_agent, &id, field);
        let size_hint = content.len();

        let msg = MailboxMessage {
            id,
            from_agent: from_agent.to_string(),
            to_agent: to_agent.to_string(),
            field: field.to_string(),
            content,
            created_at: utc_now().timestamp_millis(),
        };

        self.messages.insert(msg.id.clone(), msg);

        DataRef {
            uri,
            content_type: content_type.to_string(),
            size_hint,
        }
    }

    /// Resolve a reference to get the stored data.
    pub fn resolve(&self, data_ref: &DataRef) -> Result<String, MailboxError> {
        let msg_id = Self::extract_message_id(&data_ref.uri)?;
        self.messages
            .get(&msg_id)
            .map(|m| m.content.clone())
            .ok_or_else(|| MailboxError::NotFound(data_ref.uri.clone()))
    }

    /// List all messages for a given agent (as recipient).
    pub fn list_for_agent(&self, agent_id: &str) -> Vec<&MailboxMessage> {
        self.messages
            .values()
            .filter(|m| m.to_agent == agent_id)
            .collect()
    }

    /// Remove all messages for a given agent (as recipient).
    pub fn purge(&mut self, agent_id: &str) {
        self.messages
            .retain(|_, m| m.to_agent != agent_id);
    }

    /// Total number of stored messages.
    pub fn len(&self) -> usize {
        self.messages.len()
    }

    /// Whether the mailbox is empty.
    pub fn is_empty(&self) -> bool {
        self.messages.is_empty()
    }

    /// Extract the message_id from a URI like `ref://mailbox/{agent}/{msg_id}/{field}`.
    fn extract_message_id(uri: &str) -> Result<String, MailboxError> {
        uri.strip_prefix("ref://mailbox/")
            .ok_or_else(|| MailboxError::InvalidUri(uri.to_string()))
            .and_then(|rest| {
                // rest = "{agent}/{msg_id}/{field}"
                let parts: Vec<&str> = rest.splitn(3, '/').collect();
                if parts.len() < 2 || parts[0].is_empty() || parts[1].is_empty() {
                    return Err(MailboxError::InvalidUri(uri.to_string()));
                }
                Ok(parts[1].to_string())
            })
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn store_returns_valid_data_ref() {
        let mut mb = Mailbox::new();
        let dr = mb.store("agent-a", "agent-b", "payload", r#"{"key":"value"}"#.to_string(), "application/json");
        assert!(dr.uri.starts_with("ref://mailbox/agent-a/"));
        assert!(dr.uri.contains("/payload"));
        assert!(!dr.uri.is_empty());
    }

    #[test]
    fn resolve_returns_stored_content() {
        let mut mb = Mailbox::new();
        let content = r#"{"result":42}"#.to_string();
        let dr = mb.store("agent-a", "agent-b", "payload", content.clone(), "application/json");
        let resolved = mb.resolve(&dr).unwrap();
        assert_eq!(resolved, content);
    }

    #[test]
    fn resolve_unknown_ref_returns_error() {
        let mb = Mailbox::new();
        let dr = DataRef {
            uri: "ref://mailbox/agent-x/msg-nonexistent/payload".to_string(),
            content_type: "application/json".to_string(),
            size_hint: 0,
        };
        let err = mb.resolve(&dr).unwrap_err();
        assert!(matches!(err, MailboxError::NotFound(_)));
    }

    #[test]
    fn list_for_agent_returns_only_their_messages() {
        let mut mb = Mailbox::new();
        mb.store("agent-a", "agent-b", "payload", "data1".to_string(), "application/json");
        mb.store("agent-c", "agent-b", "result", "data2".to_string(), "application/json");
        mb.store("agent-a", "agent-c", "payload", "data3".to_string(), "application/json");

        let for_b = mb.list_for_agent("agent-b");
        assert_eq!(for_b.len(), 2);
        assert!(for_b.iter().all(|m| m.to_agent == "agent-b"));

        let for_c = mb.list_for_agent("agent-c");
        assert_eq!(for_c.len(), 1);

        let for_a = mb.list_for_agent("agent-a");
        assert!(for_a.is_empty());
    }

    #[test]
    fn purge_removes_all_agent_messages() {
        let mut mb = Mailbox::new();
        mb.store("agent-a", "agent-b", "payload", "data1".to_string(), "application/json");
        mb.store("agent-c", "agent-b", "result", "data2".to_string(), "application/json");

        assert_eq!(mb.len(), 2);
        mb.purge("agent-b");
        assert_eq!(mb.len(), 0);
        assert!(mb.list_for_agent("agent-b").is_empty());
    }

    #[test]
    fn store_multiple_messages() {
        let mut mb = Mailbox::new();
        let dr1 = mb.store("agent-a", "agent-b", "field1", "content1".to_string(), "application/json");
        let dr2 = mb.store("agent-a", "agent-b", "field2", "content2".to_string(), "application/json");
        let dr3 = mb.store("agent-b", "agent-c", "field3", "content3".to_string(), "application/json");

        assert_eq!(mb.len(), 3);
        assert_ne!(dr1.uri, dr2.uri);
        assert_ne!(dr2.uri, dr3.uri);

        assert_eq!(mb.resolve(&dr1).unwrap(), "content1");
        assert_eq!(mb.resolve(&dr2).unwrap(), "content2");
        assert_eq!(mb.resolve(&dr3).unwrap(), "content3");
    }

    #[test]
    fn data_ref_uri_format_correct() {
        let mut mb = Mailbox::new();
        let dr = mb.store("sender", "receiver", "output", "test".to_string(), "application/json");

        // Format: ref://mailbox/{agent_id}/{message_id}/{field}
        assert!(dr.uri.starts_with("ref://mailbox/sender/"));
        assert!(dr.uri.ends_with("/output"));

        let parts: Vec<&str> = dr.uri.split('/').collect();
        // ref: mailbox: sender: msg-id: output = 5 non-empty parts
        assert_eq!(parts.len(), 6); // ["ref:", "", "mailbox", "sender", "{msg_id}", "output"]
    }

    #[test]
    fn size_hint_matches_content() {
        let mut mb = Mailbox::new();
        let content = "Hello, world!".to_string();
        let dr = mb.store("a", "b", "payload", content.clone(), "text/plain");
        assert_eq!(dr.size_hint, content.len());
        assert_eq!(dr.size_hint, 13);
        assert_eq!(dr.content_type, "text/plain");
    }

    #[test]
    fn purge_does_not_affect_other_agents() {
        let mut mb = Mailbox::new();
        mb.store("agent-a", "agent-b", "payload", "for-b".to_string(), "application/json");
        mb.store("agent-a", "agent-c", "payload", "for-c".to_string(), "application/json");

        mb.purge("agent-b");

        assert_eq!(mb.len(), 1);
        assert!(mb.list_for_agent("agent-b").is_empty());
        assert_eq!(mb.list_for_agent("agent-c").len(), 1);
    }

    #[test]
    fn empty_mailbox_operations() {
        let mb = Mailbox::new();

        assert!(mb.is_empty());
        assert_eq!(mb.len(), 0);
        assert!(mb.list_for_agent("anyone").is_empty());

        // Resolve on empty mailbox should error
        let dr = DataRef {
            uri: "ref://mailbox/x/msg-0/field".to_string(),
            content_type: "text/plain".to_string(),
            size_hint: 0,
        };
        assert!(mb.resolve(&dr).is_err());
    }

    #[test]
    fn extract_message_id_rejects_invalid_uri() {
        assert!(Mailbox::extract_message_id("not-a-valid-uri").is_err());
        assert!(Mailbox::extract_message_id("ref://mailbox/").is_err());
        assert!(Mailbox::extract_message_id("ref://mailbox/only-agent").is_err());
        // P1-3 fix: empty segment should error
        assert!(Mailbox::extract_message_id("ref://mailbox/agent//field").is_err());

        let valid = Mailbox::extract_message_id("ref://mailbox/agent/msg-123/field").unwrap();
        assert_eq!(valid, "msg-123");
    }
}
