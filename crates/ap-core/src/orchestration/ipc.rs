//! `IpcStream`: JSON-lines framing over async stdin/stdout with size limits.
//!
//! Python source: `src/agent_nexus/platform/orchestration/ipc.py` (446 lines)

use serde::de::DeserializeOwned;
use serde::Serialize;
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};

const MAX_MESSAGE_SIZE: usize = 4 * 1024 * 1024; // 4MB

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

#[derive(Debug, thiserror::Error)]
pub enum IpcError {
    #[error("Connection closed (EOF)")]
    ConnectionClosed,
    #[error("Timed out after {timeout:.1}s")]
    Timeout { timeout: f64 },
    #[error("Message too large: {size} bytes (max {max})")]
    Oversized { size: usize, max: usize },
    #[error("Invalid JSON: {0}")]
    InvalidJson(#[from] serde_json::Error),
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
}

// ---------------------------------------------------------------------------
// IpcStream
// ---------------------------------------------------------------------------

pub struct IpcStream<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> {
    reader: BufReader<R>,
    writer: W,
}

impl<R: AsyncRead + Unpin, W: AsyncWrite + Unpin> IpcStream<R, W> {
    pub fn new(reader: R, writer: W) -> Self {
        Self {
            reader: BufReader::new(reader),
            writer,
        }
    }

    /// Serialize and send a message as a JSON-line.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn send<T: Serialize>(&mut self, msg: &T) -> Result<(), IpcError> {
        let json = serde_json::to_vec(msg)?;
        if json.len() > MAX_MESSAGE_SIZE {
            return Err(IpcError::Oversized {
                size: json.len(),
                max: MAX_MESSAGE_SIZE,
            });
        }
        self.writer.write_all(&json).await?;
        self.writer.write_all(b"\n").await?;
        self.writer.flush().await?;
        Ok(())
    }

    /// Receive and deserialize a JSON-line message.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn receive<T: DeserializeOwned>(&mut self) -> Result<T, IpcError> {
        let mut line = Vec::new();
        let n = self.reader.read_until(b'\n', &mut line).await?;
        if n == 0 {
            return Err(IpcError::ConnectionClosed);
        }
        if line.len() > MAX_MESSAGE_SIZE {
            return Err(IpcError::Oversized {
                size: line.len(),
                max: MAX_MESSAGE_SIZE,
            });
        }
        // Strip trailing \n or \r\n that read_until includes
        if line.last() == Some(&b'\n') {
            line.pop();
            if line.last() == Some(&b'\r') {
                line.pop();
            }
        }
        let msg: T = serde_json::from_slice(&line)?;
        Ok(msg)
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::ipc::PlatformToAgent;
    use crate::models::ipc::PlatformToAgentType;
    use tokio::io::duplex;

    #[tokio::test]
    async fn send_and_receive_message() {
        let (client, server) = duplex(4096);
        let (read, write) = tokio::io::split(server);
        let (cread, cwrite) = tokio::io::split(client);

        let mut ipc_server = IpcStream::new(read, write);
        let mut ipc_client = IpcStream::new(cread, cwrite);

        let msg = PlatformToAgent {
            msg_type: PlatformToAgentType::Chat,
            content: "hello".into(),
            conversation_id: None,
            task_id: None,
            ref_id: None,
            summary: None,
        };

        tokio::spawn(async move {
            ipc_client.send(&msg).await.unwrap();
        });

        let received = ipc_server.receive::<PlatformToAgent>().await.unwrap();
        assert_eq!(received.content, "hello");
        assert_eq!(received.msg_type, PlatformToAgentType::Chat);
    }

    #[tokio::test]
    async fn receive_handles_crlf() {
        // Verifies that receive() correctly strips \r\n before JSON parsing.
        let (client, server) = duplex(4096);
        let (read, _write) = tokio::io::split(server);
        let (_cread, mut cwrite) = tokio::io::split(client);

        // Send a JSON message with \r\n line ending
        tokio::spawn(async move {
            let msg = r#"{"type":"chat","content":"hello","conversation_id":null,"task_id":null,"ref_id":null,"summary":null}"#;
            cwrite.write_all(msg.as_bytes()).await.unwrap();
            cwrite.write_all(b"\r\n").await.unwrap();
            cwrite.flush().await.unwrap();
        });

        let mut ipc = IpcStream::new(read, tokio::io::sink());
        let received = ipc.receive::<PlatformToAgent>().await.unwrap();
        assert_eq!(received.content, "hello");
        assert_eq!(received.msg_type, PlatformToAgentType::Chat);
    }

    #[tokio::test]
    async fn oversized_message_rejected() {
        let (client, server) = duplex(1024 * 1024 * 8);
        let (_read, _write) = tokio::io::split(server);
        let (cread, cwrite) = tokio::io::split(client);

        let mut ipc = IpcStream::new(cread, cwrite);
        let big_content = "x".repeat(5 * 1024 * 1024); // 5MB, exceeds 4MB limit
        let msg = PlatformToAgent {
            msg_type: PlatformToAgentType::Chat,
            content: big_content,
            conversation_id: None,
            task_id: None,
            ref_id: None,
            summary: None,
        };
        let result = ipc.send(&msg).await;
        assert!(result.is_err());
    }
}
