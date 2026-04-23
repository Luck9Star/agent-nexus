//! `AgentIpcStream`: wraps ap-core's `IpcStream` with agent-specific features.
//!
//! Adds heartbeat ping-pong and typed send/receive using ap-core's
//! `PlatformToAgent` / `AgentToPlatform` models.

use ap_core::models::ipc::{
    AgentToPlatform, AgentToPlatformType, PlatformToAgent, PlatformToAgentType,
};
use ap_core::orchestration::ipc::{IpcError, IpcStream};
use serde::de::DeserializeOwned;
use serde::Serialize;
use std::time::Duration;
use tokio::io::{AsyncRead, AsyncWrite};

/// Heartbeat timeout in seconds.
const HEARTBEAT_TIMEOUT_SECS: f64 = 10.0;

// ---------------------------------------------------------------------------
// AgentIpcStream
// ---------------------------------------------------------------------------

/// Wraps `IpcStream` with agent-specific send/receive and heartbeat support.
pub struct AgentIpcStream<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> {
    inner: IpcStream<R, W>,
}

impl<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> AgentIpcStream<R, W> {
    /// Create a new `AgentIpcStream` from raw reader/writer halves.
    pub fn new(reader: R, writer: W) -> Self {
        Self {
            inner: IpcStream::new(reader, writer),
        }
    }

    /// Create from an existing `IpcStream`.
    pub fn from_stream(stream: IpcStream<R, W>) -> Self {
        Self { inner: stream }
    }

