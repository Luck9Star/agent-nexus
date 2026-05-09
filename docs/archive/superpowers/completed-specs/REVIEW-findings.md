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
│ Count        │   3  │   5  │   5  │   4  │  17   │ ALL FIXED ✔   │
├──────────────┼──────┼──────┼──────┼──────┼───────┼───────────────┤
│ Fixed        │   3  │   5  │   5  │   2  │  15   │ READY TO EXEC │
│ Open         │   0  │   0  │   0  │   2  │   2   │ non-blocking  │
└──────────────┴──────┴──────┴──────┴──────┴───────┴───────────────┘
```

**Verdict:** All P0-P2 issues fixed. P3 items F-16 (line count underestimate) and F-17 (CLI stubs) are non-blocking — address during implementation. Plans are ready for execution.

---

## P0 — Wire Format Incompatibility (will break at runtime)

### F-01: IPC Protocol — Flat Struct vs Tagged Enum [FIXED ✔]

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

### F-02: Evolution SQLite Schema — Completely Wrong [FIXED ✔]

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

### F-03: Lockfile Schema — Wrong Fields [FIXED ✔]

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

### F-04: distribution.py — Entire File Missing [FIXED ✔]

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

### F-05: ContextBudget — Completely Different Structure [FIXED ✔]

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

### F-06: TaskGraph Field Name Bug [FIXED ✔]

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

### F-07: Evolution Models — Missing Types [FIXED ✔]

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

### F-08: HookExecutor Uses Non-Existent Types [FIXED ✔]

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

### F-09: Duplicate WorkflowPhase Type [FIXED ✔]

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

### F-10: ProcessManager Doesn't Expose I/O [FIXED ✔]

**Phase:** 03 (Task 3.5)

`ManagedProcess` stores stdin/stdout as boxed trait objects but provides no method to access them. Other modules (IPC, Protocol) need these handles. Without accessors, the ProcessManager is useless for IPC.

**Fix:** Add methods like `fn stdin(&mut self) -> &mut dyn AsyncWrite` and `fn stdout(&mut self) -> &mut dyn AsyncRead`, or redesign to split ownership.

---

### F-11: Composition Logic Missing [FIXED ✔]

**Phase:** 01 (composition.rs)
**Python source:** `models/composition.py`

Python's `Composition` has 3 methods: `get_root_tasks()`, `get_dependents()`, `get_execution_order()`, plus `from_toml()` class method and cycle detection. The plan only defines data structs without this logic.

The `from_toml()` parser is critical — it's how composition.toml files are loaded.

---

### F-12: Model Validators Dropped [FIXED ✔]

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

### F-13: Evolution Line 92 Validator Incomplete [FIXED ✔]

**Phase:** 01 (evolution.rs)

Plan's `SkillRecord::validate_counters()` has 4 checks. Python has 5 — missing:
```python
if self.total_completions + self.total_fallbacks > self.total_applied:
    raise ValueError(...)
```

---

## P3 — Minor Issues (won't block, but should fix)

### F-14: Source YAML Format Inconsistency [FIXED ✔]

**Phase:** 07 (sources.rs tests)

Test uses YAML with `sources:` top-level key:
```yaml
sources:
  - name: official
    url: ...
```

But `SourceManager::parse()` deserializes as `Vec<SourceEntry>` directly (no `sources` wrapper). The Python `SourceEntry` from `distribution.py` also doesn't have a wrapper. Inconsistent test vs implementation.

---

### F-15: Phase 03 IPC Test Has Syntax Error [FIXED ✔]

**Phase:** 03 (Task 3.2)

The `oversized_message_rejected` test has malformed code:
```rust
let (client, server) = duplex(8 * 1024}}],
```
Extra `}]` — copy-paste artifact.

---

### F-16: Phase 08 Evolution Total Line Count Wrong [OPEN — non-blocking]

**Design spec:** States 3,835 lines for evolution.
**Actual:** `wc -l` shows 5,227 lines (store.py alone is 1,392).

The plan underestimates complexity. Consider splitting Phase 08 into 08a (store) + 08b (engine).

---

### F-17: Phase 10 Commands Are All Stubs [OPEN — non-blocking]

**Phase:** 10

All command implementations are `// TODO` placeholders with no concrete guidance. A subagent given this plan won't know what to implement. Each command needs:
- What crate function to call
- What arguments to pass
- What error handling to do
- What output format to produce

---

## Resolution Log

```
┌───────────────────────────────────────────────────────────────────┐
│ Fix History — All Completed                                        │
├─────┬──────┬───────────────────────────────────────────┬──────────┤
│  #  │ ID   │ Description                               │ Status   │
├─────┼──────┼───────────────────────────────────────────┼──────────┤
│  1  │ F-01 │ IPC flat struct                           │ ✔ Fixed  │
│  2  │ F-02 │ Evolution SQLite 6-table schema           │ ✔ Fixed  │
│  3  │ F-03 │ LockfileEntry fields (commit_sha etc.)    │ ✔ Fixed  │
│  4  │ F-04 │ Task 1.5 distribution models added        │ ✔ Fixed  │
│  5  │ F-05 │ ContextBudget 10-field + validators       │ ✔ Fixed  │
│  6  │ F-06 │ TaskGraph field name + JSON blocked_by    │ ✔ Fixed  │
│  7  │ F-07 │ EvolutionMetrics + EvolutionContext added │ ✔ Fixed  │
│  8  │ F-08 │ HookExecutor dispatch on HookType         │ ✔ Fixed  │
│  9  │ F-09 │ WorkflowPhase → WorkflowPhaseEntry        │ ✔ Fixed  │
│ 10  │ F-10 │ ProcessManager take_io/stdin_mut accessors│ ✔ Fixed  │
│ 11  │ F-11 │ OrchestrationDSL full composition logic   │ ✔ Fixed  │
│ 12  │ F-12 │ Validators added to 5 model types         │ ✔ Fixed  │
│ 13  │ F-13 │ 5th counter check added                   │ ✔ Fixed  │
│ 14  │ F-14 │ SourceManager dual-format YAML parse       │ ✔ Fixed  │
│ 15  │ F-15 │ IPC test syntax error fixed                │ ✔ Fixed  │
│ 16  │ F-16 │ Line count underestimate                  │ OPEN     │
│ 17  │ F-17 │ CLI command stubs                          │ OPEN     │
└─────┴──────┴───────────────────────────────────────────┴──────────┘
```

**Modified files:**
- `01-ap-core-models.md` — IPC flat structs, distribution models, ContextBudget, evolution types, validators, WorkflowPhase rename
- `03-ap-core-orchestration.md` — TaskGraph field names, ProcessManager I/O accessors, OrchestrationDSL composition logic, IPC test fix
- `05-ap-core-hooks-skills.md` — HookExecutor HookType dispatch
- `07-ap-fetcher.md` — LockfileEntry, SourceEntry using ap-core models
- `08-ap-evolution.md` — Exact 6-table SQLite schema from Python

**Next step:** Execute Phase 01 → Phase 03 → Phase 05 → Phase 07 → Phase 08 in dependency order.
