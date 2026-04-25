//! `IpcProtocol`: high-level semantic methods over `IpcStream`.
//!
//! Python source: `src/agent_nexus/platform/orchestration/ipc.py` — `send_chat`, `send_task`, heartbeat

use crate::models::ipc::{AgentToPlatform, AgentToPlatformType, PlatformToAgent, PlatformToAgentType};
use crate::orchestration::ipc::{IpcError, IpcStream};
use tokio::io::{AsyncRead, AsyncWrite};

// ---------------------------------------------------------------------------
// Agent result
// ---------------------------------------------------------------------------

/// Parsed result from an agent response.
#[derive(Debug, Clone, PartialEq)]
pub struct AgentResult {
    pub content: String,
    pub success: bool,
    /// Task ID from the agent response, if present.
    /// Allows correlating responses with multiple in-flight tasks.
    pub task_id: Option<String>,
}

// ---------------------------------------------------------------------------
// IpcProtocol
// ---------------------------------------------------------------------------

pub struct IpcProtocol<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> {
    stream: IpcStream<R, W>,
}

impl<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> IpcProtocol<R, W> {
    pub fn new(reader: R, writer: W) -> Self {
        Self {
            stream: IpcStream::new(reader, writer),
        }
    }

    /// Send a chat message to an agent.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn send_chat(
        &mut self,
        content: &str,
        conversation_id: Option<&str>,
    ) -> Result<(), IpcError> {
        self.stream
            .send(&PlatformToAgent {
                msg_type: PlatformToAgentType::Chat,
                content: content.into(),
                conversation_id: conversation_id.map(std::convert::Into::into),
                task_id: None,
                ref_id: None,
                summary: None,
            })
            .await
    }

    /// Send a task message to an agent.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn send_task(&mut self, content: &str, task_id: &str) -> Result<(), IpcError> {
        self.stream
            .send(&PlatformToAgent {
                msg_type: PlatformToAgentType::Task,
                content: content.into(),
                task_id: Some(task_id.into()),
                conversation_id: None,
                ref_id: None,
                summary: None,
            })
            .await
    }

    /// Receive the next agent response, converting it to an `AgentResult`.
    /// If a timeout (in seconds) is provided, aborts with `IpcError::Timeout` on expiry.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn receive_result(&mut self, timeout: Option<f64>) -> Result<AgentResult, IpcError> {
        loop {
            let receive_fut = self.stream.receive::<AgentToPlatform>();
            let mut msg = match timeout {
                Some(secs) => {
                    tokio::pin!(receive_fut);
                    match tokio::time::timeout(
                        std::time::Duration::from_secs_f64(secs),
                        &mut receive_fut,
                    )
                    .await
                    {
                        Ok(result) => result?,
                        Err(_) => return Err(IpcError::Timeout { timeout: secs }),
                    }
                }
                None => receive_fut.await?,
            };
            let success = msg.is_success();
            let task_id = msg.task_id.take();
            match msg.msg_type {
                AgentToPlatformType::Result => return Ok(AgentResult {
                    content: msg.content,
                    success,
                    task_id,
                }),
                AgentToPlatformType::Error => return Err(IpcError::Io(std::io::Error::other(
                    format!(
                        "Agent error: {}",
                        msg.error.as_deref().unwrap_or("unknown")
                    ),
                ))),
                AgentToPlatformType::Progress => {
                    // Progress messages are informational; skip and wait for the final result.
                    // Returning progress as a result was a bug — callers cannot distinguish
                    // progress from completion, and the real result stays in the buffer
                    // corrupting subsequent IPC rounds.
                    tracing::debug!("Skipping progress message, waiting for final result");
                    continue;
                }
            }
        }
    }

    /// Decompose the protocol back into the underlying reader and writer.
    ///
    /// Use after a complete IPC round to recover the IO handles for return
    /// to the ProcessManager via `return_io`.
    pub fn into_parts(self) -> (R, W) {
        self.stream.into_parts()
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

    #[tokio::test]
    async fn send_chat_and_receive_result() {
        let (client, server) = tokio::io::duplex(4096);
        let (sr, sw) = tokio::io::split(server);
        let (cr, cw) = tokio::io::split(client);

        let mut proto = IpcProtocol::new(cr, cw);

        // Server side: read chat, send result
        tokio::spawn(async move {
            let mut line = String::new();
            let mut reader = BufReader::new(sr);
            reader.read_line(&mut line).await.unwrap();
            let response = r#"{"type":"result","content":"done","task_id":null,"message":null,"progress_pct":null,"error":null,"status":"completed","output":null}"#;
            let mut writer = sw;
            writer.write_all(response.as_bytes()).await.unwrap();
            writer.write_all(b"\n").await.unwrap();
            writer.flush().await.unwrap();
        });

        proto.send_chat("hello", None).await.unwrap();
        let result = proto.receive_result(None).await.unwrap();
        assert_eq!(result.content, "done");
        assert!(result.success);
    }
}
