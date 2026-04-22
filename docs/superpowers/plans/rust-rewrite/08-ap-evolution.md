# Phase 8: ap-evolution — Self-Evolution Engine

> **Goal:** Port the self-evolution engine (SQLite store, analyzer, evolver, promotion) from Python to Rust.

**Python source:** `src/agent_nexus/platform/evolution/` (3,835 lines)
**Rust target:** `crates/ap-evolution/src/`
**Depends on:** Phase 1 (ap-core models), Phase 6 (ap-runtime for IPC)

**Key architectural shift:** Python evolver uses in-process IPythonRuntime. Rust evolver uses IPC via ap-runtime to call Agent subprocesses. This makes the architecture cleaner and strengthens the agent security boundary.

**Files:**
- Create: `crates/ap-evolution/src/lib.rs` (overwrite skeleton)
- Create: `crates/ap-evolution/src/store/mod.rs`
- Create: `crates/ap-evolution/src/store/schema.rs`
- Create: `crates/ap-evolution/src/store/queries.rs`
- Create: `crates/ap-evolution/src/analyzer.rs`
- Create: `crates/ap-evolution/src/evolver.rs`
- Create: `crates/ap-evolution/src/compaction.rs`
- Create: `crates/ap-evolution/src/promotion.rs`
- Create: `crates/ap-evolution/src/health.rs`
- Create: `crates/ap-evolution/src/thresholds.rs`
- Create: `crates/ap-evolution/src/context_describer.rs`
- Create: `crates/ap-evolution/src/engine.rs`

---

## Task 8.1: EvolutionStore (SQLite)

**Python source:** `src/agent_nexus/platform/evolution/store.py` (1,392 lines — heaviest module)
**Rust target:** `crates/ap-evolution/src/store/`

Schema must be identical to Python version so the same SQLite file can be read/written.

- [ ] **Step 1: Write store schema tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn init_schema_in_memory() {
        let store = EvolutionStore::new_in_memory().unwrap();
        // Schema tables exist
        let tables = store.list_tables();
        assert!(tables.contains(&"skills".to_string()));
        assert!(tables.contains(&"evolution_history".to_string()));
        assert!(tables.contains(&"agent_health".to_string()));
    }

    #[test]
    fn backward_compat_read_python_db() {
        // Load a fixture SQLite file written by Python
        // Verify all tables are readable
    }

    #[test]
    fn skill_crud() {
        let store = EvolutionStore::new_in_memory().unwrap();
        let skill = SkillRecord {
            name: "fix-imports".into(),
            agent_name: "code-reviewer".into(),
            skill_type: SkillType::Fix,
            description: "Fix import ordering".into(),
            code: "reorder_imports(file)".into(),
            ..Default::default()
        };
        store.insert_skill(&skill).unwrap();
        let loaded = store.get_skill("fix-imports").unwrap().unwrap();
        assert_eq!(loaded.name, "fix-imports");
        assert_eq!(loaded.skill_type, SkillType::Fix);

        store.delete_skill("fix-imports").unwrap();
        assert!(store.get_skill("fix-imports").unwrap().is_none());
    }

    #[test]
    fn evolution_history_tracking() {
        let store = EvolutionStore::new_in_memory().unwrap();
        store.record_evolution(&EvolutionRecord {
            skill_name: "fix-imports".into(),
            trigger: "post_task_analysis".into(),
            result: "success".into(),
            ..Default::default()
        }).unwrap();
        let history = store.get_evolution_history("fix-imports").unwrap();
        assert_eq!(history.len(), 1);
    }

    #[test]
    fn agent_health_tracking() {
        let store = EvolutionStore::new_in_memory().unwrap();
        store.record_health(&HealthRecord {
            agent_name: "code-reviewer".into(),
            task_success: true,
            duration_ms: 5000,
            ..Default::default()
        }).unwrap();
        let health = store.get_agent_health("code-reviewer").unwrap();
        assert!(!health.is_empty());
    }
}
```

- [ ] **Step 2: Implement schema.rs**

```rust
// crates/ap-evolution/src/store/schema.rs

