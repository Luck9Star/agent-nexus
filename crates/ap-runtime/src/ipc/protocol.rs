//! High-level IPC protocol for agent communication.
//!
//! Wraps ap-core's [`IpcProtocol`] and adds heartbeat support.
//! Typed send/receive (`send_chat`, `send_task`, `receive_result`) are
//! delegated to `IpcProtocol` — no duplication.

use ap_core::models::ipc::AgentToPlatform;
use ap_core::models::ipc::AgentToPlatformType;
use ap_core::orchestration::ipc::IpcError;
use ap_core::orchestration::ipc_protocol::IpcProtocol;
use std::time::Duration;
use tokio::io::{AsyncRead, AsyncWrite};

/// Heartbeat timeout — matches Python's _HEARTBEAT_TIMEOUT (10s).
const HEARTBEAT_TIMEOUT: Duration = Duration::from_secs(10);

// ---------------------------------------------------------------------------
// Re-exports
// ---------------------------------------------------------------------------

/// Re-export `AgentResult` from ap-core (canonical definition).
pub use ap_core::orchestration::ipc_protocol::AgentResult;

// ---------------------------------------------------------------------------
// AgentProtocol
// ---------------------------------------------------------------------------

/// High-level protocol for communicating with an agent subprocess.
///
/// Wraps ap-core's [`IpcProtocol`] (which provides `send_chat`, `send_task`,
/// `receive_result`) and adds a heartbeat convenience method.
///
/// Layer hierarchy: `IpcStream` (wire) → `IpcProtocol` (typed) → **`AgentProtocol`** (heartbeat).
pub struct AgentProtocol<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> {
    inner: IpcProtocol<R, W>,
}

impl<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> AgentProtocol<R, W> {
    /// Create a new `AgentProtocol` from raw reader/writer halves.
    pub fn new(reader: R, writer: W) -> Self {
        Self {
            inner: IpcProtocol::new(reader, writer),
        }
    }

    /// Create from an existing `IpcProtocol`.
    pub fn from_protocol(protocol: IpcProtocol<R, W>) -> Self {
        Self { inner: protocol }
    }

    /// Send a chat message to the agent.
    ///
    /// Delegates to [`IpcProtocol::send_chat`].
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn send_chat(
        &mut self,
        content: &str,
        conversation_id: &str,
    ) -> Result<(), IpcError> {
        self.inner
            .send_chat(content, Some(conversation_id))
            .await
    }

    /// Send a task message to the agent.
    ///
    /// Delegates to [`IpcProtocol::send_task`].
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn send_task(&mut self, content: &str, task_id: &str) -> Result<(), IpcError> {
        self.inner.send_task(content, task_id).await
    }

    /// Receive the next agent response and convert it to an [`AgentResult`].
    ///
    /// Delegates to [`IpcProtocol::receive_result`].
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn receive_result(&mut self, timeout: Option<f64>) -> Result<AgentResult, IpcError> {
        self.inner.receive_result(timeout).await
    }

    /// Heartbeat: send `__heartbeat__`, expect pong within timeout.
    ///
    /// Sends `__heartbeat__` with `conversation_id="__hb__"` (matching the Python
    /// IPC protocol) and waits up to `HEARTBEAT_TIMEOUT` for a progress response
    /// containing "pong". Non-pong progress messages are skipped. Result/Error
    /// messages are treated as terminal (not a pong).
    ///
    /// # Errors
    /// Returns an error if the underlying send/receive operation fails.
    pub async fn heartbeat(&mut self) -> Result<bool, IpcError> {
        self.inner.send_chat("__heartbeat__", Some("__hb__")).await?;
        let deadline = std::time::Instant::now() + HEARTBEAT_TIMEOUT;
        loop {
            let remaining = deadline.saturating_duration_since(std::time::Instant::now());
            if remaining.is_zero() {
                return Ok(false);
            }
            let recv_fut = self.inner.stream_mut().receive::<AgentToPlatform>();
            tokio::pin!(recv_fut);
            match tokio::time::timeout(remaining, &mut recv_fut).await {
                Ok(Ok(msg)) => {
                    if msg.msg_type == AgentToPlatformType::Progress
                        && msg.content.to_lowercase().contains("pong")
                    {
                        return Ok(true);
                    }
                    // Result/Error messages are terminal — not a pong.
                    if msg.msg_type == AgentToPlatformType::Result
                        || msg.msg_type == AgentToPlatformType::Error
                    {
                        return Ok(false);
                    }
                    // Other types (A2A etc.) — skip and keep waiting.
                    continue;
                }
                Ok(Err(e)) => return Err(e),
                Err(_) => return Ok(false), // timeout
            }
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
        assert_eq!(result.task_id.as_deref(), Some("t-1"));
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

            // Heartbeat expects a "progress" type response containing "pong"
            let resp = r#"{"type":"progress","content":"pong","task_id":null,"message":null,"progress_pct":null,"error":null,"status":null,"output":null}"#;
            let mut writer = sw;
            writer.write_all(resp.as_bytes()).await.unwrap();
            writer.write_all(b"\n").await.unwrap();
            writer.flush().await.unwrap();
        });

        let pong = proto.heartbeat().await.expect("heartbeat should succeed");
        assert!(pong, "heartbeat should receive pong");
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
            .receive_result(Some(5.0))
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
            .receive_result(Some(0.05))
            .await;
        assert!(result.is_err());
    }
}
