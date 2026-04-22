# Phase 1: ap-core/models — Shared Data Models

> **Goal:** Port all 13 Python Pydantic models to Rust structs with serde. These are the foundation for every other crate.

**Python source:** `src/agent_nexus/models/*.py` (1,577 lines)
**Rust target:** `crates/ap-core/src/models/*.rs`

**Files:**
- Create: `crates/ap-core/src/models/mod.rs`
- Create: `crates/ap-core/src/models/ipc.rs`
- Create: `crates/ap-core/src/models/agent.rs`
- Create: `crates/ap-core/src/models/task.rs`
- Create: `crates/ap-core/src/models/config.rs`
- Create: `crates/ap-core/src/models/permission.rs`
- Create: `crates/ap-core/src/models/evolution.rs`
- Create: `crates/ap-core/src/models/runtime.rs`
- Create: `crates/ap-core/src/models/hooks.rs`
- Create: `crates/ap-core/src/models/composition.rs`
- Create: `crates/ap-core/src/models/context.rs`
- Test: `crates/ap-core/tests/models_tests.rs`

---

## Task 1.1: IPC Models

**Python source:** `models/ipc.py`
**Files:**
- Create: `crates/ap-core/src/models/ipc.rs`
- Test: inline `#[cfg(test)]` module

> **Wire-format compatibility note (F-01 fix):**
> Python uses **flat structs** with a `type` discriminator field, NOT tagged enums.
> Both `PlatformToAgent` and `AgentToPlatform` are single classes with ALL optional fields.
> Using `#[serde(tag = "type")] enum` would produce a different JSON shape and break
> serialization with Python agents. We must use flat structs.

- [ ] **Step 1: Write the IPC model test**

