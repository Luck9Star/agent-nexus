//! High-level IPC protocol operations for agent communication.
//!
//! Provides convenience methods: `send_chat`, `send_task`, `receive_result`, heartbeat.

use ap_core::models::ipc::{
    AgentToPlatformType, PlatformToAgent, PlatformToAgentType,
};
use ap_core::orchestration::ipc::IpcError;
use std::time::Duration;
use tokio::io::{AsyncRead, AsyncWrite};

use super::stream::AgentIpcStream;

/// Default heartbeat timeout: 10 seconds.
const HEARTBEAT_TIMEOUT: Duration = Duration::from_secs(10);

/// Re-export `AgentResult` from ap-core (canonical definition).
pub use ap_core::orchestration::ipc_protocol::AgentResult;

// ---------------------------------------------------------------------------
// AgentProtocol
// ---------------------------------------------------------------------------

/// High-level protocol for communicating with an agent subprocess.
pub struct AgentProtocol<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> {
    stream: AgentIpcStream<R, W>,
}

impl<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> AgentProtocol<R, W> {
    /// Create a new `AgentProtocol` from raw reader/writer halves.
    pub fn new(reader: R, writer: W) -> Self {
        Self {
            stream: AgentIpcStream::new(reader, writer),
        }
    }

    /// Create from an existing `AgentIpcStream`.
    pub fn from_stream(stream: AgentIpcStream<R, W>) -> Self {
        Self { stream }
    }

