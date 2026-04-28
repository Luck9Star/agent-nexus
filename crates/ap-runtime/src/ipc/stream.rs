//! `AgentIpcStream`: thin wrapper around ap-core's `IpcStream`.
//!
//! This is the **wire layer** — it only provides raw send/receive over
//! JSON-lines. Typed protocol methods (`send_chat`, `send_task`,
//! `receive_result`) live in ap-core's `IpcProtocol`. The heartbeat
//! convenience is in this crate's `AgentProtocol`.
//!
//! Layer hierarchy:
//!   `IpcStream` (wire) → `IpcProtocol` (typed) → `AgentProtocol` (heartbeat)
//!
//! # Message size limits
//!
//! The underlying `ap_core::orchestration::ipc::IpcStream` enforces a 4 MiB
//! message size limit (`MAX_MESSAGE_SIZE`) on both send and receive paths:
//!
//! - **Send**: Uses a `LimitedWriter` that aborts `serde_json` serialization
//!   as soon as the serialized size exceeds the limit, returning
//!   `IpcError::Oversized` without allocating the full buffer.
//! - **Receive**: Uses `tokio::io::AsyncReadExt::take()` to cap the read at
//!   the limit, then checks the resulting length before deserialization.
//!   Oversized messages return `IpcError::Oversized`.
//!
//! No silent truncation can occur — every oversized message produces an error.
//! No additional size checks are needed in this wrapper.

use ap_core::orchestration::ipc::{IpcError, IpcStream};
use serde::de::DeserializeOwned;
use serde::Serialize;
use tokio::io::{AsyncRead, AsyncWrite};

// ---------------------------------------------------------------------------
// AgentIpcStream
// ---------------------------------------------------------------------------

/// Thin wrapper around ap-core's [`IpcStream`].
///
/// Delegates all wire-level operations. Typed protocol methods are in
/// ap-core's [`IpcProtocol`] (re-exported via this crate's [`AgentProtocol`]).
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

    /// Convert into the underlying `IpcStream`.
    /// Useful for constructing an `IpcProtocol` from this stream.
    pub fn into_inner(self) -> IpcStream<R, W> {
        self.inner
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
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use ap_core::models::ipc::{AgentToPlatform, AgentToPlatformType, PlatformToAgent, PlatformToAgentType};
    use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

    #[tokio::test]
    async fn send_and_receive_roundtrip() {
        let (client, server) = tokio::io::duplex(4096);
        let (sr, sw) = tokio::io::split(server);
        let (cr, cw) = tokio::io::split(client);

        let mut stream = AgentIpcStream::new(cr, cw);

        // Server side: read a message, verify it, send a response
        tokio::spawn(async move {
            let mut line = String::new();
            let mut reader = BufReader::new(sr);
            reader.read_line(&mut line).await.unwrap();
            assert!(line.contains(r#""type":"chat"#));

            let response =
                r#"{"type":"result","content":"ok","task_id":null,"message":null,"progress_pct":null,"error":null,"status":"completed","output":null}"#;
            let mut writer = sw;
            writer.write_all(response.as_bytes()).await.unwrap();
            writer.write_all(b"\n").await.unwrap();
            writer.flush().await.unwrap();
        });

        stream.send(&PlatformToAgent {
            msg_type: PlatformToAgentType::Chat,
            content: "hello agent".to_string(),
            conversation_id: Some("conv-1".to_string()),
            task_id: None,
            ref_id: None,
            summary: None,
        }).await.unwrap();

        let resp: AgentToPlatform = stream.receive().await.unwrap();
        assert_eq!(resp.content, "ok");
        assert_eq!(resp.msg_type, AgentToPlatformType::Result);
        assert!(resp.is_success());
    }

    #[tokio::test]
    async fn into_inner_preserves_stream() {
        let (client, server) = tokio::io::duplex(4096);
        let (cr, cw) = tokio::io::split(client);
        let (_sr, _sw) = tokio::io::split(server);

        let stream = AgentIpcStream::new(cr, cw);
        let _inner: IpcStream<_, _> = stream.into_inner();
    }
}