```rust
// crates/ap-core/src/models/ipc.rs (top of file, test at bottom)

//! IPC message models: Platform <-> Agent communication via stdin/stdout JSON-lines.
//!
//! Wire format: FLAT STRUCTS with `type` discriminator.
//! Python uses single classes with all optional fields, not tagged unions.
//! See models/ipc.py for the source of truth.

use serde::{Deserialize, Serialize};

// ── Direction ──────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MessageDirection {
    PlatformToAgent,
    AgentToPlatform,
}

// ── Type discriminators ────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PlatformToAgentType {
    Chat,
    Task,
    DataReference,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AgentToPlatformType {
    Result,
    Progress,
    Error,
}

// ── Flat message structs (matches Python exactly) ──────────────────

/// Message from Platform Router to Agent subprocess (stdin).
///
/// Python source: models/ipc.py:37-51
/// Wire examples:
///   Chat:  {"type":"chat","content":"...","conversation_id":"..."}
///   Task:  {"type":"task","content":"...","task_id":"..."}
///   Data:  {"type":"data_reference","ref_id":"var://...","summary":"..."}
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PlatformToAgent {
    #[serde(rename = "type")]
    pub msg_type: PlatformToAgentType,
    #[serde(default)]
    pub content: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub conversation_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ref_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub summary: Option<String>,
}

/// Message from Agent subprocess to Platform Router (stdout).
///
/// Python source: models/ipc.py:54-95
/// Wire examples:
///   Result:   {"type":"result","task_id":"...","output":"...","status":"completed"}
///   Progress: {"type":"progress","task_id":"...","message":"...","progress_pct":50.0}
///   Error:    {"type":"error","task_id":"...","error":"..."}
///
/// NOTE: `is_success` is a computed property in Python, not a wire field.
/// NOTE: All fields are optional; `type` is the only required discriminator.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AgentToPlatform {
    #[serde(rename = "type")]
    pub msg_type: AgentToPlatformType,
    #[serde(default)]
    pub content: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub progress_pct: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output: Option<serde_json::Value>,
}

impl AgentToPlatform {
    /// Check if this response indicates successful completion.
    /// Mirrors Python's `is_success` computed property.
    pub fn is_success(&self) -> bool {
        if self.msg_type == AgentToPlatformType::Error {
            return false;
        }
        self.status.as_ref().map_or(true, |s| s.to_lowercase() == "completed")
    }
}

// ── Envelope ───────────────────────────────────────────────────────

/// Envelope for any IPC message, with direction tagging.
///
/// Python source: models/ipc.py:98-125
/// Used for deserialization of raw JSON-lines from stdin/stdout pipes.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct IPCMessage {
    pub direction: MessageDirection,
    pub payload: serde_json::Value,
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── PlatformToAgent roundtrips ─────────────────────────────────

    #[test]
    fn roundtrip_chat_message() {
        let msg = PlatformToAgent {
            msg_type: PlatformToAgentType::Chat,
            content: "hello agent".to_string(),
            task_id: None,
            conversation_id: Some("conv-123".to_string()),
            ref_id: None,
            summary: None,
        };
        let json = serde_json::to_string(&msg).unwrap();
        // Must produce: {"type":"chat","content":"hello agent","conversation_id":"conv-123"}
        assert!(json.contains(r#""type":"chat""#));
        let de: PlatformToAgent = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, de);
    }

    #[test]
    fn roundtrip_task_message() {
        let msg = PlatformToAgent {
            msg_type: PlatformToAgentType::Task,
            content: "do something".to_string(),
            task_id: Some("t-1".to_string()),
            conversation_id: None,
            ref_id: None,
            summary: None,
        };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""type":"task""#));
        let de: PlatformToAgent = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, de);
    }

    #[test]
    fn roundtrip_data_reference_message() {
        let msg = PlatformToAgent {
            msg_type: PlatformToAgentType::DataReference,
            content: String::new(),
            task_id: None,
            conversation_id: None,
            ref_id: Some("var://x".to_string()),
            summary: Some("variable x".to_string()),
        };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""type":"data_reference""#));
        let de: PlatformToAgent = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, de);
    }

    // ── AgentToPlatform roundtrips ─────────────────────────────────

    #[test]
    fn roundtrip_result_message() {
        let msg = AgentToPlatform {
            msg_type: AgentToPlatformType::Result,
            content: "done".to_string(),
            task_id: Some("t-1".to_string()),
            message: None,
            progress_pct: None,
            error: None,
            status: Some("completed".to_string()),
            output: Some(serde_json::json!("result text")),
        };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""type":"result""#));
        assert!(!json.contains("success")); // no fabricated field
        let de: AgentToPlatform = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, de);
    }

    #[test]
    fn roundtrip_progress_message() {
        let msg = AgentToPlatform {
            msg_type: AgentToPlatformType::Progress,
            content: String::new(),
            task_id: Some("t-1".to_string()),
            message: Some("halfway".to_string()),
            progress_pct: Some(50.0),
            error: None,
            status: None,
            output: None,
        };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""type":"progress""#));
        let de: AgentToPlatform = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, de);
    }

    #[test]
    fn roundtrip_error_message() {
        let msg = AgentToPlatform {
            msg_type: AgentToPlatformType::Error,
            content: String::new(),
            task_id: Some("t-1".to_string()),
            message: None,
            progress_pct: None,
            error: Some("something broke".to_string()),
            status: None,
            output: None,
        };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""type":"error""#));
        assert!(!json.contains("error_type")); // no fabricated field
        let de: AgentToPlatform = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, de);
    }

    // ── Python wire-format compatibility ───────────────────────────

    #[test]
    fn deserialize_python_chat() {
        // Python sends: {"type":"chat","content":"hello","conversation_id":"c1"}
        let json = r#"{"type":"chat","content":"hello","conversation_id":"c1"}"#;
        let msg: PlatformToAgent = serde_json::from_str(json).unwrap();
        assert_eq!(msg.msg_type, PlatformToAgentType::Chat);
        assert_eq!(msg.content, "hello");
        assert_eq!(msg.conversation_id.as_deref(), Some("c1"));
    }

    #[test]
    fn deserialize_python_task() {
        // Python sends: {"type":"task","content":"...","task_id":"t1"}
        let json = r#"{"type":"task","content":"review code","task_id":"t-1"}"#;
        let msg: PlatformToAgent = serde_json::from_str(json).unwrap();
        assert_eq!(msg.msg_type, PlatformToAgentType::Task);
        assert_eq!(msg.task_id.as_deref(), Some("t-1"));
    }

    #[test]
    fn deserialize_python_result() {
        // Python sends: {"type":"result","task_id":"t1","output":"ok","status":"completed"}
        let json = r#"{"type":"result","content":"","task_id":"t-1","output":"ok","status":"completed"}"#;
        let msg: AgentToPlatform = serde_json::from_str(json).unwrap();
        assert_eq!(msg.msg_type, AgentToPlatformType::Result);
        assert_eq!(msg.status.as_deref(), Some("completed"));
        assert!(msg.is_success());
    }

    #[test]
    fn deserialize_python_error() {
        // Python sends: {"type":"error","task_id":"t1","error":"ImportError: ..."}
        let json = r#"{"type":"error","content":"","task_id":"t-1","error":"ImportError: module not found"}"#;
        let msg: AgentToPlatform = serde_json::from_str(json).unwrap();
        assert_eq!(msg.msg_type, AgentToPlatformType::Error);
        assert!(!msg.is_success());
    }

    #[test]
    fn deserialize_python_progress() {
        // Python sends: {"type":"progress","task_id":"t1","message":"50% done","progress_pct":50.0}
        let json = r#"{"type":"progress","content":"","task_id":"t-1","message":"50% done","progress_pct":50.0}"#;
        let msg: AgentToPlatform = serde_json::from_str(json).unwrap();
        assert_eq!(msg.msg_type, AgentToPlatformType::Progress);
        assert_eq!(msg.progress_pct, Some(50.0));
        assert_eq!(msg.message.as_deref(), Some("50% done"));
    }

    #[test]
    fn is_success_returns_false_for_error() {
        let msg = AgentToPlatform {
            msg_type: AgentToPlatformType::Error,
            content: String::new(),
            task_id: None,
            message: None,
            progress_pct: None,
            error: Some("fail".to_string()),
            status: None,
            output: None,
        };
        assert!(!msg.is_success());
    }

    #[test]
    fn is_success_returns_true_when_status_completed() {
        let msg = AgentToPlatform {
            msg_type: AgentToPlatformType::Result,
            content: String::new(),
            task_id: None,
            message: None,
            progress_pct: None,
            error: None,
            status: Some("completed".to_string()),
            output: None,
        };
        assert!(msg.is_success());
    }
}
```

- [ ] **Step 2: Run test to verify it compiles**

Run: `cargo test -p ap-core -- models::ipc`
Expected: PASS (all 14 tests)

- [ ] **Step 3: Commit**

```bash
git add crates/ap-core/src/models/
git commit -m "feat(ap-core): add IPC flat-struct models matching Python wire format"
```

---

## Task 1.2: Agent Models

**Files:**
- Create: `crates/ap-core/src/models/agent.rs`

- [ ] **Step 1: Write agent model structs**

