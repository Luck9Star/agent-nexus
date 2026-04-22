# Phase 4: ap-core/router — 4-Phase Platform Router

> **Goal:** Port the 4-phase composite workflow (Research → Synthesis → Implementation → Verification) and subtask controller.

**Python source:** `src/agent_nexus/platform/router/` (992 lines)
**Rust target:** `crates/ap-core/src/router/`
**Depends on:** Phase 1 (models), Phase 3 (orchestration)

**Files:**
- Create: `crates/ap-core/src/router/mod.rs`
- Create: `crates/ap-core/src/router/router.rs`
- Create: `crates/ap-core/src/router/subtask.rs`
- Create: `crates/ap-core/src/router/workflow.rs`

---

## Task 4.1: Workflow types

**Python source:** `src/agent_nexus/platform/router/workflow.py`
**Rust target:** `crates/ap-core/src/router/workflow.rs`

- [ ] **Step 1: Write workflow tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn phase_ordering() {
        assert!(WorkflowPhase::Research < WorkflowPhase::Synthesis);
        assert!(WorkflowPhase::Synthesis < WorkflowPhase::Implementation);
        assert!(WorkflowPhase::Implementation < WorkflowPhase::Verification);
    }

    #[test]
    fn context_accumulates_results() {
        let mut ctx = WorkflowContext::new("test-task".into());
        ctx.add_phase_result(WorkflowPhase::Research, "Found 3 approaches".into());
        ctx.add_phase_result(WorkflowPhase::Synthesis, "Best approach: A".into());
        assert_eq!(ctx.results.len(), 2);
        assert!(ctx.has_phase_completed(&WorkflowPhase::Research));
    }

    #[test]
    fn total_timeout_calculation() {
        let config = WorkflowConfig {
            phases: vec![WorkflowPhase::Research, WorkflowPhase::Synthesis],
            per_phase_timeout_secs: 60,
            max_retries: 2,
            parallel_workers: 4,
        };
        assert_eq!(config.total_timeout_secs(), 120);
    }
}
```

- [ ] **Step 2: Implement workflow types**

```rust
use serde::{Serialize, Deserialize};
use std::collections::HashMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkflowPhase {
    Research = 1,
    Synthesis = 2,
    Implementation = 3,
    Verification = 4,
}

#[derive(Debug, Clone)]
pub struct WorkflowContext {
    pub task_id: String,
    pub results: HashMap<WorkflowPhase, String>,
    pub current_phase: Option<WorkflowPhase>,
}

impl WorkflowContext {
    pub fn new(task_id: String) -> Self {
        Self { task_id, results: HashMap::new(), current_phase: None }
    }

    pub fn add_phase_result(&mut self, phase: WorkflowPhase, result: String) {
        self.current_phase = Some(phase);
        self.results.insert(phase, result);
    }

    pub fn has_phase_completed(&self, phase: &WorkflowPhase) -> bool {
        self.results.contains_key(phase)
    }

    pub fn get_result(&self, phase: &WorkflowPhase) -> Option<&str> {
        self.results.get(phase).map(|s| s.as_str())
    }
}

#[derive(Debug, Clone)]
pub struct WorkflowResult {
    pub success: bool,
    pub final_output: String,
    pub phase_results: HashMap<WorkflowPhase, String>,
    pub total_duration_ms: u64,
}

pub struct WorkflowConfig {
    pub phases: Vec<WorkflowPhase>,
    pub per_phase_timeout_secs: u64,
    pub max_retries: u32,
    pub parallel_workers: usize,
}

impl WorkflowConfig {
    pub fn total_timeout_secs(&self) -> u64 {
        self.phases.len() as u64 * self.per_phase_timeout_secs
    }

    pub fn default_composite() -> Self {
        Self {
            phases: vec![WorkflowPhase::Research, WorkflowPhase::Synthesis,
                         WorkflowPhase::Implementation, WorkflowPhase::Verification],
            per_phase_timeout_secs: 120,
            max_retries: 2,
            parallel_workers: 4,
        }
    }
}
```

- [ ] **Step 3: Verify and commit**

```bash
cargo test -p ap-core -- router::workflow
git add crates/ap-core/src/router/workflow.rs
git commit -m "feat(ap-core): workflow types with phase ordering and context accumulation"
```

---

## Task 4.2: SubtaskController

**Python source:** `src/agent_nexus/platform/router/subtask.py` (~400 lines)
**Rust target:** `crates/ap-core/src/router/subtask.rs`

Features: timeout, retry with backoff, parallel execution via `tokio::JoinSet`

- [ ] **Step 1: Write subtask tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn execute_with_timeout_success() {
        let ctrl = SubtaskController::new(SubtaskConfig {
            timeout_secs: 5,
            max_retries: 0,
            ..Default::default()
        });
        let result = ctrl.execute("test-id", || async {
            Ok("done".to_string())
        }).await;
        assert!(result.is_ok());
    }

    #[tokio::test]
    async fn execute_with_retry() {
        let attempts = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let attempts_clone = attempts.clone();
        let ctrl = SubtaskController::new(SubtaskConfig {
            timeout_secs: 5,
            max_retries: 2,
            ..Default::default()
        });
        let result = ctrl.execute("test-id", move || {
            let a = attempts_clone.clone();
            async move {
                let n = a.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                if n < 2 { Err("fail".into()) } else { Ok("done".into()) }
            }
        }).await;
        assert!(result.is_ok());
        assert_eq!(attempts.load(std::sync::atomic::Ordering::SeqCst), 3);
    }
}
```

- [ ] **Step 2: Implement SubtaskController + verify + commit**

```bash
cargo test -p ap-core -- router::subtask
git add crates/ap-core/src/router/subtask.rs
git commit -m "feat(ap-core): SubtaskController with timeout, retry, and backoff"
```

---

## Task 4.3: PlatformRouter

**Python source:** `src/agent_nexus/platform/router/router.py` (~490 lines)
**Rust target:** `crates/ap-core/src/router/router.rs`

4-Phase workflow: Research → Synthesis → Implementation → Verification.
Each phase creates a TaskGraph, runs agents, collects results.

- [ ] **Step 1: Write router tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn route_single_agent_bypasses_workflow() {
        // Single agent: no 4-phase, direct IPC call
        let router = PlatformRouter::new(WorkflowConfig::default_composite());
        let result = router.route_chat("agent-a", "hello").await;
        // Without real agents, this tests the routing logic only
    }

    #[tokio::test]
    async fn route_composite_runs_all_phases() {
        // Composite agent: runs through all 4 phases
        // Use mock agents for testing
    }
}
```

- [ ] **Step 2: Implement PlatformRouter + verify + commit**

The router orchestrates the 4-phase flow using `SubtaskController` and `TaskGraph`.
It uses `tokio::JoinSet` for parallel workers within each phase.

```bash
cargo test -p ap-core -- router::router
git add crates/ap-core/src/router/router.rs
git commit -m "feat(ap-core): PlatformRouter with 4-phase composite workflow"
```

---

## Task 4.4: Module glue

- Create: `crates/ap-core/src/router/mod.rs`
- Update: `crates/ap-core/src/lib.rs` — add `pub mod router;`

```bash
git add crates/ap-core/src/router/mod.rs crates/ap-core/src/lib.rs
git commit -m "feat(ap-core): router module glue"
```

---

## Final Verification

- [ ] `cargo test -p ap-core`
- [ ] `cargo clippy -p ap-core -- -D warnings`
