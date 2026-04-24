//! ap-runtime — Python subprocess bridge: MCP client + raw IPC protocol.
//!
//! This crate provides a unified API surface for agent process management:
//! - `process` — `AgentProcess`: subprocess lifecycle management with piped I/O
//! - `ipc` — `AgentIpcStream` + `AgentProtocol`: typed IPC over JSON-lines
//! - `lock` — `LockRegistry`: per-agent Mutex with FIFO eviction
//! - `mcp_client` — `McpClient` trait + `NoopMcpClient` stub
//!
//! ## Facade Pattern
//!
//! Some types are re-exported from `ap-core` for convenience. Consumers of `ap-runtime` should
//! not need to depend on `ap-core` directly for runtime operations. This crate serves as the
//! stable interface boundary between agent process concerns and the rest of the platform.

pub mod ipc;
pub mod lock;
pub mod mcp_client;
pub mod process;

pub use ipc::{AgentIpcStream, AgentProtocol, AgentResult};
pub use lock::LockRegistry;
pub use mcp_client::{McpClient, McpError, NoopMcpClient, ToolInfo};
pub use process::{AgentProcess, DetachedProcess, ProcessError};