```rust
// crates/ap-core/src/models/agent.rs

//! Agent system models: AgentManifest, AgentType, RunMode, AgentRole, ModelTier.

use serde::{Deserialize, Serialize};

use super::hooks::HookDefinition;
use super::permission::{PermissionConfig, PermissionMode};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AgentType {
    Atomic,
    Composite,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunMode {
    Mcp,
    Local,
    Cli,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AgentRole {
    Explore,
    Plan,
    Worker,
    Verification,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelTier {
    Lightweight,
    Standard,
    Powerful,
    Premium,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AgentModelConfig {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recommended: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fallback: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct McpServerConfig {
    #[serde(default = "default_stdio")]
    pub transport: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub command: Option<String>,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
}

fn default_stdio() -> String {
    "stdio".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct AgentDependencies {
    #[serde(default)]
    pub atomic_agents: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AgentManifest {
    pub name: String,
    pub version: String,
    #[serde(rename = "type")]
    pub agent_type: AgentType,
    pub description: String,
    #[serde(default)]
    pub capabilities: Vec<String>,
    #[serde(alias = "model_config", skip_serializing_if = "Option::is_none")]
    pub model_preferences: Option<AgentModelConfig>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub role: Option<AgentRole>,
    #[serde(default)]
    pub dependencies: AgentDependencies,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub permissions: Option<PermissionConfig>,
    #[serde(default)]
    pub tools: Vec<String>,
    #[serde(default)]
    pub denied_tools: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub permission_mode: Option<PermissionMode>,
    #[serde(default)]
    pub skills: Vec<String>,
    #[serde(default)]
    pub hooks: std::collections::HashMap<String, Vec<HookDefinition>>,
    #[serde(default)]
    pub mcp_servers: std::collections::HashMap<String, McpServerConfig>,
    #[serde(default)]
    pub pip_dependencies: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub effort: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_turns: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub memory_scope: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub isolation: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub color: Option<String>,
    #[serde(default)]
    pub background: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub initial_prompt: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_minimal_manifest() {
        let yaml = r#"
name: code-reviewer
type: atomic
version: "0.1.0"
description: Code review agent
"#;
        let manifest: AgentManifest = serde_yaml::from_str(yaml).unwrap();
        assert_eq!(manifest.name, "code-reviewer");
        assert_eq!(manifest.agent_type, AgentType::Atomic);
        assert!(manifest.capabilities.is_empty());
        assert!(manifest.tools.is_empty());
    }

    #[test]
    fn parse_full_manifest() {
        let yaml = r#"
name: feature-delivery
type: composite
version: "1.0.0"
description: Feature delivery pipeline
capabilities: [code-gen, testing]
model_config:
  recommended: standard
  fallback: lightweight
permissions:
  mode: default
  allowed_tools: [file_read, grep]
  denied_tools: [bash]
"#;
        let manifest: AgentManifest = serde_yaml::from_str(yaml).unwrap();
        assert_eq!(manifest.agent_type, AgentType::Composite);
        assert_eq!(manifest.capabilities.len(), 2);
        assert!(manifest.model_preferences.is_some());
        assert!(manifest.permissions.is_some());
    }

    #[test]
    fn manifest_uses_model_config_alias() {
        let yaml = r#"
name: test-agent
type: atomic
version: "0.1.0"
description: test
model_config:
  recommended: powerful
"#;
        let manifest: AgentManifest = serde_yaml::from_str(yaml).unwrap();
        let prefs = manifest.model_preferences.unwrap();
        assert_eq!(prefs.recommended.as_deref(), Some("powerful"));
    }
}
```

- [ ] **Step 2: Run tests**

Run: `cargo test -p ap-core -- models::agent`
Expected: PASS (3 tests)

- [ ] **Step 3: Commit**

```bash
git add crates/ap-core/src/models/agent.rs
git commit -m "feat(ap-core): add AgentManifest and related models with YAML parse tests"
```

---

## Task 1.3: Task Models

**Files:**
- Create: `crates/ap-core/src/models/task.rs`

- [ ] **Step 1: Write task model structs**

```rust
// crates/ap-core/src/models/task.rs

//! Task graph models: TaskItem, TaskState.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::models::common::utc_now;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TaskState {
    Pending,
    InProgress,
    Completed,
    Failed,
}

impl Default for TaskState {
    fn default() -> Self {
        Self::Pending
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TaskItem {
    pub id: String,
    pub description: String,
    pub agent: String,
    #[serde(default)]
    pub blocked_by: Vec<String>,
    #[serde(default)]
    pub vars: serde_json::Value,
    #[serde(default)]
    pub state: TaskState,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<serde_json::Value>,
    #[serde(default = "utc_now")]
    pub created_at: DateTime<Utc>,
    #[serde(default = "utc_now")]
    pub updated_at: DateTime<Utc>,
}

impl TaskItem {
    /// Validate that a task does not block itself.
    pub fn validate_no_self_reference(&self) -> Result<(), String> {
        if self.blocked_by.contains(&self.id) {
            return Err(format!("Task '{}' cannot block itself", self.id));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TaskGraphSnapshot {
    #[serde(default)]
    pub tasks: Vec<TaskItem>,
    #[serde(default)]
    pub parallel_groups: Vec<Vec<String>>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn task_default_state_is_pending() {
        let task = TaskItem {
            id: "t1".into(),
            description: "test".into(),
            agent: "agent1".into(),
            blocked_by: vec![],
            vars: serde_json::Value::Null,
            state: TaskState::default(),
            result: None,
            created_at: utc_now(),
            updated_at: utc_now(),
        };
        assert_eq!(task.state, TaskState::Pending);
    }

    #[test]
    fn task_self_reference_detected() {
        let task = TaskItem {
            id: "t1".into(),
            description: "test".into(),
            agent: "agent1".into(),
            blocked_by: vec!["t1".into()],
            vars: serde_json::Value::Null,
            state: TaskState::default(),
            result: None,
            created_at: utc_now(),
            updated_at: utc_now(),
        };
        assert!(task.validate_no_self_reference().is_err());
    }

    #[test]
    fn task_valid_no_self_block() {
        let task = TaskItem {
            id: "t1".into(),
            description: "test".into(),
            agent: "agent1".into(),
            blocked_by: vec!["t0".into()],
            vars: serde_json::Value::Null,
            state: TaskState::default(),
            result: None,
            created_at: utc_now(),
            updated_at: utc_now(),
        };
        assert!(task.validate_no_self_reference().is_ok());
    }

    #[test]
    fn roundtrip_task_state_json() {
        let states = vec![TaskState::Pending, TaskState::InProgress, TaskState::Completed, TaskState::Failed];
        for state in states {
            let json = serde_json::to_string(&state).unwrap();
            let de: TaskState = serde_json::from_str(&json).unwrap();
            assert_eq!(state, de);
        }
    }
}
```