/// SQLite DDL — must match Python evolution/store.py exactly.
pub const SCHEMA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS skills (
    name TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    skill_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    code TEXT NOT NULL,
    trigger_condition TEXT DEFAULT '',
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evolution_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_name TEXT NOT NULL,
    trigger TEXT NOT NULL,
    result TEXT NOT NULL,
    details TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (skill_name) REFERENCES skills(name)
);

CREATE TABLE IF NOT EXISTS agent_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_name TEXT NOT NULL,
    task_success INTEGER NOT NULL,
    duration_ms INTEGER,
    error_type TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_skills_agent ON skills(agent_name);
CREATE INDEX IF NOT EXISTS idx_history_skill ON evolution_history(skill_name);
CREATE INDEX IF NOT EXISTS idx_health_agent ON agent_health(agent_name);
"#;
```

- [ ] **Step 3: Implement queries.rs + mod.rs**

`queries.rs` contains all typed query methods.
`mod.rs` is the `EvolutionStore` facade with `new()`, `new_in_memory()`, and delegation to queries.

- [ ] **Step 4: Verify and commit**

```bash
cargo test -p ap-evolution -- store
git add crates/ap-evolution/src/store/
git commit -m "feat(ap-evolution): EvolutionStore with SQLite schema compatible with Python"
```

---

## Task 8.2: ExecutionAnalyzer

**Python source:** `src/agent_nexus/platform/evolution/analyzer.py`
**Rust target:** `crates/ap-evolution/src/analyzer.rs`

Post-task analysis to identify evolution opportunities (FIX, DERIVED, CAPTURED).

- [ ] **Step 1: Write analyzer tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn analyze_successful_task_no_evolution() {
        let result = TaskResult { success: true, ..Default::default() };
        let suggestions = Analyzer::analyze(&result);
        assert!(suggestions.is_empty());
    }

    #[test]
    fn analyze_failed_task_suggests_fix() {
        let result = TaskResult {
            success: false,
            error: Some("ImportError: module not found".into()),
            ..Default::default()
        };
        let suggestions = Analyzer::analyze(&result);
        assert!(suggestions.iter().any(|s| s.evolution_type == EvolutionType::Fix));
    }

    #[test]
    fn analyze_repeated_pattern_suggests_derived() {
        // After N similar tasks, suggest derived skill
    }
}
```

- [ ] **Step 2: Implement + verify + commit**

```bash
cargo test -p ap-evolution -- analyzer
git add crates/ap-evolution/src/analyzer.rs
git commit -m "feat(ap-evolution): ExecutionAnalyzer for FIX/DERIVED/CAPTURED suggestions"
```

---

## Task 8.3: SkillEvolver (via IPC)

**Python source:** `src/agent_nexus/platform/evolution/evolver.py` (443 lines)
**Rust target:** `crates/ap-evolution/src/evolver.rs`

**Key shift:** Python evolver uses in-process `IPythonRuntime`. Rust evolver uses `ap-runtime` IPC to call Agent subprocesses.

- [ ] **Step 1: Write evolver tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn evolve_fix_skill_via_ipc() {
        // Mock IPC: send skill code, get evolved code back
    }

    #[tokio::test]
    async fn evolve_derived_skill_from_context() {
        // Collect context from multiple tasks, derive new skill
    }
}
```

- [ ] **Step 2: Implement evolver**

The evolver uses `ap_runtime::ipc::IpcProtocol` to send evolution requests to an agent subprocess.
This is the key architectural improvement — no more in-process code execution for evolution.

```rust
use ap_runtime::ipc::protocol::IpcProtocol;
use crate::store::EvolutionStore;
use crate::thresholds::Thresholds;

pub struct SkillEvolver {
    store: EvolutionStore,
    thresholds: Thresholds,
}

impl SkillEvolver {
    pub fn new(store: EvolutionStore) -> Self {
        Self { store, thresholds: Thresholds::default() }
    }

