# Rust Rewrite Plan — Review Findings

> **Reviewer:** Claude Sonnet 4.6
> **Date:** 2026-04-22
> **Scope:** 12 sub-plan files (00-11) vs Python source code
> **Method:** Line-by-line cross-reference of plan code against actual Python models + platform modules

```
┌──────────────────────────────────────────────────────────────────┐
│ KPI Summary                                                      │
├──────────────┬──────┬──────┬──────┬──────┬───────┬───────────────┤
│ Severity     │  P0  │  P1  │  P2  │  P3  │ Total │ Status        │
├──────────────┼──────┼──────┼──────┼──────┼───────┼───────────────┤
│ Count        │   3  │   5  │   5  │   4  │  17   │ NEEDS REWORK  │
└──────────────┴──────┴──────┴──────┴──────┴───────┴───────────────┘
```

**Verdict:** Plans cannot be executed as-is. 3 P0 issues mean Phase 01 will produce code that is wire-incompatible with Python agents. Fix P0+P1 before any implementation starts.

---

## P0 — Wire Format Incompatibility (will break at runtime)

### F-01: IPC Protocol — Flat Struct vs Tagged Enum

**Phase:** 01 (Task 1.1)
**Python source:** `models/ipc.py:37-71`

Python uses **flat structs** with a `type` discriminator field, NOT a tagged union:

```python
# Python: AgentToPlatform is ONE class with ALL optional fields
class AgentToPlatform(FrozenModel):
    type: AgentToPlatformType
    content: str = ""
    task_id: str | None = None
    message: str | None = None
    progress_pct: float | None = None
    error: str | None = None
    status: str | None = None
    output: Any | None = None
```

Plan uses `#[serde(tag = "type")] enum` which produces a **completely different wire format**:

```rust
// Plan: tagged enum — each variant has DIFFERENT fields
#[serde(tag = "type")]
pub enum AgentToPlatform {
    Result { content, task_id, success },    // "success" doesn't exist in Python!
    Progress { content, task_id, progress_pct },
    Error { error, error_type, task_id },    // "error_type" doesn't exist in Python!
}
```

**Specific field mismatches:**

| Plan Field | Python Equivalent | Issue |
|-----------|-------------------|-------|
| `Result.success` | Does not exist | Fabricated field |
| `Error.error_type` | Does not exist | Fabricated field |
| `Progress.progress_pct` | `progress_pct` | Type: plan uses `Option<u8>`, Python uses `float` 0.0-100.0 |
| Missing from Result | `message`, `status`, `output` | 3 fields dropped |
| Missing from Error | `content`, `status` | 2 fields dropped |

**Python also has** `MessageDirection` enum and `IPCMessage` envelope class (lines 14-125) — completely omitted.

**Impact:** Rust platform cannot communicate with Python agents. Every IPC message will fail serialization/deserialization.

**Fix:** Use flat structs matching Python exactly:
```rust
pub struct PlatformToAgent {
    #[serde(rename = "type")]
    pub msg_type: PlatformToAgentType,
    pub content: String,
    pub task_id: Option<String>,
    pub conversation_id: Option<String>,
    pub ref_id: Option<String>,
    pub summary: Option<String>,
}
```

---

### F-02: Evolution SQLite Schema — Completely Wrong

**Phase:** 08 (Task 8.1)
**Python source:** `platform/evolution/store.py:84-165`

Plan invents tables that **don't exist** in Python:

| Plan Table | Python Table | Status |
|-----------|-------------|--------|
| `skills` | `skill_records` | WRONG NAME |
| `evolution_history` | `execution_analyses` + `skill_judgments` | WRONG STRUCTURE |
| `agent_health` | Does not exist | FABRICATED |

**Python's actual 6 tables:**
1. `skill_records` — 14 columns (id, name, version, lineage_origin, lineage_generation, lineage_content_diff, lineage_content_snapshot, directory, is_active, total_selections, total_applied, total_completions, total_fallbacks, created_at, updated_at)
2. `skill_lineage_parents` — many-to-many (skill_id, parent_id)
3. `execution_analyses` — task analysis results
4. `skill_judgments` — skill usage tracking per analysis
5. `context_budget_log` — compaction events
6. `agent_records` — agent metadata with effective_rate, avg_steps, avg_duration_ms

**Impact:** Rust cannot read Python-written evolution SQLite files. Phase 11 compatibility tests will fail.

**Fix:** Copy schema verbatim from `store.py:84-165` SQL. The `_SCHEMA_SQL` string is the source of truth.

---

### F-03: Lockfile Schema — Wrong Fields

**Phase:** 07 (Task 7.2)
**Python source:** `models/distribution.py:60-105`

Plan's `LockEntry`:
```rust
pub struct LockEntry {
    source, version, path, installed_at, git_hash
}
```