- [ ] **Step 2: Run tests**

Run: `cargo test -p ap-core -- models::task`
Expected: PASS (4 tests)

- [ ] **Step 3: Commit**

```bash
git add crates/ap-core/src/models/task.rs
git commit -m "feat(ap-core): add TaskItem and TaskState models"
```

---

## Task 1.4: Remaining Models (config, permission, evolution, runtime, hooks, composition, context)

**Files:**
- Create: `crates/ap-core/src/models/config.rs`
- Create: `crates/ap-core/src/models/permission.rs`
- Create: `crates/ap-core/src/models/evolution.rs`
- Create: `crates/ap-core/src/models/runtime.rs`
- Create: `crates/ap-core/src/models/hooks.rs`
- Create: `crates/ap-core/src/models/composition.rs`
- Create: `crates/ap-core/src/models/context.rs`
- Create: `crates/ap-core/src/models/common.rs`
- Create: `crates/ap-core/src/models/mod.rs`

- [ ] **Step 1: Write common utilities**

```rust
// crates/ap-core/src/models/common.rs

//! Shared utilities for model definitions.

use chrono::{DateTime, Utc};

/// Returns the current UTC timestamp. Used as default for datetime fields.
pub fn utc_now() -> DateTime<Utc> {
    Utc::now()
}
```

- [ ] **Step 2: Write config models**

```rust
// crates/ap-core/src/models/config.rs

//! Configuration models: ProviderConfig, ModelConfig, RuntimeConfig, PlatformConfig.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ProviderApiType {
    OpenaiCompatible,
    AnthropicMessages,
    Ollama,
}

impl Default for ProviderApiType {
    fn default() -> Self {
        Self::OpenaiCompatible
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ProviderConfig {
    #[serde(default)]
    pub base_url: String,
    #[serde(default)]
    pub api_key_env: String,
    #[serde(default)]
    pub api: ProviderApiType,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ModelConfig {
    #[serde(default = "default_model")]
    pub default: String,
    #[serde(default)]
    pub providers: std::collections::HashMap<String, ProviderConfig>,
}

fn default_model() -> String {
    "openai:gpt-4o".to_string()
}

impl Default for ModelConfig {
    fn default() -> Self {
        Self {
            default: default_model(),
            providers: std::collections::HashMap::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RuntimeConfig {
    #[serde(default = "default_python")]
    pub python_path: String,
    #[serde(default = "default_uv")]
    pub uv_path: String,
}

fn default_python() -> String { "python3".to_string() }
fn default_uv() -> String { "uv".to_string() }

impl Default for RuntimeConfig {
    fn default() -> Self {
        Self {
            python_path: default_python(),
            uv_path: default_uv(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct PlatformConfig {
    #[serde(default)]
    pub runtime: RuntimeConfig,
    #[serde(default)]
    pub models: ModelConfig,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_config_toml() {
        let toml_str = r#"
[runtime]
python_path = "python3.12"

[models]
default = "deepseek:deepseek-chat"

[models.providers.deepseek]
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"
api = "openai-compatible"
"#;
        let config: PlatformConfig = toml::from_str(toml_str).unwrap();
        assert_eq!(config.runtime.python_path, "python3.12");
        assert_eq!(config.models.default, "deepseek:deepseek-chat");
        assert!(config.models.providers.contains_key("deepseek"));
    }

    #[test]
    fn default_config() {
        let config = PlatformConfig::default();
        assert_eq!(config.models.default, "openai:gpt-4o");
        assert_eq!(config.runtime.python_path, "python3");
    }
}
```

- [ ] **Step 3: Write permission models**

```rust
// crates/ap-core/src/models/permission.rs

//! Permission models: PermissionMode, PermissionConfig, PathRule, PermissionDecision.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PermissionMode {
    Default,
    Plan,
    FullAuto,
}

impl Default for PermissionMode {
    fn default() -> Self {
        Self::Default
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum PathAccess {
    Read,
    Write,
    ReadWrite,
    Deny,
}

impl Default for PathAccess {
    fn default() -> Self {
        Self::Read
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PathRule {
    pub pattern: String,
    #[serde(default)]
    pub access: PathAccess,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct PermissionConfig {
    #[serde(default)]
    pub mode: PermissionMode,
    #[serde(default)]
    pub allowed_tools: Vec<String>,
    #[serde(default)]
    pub denied_tools: Vec<String>,
    #[serde(default)]
    pub path_rules: Vec<PathRule>,
    #[serde(default)]
    pub denied_commands: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PermissionDecision {
    pub allowed: bool,
    #[serde(default)]
    pub reason: String,
    #[serde(default)]
    pub requires_confirmation: bool,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_permission_config() {
        let yaml = r#"
mode: default
allowed_tools: [file_read, grep]
denied_tools: [bash]
path_rules:
  - pattern: "*.docx"
    access: read-write
  - pattern: "*.env"
    access: deny
"#;
        let config: PermissionConfig = serde_yaml::from_str(yaml).unwrap();
        assert_eq!(config.mode, PermissionMode::Default);
        assert_eq!(config.allowed_tools.len(), 2);
        assert_eq!(config.denied_tools, vec!["bash"]);
        assert_eq!(config.path_rules.len(), 2);
        assert_eq!(config.path_rules[1].access, PathAccess::Deny);
    }
}
```

- [ ] **Step 4: Write evolution models**

