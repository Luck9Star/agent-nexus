# Phase 5: ap-core/hooks + skills — Lifecycle Hooks & Skill Loader

> **Goal:** Port hook executor (pre/post lifecycle callbacks) and SKILL.md parser.

**Python source:** `src/agent_nexus/platform/hooks/` (492 lines) + `src/agent_nexus/platform/skills/` (433 lines)
**Rust target:** `crates/ap-core/src/hooks/` + `crates/ap-core/src/skills/`
**Depends on:** Phase 1 (models)

**Files:**
- Create: `crates/ap-core/src/hooks/mod.rs`
- Create: `crates/ap-core/src/hooks/executor.rs`
- Create: `crates/ap-core/src/skills/mod.rs`
- Create: `crates/ap-core/src/skills/loader.rs`
- Create: `crates/ap-core/src/skills/models.rs`

---

## Task 5.1: HookExecutor

**Python source:** `src/agent_nexus/platform/hooks/executor.py` (492 lines)
**Rust target:** `crates/ap-core/src/hooks/executor.rs`

> **F-08 fix:** Dispatch on `HookType` (command/http/prompt/agent), NOT on a
> fabricated `HookAction` enum. The `HookDefinition` model from Phase 01 has
> `hook_type: HookType` with optional `command`, `url`, `prompt`, `model` fields.

Hooks run at lifecycle events: `pre_run`, `post_run`, `on_error`, `on_timeout`.
A hook dispatches based on its `hook_type` field.

- [ ] **Step 1: Write hook executor tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    fn make_command_hook(event: HookEvent, cmd: &str) -> HookDefinition {
        HookDefinition {
            hook_type: HookType::Command,
            event,
            config: serde_json::Value::Null,
            enabled: true,
            block_on_failure: false,
            timeout_seconds: 10.0,
            matcher: None,
            command: Some(cmd.to_string()),
            url: None,
            prompt: None,
            model: None,
        }
    }

    #[test]
    fn register_and_list_hooks() {
        let mut exec = HookExecutor::new();
        exec.register(make_command_hook(HookEvent::PreExecution, "echo starting"));
        let hooks = exec.list_for_event(&HookEvent::PreExecution);
        assert_eq!(hooks.len(), 1);
        assert_eq!(hooks[0].hook_type, HookType::Command);
    }

    #[tokio::test]
    async fn execute_command_hook() {
        let exec = HookExecutor::new();
        let def = make_command_hook(HookEvent::PreExecution, "echo hello");
        let result = exec.execute(&def).await.unwrap();
        assert!(result.passed);
        assert!(!result.blocked);
        assert!(result.output.as_deref().unwrap_or("").contains("hello"));
    }

    #[tokio::test]
    async fn execute_failing_hook_returns_blocked() {
        let exec = HookExecutor::new();
        let def = make_command_hook(HookEvent::PreExecution, "false");
        let result = exec.execute(&def).await.unwrap();
        assert!(!result.passed);
    }

    #[tokio::test]
    async fn run_all_hooks_for_event() {
        let mut exec = HookExecutor::new();
        exec.register(make_command_hook(HookEvent::PostExecution, "echo done"));
        exec.register(make_command_hook(HookEvent::PostExecution, "echo complete"));
        let aggregated = exec.run_all(&HookEvent::PostExecution).await;
        assert_eq!(aggregated.results.len(), 2);
        assert!(aggregated.results.iter().all(|r| r.passed));
        assert!(!aggregated.should_block);
    }

    #[tokio::test]
    async fn http_hook_skipped_when_no_url() {
        let exec = HookExecutor::new();
        let def = HookDefinition {
            hook_type: HookType::Http,
            event: HookEvent::PostExecution,
            config: serde_json::Value::Null,
            enabled: true,
            block_on_failure: false,
            timeout_seconds: 10.0,
            matcher: None,
            command: None,
            url: None,
            prompt: None,
            model: None,
        };
        let result = exec.execute(&def).await.unwrap();
        // Should gracefully handle missing URL
        assert!(!result.passed);
    }
}
```

- [ ] **Step 2: Implement HookExecutor — dispatch on HookType**

```rust
use crate::models::hooks::{
    HookType, HookEvent, HookDefinition, HookExecution, AggregatedHookResult,
};
use std::collections::HashMap;

