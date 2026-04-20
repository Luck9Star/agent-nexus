# Refactor Protocol

> **When to use**: Rust rewrite, module splitting, abstraction extraction, API interface changes, performance optimization (change implementation, not behavior).
> **Not for**: New features → use feature-dev-protocol; bug fixes → use quality-protocol.
> **Core principle**: Behavior unchanged = tests unchanged. Small steps, each step verifiable.

---

## Protocol Architecture

```
Phase R1: Baseline Establishment
  → Output: Existing tests all green + invariants checklist

Phase R2: Interface Contract
  → Output: Input/output contract definition

Phase R3: Small-Step Migration
  → Output: Incremental replacement, each step tests green

Phase R4: Contract Verification
  → Output: New implementation passes original tests + interface compatible
```

---

## Phase R1: Baseline Establishment

### SOP

1. **Record Test Baseline**:
   ```bash
   uv run pytest tests/ -x -q  # Must all pass
   # Record test count
   ```

2. **Identify Invariants** — List behaviors within refactor scope that must be preserved:
   ```markdown
   ## Invariants: [refactor scope]

   - [ ] `add_task()` returns task_id, status=READY or BLOCKED
   - [ ] `get_ready()` only returns READY status tasks with no blocking dependencies
   - [ ] `close()` releases all SQLite connections
   - [ ] lockfile.json format consistent with existing
   ```

3. **Impact Analysis**:
   ```bash
   gitnexus_impact --target [refactor_target] --direction upstream
   ```
   - What are the d=1 callers?
   - What are the d=2 indirect dependencies?
   - Where are the cross-language interfaces (Python↔Rust)?

4. **Identify Risk Points**:
   - Does it involve Pydantic model serialization format? → Cannot change JSON structure
   - Does it involve SQLite schema? → Migration needed
   - Does it involve IPC protocol? → Cannot change message format
   - Does it involve lockfile.json? → Python/Rust compatibility test needed

### R1 Exit Conditions

- [ ] Full test suite green
- [ ] Invariants checklist listed
- [ ] gitnexus_impact run, d=1 callers identified
- [ ] Risk points annotated

---

## Phase R2: Interface Contract

### SOP

1. **Define Interface Contract** — Specify which input/output aspects are invariant:

   For Python→Python refactoring (module splitting/abstraction extraction):
   ```markdown
   ## Contract: [module name]

   ### Public API (immutable)
   - `def foo(x: str) -> int` — unchanged
   - `async def bar(data: dict) -> list[str]` — unchanged

   ### Internal API (mutable)
   - `_helper()` → can rename/delete/extract
   ```

   For Python→Rust rewrite:
   ```markdown
   ## Contract: [crate name]

   ### File Formats (must be compatible)
   - lockfile.json: read/write format identical to Python version
   - config.toml: parse results identical to Python version
   - TOML DAG templates: parse behavior identical to Python version

   ### IPC Message Format (must be compatible)
   - PlatformToAgent JSON schema
   - AgentToPlatform JSON schema
   - Error response format

   ### Behavioral Contract (must pass original tests)
   - All test files test_xxx.py pass
   - Boundary behavior: empty input, timeout, process crash
   ```

2. **Write Contract Tests** (Python→Rust scenario):
   ```python
   # Test Python and Rust reading/writing same lockfile.json
   def test_lockfile_compatibility():
       py_data = python_write_lockfile(...)
       rs_data = rust_read_lockfile(...)
       assert py_data == rs_data
   ```

3. **Contract Review** — Use `/code-review-expert` to review contract definition

### R2 Exit Conditions

- [ ] Contract definition output (Public API / File Formats / IPC Message Format)
- [ ] Contract tests written (cross-language scenario)
- [ ] Contract review passed

---

## Phase R3: Small-Step Migration

### SOP

1. **Change one module at a time** — Do not modify multiple modules in parallel:
   ```
   Step 1: Change module_a → test → commit
   Step 2: Change module_b → test → commit
   Step 3: Change module_c → test → commit
   ```

2. **Verify After Each Step**:
   ```bash
   # Verify immediately after change
   uv run pytest tests/unit/test_changed_module.py -v
   uv run pytest tests/ -x -q
   ```