```rust
// crates/ap-core/src/models/evolution.rs

//! Self-Evolution Engine models: SkillRecord, EvolutionType, SkillOrigin, SkillLineage.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::models::common::utc_now;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EvolutionType {
    Fix,
    Derived,
    Captured,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SkillOrigin {
    Imported,
    Captured,
    Derived,
    Fixed,
}

impl Default for SkillOrigin {
    fn default() -> Self {
        Self::Imported
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SkillLineage {
    #[serde(default)]
    pub origin: SkillOrigin,
    #[serde(default)]
    pub generation: u32,
    #[serde(default)]
    pub parent_skill_ids: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub content_diff: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub content_snapshot: Option<std::collections::HashMap<String, String>>,
}

impl Default for SkillLineage {
    fn default() -> Self {
        Self {
            origin: SkillOrigin::Imported,
            generation: 0,
            parent_skill_ids: Vec::new(),
            content_diff: None,
            content_snapshot: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SkillRecord {
    pub id: String,
    pub name: String,
    #[serde(default = "default_version")]
    pub version: String,
    #[serde(default)]
    pub lineage: SkillLineage,
    #[serde(default)]
    pub directory: String,
    #[serde(default = "default_true")]
    pub is_active: bool,
    #[serde(default)]
    pub total_selections: u64,
    #[serde(default)]
    pub total_applied: u64,
    #[serde(default)]
    pub total_completions: u64,
    #[serde(default)]
    pub total_fallbacks: u64,
    #[serde(default = "utc_now")]
    pub first_seen: DateTime<Utc>,
    #[serde(default = "utc_now")]
    pub last_updated: DateTime<Utc>,
}

fn default_version() -> String { "1.0.0".to_string() }
fn default_true() -> bool { true }

impl SkillRecord {
    /// Validate counter invariants.
    /// Python source: models/evolution.py `_validate_counters` — 5 checks.
    pub fn validate_counters(&self) -> Result<(), String> {
        // Check 1: zero selections means zero applied and zero fallbacks
        if self.total_selections == 0 {
            if self.total_applied != 0 || self.total_fallbacks != 0 {
                return Err("zero selections requires zero applied and zero fallbacks".into());
            }
        }
        // Check 2: applied <= selections
        if self.total_applied > self.total_selections {
            return Err("total_applied cannot exceed total_selections".into());
        }
        // Check 3: completions <= applied
        if self.total_completions > self.total_applied {
            return Err("total_completions cannot exceed total_applied".into());
        }
        // Check 4: fallbacks <= applied
        if self.total_fallbacks > self.total_applied {
            return Err("total_fallbacks cannot exceed total_applied".into());
        }
        // Check 5 (F-13 fix): completions + fallbacks <= applied
        if self.total_completions + self.total_fallbacks > self.total_applied {
            return Err("total_completions + total_fallbacks cannot exceed total_applied".into());
        }
        Ok(())
    }
}

/// Standalone evolution metrics with same counter validators as SkillRecord.
///
/// Python source: models/evolution.py:97-123 `EvolutionMetrics`
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct EvolutionMetrics {
    #[serde(default)]
    pub total_selections: u64,
    #[serde(default)]
    pub total_applied: u64,
    #[serde(default)]
    pub total_completions: u64,
    #[serde(default)]
    pub total_fallbacks: u64,
}

impl EvolutionMetrics {
    pub fn validate(&self) -> Result<(), String> {
        SkillRecord::validate_counters_from_parts(
            self.total_selections, self.total_applied,
            self.total_completions, self.total_fallbacks,
        )
    }
}

/// Context passed to evolver with task/agent metadata.
///
/// Python source: models/evolution.py:126-141 `EvolutionContext`
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct EvolutionContext {
    #[serde(default)]
    pub agent_id: String,
    #[serde(default)]
    pub task_id: String,
    #[serde(default)]
    pub skill_ids_used: Vec<String>,
    #[serde(default)]
    pub task_description: String,
    #[serde(default)]
    pub task_result: Option<String>,
    #[serde(default)]
    pub error_info: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_record() -> SkillRecord {
        SkillRecord {
            id: "s1".into(),
            name: "test-skill".into(),
            version: default_version(),
            lineage: SkillLineage::default(),
            directory: String::new(),
            is_active: true,
            total_selections: 0,
            total_applied: 0,
            total_completions: 0,
            total_fallbacks: 0,
            first_seen: utc_now(),
            last_updated: utc_now(),
        }
    }

    #[test]
    fn valid_zero_counters() {
        assert!(make_record().validate_counters().is_ok());
    }

    #[test]
    fn invalid_applied_exceeds_selections() {
        let mut r = make_record();
        r.total_selections = 5;
        r.total_applied = 10;
        assert!(r.validate_counters().is_err());
    }

    #[test]
    fn valid_nonzero_counters() {
        let mut r = make_record();
        r.total_selections = 100;
        r.total_applied = 80;
        r.total_completions = 70;
        r.total_fallbacks = 10;
        assert!(r.validate_counters().is_ok());
    }
}
```

- [ ] **Step 5: Write runtime, hooks, composition, context models**

```rust
// crates/ap-core/src/models/runtime.rs

//! Python Runtime models: Variable, Function, RuntimeType, ExecutionResult, SecurityViolation.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Variable {
    pub name: String,
    /// Current value — can be any JSON-serializable type. Python: `value: Any`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub value: Option<serde_json::Value>,
    #[serde(default)]
    pub description: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub type_name: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Function {
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub signature: Option<String>,
    #[serde(default)]
    pub is_async: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RuntimeType {
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub python_type: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub json_schema: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ExecutionResult {
    pub success: bool,
    #[serde(default)]
    pub output: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(default)]
    pub variables_created: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SecurityViolation {
    pub rule_type: String,
    pub node_type: String,
    #[serde(default)]
    pub code_snippet: String,
    #[serde(default)]
    pub message: String,
}
```

