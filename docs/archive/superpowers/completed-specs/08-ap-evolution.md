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

> **F-02 fix:** Schema must be IDENTICAL to Python version.
> The original plan invented 3 wrong tables (`skills`, `evolution_history`, `agent_health`).
> Python actually has **6 tables**: `skill_records`, `skill_lineage_parents`,
> `execution_analyses`, `skill_judgments`, `context_budget_log`, `agent_records`.
> The exact SQL from `store.py:83-166` is the source of truth.

Schema must be identical to Python version so the same SQLite file can be read/written.

- [ ] **Step 1: Write store schema tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn init_schema_in_memory() {
        let store = EvolutionStore::new_in_memory().unwrap();
        let tables = store.list_tables();
        assert!(tables.contains(&"skill_records".to_string()));
        assert!(tables.contains(&"skill_lineage_parents".to_string()));
        assert!(tables.contains(&"execution_analyses".to_string()));
        assert!(tables.contains(&"skill_judgments".to_string()));
        assert!(tables.contains(&"context_budget_log".to_string()));
        assert!(tables.contains(&"agent_records".to_string()));
    }

    #[test]
    fn backward_compat_read_python_db() {
        // Load a fixture SQLite file written by Python
        // Verify all 6 tables are readable
    }

    #[test]
    fn skill_record_crud() {
        let store = EvolutionStore::new_in_memory().unwrap();
        let skill = SkillRecord {
            id: "s-uuid-1".into(),
            name: "fix-imports".into(),
            version: "1.0.0".into(),
            lineage: SkillLineage::default(),
            directory: "skills/fix-imports".into(),
            is_active: true,
            total_selections: 0,
            total_applied: 0,
            total_completions: 0,
            total_fallbacks: 0,
        };
        store.insert_skill(&skill).unwrap();
        let loaded = store.get_skill_by_name("fix-imports").unwrap().unwrap();
        assert_eq!(loaded.name, "fix-imports");
        assert_eq!(loaded.id, "s-uuid-1");

        store.delete_skill("s-uuid-1").unwrap();
        assert!(store.get_skill_by_name("fix-imports").unwrap().is_none());
    }

    #[test]
    fn execution_analysis_roundtrip() {
        let store = EvolutionStore::new_in_memory().unwrap();
        store.insert_execution_analysis(
            "a-uuid-1", "t-uuid-1", "code-reviewer",
            "Analysis of task...", Some("[\"fix-imports\"]"),
        ).unwrap();
        let analyses = store.get_analyses_for_task("t-uuid-1").unwrap();
        assert_eq!(analyses.len(), 1);
    }

    #[test]
    fn context_budget_log_roundtrip() {
        let store = EvolutionStore::new_in_memory().unwrap();
        store.insert_context_budget_log(
            "cbl-1", "code-reviewer", "compaction",
            Some(5000), Some(2000), Some("{\"reason\":\"trigger_threshold\"}"),
        ).unwrap();
    }

    #[test]
    fn agent_record_roundtrip() {
        let store = EvolutionStore::new_in_memory().unwrap();
        store.upsert_agent_record(
            "ar-1", "code-reviewer", "atomic",
            "[]", None,
        ).unwrap();
        let agent = store.get_agent_record("code-reviewer").unwrap().unwrap();
        assert_eq!(agent.name, "code-reviewer");
        assert_eq!(agent.agent_type, "atomic");
    }
}
```

- [ ] **Step 2: Implement schema.rs — EXACT copy from Python store.py:83-166**

```rust
// crates/ap-evolution/src/store/schema.rs

/// SQLite DDL — verbatim copy from Python evolution/store.py:83-166.
///
/// DO NOT modify this schema. It must be identical so that Rust and Python
/// can read/write the same SQLite file.
///
/// 6 tables:
///   skill_records          — Skill identity + lineage + quality counters (14 cols)
///   skill_lineage_parents  — DAG edges (many-to-many)
///   execution_analyses     — Post-task analysis (one per task per agent)
///   skill_judgments        — Per-skill assessment within an analysis
///   context_budget_log     — Token usage / compaction observability
///   agent_records          — Composite Agent evolution tracking (Layer 2)
pub const SCHEMA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS skill_records (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '1.0.0',
    lineage_origin TEXT NOT NULL DEFAULT 'imported',
    lineage_generation INTEGER NOT NULL DEFAULT 0,
    lineage_content_diff TEXT,
    lineage_content_snapshot TEXT,
    directory TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    total_selections INTEGER NOT NULL DEFAULT 0,
    total_applied INTEGER NOT NULL DEFAULT 0,
    total_completions INTEGER NOT NULL DEFAULT 0,
    total_fallbacks INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sr_active ON skill_records(is_active);
CREATE INDEX IF NOT EXISTS idx_sr_name ON skill_records(name);
CREATE INDEX IF NOT EXISTS idx_sr_updated ON skill_records(updated_at);

CREATE TABLE IF NOT EXISTS skill_lineage_parents (
    skill_id TEXT NOT NULL,
    parent_id TEXT NOT NULL,
    PRIMARY KEY (skill_id, parent_id),
    FOREIGN KEY (skill_id) REFERENCES skill_records(id),
    FOREIGN KEY (parent_id) REFERENCES skill_records(id)
);
CREATE INDEX IF NOT EXISTS idx_lp_parent ON skill_lineage_parents(parent_id);

CREATE TABLE IF NOT EXISTS execution_analyses (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,
    analysis TEXT NOT NULL,
    evolution_suggestions TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ea_task ON execution_analyses(task_id);

CREATE TABLE IF NOT EXISTS skill_judgments (
    id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    selected INTEGER NOT NULL DEFAULT 0,
    applied INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0,
    fell_back INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (analysis_id) REFERENCES execution_analyses(id),
    FOREIGN KEY (skill_id) REFERENCES skill_records(id)
);
CREATE INDEX IF NOT EXISTS idx_sj_skill ON skill_judgments(skill_id);
CREATE INDEX IF NOT EXISTS idx_sj_analysis ON skill_judgments(analysis_id);

CREATE TABLE IF NOT EXISTS context_budget_log (
    id TEXT PRIMARY KEY,
    agent_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    tokens_before INTEGER,
    tokens_after INTEGER,
    details TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cbl_agent ON context_budget_log(agent_name);
CREATE INDEX IF NOT EXISTS idx_cbl_agent_created
    ON context_budget_log(agent_name, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_records (
    agent_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'atomic',
    skill_ids TEXT DEFAULT '[]',
    orchestration_toml TEXT,
    effective_rate REAL DEFAULT 0.0,
    avg_steps REAL,
    avg_duration_ms REAL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ar_active ON agent_records(is_active);
CREATE INDEX IF NOT EXISTS idx_ar_name ON agent_records(name);
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
