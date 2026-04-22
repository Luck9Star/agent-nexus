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

Hooks run at lifecycle events: `pre_run`, `post_run`, `on_error`, `on_timeout`.
A hook is a shell command or inline script that executes at the appropriate moment.

- [ ] **Step 1: Write hook executor tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn register_and_list_hooks() {
        let mut exec = HookExecutor::new();
        exec.register(HookDefinition {
            event: HookEvent::PreRun,
            action: HookAction::Command("echo starting".into()),
            name: "log-start".into(),
        });
        let hooks = exec.list_for_event(&HookEvent::PreRun);
        assert_eq!(hooks.len(), 1);
        assert_eq!(hooks[0].name, "log-start");
    }

    #[tokio::test]
    async fn execute_command_hook() {
        let exec = HookExecutor::new();
        let def = HookDefinition {
            event: HookEvent::PreRun,
            action: HookAction::Command("echo hello".into()),
            name: "test".into(),
        };
        let result = exec.execute(&def).await.unwrap();
        assert!(result.success);
        assert!(result.output.contains("hello"));
    }

    #[tokio::test]
    async fn execute_failing_hook_returns_error() {
        let exec = HookExecutor::new();
        let def = HookDefinition {
            event: HookEvent::PreRun,
            action: HookAction::Command("false".into()), // exits 1
            name: "fail".into(),
        };
        let result = exec.execute(&def).await.unwrap();
        assert!(!result.success);
    }

    #[tokio::test]
    async fn run_all_hooks_for_event() {
        let mut exec = HookExecutor::new();
        exec.register(HookDefinition {
            event: HookEvent::PostRun,
            action: HookAction::Command("echo done".into()),
            name: "h1".into(),
        });
        exec.register(HookDefinition {
            event: HookEvent::PostRun,
            action: HookAction::Command("echo complete".into()),
            name: "h2".into(),
        });
        let results = exec.run_all(&HookEvent::PostRun).await;
        assert_eq!(results.len(), 2);
        assert!(results.iter().all(|r| r.success));
    }
}
```

- [ ] **Step 2: Implement HookExecutor**

```rust
use crate::models::hooks::{HookEvent, HookAction, HookDefinition, HookExecution};
use std::collections::HashMap;

pub struct HookExecutor {
    hooks: HashMap<HookEvent, Vec<HookDefinition>>,
}

impl HookExecutor {
    pub fn new() -> Self {
        Self { hooks: HashMap::new() }
    }

    pub fn register(&mut self, hook: HookDefinition) {
        self.hooks.entry(hook.event.clone())
            .or_default()
            .push(hook);
    }

    pub fn list_for_event(&self, event: &HookEvent) -> &[HookDefinition] {
        self.hooks.get(event).map(|v| v.as_slice()).unwrap_or(&[])
    }

    pub async fn execute(&self, hook: &HookDefinition) -> Result<HookExecution, std::io::Error> {
        let output = match &hook.action {
            HookAction::Command(cmd) => {
                let parts: Vec<&str> = cmd.split_whitespace().collect();
                let result = tokio::process::Command::new(parts[0])
                    .args(&parts[1..])
                    .output()
                    .await?;
                HookExecution {
                    name: hook.name.clone(),
                    success: result.status.success(),
                    output: String::from_utf8_lossy(&result.stdout).into(),
                    duration_ms: 0, // TODO: measure
                }
            }
            HookAction::Script(_script) => {
                // Execute inline script via shell
                todo!()
            }
        };
        Ok(output)
    }

    pub async fn run_all(&self, event: &HookEvent) -> Vec<HookExecution> {
        let mut results = Vec::new();
        if let Some(hooks) = self.hooks.get(event) {
            for hook in hooks {
                if let Ok(exec) = self.execute(hook).await {
                    results.push(exec);
                }
            }
        }
        results
    }
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
