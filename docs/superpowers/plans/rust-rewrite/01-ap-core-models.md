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

**Files:**
- Create: `crates/ap-core/src/models/ipc.rs`
- Test: inline `#[cfg(test)]` module

- [ ] **Step 1: Write the IPC model test**

```rust
// crates/ap-core/src/models/ipc.rs (top of file, test at bottom)

//! IPC message models: Platform <-> Agent communication via stdin/stdout JSON-lines.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "type")]
pub enum PlatformToAgent {
    #[serde(rename = "chat")]
    Chat {
        content: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        conversation_id: Option<String>,
    },
    #[serde(rename = "task")]
    Task {
        content: String,
        task_id: String,
    },
    #[serde(rename = "data_reference")]
    DataReference {
        content: String,
        ref_id: String,
        summary: String,
    },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "type")]
pub enum AgentToPlatform {
    #[serde(rename = "result")]
    Result {
        content: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        task_id: Option<String>,
        #[serde(default)]
        success: bool,
    },
    #[serde(rename = "progress")]
    Progress {
        content: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        task_id: Option<String>,
        #[serde(skip_serializing_if = "Option::is_none")]
        progress_pct: Option<f64>,
    },
    #[serde(rename = "error")]
    Error {
        error: String,
        #[serde(rename = "type")]
        error_type: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        task_id: Option<String>,
    },
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_chat_message() {
        let msg = PlatformToAgent::Chat {
            content: "hello agent".to_string(),
            conversation_id: Some("conv-123".to_string()),
        };
        let json = serde_json::to_string(&msg).unwrap();
        // Must serialize as {"type":"chat","content":"hello agent","conversation_id":"conv-123"}
        assert!(json.contains(r#""type":"chat""#));
        let de: PlatformToAgent = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, de);
    }

    #[test]
    fn roundtrip_task_message() {
        let msg = PlatformToAgent::Task {
            content: "do something".to_string(),
            task_id: "t-1".to_string(),
        };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""type":"task""#));
        let de: PlatformToAgent = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, de);
    }

    #[test]
    fn roundtrip_result_message() {
        let msg = AgentToPlatform::Result {
            content: "done".to_string(),
            task_id: Some("t-1".to_string()),
            success: true,
        };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""type":"result""#));
        let de: AgentToPlatform = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, de);
    }

    #[test]
    fn roundtrip_error_message() {
        let msg = AgentToPlatform::Error {
            error: "something broke".to_string(),
            error_type: "RuntimeError".to_string(),
            task_id: None,
        };
        let json = serde_json::to_string(&msg).unwrap();
        assert!(json.contains(r#""type":"error""#));
        // Python sends {"type":"error","error":"...","error_type":"..."}
        // Our Rust uses #[serde(rename = "type")] for error_type
        // Actually we need to handle the Python format which uses "error" and "error_type"
        let de: AgentToPlatform = serde_json::from_str(&json).unwrap();
        assert_eq!(msg, de);
    }

    #[test]
    fn deserialize_python_format_chat() {
        // Python sends: {"type":"chat","content":"hello","conversation_id":"c1"}
        let json = r#"{"type":"chat","content":"hello","conversation_id":"c1"}"#;
        let msg: PlatformToAgent = serde_json::from_str(json).unwrap();
        match msg {
            PlatformToAgent::Chat { content, conversation_id } => {
                assert_eq!(content, "hello");
                assert_eq!(conversation_id, Some("c1".to_string()));
            }
            _ => panic!("Expected Chat variant"),
        }
    }
}
```

- [ ] **Step 2: Run test to verify it compiles**

Run: `cargo test -p ap-core -- models::ipc`
Expected: PASS (all 5 tests)

- [ ] **Step 3: Commit**

```bash
git add crates/ap-core/src/models/
git commit -m "feat(ap-core): add IPC message models with serde roundtrip tests"
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
    pub fn validate_counters(&self) -> Result<(), String> {
        if self.total_selections == 0 {
            if self.total_applied != 0 || self.total_fallbacks != 0 {
                return Err("zero selections requires zero applied and zero fallbacks".into());
            }
        }
        if self.total_applied > self.total_selections {
            return Err("total_applied cannot exceed total_selections".into());
        }
        if self.total_completions > self.total_applied {
            return Err("total_completions cannot exceed total_applied".into());
        }
        if self.total_fallbacks > self.total_applied {
            return Err("total_fallbacks cannot exceed total_applied".into());
        }
        Ok(())
    }
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

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct WorkflowPhase {
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

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ContextBudget {
    pub max_tokens: u32,
    pub used_tokens: u32,
    pub compaction_threshold: f64,
    pub compaction_target: f64,
}

impl ContextBudget {
    pub fn usage_ratio(&self) -> f64 {
        if self.max_tokens == 0 {
            return 0.0;
        }
        self.used_tokens as f64 / self.max_tokens as f64
    }

    pub fn needs_compaction(&self) -> bool {
        self.usage_ratio() >= self.compaction_threshold
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ContextWindow {
    pub budget: ContextBudget,
    pub min_turns_between_compactions: u32,
    pub consecutive_compaction_count: u32,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn context_budget_usage() {
        let budget = ContextBudget {
            max_tokens: 1000,
            used_tokens: 800,
            compaction_threshold: 0.8,
            compaction_target: 0.4,
        };
        assert!((budget.usage_ratio() - 0.8).abs() < 0.001);
        assert!(budget.needs_compaction());
    }

    #[test]
    fn context_budget_no_compaction_needed() {
        let budget = ContextBudget {
            max_tokens: 1000,
            used_tokens: 500,
            compaction_threshold: 0.8,
            compaction_target: 0.4,
        };
        assert!(!budget.needs_compaction());
    }
}
```

- [ ] **Step 6: Write models/mod.rs**

```rust
// crates/ap-core/src/models/mod.rs

//! Shared data models for the Agent Nexus Platform.

pub mod agent;
pub mod common;
pub mod composition;
pub mod config;
pub mod context;
pub mod evolution;
pub mod hooks;
pub mod ipc;
pub mod permission;
pub mod runtime;
pub mod task;

// Re-export key types for convenience
pub use agent::{AgentManifest, AgentType, RunMode, AgentRole, ModelTier};
pub use config::{PlatformConfig, ModelConfig, RuntimeConfig, ProviderConfig};
pub use ipc::{PlatformToAgent, AgentToPlatform};
pub use task::{TaskItem, TaskState, TaskGraphSnapshot};
pub use permission::{PermissionConfig, PermissionMode, PermissionDecision};
pub use evolution::{SkillRecord, EvolutionType, SkillOrigin, SkillLineage};
pub use composition::{Composition, CompositionTask, WorkflowPhase, WorkflowResult, WorkflowContext};
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
