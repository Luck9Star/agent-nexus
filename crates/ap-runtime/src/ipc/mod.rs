//! IPC layer for agent communication.
//!
//! - `stream` — AgentIpcStream: wraps ap-core's IpcStream with heartbeat + typed send/receive
//! - `protocol` — AgentProtocol: high-level operations (send_chat, send_task, heartbeat)

pub mod protocol;
pub mod stream;

pub use protocol::{AgentProtocol, AgentResult};
pub use stream::AgentIpcStream;