```rust
// crates/ap-core/src/models/hooks.rs

//! Hook system models: HookType, HookEvent, HookDefinition, HookExecution.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::models::common::utc_now;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HookType {
    Command,
    Http,
    Prompt,
    Agent,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HookEvent {
    PreExecution,
    PostExecution,
    PreToolUse,
    PostToolUse,
    OnError,
    OnEvolution,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct HookDefinition {
    #[serde(rename = "type")]
    pub hook_type: HookType,
    pub event: HookEvent,
    #[serde(default)]
    pub config: serde_json::Value,
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default)]
    pub block_on_failure: bool,
    #[serde(default = "default_hook_timeout")]
    pub timeout_seconds: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub matcher: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub command: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub prompt: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
}

fn default_true() -> bool { true }
fn default_hook_timeout() -> f64 { 10.0 }

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct HookExecution {
    pub hook: HookDefinition,
    pub passed: bool,
    #[serde(default)]
    pub blocked: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_type: Option<String>,
    #[serde(default)]
    pub duration_ms: f64,
    #[serde(default = "utc_now")]
    pub executed_at: DateTime<Utc>,
}

impl HookExecution {
    /// Validate: cannot be both passed and blocked.
    /// Python source: models/hooks.py `_validate_passed_blocked`
    pub fn validate(&self) -> Result<(), String> {
        if self.passed && self.blocked {
            return Err("HookExecution cannot be both passed and blocked".into());
        }
        Ok(())
    }
}

/// Aggregated result of all hook executions for a single event.
///
/// Python source: models/hooks.py `AggregatedHookResult`
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct AggregatedHookResult {
    #[serde(default)]
    pub results: Vec<HookExecution>,
    pub should_block: bool,
    #[serde(default)]
    pub outputs: Vec<String>,
}
```

```rust
// crates/ap-core/src/models/composition.rs

//! Composition models: CompositionTask, Composition.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CompositionTask {
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub agent: String,
    #[serde(default)]
    pub blocked_by: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Composition {
    pub name: String,
    #[serde(default)]
    pub description: String,
    pub tasks: std::collections::HashMap<String, CompositionTask>,
}

/// F-09 fix: Renamed from `WorkflowPhase` to `WorkflowPhaseEntry` to avoid
/// collision with the router's `WorkflowPhase` enum in Phase 04.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct WorkflowPhaseEntry {
    pub phase: String,
    #[serde(default)]
    pub tasks: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct WorkflowResult {
    pub success: bool,
    pub final_output: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_type: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct WorkflowContext {
    pub conversation_id: String,
    pub phases_completed: Vec<String>,
    pub phase_results: std::collections::HashMap<String, serde_json::Value>,
}
```

```rust
// crates/ap-core/src/models/context.rs

//! Context window models for token budget management.
//!
//! Python source: models/context.py
//!
//! IMPORTANT (F-05 fix): ContextBudget has 10 configurable fields with
//! cross-field validators, NOT 4 simple fields. The plan originally
//! oversimplified this to {max_tokens, used_tokens, compaction_threshold,
//! compaction_target} which is wrong. The real model has tiered loading
//! levels (L0-L3), session safety thresholds, and compaction cooldown.

use serde::{Deserialize, Serialize};

/// Tiered context loading levels.
///
/// L0: Identity core — injected every turn (<= 800 tokens).
/// L1: Execution context — injected on first turn only (<= 3,000 tokens).
/// L2: Extended knowledge — loaded on demand.
/// L3: Runtime data — dynamic, never pre-loaded.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ContextLevel {
    L0Identity,
    L1Execution,
    L2Extended,
    L3Runtime,
}

/// Alert levels from token budget checking.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BudgetAlertLevel {
    HardCeiling,
    ForcedTruncate,
    Compaction,
}

/// Token budget limits for context tiered loading.
///
/// Python source: models/context.py:43-104
/// All threshold values are fractions in 0.0-1.0 range.
/// Cross-field constraints:
///   - compaction_trigger > compaction_target
///   - forced_truncate_threshold < session_hard_ceiling
///   - l0_max + l1_max <= bootstrap_max
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ContextBudget {
    pub l0_max: u32,
    pub l1_max: u32,
    pub bootstrap_max: u32,
    pub single_file_max: u32,
    pub compaction_trigger: f64,
    pub compaction_target: f64,
    pub session_hard_ceiling: f64,
    pub forced_truncate_threshold: f64,
    pub min_turns_between_compactions: u32,
    pub consecutive_compaction_alert: u32,
}

impl Default for ContextBudget {
    fn default() -> Self {
        Self {
            l0_max: 800,
            l1_max: 3000,
            bootstrap_max: 5000,
            single_file_max: 8000,
            compaction_trigger: 0.8,
            compaction_target: 0.4,
            session_hard_ceiling: 0.95,
            forced_truncate_threshold: 0.9,
            min_turns_between_compactions: 5,
            consecutive_compaction_alert: 3,
        }
    }
}

impl ContextBudget {
    /// Validate all cross-field constraints. Mirrors Python's `_validate_thresholds`.
    pub fn validate(&self) -> Result<(), String> {
        // Thresholds must be fractions 0.0-1.0
        for (name, value) in [
            ("compaction_trigger", self.compaction_trigger),
            ("compaction_target", self.compaction_target),
            ("session_hard_ceiling", self.session_hard_ceiling),
            ("forced_truncate_threshold", self.forced_truncate_threshold),
        ] {
            if !(0.0..=1.0).contains(&value) {
                return Err(format!("{name}={value} out of range 0.0-1.0"));
            }
        }
        if self.compaction_trigger <= self.compaction_target {
            return Err(format!(
                "compaction_trigger ({}) must be > compaction_target ({})",
                self.compaction_trigger, self.compaction_target
            ));
        }
        if self.forced_truncate_threshold >= self.session_hard_ceiling {
            return Err(format!(
                "forced_truncate_threshold ({}) must be < session_hard_ceiling ({})",
                self.forced_truncate_threshold, self.session_hard_ceiling
            ));
        }
        if self.l0_max + self.l1_max > self.bootstrap_max {
            return Err(format!(
                "l0_max ({}) + l1_max ({}) = {} exceeds bootstrap_max ({})",
                self.l0_max, self.l1_max, self.l0_max + self.l1_max, self.bootstrap_max
            ));
        }
        Ok(())
    }
}

/// Session-scoped token usage tracking. Attached to AgentContext.
///
/// Python source: models/context.py:110-150
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TokenUsage {
    pub prompt_tokens: u64,
    pub completion_tokens: u64,
    pub compaction_count: u32,
    pub last_compaction_turn: u32,
}

impl Default for TokenUsage {
    fn default() -> Self {
        Self {
            prompt_tokens: 0,
            completion_tokens: 0,
            compaction_count: 0,
            last_compaction_turn: 0,
        }
    }
}

impl TokenUsage {
    pub fn total_tokens(&self) -> u64 {
        self.prompt_tokens + self.completion_tokens
    }

    /// Return alert level or None if within budget.
    /// Mirrors Python's `check_budget` method.
    pub fn check_budget(
        &self,
        context_window: u64,
        budget: &ContextBudget,
    ) -> Option<BudgetAlertLevel> {
        let ratio = if context_window == 0 { return None; } else {
            self.total_tokens() as f64 / context_window as f64
        };
        if ratio >= budget.session_hard_ceiling {
            Some(BudgetAlertLevel::HardCeiling)
        } else if ratio >= budget.forced_truncate_threshold {
            Some(BudgetAlertLevel::ForcedTruncate)
        } else if ratio >= budget.compaction_trigger {
            Some(BudgetAlertLevel::Compaction)
        } else {
            None
        }
    }
}

/// Context budget log entry for compaction observability.
///
/// Python source: used by EvolutionStore's context_budget_log table.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ContextBudgetLogEntry {
    pub id: String,
    pub agent_name: String,
    pub event_type: String,
    pub tokens_before: Option<i64>,
    pub tokens_after: Option<i64>,
    pub details: Option<String>,
    pub created_at: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_budget_validates() {
        let budget = ContextBudget::default();
        assert!(budget.validate().is_ok());
    }

    #[test]
    fn trigger_must_exceed_target() {
        let mut budget = ContextBudget::default();
        budget.compaction_trigger = 0.3;
        budget.compaction_target = 0.5;
        assert!(budget.validate().is_err());
    }

    #[test]
    fn bootstrap_must_fit_l0_plus_l1() {
        let mut budget = ContextBudget::default();
        budget.bootstrap_max = 1000;
        assert!(budget.validate().is_err());
    }

    #[test]
    fn forced_truncate_must_be_below_ceiling() {
        let mut budget = ContextBudget::default();
        budget.forced_truncate_threshold = 0.96;
        assert!(budget.validate().is_err());
    }

    #[test]
    fn token_usage_check_budget() {
        let usage = TokenUsage {
            prompt_tokens: 850,
            completion_tokens: 0,
            compaction_count: 0,
            last_compaction_turn: 0,
        };
        let budget = ContextBudget::default();
        // 850/1000 = 0.85 >= compaction_trigger(0.8)
        assert_eq!(usage.check_budget(1000, &budget), Some(BudgetAlertLevel::Compaction));
    }

    #[test]
    fn token_usage_below_threshold() {
        let usage = TokenUsage {
            prompt_tokens: 500,
            completion_tokens: 0,
            compaction_count: 0,
            last_compaction_turn: 0,
        };
        let budget = ContextBudget::default();
        assert_eq!(usage.check_budget(1000, &budget), None);
    }
}
```

