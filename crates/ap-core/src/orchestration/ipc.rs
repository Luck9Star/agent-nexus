//! `IpcStream`: JSON-lines framing over async stdin/stdout with size limits.
//!
//! Python source: `src/agent_nexus/platform/orchestration/ipc.py` (446 lines)

use serde::de::DeserializeOwned;
use serde::Serialize;
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader};

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
// Size-limited writer for early oversized-message rejection
// ---------------------------------------------------------------------------

/// A `std::io::Write` wrapper that aborts once the written bytes exceed a limit.
/// This prevents `serde_json::to_writer` from allocating the full message before
/// we can reject it as oversized.
struct LimitedWriter {
    buf: Vec<u8>,
    limit: usize,
    written: usize,
    exceeded: bool,
}

impl LimitedWriter {
    fn new(limit: usize) -> Self {
        Self {
            buf: Vec::with_capacity(4096),
            limit,
            written: 0,
            exceeded: false,
        }
    }
    fn into_bytes(self) -> Vec<u8> {
        self.buf
    }
}

impl std::io::Write for LimitedWriter {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        if self.written + buf.len() > self.limit {
            self.exceeded = true;
            return Err(std::io::Error::other("message exceeds size limit"));
        }
        self.buf.extend_from_slice(buf);
        self.written += buf.len();
        Ok(buf.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

#[cfg(test)]
mod limited_writer_tests {
    use std::io::Write;
    use super::*;

    #[test]
    fn writer_allows_within_limit() {
        let mut w = LimitedWriter::new(100);
        assert!(w.write_all(b"hello").is_ok());
        let bytes = w.into_bytes();
        assert_eq!(bytes, b"hello");
    }

    #[test]
    fn writer_rejects_over_limit() {
        let mut w = LimitedWriter::new(10);
        let result = w.write_all(b"this is way more than ten bytes");
        assert!(result.is_err());
        assert!(w.exceeded);
        // Partial data should not be in the buffer — write_all failed before extending
        assert!(w.buf.len() <= 10);
    }

    #[test]
    fn writer_exact_limit_passes() {
        let data = vec![0xABu8; 64];
        let mut w = LimitedWriter::new(64);
        assert!(w.write_all(&data).is_ok());
        assert_eq!(w.into_bytes().len(), 64);
    }

    #[test]
    fn writer_one_over_limit_rejected() {
        let data = vec![0xABu8; 65];
        let mut w = LimitedWriter::new(64);
        assert!(w.write_all(&data).is_err());
        assert!(w.exceeded);
    }
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
    /// Uses a size-limited writer to reject oversized messages without allocating
    /// the full serialized buffer first.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub async fn send<T: Serialize>(&mut self, msg: &T) -> Result<(), IpcError> {
        let mut writer = LimitedWriter::new(MAX_MESSAGE_SIZE);
        serde_json::to_writer(&mut writer, msg).map_err(|e| {
            if writer.exceeded {
                IpcError::Oversized {
                    size: writer.written,
                    max: MAX_MESSAGE_SIZE,
                }
            } else {
                IpcError::from(e)
            }
        })?;
        let json = writer.into_bytes();
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
        let mut line = Vec::with_capacity(4096);
        let n = (&mut self.reader).take(MAX_MESSAGE_SIZE as u64).read_until(b'\n', &mut line).await?;
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

    /// Decompose the stream back into the underlying reader and writer.
    ///
    /// Call this after a complete IPC round to recover ownership of the IO
    /// handles. Any data buffered in the reader's internal buffer is discarded
    /// — this is safe after a complete `receive()` call which reads exactly one
    /// JSON-line message.
    pub fn into_parts(self) -> (R, W) {
        (self.reader.into_inner(), self.writer)
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