    /// Send a chat message to the agent.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn send_chat(
        &mut self,
        content: &str,
        conversation_id: &str,
    ) -> Result<(), IpcError> {
        self.stream
            .send_chat(content, Some(conversation_id))
            .await
    }

    /// Send a task message to the agent.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn send_task(&mut self, content: &str, task_id: &str) -> Result<(), IpcError> {
        self.stream.send_task(content, task_id).await
    }

    /// Receive the next agent response and convert it to an `AgentResult`.
    /// If a timeout is provided, aborts with `IpcError::Timeout` on expiry.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn receive_result(&mut self, timeout: Option<Duration>) -> Result<AgentResult, IpcError> {
        let msg = self.stream.receive_response(timeout).await?;
        let success = msg.is_success();
        match msg.msg_type {
            AgentToPlatformType::Result => Ok(AgentResult {
                content: msg.content,
                success,
            }),
            AgentToPlatformType::Error => Err(IpcError::Io(std::io::Error::other(format!(
                "Agent error: {}",
                msg.error.as_deref().unwrap_or("unknown")
            )))),
            AgentToPlatformType::Progress => {
                // Progress messages are informational; return as partial result
                Ok(AgentResult {
                    content: msg.message.unwrap_or_default(),
                    success: true,
                })
            }
        }
    }

    /// Heartbeat: send ping, expect pong within 10 seconds.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn heartbeat(&mut self) -> Result<(), IpcError> {
        // Send ping message
        self.stream
            .send(&PlatformToAgent {
                msg_type: PlatformToAgentType::Chat,
                content: "__ping__".to_string(),
                conversation_id: None,
                task_id: None,
                ref_id: None,
                summary: None,
            })
            .await?;

        // Expect pong within timeout
        let response = self
            .stream
            .receive_response(Some(HEARTBEAT_TIMEOUT))
            .await?;

        if response.msg_type == AgentToPlatformType::Result && response.content == "__pong__" {
            Ok(())
        } else {
            Err(IpcError::Io(std::io::Error::other(
                "heartbeat failed: unexpected response",
            )))
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};

    fn agent_response(content: &str, task_id: Option<&str>) -> String {
        let tid = match task_id {
            Some(id) => format!("\"{id}\""),
            None => "null".to_string(),
        };
        format!(
            r#"{{"type":"result","content":"{content}","task_id":{tid},"message":null,"progress_pct":null,"error":null,"status":"completed","output":null}}"#
        )
    }

    #[tokio::test]
    async fn send_chat_and_receive_result() {
        let (client, server) = tokio::io::duplex(4096);
        let (sr, sw) = tokio::io::split(server);
        let (cr, cw) = tokio::io::split(client);

        let mut proto = AgentProtocol::new(cr, cw);

        tokio::spawn(async move {
            let mut line = String::new();
            let mut reader = BufReader::new(sr);
            reader.read_line(&mut line).await.unwrap();

            let resp = agent_response("chat reply", None);
            let mut writer = sw;
            writer.write_all(resp.as_bytes()).await.unwrap();
            writer.write_all(b"\n").await.unwrap();
            writer.flush().await.unwrap();
        });

        proto.send_chat("hello", "conv-1").await.unwrap();
        let result = proto.receive_result(None).await.unwrap();
        assert_eq!(result.content, "chat reply");
        assert!(result.success);
    }

    #[tokio::test]
    async fn send_task_and_receive_result() {
        let (client, server) = tokio::io::duplex(4096);
        let (sr, sw) = tokio::io::split(server);
        let (cr, cw) = tokio::io::split(client);

        let mut proto = AgentProtocol::new(cr, cw);

        tokio::spawn(async move {
            let mut line = String::new();
            let mut reader = BufReader::new(sr);
            reader.read_line(&mut line).await.unwrap();

            let resp = agent_response("task output", Some("t-1"));
            let mut writer = sw;
            writer.write_all(resp.as_bytes()).await.unwrap();
            writer.write_all(b"\n").await.unwrap();
            writer.flush().await.unwrap();
        });

        proto.send_task("do something", "t-1").await.unwrap();
        let result = proto.receive_result(None).await.unwrap();
        assert_eq!(result.content, "task output");
        assert!(result.success);
    }

    #[tokio::test]
    async fn receive_result_handles_error_response() {
        let (client, server) = tokio::io::duplex(4096);
        let (sr, sw) = tokio::io::split(server);
        let (cr, cw) = tokio::io::split(client);

        let mut proto = AgentProtocol::new(cr, cw);

        tokio::spawn(async move {
            let mut line = String::new();
            let mut reader = BufReader::new(sr);
            reader.read_line(&mut line).await.unwrap();

            let resp =
                r#"{"type":"error","content":"","task_id":"t-1","message":null,"progress_pct":null,"error":"something went wrong","status":null,"output":null}"#;
            let mut writer = sw;
            writer.write_all(resp.as_bytes()).await.unwrap();
            writer.write_all(b"\n").await.unwrap();
            writer.flush().await.unwrap();
        });

        proto.send_task("fail this", "t-1").await.unwrap();
        let result = proto.receive_result(None).await;
        assert!(result.is_err());
    }

    #[tokio::test]
    async fn heartbeat_with_pong_succeeds() {
        let (client, server) = tokio::io::duplex(4096);
        let (sr, sw) = tokio::io::split(server);
        let (cr, cw) = tokio::io::split(client);

        let mut proto = AgentProtocol::new(cr, cw);

        tokio::spawn(async move {
            let mut line = String::new();
            let mut reader = BufReader::new(sr);
            reader.read_line(&mut line).await.unwrap();

            let resp = agent_response("__pong__", None);
            let mut writer = sw;
            writer.write_all(resp.as_bytes()).await.unwrap();
            writer.write_all(b"\n").await.unwrap();
            writer.flush().await.unwrap();
        });

        proto.heartbeat().await.expect("heartbeat should succeed");
    }

    #[tokio::test]
    async fn receive_result_with_timeout() {
        let (client, server) = tokio::io::duplex(4096);
        let (sr, sw) = tokio::io::split(server);
        let (cr, cw) = tokio::io::split(client);

        let mut proto = AgentProtocol::new(cr, cw);

        tokio::spawn(async move {
            let mut line = String::new();
            let mut reader = BufReader::new(sr);
            reader.read_line(&mut line).await.unwrap();

            // Respond quickly
            let resp = agent_response("fast response", None);
            let mut writer = sw;
            writer.write_all(resp.as_bytes()).await.unwrap();
            writer.write_all(b"\n").await.unwrap();
            writer.flush().await.unwrap();
        });

        proto.send_chat("test", "c-1").await.unwrap();
        let result = proto
            .receive_result(Some(Duration::from_secs(5)))
            .await
            .unwrap();
        assert_eq!(result.content, "fast response");
    }

    #[tokio::test]
    async fn receive_result_timeout_expires() {
        let (client, server) = tokio::io::duplex(4096);
        let (sr, _sw) = tokio::io::split(server);
        let (cr, cw) = tokio::io::split(client);

        let mut proto = AgentProtocol::new(cr, cw);

        // Server reads but never responds
        tokio::spawn(async move {
            let mut reader = BufReader::new(sr);
            let mut buf = vec![0u8; 1024];
            if let Err(e) = reader.read(&mut buf).await {
                tracing::warn!("IPC read error in test helper: {e}");
            }
        });

        // send a message first
        proto.send_chat("test", "c-1").await.unwrap();

        // Very short timeout should expire
        let result = proto
            .receive_result(Some(Duration::from_millis(50)))
            .await;
        assert!(result.is_err());
    }
}