- [ ] **Step 6: Write distribution models (F-04 fix — was missing entirely)**

```rust
// crates/ap-core/src/models/distribution.rs

//! Git-based distribution models: PackageSource, SourceEntry, LockfileEntry, InstallationStatus.
//!
//! Python source: models/distribution.py (150 lines)
//! IMPORTANT: These types were completely missing from the original plan.
//! Phase 07 (ap-fetcher) was defining local types that don't match Python.
//! Now Phase 07 should reference these types instead.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

use super::agent::AgentType;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceType {
    Official,
    Private,
    Direct,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InstallationStatus {
    Installed,
    Outdated,
    NotInstalled,
    Installing,
    Failed,
}

/// A package source entry from sources.yaml.
///
/// Python source: models/distribution.py:33-57
/// Has `_validate_git_url` validator: git-type sources must have non-empty URL.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SourceEntry {
    #[serde(default)]
    pub name: String,
    #[serde(default = "default_git")]
    pub source_type: String,
    #[serde(default)]
    pub url: String,
    #[serde(default = "default_branch")]
    pub branch: String,
}

fn default_git() -> String { "git".to_string() }
fn default_branch() -> String { "main".to_string() }

impl SourceEntry {
    /// Validate: git-type sources must have non-empty URL.
    pub fn validate(&self) -> Result<(), String> {
        if self.source_type == "git" && self.url.trim().is_empty() {
            return Err(format!(
                "Git-type source requires a non-empty 'url'. Source '{}' has type='git' but url is empty.",
                self.name
            ));
        }
        Ok(())
    }
}

/// A single Agent entry in lockfile.json.
///
/// Python source: models/distribution.py:60-94
/// IMPORTANT (F-03 fix): `commit_sha` not `git_hash`.
/// Missing fields were: `agent_type`, `venv_path`, `dependencies`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct LockfileEntry {
    /// Version string. Python validates: r"^[a-zA-Z0-9._-]+$"
    pub version: String,
    pub source: String,
    /// Commit SHA — 40/64 hex chars, or sentinel 'latest'/'head'.
    /// NOT `git_hash` (original plan had wrong name).
    pub commit_sha: String,
    pub agent_type: AgentType,
    #[serde(default)]
    pub installed_at: String,
    #[serde(default)]
    pub venv_path: String,
    #[serde(default)]
    pub dependencies: Vec<String>,
}

impl LockfileEntry {
    /// Validate commit_sha format: 40/64 hex or 'latest'/'head'.
    pub fn validate_commit_sha(&self) -> Result<(), String> {
        let valid = self.commit_sha.len() == 40
            || self.commit_sha.len() == 64
            || self.commit_sha == "latest"
            || self.commit_sha == "head";
        if valid && self.commit_sha.len() >= 40 {
            // Check hex format for actual SHAs
            if !self.commit_sha.chars().all(|c| c.is_ascii_hexdigit()) {
                return Err(format!("commit_sha '{}' is not valid hex", self.commit_sha));
            }
        }
        Ok(())
    }
}

/// The complete lockfile.json structure.
///
/// Python source: models/distribution.py:97-105
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Lockfile {
    #[serde(default = "default_lockfile_version")]
    pub version: u32,
    #[serde(default)]
    pub agents: HashMap<String, LockfileEntry>,
}

fn default_lockfile_version() -> u32 { 1 }

impl Default for Lockfile {
    fn default() -> Self {
        Self { version: 1, agents: HashMap::new() }
    }
}

/// Git package source with local cache path.
///
/// Python source: models/distribution.py:108-115
/// Extends SourceEntry with runtime state (local cache directory).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PackageSource {
    #[serde(flatten)]
    pub entry: SourceEntry,
    #[serde(default)]
    pub local_cache: String,
}

/// A single Agent entry from a source's index.yaml.
///
/// Python source: models/distribution.py:118-150
/// Has `_reject_path_traversal` validator: no ".." in path.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct IndexEntry {
    pub name: String,
    pub version: String,
    pub agent_type: AgentType,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub dependencies: Vec<String>,
    #[serde(default)]
    pub path: String,
}

impl IndexEntry {
    /// Reject path traversal sequences in the path field.
    pub fn validate_path(&self) -> Result<(), String> {
        if !self.path.is_empty() && self.path.contains("..") {
            return Err(format!("IndexEntry.path must not contain '..': got '{}'", self.path));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn source_entry_validates_git_url() {
        let entry = SourceEntry {
            name: "test".into(),
            source_type: "git".into(),
            url: "".into(),
            branch: "main".into(),
        };
        assert!(entry.validate().is_err());
    }

    #[test]
    fn source_entry_passes_with_url() {
        let entry = SourceEntry {
            name: "test".into(),
            source_type: "git".into(),
            url: "https://github.com/test/repo".into(),
            branch: "main".into(),
        };
        assert!(entry.validate().is_ok());
    }

    #[test]
    fn lockfile_entry_valid_commit_sha() {
        let entry = LockfileEntry {
            version: "1.0.0".into(),
            source: "official".into(),
            commit_sha: "abc123def456abc123def456abc123def456abc1".into(),
            agent_type: AgentType::Atomic,
            installed_at: "2026-04-22T00:00:00Z".into(),
            venv_path: String::new(),
            dependencies: vec![],
        };
        assert!(entry.validate_commit_sha().is_ok());
    }

    #[test]
    fn lockfile_entry_latest_sentinel() {
        let entry = LockfileEntry {
            version: "1.0.0".into(),
            source: "official".into(),
            commit_sha: "latest".into(),
            agent_type: AgentType::Atomic,
            installed_at: String::new(),
            venv_path: String::new(),
            dependencies: vec![],
        };
        assert!(entry.validate_commit_sha().is_ok());
    }

    #[test]
    fn index_entry_rejects_path_traversal() {
        let entry = IndexEntry {
            name: "test".into(),
            version: "1.0.0".into(),
            agent_type: AgentType::Atomic,
            description: String::new(),
            tags: vec![],
            dependencies: vec![],
            path: "../etc/passwd".into(),
        };
        assert!(entry.validate_path().is_err());
    }

    #[test]
    fn deserialize_python_lockfile() {
        let json = r#"{
            "version": 1,
            "agents": {
                "code-reviewer": {
                    "version": "1.2.0",
                    "source": "official",
                    "commit_sha": "abc123def456abc123def456abc123def456abc1",
                    "agent_type": "atomic",
                    "installed_at": "2026-04-20T10:00:00Z",
                    "venv_path": "~/.agent-nexus/venvs/doc-filler",
                    "dependencies": ["pydantic>=2.0"]
                }
            }
        }"#;
        let lockfile: Lockfile = serde_json::from_str(json).unwrap();
        assert!(lockfile.agents.contains_key("code-reviewer"));
        let entry = &lockfile.agents["code-reviewer"];
        assert_eq!(entry.version, "1.2.0");
        assert_eq!(entry.agent_type, AgentType::Atomic);
        assert_eq!(entry.venv_path, "~/.agent-nexus/venvs/doc-filler");
        assert_eq!(entry.dependencies.len(), 1);
    }
}
```