    /// Evolve a FIX skill — modify existing code to handle a failure case.
    pub async fn evolve_fix(
        &self,
        skill_name: &str,
        error: &str,
        ipc: &mut IpcProtocol<impl tokio::io::AsyncRead + Unpin, impl tokio::io::AsyncWrite + Unpin>,
    ) -> Result<EvolutionOutcome, EvolverError> {
        // 1. Load skill from store
        let skill = self.store.get_skill(skill_name)?
            .ok_or(EvolverError::SkillNotFound(skill_name.into()))?;

        // 2. Send to agent via IPC for code modification
        let prompt = format!(
            "Modify this skill to handle the error: {}\n\nCurrent code:\n{}",
            error, skill.code
        );
        ipc.send_task(&prompt, &format!("evolve-{}", skill_name)).await?;

        // 3. Receive result
        let result = ipc.receive_until_result(|_| {}).await?;

        // 4. Store evolved version
        // TODO: store updated skill code

        Ok(EvolutionOutcome::Success { new_code: result.content })
    }
}

pub enum EvolutionOutcome {
    Success { new_code: String },
    NoChange,
    Failed { reason: String },
}

#[derive(Debug, thiserror::Error)]
pub enum EvolverError {
    #[error("Skill not found: {0}")]
    SkillNotFound(String),
    #[error("IPC error: {0}")]
    Ipc(#[from] ap_core::orchestration::ipc::IpcError),
    #[error("Store error: {0}")]
    Store(#[from] crate::store::StoreError),
}
```

- [ ] **Step 3: Verify and commit**

```bash
cargo test -p ap-evolution -- evolver
git add crates/ap-evolution/src/evolver.rs
git commit -m "feat(ap-evolution): SkillEvolver using IPC instead of in-process runtime"
```

---

## Task 8.4: AgentPromoter

**Python source:** `src/agent_nexus/platform/evolution/promotion.py`
**Rust target:** `crates/ap-evolution/src/promotion.rs`

Promotes a mature skill to a standalone agent (generates manifest, pyproject.toml, SKILL.md).

- [ ] **Write tests + implement**

Test that generated files are valid YAML/TOML/Markdown.
Test rollback on partial failure (track written files, clean up).

```bash
cargo test -p ap-evolution -- promotion
git add crates/ap-evolution/src/promotion.rs
git commit -m "feat(ap-evolution): AgentPromoter with atomic file generation and rollback"
```

---

## Task 8.5: Supporting modules

- `compaction.rs` — Context compaction for evolution
- `health.rs` — Health tracking and scoring
- `thresholds.rs` — Evolution threshold constants
- `context_describer.rs` — Evolution context description generation

Each is relatively small. Write tests + implement + commit individually.

---

## Task 8.6: EvolutionEngine (facade)

**Python source:** `src/agent_nexus/platform/evolution/engine.py`
**Rust target:** `crates/ap-evolution/src/engine.rs`

Unified facade that combines store + analyzer + evolver + promoter.

```rust
pub struct EvolutionEngine {
    store: EvolutionStore,
    analyzer: Analyzer,
    evolver: SkillEvolver,
    promoter: AgentPromoter,
}

impl EvolutionEngine {
    pub fn new(store: EvolutionStore) -> Self {
        let analyzer = Analyzer::new();
        let evolver = SkillEvolver::new(store.clone()); // or Arc
        let promoter = AgentPromoter::new();
        Self { store, analyzer, evolver, promoter }
    }

    /// Post-task evolution pipeline.
    pub async fn post_task_evolve(&self, result: &TaskResult) -> Vec<EvolutionOutcome> {
        let suggestions = self.analyzer.analyze(result);
        let mut outcomes = Vec::new();
        for suggestion in suggestions {
            // Apply suggestion via evolver
        }
        outcomes
    }
}
```

- [ ] **Verify and commit**

```bash
cargo test -p ap-evolution -- engine
git add crates/ap-evolution/src/engine.rs crates/ap-evolution/src/lib.rs
git commit -m "feat(ap-evolution): EvolutionEngine facade"
```

---

## Final Verification

- [ ] `cargo test -p ap-evolution`
- [ ] `cargo clippy -p ap-evolution -- -D warnings`