Python's `LockfileEntry`:
```python
class LockfileEntry(FrozenModel):
    version: str          # has regex validation: r"^[a-zA-Z0-9._-]+$"
    source: str
    commit_sha: str       # NOT git_hash! Validates as 40/64 hex or 'latest'/'head'
    agent_type: AgentType # MISSING from plan
    installed_at: datetime
    venv_path: str        # MISSING from plan
    dependencies: list[str]  # MISSING from plan
```

**Missing fields:** `agent_type`, `venv_path`, `dependencies`
**Wrong field name:** `git_hash` → should be `commit_sha`

**Impact:** Cannot read Python-written `lockfile.json`.

---

## P1 — Missing Coverage (will leave gaps)

### F-04: distribution.py — Entire File Missing

**Phase:** 01 (no coverage)
**Python source:** `models/distribution.py` (150 lines)

Contains 7 types not covered anywhere in the plan:
- `SourceType` (official/private/direct)
- `InstallationStatus` (installed/outdated/not_installed/installing/failed)
- `SourceEntry` (with `_validate_git_url` validator)
- `LockfileEntry` (described above)
- `Lockfile`
- `PackageSource` (extends SourceEntry with `local_cache`)
- `IndexEntry` (with `_reject_path_traversal` validator)

These are used by `ap-fetcher` (Phase 07). Phase 07's `sources.rs` and `lockfile.rs` define local types that **don't match** the Python models.

**Fix:** Add Task 1.5 to Phase 01 for distribution models. Phase 07 should reference these, not redefine them.

---

### F-05: ContextBudget — Completely Different Structure

**Phase:** 01 (context.rs)
**Python source:** `models/context.py:43-104`

Plan's `ContextBudget`:
```rust
struct ContextBudget {
    max_tokens: u32, used_tokens: u32,
    compaction_threshold: f64, compaction_target: f64
}
```

Python's `ContextBudget`:
```python
class ContextBudget:
    l0_max: int = 800
    l1_max: int = 3000
    bootstrap_max: int = 5000
    single_file_max: int = 8000
    compaction_trigger: float = 0.8    # plan calls it "compaction_threshold"
    compaction_target: float = 0.4
    session_hard_ceiling: float = 0.95  # MISSING
    forced_truncate_threshold: float = 0.9  # MISSING
    min_turns_between_compactions: int = 5  # MISSING
    consecutive_compaction_alert: int = 3  # MISSING
    # + cross-field validator: trigger > target, truncate < ceiling, l0+l1 <= bootstrap
```

**Also missing from context.py:** `ContextLevel` (L0-L3 IntEnum), `BudgetAlertLevel`, `TokenUsage` (with `check_budget` method), `ContextBudgetLogEntry`.

**Impact:** Context management won't work correctly. Tiered loading (L0-L3) is a core feature.

---

### F-06: TaskGraph Field Name Bug

**Phase:** 03 (Task 3.1)
**Cross-reference:** Phase 01 TaskItem model vs Phase 03 SQL schema

`TaskItem` model (Phase 01) uses field name `id`.
TaskGraph SQL (Phase 03) uses column name `task_id`.
`add_task` method references `task.task_id` — **this field doesn't exist** on the model.

```rust
// Phase 03 add_task references:
params![task.task_id, task.agent_name, ...]  // BUG: should be task.id
```

Also: `blocked_by` is `Vec<String>` in model but `TEXT` in SQL — needs JSON serialization or comma-separated storage. Not addressed.

---

### F-07: Evolution Models — Missing Types

**Phase:** 01 (evolution.rs) + Phase 08
**Python source:** `models/evolution.py`

Phase 01 omits:
- `EvolutionMetrics` (lines 97-123) — standalone metrics with same counter validators as SkillRecord
- `EvolutionContext` (lines 126-141) — passed to evolver with `agent_id`, `task_id`, `skill_ids_used`, etc.

Phase 08 references types that don't exist in any model:
- `EvolutionRecord` — used in `store.record_evolution()` but never defined
- `HealthRecord` — used in `store.record_health()` but never defined
- `TaskResult` — used in `Analyzer::analyze()` but never defined
- `EvolutionSuggestion` — referenced in design spec but never defined in plan

---

### F-08: HookExecutor Uses Non-Existent Types

**Phase:** 05 (Task 5.1)

Plan creates `HookAction::Command` and `HookAction::Script` enum — these don't exist in the models. The actual `HookDefinition` model uses:
```rust
hook_type: HookType,  // command/http/prompt/agent
command: Option<String>,
url: Option<String>,
prompt: Option<String>,
model: Option<String>,
```

The executor should dispatch on `hook_type`, not a fabricated `HookAction` enum.

---

## P2 — Code Quality Issues (will cause compilation errors or confusion)

### F-09: Duplicate WorkflowPhase Type

**Phase:** 01 `models/composition.rs` defines `WorkflowPhase` as a data struct:
```rust
pub struct WorkflowPhase { pub phase: String, pub tasks: Vec<String> }
```

