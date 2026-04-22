//! Orchestration layer: TaskGraph, ProcessManager, IPC, DSL.
//!
//! This module implements the self-built orchestration core:
//! - `TaskGraph` — SQLite-backed DAG of tasks with cycle detection
//! - `ProcessManager` — async process spawning via tokio
//! - `IpcStream` / `IpcProtocol` — JSON-lines framing over stdin/stdout
//! - `IpcLockRegistry` — per-agent Mutex with FIFO eviction
//! - `OrchestrationDsl` — TOML DAG parser

pub mod dsl;
pub mod ipc;
pub mod ipc_lock;
pub mod ipc_protocol;
pub mod process_manager;
pub mod task_graph;

pub use dsl::{DslError, DslTask, OrchestrationDsl};
pub use ipc::{IpcError, IpcStream};
pub use ipc_lock::IpcLockRegistry;
pub use ipc_protocol::{AgentResult, IpcProtocol};
pub use process_manager::{ManagedProcess, ProcessError, ProcessManager};
pub use task_graph::{TaskGraph, TaskGraphError};
