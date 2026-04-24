//! ap-runtime — Python subprocess bridge: MCP client + raw IPC protocol.
//!
//! This crate provides:
//! - `process` — `AgentProcess`: subprocess lifecycle management with piped I/O
//! - `ipc` — `AgentIpcStream` + `AgentProtocol`: typed IPC over JSON-lines
//! - `lock` — `LockRegistry`: per-agent Mutex with FIFO eviction
//! - `mcp_client` — `McpClient` trait + `NoopMcpClient` stub

pub mod ipc;
pub mod lock;
pub mod mcp_client;
pub mod process;

pub use ipc::{AgentIpcStream, AgentProtocol, AgentResult};
pub use lock::LockRegistry;
pub use mcp_client::{McpClient, McpError, NoopMcpClient, ToolInfo};
pub use process::{AgentProcess, DetachedProcess, ProcessError};