**Phase:** 04 `router/workflow.rs` defines `WorkflowPhase` as an enum:
```rust
pub enum WorkflowPhase { Research, Synthesis, Implementation, Verification }
```

Same name, different types. Will cause compilation conflicts when both are imported.

**Fix:** Rename one. The model struct could be `WorkflowPhaseEntry`, the enum stays `WorkflowPhase`.

---

### F-10: ProcessManager Doesn't Expose I/O

**Phase:** 03 (Task 3.5)

`ManagedProcess` stores stdin/stdout as boxed trait objects but provides no method to access them. Other modules (IPC, Protocol) need these handles. Without accessors, the ProcessManager is useless for IPC.

**Fix:** Add methods like `fn stdin(&mut self) -> &mut dyn AsyncWrite` and `fn stdout(&mut self) -> &mut dyn AsyncRead`, or redesign to split ownership.

---

### F-11: Composition Logic Missing

**Phase:** 01 (composition.rs)
**Python source:** `models/composition.py`

Python's `Composition` has 3 methods: `get_root_tasks()`, `get_dependents()`, `get_execution_order()`, plus `from_toml()` class method and cycle detection. The plan only defines data structs without this logic.

The `from_toml()` parser is critical — it's how composition.toml files are loaded.

---

### F-12: Model Validators Dropped

**Phase:** 01 (multiple files)

Python has Pydantic `model_validator`s that enforce business invariants. All dropped without replacement:

| Model | Validator | Purpose |
|-------|-----------|---------|
| `McpServerConfig` | `_validate_transport_fields` | stdio needs command, sse needs url |
| `AgentManifest` | `_validate_permission_consistency` | permission_mode vs permissions.mode |
| `HookExecution` | `_validate_passed_blocked` | can't pass and block simultaneously |
| `ContextBudget` | `_validate_thresholds` | trigger > target, truncate < ceiling |
| `SkillRecord` | `_validate_counters` | counter invariants |
| `SourceEntry` | `_validate_git_url` | git source requires url |
| `IndexEntry` | `_reject_path_traversal` | no ".." in path |

**Fix:** Add Rust-side validation (either `TryFrom` or custom `Deserialize` or builder pattern with validation).

---

### F-13: Evolution Line 92 Validator Incomplete

**Phase:** 01 (evolution.rs)

Plan's `SkillRecord::validate_counters()` has 4 checks. Python has 5 — missing:
```python
if self.total_completions + self.total_fallbacks > self.total_applied:
    raise ValueError(...)
```

---

## P3 — Minor Issues (won't block, but should fix)

### F-14: Source YAML Format Inconsistency

**Phase:** 07 (sources.rs tests)

Test uses YAML with `sources:` top-level key:
```yaml
sources:
  - name: official
    url: ...
```

But `SourceManager::parse()` deserializes as `Vec<SourceEntry>` directly (no `sources` wrapper). The Python `SourceEntry` from `distribution.py` also doesn't have a wrapper. Inconsistent test vs implementation.

---

### F-15: Phase 03 IPC Test Has Syntax Error

**Phase:** 03 (Task 3.2)

The `oversized_message_rejected` test has malformed code:
```rust
let (client, server) = duplex(8 * 1024}}],
```
Extra `}]` — copy-paste artifact.

---

### F-16: Phase 08 Evolution Total Line Count Wrong

**Design spec:** States 3,835 lines for evolution.
**Actual:** `wc -l` shows 5,227 lines (store.py alone is 1,392).

The plan underestimates complexity. Consider splitting Phase 08 into 08a (store) + 08b (engine).

---

### F-17: Phase 10 Commands Are All Stubs

**Phase:** 10

All command implementations are `// TODO` placeholders with no concrete guidance. A subagent given this plan won't know what to implement. Each command needs:
- What crate function to call
- What arguments to pass
- What error handling to do
- What output format to produce

---

## Recommended Fix Order

```
┌────────────────────────────────────────────────────────────┐
│ Fix Priority — Blockers First                              │
├─────┬──────────────────────────────────────────────────────┤
│  1  │ F-01: Fix IPC to flat struct (P0, affects all IPC)   │
│  2  │ F-02: Copy exact SQLite schema from store.py (P0)    │
│  3  │ F-03: Fix LockfileEntry fields (P0)                  │
│  4  │ F-04: Add distribution.py models (P1)                │
│  5  │ F-05: Fix ContextBudget structure (P1)               │
│  6  │ F-06: Fix TaskGraph field names (P1)                 │
│  7  │ F-07: Add missing evolution types (P1)               │
│  8  │ F-08: Fix HookExecutor dispatch (P1)                 │
│  9  │ F-09..13: P2+P3 fixes                              │
└─────┴──────────────────────────────────────────────────────┘
```

Fix F-01 through F-08 first, then regenerate Phase 01 and Phase 08 sub-plans. The remaining phases (03, 05, 07) need targeted patches, not full rewrites.