pub struct HookExecutor {
    hooks: HashMap<HookEvent, Vec<HookDefinition>>,
}

impl HookExecutor {
    pub fn new() -> Self {
        Self { hooks: HashMap::new() }
    }

    pub fn register(&mut self, hook: HookDefinition) {
        if !hook.enabled { return; }
        self.hooks.entry(hook.event)
            .or_default()
            .push(hook);
    }

    pub fn list_for_event(&self, event: &HookEvent) -> &[HookDefinition] {
        self.hooks.get(event).map(|v| v.as_slice()).unwrap_or(&[])
    }

    /// Execute a single hook, dispatching on `hook_type`.
    /// F-08 fix: dispatch on HookType, not a fabricated HookAction enum.
    pub async fn execute(&self, hook: &HookDefinition) -> Result<HookExecution, HookExecuteError> {
        let start = std::time::Instant::now();
        let (passed, output, error) = match hook.hook_type {
            HookType::Command => self.execute_command(hook).await?,
            HookType::Http => self.execute_http(hook).await?,
            HookType::Prompt => {
                // Prompt hooks require model interaction — placeholder
                (true, None, None)
            }
            HookType::Agent => {
                // Agent hooks delegate to an agent subprocess — placeholder
                (true, None, None)
            }
        };
        let duration_ms = start.elapsed().as_secs_f64() * 1000.0;
        Ok(HookExecution {
            hook: hook.clone(),
            passed,
            blocked: !passed && hook.block_on_failure,
            output,
            error,
            error_type: None,
            duration_ms,
            executed_at: chrono::Utc::now(),
        })
    }

    async fn execute_command(
        &self, hook: &HookDefinition,
    ) -> Result<(bool, Option<String>, Option<String>), HookExecuteError> {
        let cmd = hook.command.as_deref().ok_or(HookExecuteError::MissingField("command"))?;
        let parts: Vec<&str> = cmd.split_whitespace().collect();
        if parts.is_empty() {
            return Ok((false, None, Some("empty command".into())));
        }
        let result = tokio::process::Command::new(parts[0])
            .args(&parts[1..])
            .output()
            .await
 .map_err(|e| HookExecuteError::ExecutionFailed(e.to_string()))?;
        let output = String::from_utf8_lossy(&result.stdout).to_string();
        let error = if result.status.success() { None } else {
            Some(String::from_utf8_lossy(&result.stderr).to_string())
        };
        Ok((result.status.success(), Some(output), error))
    }

    async fn execute_http(
        &self, hook: &HookDefinition,
    ) -> Result<(bool, Option<String>, Option<String>), HookExecuteError> {
        let url = hook.url.as_deref().ok_or(HookExecuteError::MissingField("url"))?;
        // HTTP POST with hook config as body — placeholder
        let _ = url;
        Ok((true, None, None))
    }