    /// Send any serializable message.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn send<T: Serialize>(&mut self, msg: &T) -> Result<(), IpcError> {
        self.inner.send(msg).await
    }

    /// Receive any deserializable message.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn receive<T: DeserializeOwned>(&mut self) -> Result<T, IpcError> {
        self.inner.receive::<T>().await
    }

    /// Send a `PlatformToAgent` chat message.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn send_chat(
        &mut self,
        content: &str,
        conversation_id: Option<&str>,
    ) -> Result<(), IpcError> {
        self.send(&PlatformToAgent {
            msg_type: PlatformToAgentType::Chat,
            content: content.to_string(),
            conversation_id: conversation_id.map(std::string::ToString::to_string),
            task_id: None,
            ref_id: None,
            summary: None,
        })
        .await
    }

    /// Send a `PlatformToAgent` task message.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn send_task(&mut self, content: &str, task_id: &str) -> Result<(), IpcError> {
        self.send(&PlatformToAgent {
            msg_type: PlatformToAgentType::Task,
            content: content.to_string(),
            task_id: Some(task_id.to_string()),
            conversation_id: None,
            ref_id: None,
            summary: None,
        })
        .await
    }

    /// Receive an `AgentToPlatform` response with optional timeout.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn receive_response(
        &mut self,
        timeout: Option<Duration>,
    ) -> Result<AgentToPlatform, IpcError> {
        match timeout {
            Some(dur) => {
                let fut = self.inner.receive::<AgentToPlatform>();
                tokio::pin!(fut);
                match tokio::time::timeout(dur, &mut fut).await {
                    Ok(result) => result,
                    Err(_) => Err(IpcError::Timeout {
                        timeout: dur.as_secs_f64(),
                    }),
                }
            }
            None => self.inner.receive::<AgentToPlatform>().await,
        }
    }

    /// Heartbeat: send a ping message and expect a pong within `HEARTBEAT_TIMEOUT_SECS`.
    ///
    /// Uses a chat message with content "__ping__" as heartbeat. The agent
    /// is expected to respond with a result message containing "__pong__".
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn heartbeat(&mut self) -> Result<(), IpcError> {
        self.send_chat("__ping__", None).await?;
        let timeout_dur = Duration::from_secs_f64(HEARTBEAT_TIMEOUT_SECS);
        let response = self.receive_response(Some(timeout_dur)).await?;
        if response.msg_type == AgentToPlatformType::Result && response.content == "__pong__" {
            Ok(())
        } else {
            Err(IpcError::Io(std::io::Error::other(
                "heartbeat failed: unexpected response from agent",
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

    #[tokio::test]
    async fn send_chat_and_receive() {
        let (client, server) = tokio::io::duplex(4096);
        let (sr, sw) = tokio::io::split(server);
        let (cr, cw) = tokio::io::split(client);

        let mut stream = AgentIpcStream::new(cr, cw);

        // Server side: read a line, verify it's a chat
        tokio::spawn(async move {
            let mut line = String::new();
            let mut reader = BufReader::new(sr);
            reader.read_line(&mut line).await.unwrap();
            assert!(line.contains(r#""type":"chat""#));
            assert!(line.contains("hello agent"));

            let response =
                r#"{"type":"result","content":"ok","task_id":null,"message":null,"progress_pct":null,"error":null,"status":"completed","output":null}"#;
            let mut writer = sw;
            writer.write_all(response.as_bytes()).await.unwrap();
            writer.write_all(b"\n").await.unwrap();
            writer.flush().await.unwrap();
        });

        stream.send_chat("hello agent", Some("conv-1")).await.unwrap();
        let resp = stream.receive_response(None).await.unwrap();
        assert_eq!(resp.content, "ok");
        assert!(resp.is_success());
    }

    #[tokio::test]
    async fn send_task_and_receive() {
        let (client, server) = tokio::io::duplex(4096);
        let (sr, sw) = tokio::io::split(server);
        let (cr, cw) = tokio::io::split(client);

        let mut stream = AgentIpcStream::new(cr, cw);

        tokio::spawn(async move {
            let mut line = String::new();
            let mut reader = BufReader::new(sr);
            reader.read_line(&mut line).await.unwrap();
            assert!(line.contains(r#""type":"task""#));

            let response =
                r#"{"type":"result","content":"task done","task_id":"t-1","message":null,"progress_pct":null,"error":null,"status":"completed","output":null}"#;
            let mut writer = sw;
            writer.write_all(response.as_bytes()).await.unwrap();
            writer.write_all(b"\n").await.unwrap();
            writer.flush().await.unwrap();
        });

        stream.send_task("do work", "t-1").await.unwrap();
        let resp = stream.receive_response(None).await.unwrap();
        assert_eq!(resp.content, "task done");
    }

    #[tokio::test]
    async fn heartbeat_success() {
        let (client, server) = tokio::io::duplex(4096);
        let (sr, sw) = tokio::io::split(server);
        let (cr, cw) = tokio::io::split(client);

        let mut stream = AgentIpcStream::new(cr, cw);

        tokio::spawn(async move {
            let mut line = String::new();
            let mut reader = BufReader::new(sr);
            reader.read_line(&mut line).await.unwrap();

            let response =
                r#"{"type":"result","content":"__pong__","task_id":null,"message":null,"progress_pct":null,"error":null,"status":"completed","output":null}"#;
            let mut writer = sw;
            writer.write_all(response.as_bytes()).await.unwrap();
            writer.write_all(b"\n").await.unwrap();
            writer.flush().await.unwrap();
        });

        stream.heartbeat().await.expect("heartbeat should succeed");
    }

    #[tokio::test]
    async fn heartbeat_timeout_fails() {
        let (client, server) = tokio::io::duplex(4096);
        let (sr, _sw) = tokio::io::split(server);
        let (cr, cw) = tokio::io::split(client);

        let mut stream = AgentIpcStream::new(cr, cw);

        // Server reads but never responds
        tokio::spawn(async move {
            let mut reader = BufReader::new(sr);
            let mut buf = vec![0u8; 1024];
            // Hold the connection open but never respond
            if let Err(e) = reader.read(&mut buf).await {
                tracing::warn!("IPC read error in test helper: {e}");
            }
        });

        // Use a very short timeout for testing
        let result = tokio::time::timeout(
            Duration::from_millis(500),
            stream.heartbeat(),
        )
        .await;

        // Should either timeout from our short Duration or from heartbeat's internal timeout
        assert!(result.is_err() || result.unwrap().is_err());
    }

    #[tokio::test]
    async fn receive_with_timeout() {
        let (client, server) = tokio::io::duplex(4096);
        let (sr, sw) = tokio::io::split(server);
        let (cr, cw) = tokio::io::split(client);

        let mut stream = AgentIpcStream::new(cr, cw);

        tokio::spawn(async move {
            let mut line = String::new();
            let mut reader = BufReader::new(sr);
            reader.read_line(&mut line).await.unwrap();

            // Delay response slightly but within timeout
            tokio::time::sleep(Duration::from_millis(10)).await;
            let response =
                r#"{"type":"result","content":"delayed","task_id":null,"message":null,"progress_pct":null,"error":null,"status":"completed","output":null}"#;
            let mut writer = sw;
            writer.write_all(response.as_bytes()).await.unwrap();
            writer.write_all(b"\n").await.unwrap();
            writer.flush().await.unwrap();
        });

        stream.send_chat("test", None).await.unwrap();
        let resp = stream
            .receive_response(Some(Duration::from_secs(5)))
            .await
            .unwrap();
        assert_eq!(resp.content, "delayed");
    }
}