- [ ] **Step 7: Write models/mod.rs**

```rust
// crates/ap-core/src/models/mod.rs

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
pub use agent::{AgentManifest, AgentType, RunMode, AgentRole, ModelTier};
pub use config::{PlatformConfig, ModelConfig, RuntimeConfig, ProviderConfig};
pub use context::{ContextBudget, ContextLevel, BudgetAlertLevel, TokenUsage};
pub use distribution::{SourceType, InstallationStatus, SourceEntry, LockfileEntry, Lockfile, PackageSource, IndexEntry};
pub use evolution::{SkillRecord, EvolutionType, SkillOrigin, SkillLineage, EvolutionMetrics, EvolutionContext};
pub use hooks::{HookType, HookEvent, HookDefinition, HookExecution};
pub use ipc::{PlatformToAgent, AgentToPlatform, IPCMessage, MessageDirection};
pub use permission::{PermissionConfig, PermissionMode, PermissionDecision};
pub use task::{TaskItem, TaskState, TaskGraphSnapshot};
pub use composition::{Composition, CompositionTask, WorkflowPhaseEntry, WorkflowResult, WorkflowContext};
```

- [ ] **Step 7: Update lib.rs and add missing serde_json dep if needed**

The `Cargo.toml` already has `serde_json`. Ensure `lib.rs` exports models:

```rust
// crates/ap-core/src/lib.rs
pub mod models;
```

- [ ] **Step 8: Run all model tests**

Run: `cargo test -p ap-core`
Expected: All model tests PASS. Fix any compilation errors.

- [ ] **Step 9: Commit**

```bash
git add crates/ap-core/
git commit -m "feat(ap-core): add all shared data models (ipc, agent, task, config, permission, evolution, runtime, hooks, composition, context)"
```
