//! Shared data models for the Agent Nexus Platform.

pub mod agent;
pub mod common;
pub mod composition;
pub mod config;
pub mod context;
pub mod distribution;
pub mod evolution;
pub mod hooks;
pub mod ipc;
pub mod permission;
pub mod runtime;
pub mod task;

// Re-export key types for convenience
pub use agent::{AgentManifest, AgentType, RunMode, AgentRole, ModelTier, SkillDefinition, CommandDef, AgentDefinition, AgentPackage};
pub use config::{PlatformConfig, ModelConfig, RuntimeConfig, ProviderConfig};
pub use context::{ContextBudget, ContextLevel, BudgetAlertLevel, TokenUsage};
pub use distribution::{SourceType, InstallationStatus, SourceEntry, LockfileEntry, Lockfile, PackageSource, IndexEntry};
pub use evolution::{SkillRecord, EvolutionType, SkillOrigin, SkillLineage, EvolutionMetrics, EvolutionContext};
pub use hooks::{HookType, HookEvent, HookDefinition, HookExecution};
pub use ipc::{PlatformToAgent, AgentToPlatform, IPCMessage, IpcPayload, MessageDirection};
pub use permission::{PermissionConfig, PermissionMode, PermissionDecision};
pub use task::{TaskItem, TaskState, TaskGraphSnapshot};
pub use composition::{Composition, CompositionTask, WorkflowPhaseEntry, WorkflowResult, WorkflowContext};