3. **Test Strategy**:
   - **Python→Python**: Original tests unchanged; new implementation must pass original tests
   - **Python→Rust**: Run Python tests first to record expectations; Rust must produce identical results
   - **API Changes**: Add compatibility layer (old API → new API) first, remove in next version

4. **Rollback Strategy**: Each step is an independent commit; failure → `git revert`

5. **Prohibitions**:
   - Do not change implementation and tests simultaneously (if implementation changes, tests should pass unchanged)
   - Do not modify multiple modules at once (serial, not parallel)
   - Do not "opportunistically" add new features during refactoring (Feature and Refactor are separate)

### R3 Exit Conditions

- [ ] All steps complete
- [ ] Full test suite green
- [ ] Each step is an independent commit
- [ ] Invariants checklist verified item by item

---

## Phase R4: Contract Verification

### SOP

1. **Invariants Verification Item by Item**:
   ```markdown
   ## Invariants Check: [refactor scope]

   - [x] `add_task()` returns task_id, status=READY or BLOCKED → test passed
   - [x] `get_ready()` only returns READY status → test passed
   - [x] `close()` releases connections → test passed
   ```

2. **d=1 Caller Regression** — Run tests for every d=1 caller identified by gitnexus_impact

3. **Cross-Language Compatibility** (Python→Rust scenario):
   - Python writes file, Rust reads → consistent
   - Rust writes file, Python reads → consistent
   - Same input, both sides produce output → consistent

4. **Changeset Verification**:
   ```bash
   gitnexus_detect_changes  # Confirm only expected scope changed
   ```

5. **Hand Off to Quality Protocol**:
   - Output summary: what was refactored, whether interfaces changed, whether compatibility was verified

### R4 Exit Conditions

- [ ] Full test suite green
- [ ] All invariants passed
- [ ] d=1 caller tests passed
- [ ] Cross-language compatibility tests passed (if applicable)
- [ ] gitnexus_detect_changes scope correct
- [ ] Handoff summary output

---

## Rust Rewrite Special Constraints

Phase 7 Rust rewrite is a special refactoring scenario with additional constraints:

### Per-Crate Replacement Order

```
ap-core     → Core types (pure data, no I/O)
ap-fetcher  → Git package fetching (replaces installer.py)
ap-runtime  → Agent Supervisor (replaces process_manager.py + supervisor.py)
ap-gateway  → MCP Gateway (replaces gateway.py + deferred_registry.py)
ap-cli      → CLI (replaces cli.py)
```

### Replacement Strategy

1. **ap-core first** — No runtime involved, only type definitions
2. **ap-fetcher → ap-runtime → ap-gateway → ap-cli** — Dependencies require this order
3. **After each crate replacement, Python version retained but marked deprecated** — Coexistence period

### Compatibility Test Checklist

```markdown
- [ ] lockfile.json: Python write → Rust read
- [ ] lockfile.json: Rust write → Python read
- [ ] config.toml: Python parse == Rust parse
- [ ] IPC messages: Python→Agent == Rust→Agent
- [ ] CLI output: Python format == Rust format
```

### What Stays Unchanged

- Agent Runtime (Python) — Python forever
- IPC Message Format — Do not change
- SKILL.md Format — Do not change
- TOML DAG Template Format — Do not change

---

## Anti-Patterns (Forbidden)

| # | Anti-Pattern | Why Forbidden |
|---|-------------|---------------|
| 1 | Changing implementation and tests simultaneously | Loses baseline, cannot verify behavior unchanged |
| 2 | Refactoring multiple modules at once | One failure affects all; rollback difficult |
| 3 | "Opportunistically" adding features during refactoring | Feature + Refactor mixed = change scope uncontrolled |
| 4 | Skipping cross-language compatibility tests | Python/Rust coexistence period, format mismatch = data loss |
| 5 | Deleting old implementation before running compatibility tests | Both sides must be able to read/write the same data |
| 6 | Changing IPC message format | MCP protocol boundary is language boundary, do not touch |

---

## Relationship to Other Protocols

```
Refactor Protocol (this file)
    |
    +-- R4 complete
    |   +--> Quality Protocol (quality-protocol.md)
    |
    +-- R1 discovers new feature needed
    |   +--> Feature Dev Protocol (feature-dev-protocol.md)
    |
    +-- R3 finds bugs during refactoring
        +--> Record them; fix after refactoring completes (do not switch tasks mid-protocol)
```
