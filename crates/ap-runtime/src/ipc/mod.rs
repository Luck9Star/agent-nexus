//! IPC layer for agent communication.
//!
//! - `stream` — `AgentIpcStream`: thin wire-level wrapper around ap-core's `IpcStream`
//! - `protocol` — `AgentProtocol`: high-level operations (delegates to `IpcProtocol` + heartbeat)

pub mod protocol;
pub mod stream;

pub use protocol::{AgentProtocol, AgentResult};
pub use stream::AgentIpcStream;