    /// Run all hooks for an event, return aggregated result.
    pub async fn run_all(&self, event: &HookEvent) -> AggregatedHookResult {
        let mut results = Vec::new();
        if let Some(hooks) = self.hooks.get(event) {
            for hook in hooks {
                if let Ok(exec) = self.execute(hook).await {
                    results.push(exec);
                }
            }
        }
        let should_block = results.iter().any(|r| r.blocked);
        let outputs = results.iter()
            .filter_map(|r| r.output.clone())
            .collect();
        AggregatedHookResult { results, should_block, outputs }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum HookExecuteError {
    #[error("Missing required field: {0}")]
    MissingField(&'static str),
    #[error("Execution failed: {0}")]
    ExecutionFailed(String),
}
```

- [ ] **Step 3: Verify and commit**

```bash
cargo test -p ap-core -- hooks
git add crates/ap-core/src/hooks/
git commit -m "feat(ap-core): HookExecutor with lifecycle event dispatch"
```

---

## Task 5.2: SkillLoader (SKILL.md parser)

**Python source:** `src/agent_nexus/platform/skills/loader.py` (433 lines)
**Rust target:** `crates/ap-core/src/skills/loader.rs` + `crates/ap-core/src/skills/models.rs`

Parses SKILL.md files with YAML frontmatter to extract skill metadata.

- [ ] **Step 1: Write skill loader tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_skill_with_frontmatter() {
        let content = r#"---
name: code-review
description: Reviews code for quality and security
inputs:
  - name: code_path
    type: string
    required: true
outputs:
  - name: review
    type: string
---

# Code Review Skill

This skill reviews code...

## Steps
1. Read the code
2. Analyze patterns
3. Generate review
"#;
        let skill = SkillLoader::parse(content).unwrap();
        assert_eq!(skill.name, "code-review");
        assert_eq!(skill.description, "Reviews code for quality and security");
        assert_eq!(skill.inputs.len(), 1);
        assert_eq!(skill.inputs[0].name, "code_path");
        assert!(skill.body.contains("## Steps"));
    }

    #[test]
    fn parse_skill_without_frontmatter() {
        let content = "# Simple Skill\n\nJust a body.";
        let skill = SkillLoader::parse(content).unwrap();
        assert!(skill.name.is_empty());
        assert!(skill.body.contains("Simple Skill"));
    }

    #[test]
    fn parse_from_file() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("SKILL.md");
        std::fs::write(&path, "---\nname: test\n---\nBody").unwrap();
        let skill = SkillLoader::load_from_file(&path).unwrap();
        assert_eq!(skill.name, "test");
    }
}
```

- [ ] **Step 2: Implement Skill models and loader**

```rust
// crates/ap-core/src/skills/models.rs
use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Skill {
    pub name: String,
    pub description: String,
    pub inputs: Vec<SkillInput>,
    pub outputs: Vec<SkillOutput>,
    pub body: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillInput {
    pub name: String,
    #[serde(default)]
    pub r#type: String,
    #[serde(default)]
    pub required: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SkillOutput {
    pub name: String,
    #[serde(default)]
    pub r#type: String,
}
```

```rust
// crates/ap-core/src/skills/loader.rs
use super::models::Skill;

pub struct SkillLoader;

impl SkillLoader {
    /// Parse a SKILL.md string with optional YAML frontmatter.
    pub fn parse(content: &str) -> Result<Skill, SkillError> {
        if let Some(body) = content.strip_prefix("---") {
            if let Some(end) = body.find("---") {
                let frontmatter = &body[..end];
                let body_content = body[end + 3..].trim();
                let mut skill: Skill = serde_yaml::from_str(frontmatter)
                    .unwrap_or(Skill {
                        name: String::new(),
                        description: String::new(),
                        inputs: Vec::new(),
                        outputs: Vec::new(),
                        body: String::new(),
                    });
                skill.body = body_content.to_string();
                return Ok(skill);
            }
        }
        Ok(Skill {
            name: String::new(),
            description: String::new(),
            inputs: Vec::new(),
            outputs: Vec::new(),
            body: content.to_string(),
        })
    }

    pub fn load_from_file(path: &std::path::Path) -> Result<Skill, SkillError> {
        let content = std::fs::read_to_string(path)?;
        Self::parse(&content)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum SkillError {
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
}
```

- [ ] **Step 3: Module glue + verify + commit**

Create `mod.rs` files, update `lib.rs`, then:

```bash
cargo test -p ap-core -- skills
git add crates/ap-core/src/skills/ crates/ap-core/src/hooks/mod.rs crates/ap-core/src/lib.rs
git commit -m "feat(ap-core): skills loader with SKILL.md frontmatter parsing"
```

---

## Final Verification

- [ ] `cargo test -p ap-core`
- [ ] `cargo clippy -p ap-core -- -D warnings`
