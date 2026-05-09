//! Orchestration layer: `TaskGraph`, `ProcessManager`, IPC, DSL.
//!
//! This module implements the self-built orchestration core:
//! - `TaskGraph` — SQLite-backed DAG of tasks with cycle detection
//! - `ProcessManager` — async process spawning via tokio
//! - `IpcStream` / `IpcProtocol` — JSON-lines framing over stdin/stdout
//! - `IpcLockRegistry` — per-agent Mutex with FIFO eviction
//! - `OrchestrationDsl` — TOML DAG parser
//! - `SubtaskController` — timeout, retry, bounded parallelism
//! - `WorkflowPhase` — 4-phase composite agent orchestration types
//! - `Mailbox` — cross-agent data reference passing

pub mod dsl;
pub mod ipc;
pub mod ipc_lock;
pub mod ipc_protocol;
pub mod mailbox;
pub mod process_manager;
pub mod subtask;
pub mod task_graph;
pub mod workflow;

pub use dsl::{DslError, DslTask, OrchestrationDsl};
pub use ipc::{IpcError, IpcStream};
pub use ipc_lock::IpcLockRegistry;
pub use mailbox::{DataRef, Mailbox, MailboxError, MailboxMessage};
pub use ipc_protocol::{AgentResult, IpcProtocol};
pub use process_manager::{HandleError, ManagedProcess, ProcessError, ProcessManager, ProcessManagerHandle, SpawnConfig};
pub use subtask::{SubtaskConfig, SubtaskController, SubtaskError};
pub use task_graph::{TaskGraph, TaskGraphError};
pub mod router;

pub use router::{CompositeDefinition, PlatformRouter, RouterError};
pub use workflow::{CompositeWorkflowResult, PhaseResult, WorkflowPhase};
