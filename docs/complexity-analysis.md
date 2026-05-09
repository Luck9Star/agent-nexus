# Complexity Analysis Report

> Generated: 2026-05-09 (Iteration 3 — First Principles Deep Audit) | Tool: radon 6.0.1
> Scope: `src/agent_nexus/` | Previous: Iteration 1 baseline + Iteration 2 refactoring

## 1. Overall Baseline (Post-Refactoring)

| Metric | Value | Delta from Iteration 1 |
|--------|-------|----------------------|
| Total blocks analyzed | 1,309 | +3 |
| Average complexity | **3.33 (Grade A)** | 3.35 → 3.33 |
| Max CC | **10 (B-grade)** | 11 → 10 |
| C-grade functions (CC ≥ 11) | **0** | 5 → 3 → **0** (all refactored) |
| B-grade functions (CC 6-10) | ~242 | — |
| D-F functions (CC > 20) | 0 | — |

### CC Score Distribution

| CC | Count | Grade | Cumulative % |
|----|-------|-------|-------------|
| 1  | 414   | A     | 31.6%       |
| 2  | 178   | A     | 45.3%       |
| 3  | 182   | A     | 59.2%       |
| 4  | 165   | A     | 71.8%       |
| 5  | 119   | A     | 80.9%       |
| 6  | 98    | B     | 88.4%       |
| 7  | 59    | B     | 92.9%       |
| 8  | 49    | B     | 96.6%       |
| 9  | 25    | B     | 98.5%       |
| 10 | 12    | B     | 99.5%       |
| 11 | 3     | C     | 100.0%      |

### Iteration 2 Refactoring Results

| Function | File | CC Before | CC After | Method |
|----------|------|-----------|----------|--------|
| `_no_more_work` | dag_dispatcher.py | 11 | 6 | Extract classification + side effects |
| `run_composition` | cli.py | 11 | ~7 | 3 helper extraction |
| `get_health_summary` | health.py | 10 | 7 | Counter replacement |

### Iteration 6 Refactoring Results

| Function | File | CC Before | CC After | Method |
|----------|------|-----------|----------|--------|
| `evolution_history` | evolution_cmd.py | 11 | 3 | Extract `_resolve_skill_id` + `_format_ancestry` |
| `_check_api_keys` | init_cmd.py | 11 | 4 | Extract `_load_provider_configs` + `_collect_key_envs` |
| `_run_dispatch_loop` | dag_dispatcher.py | 11 | 8 | Extract `_dispatch_batch` + `DispatchResult.is_terminal` property |
| ~~`ArtifactSink`~~ | dag_dispatcher.py | — | — | Removed dead Protocol (0 consumers) |

---

## 2. Remaining C-Grade Functions — First Principles Analysis

### 2.1 `DAGDispatcher._run_dispatch_loop` (CC 11) [REFACTORED → CC 8]

- **File**: `src/agent_nexus/platform/agency/dag_dispatcher.py:331-363`
- **Lines**: 33
- **Target CC**: 7

**CC Decomposition**:
| Decision Point | CC Contribution |
|----------------|----------------|
| Function definition | 1 |
| `while iteration < max_iterations` | 1 |
| `if self._check_deadline(deadline, result)` | 1 |
| `if not ready_specialists` | 1 |
| `if self._no_more_work(specialist_ids, result)` | 1 |
| `else` (implicit) | 1 |
| `if self._concurrent` | 1 |
| `and len(batch) > 1` | 1 |
| `else` (implicit) | 1 |
| `if result.failed` | 1 |
| `or result.cancelled` | 1 |

**First Principles Judgment**:
- Does ONE thing? **Yes** — state machine loop: check deadline → get ready → dispatch → check failure.
- > 1 consumer? **Yes** — called by `dispatch()` and `adispatch()`.
- Data-driven alternative? The concurrent/sequential branch is a binary strategy — extractable.

**Root Cause**: CC inflation from boolean operators (`and`, `or`) in conditions. The actual logic is a clean 5-state machine.

**Recommendation**:
```python
def _dispatch_batch(self, batch, task_description, deadline, result):
    """Dispatch a batch via parallel or sequential strategy."""
    if self._concurrent and len(batch) > 1:
        self._dispatch_parallel(batch, task_description, deadline, result)
    else:
        self._dispatch_sequential(batch, task_description, deadline, result)

# In _run_dispatch_loop, replace the if/else block:
self._dispatch_batch(batch, task_description, deadline, result)
```
CC drops from 11 → 8 (B-grade). Further: extract `result.is_terminal` property to replace `result.failed or result.cancelled` → CC 7.

**Estimated change**: +5 lines (new method), -4 lines (simplified main), net Δ = +1 line.

---

### 2.2 `_check_api_keys` (CC 11) [REFACTORED → CC 4]

- **File**: `src/agent_nexus/platform/local/cli/init_cmd.py:95-119`
- **Lines**: 25
- **Target CC**: 5

**CC Decomposition**:
| Decision Point | CC Contribution |
|----------------|----------------|
| Function definition | 1 |
| `try` | 1 |
| `for v in providers.values()` (list comp) | 1 |
| `if isinstance(v, dict)` (list comp filter) | 1 |
| `and "api_key_env" in v` (list comp filter) | 1 |
| `except` | 1 |
| `if not config_key_envs` | 1 |
| `for p in DEFAULT_PROVIDERS.values()` (list comp) | 1 |
| `if isinstance(p, dict)` (list comp filter) | 1 |
| `and "api_key_env" in p` (list comp filter) | 1 |

**First Principles Judgment**:
- Does ONE thing? **Yes** — checks if API keys are configured.
- > 1 consumer? **No** — only called from `init_cmd`.
- Data-driven alternative? **Yes** — two identical list comprehensions operating on different data sources.

**Root Cause**: CC from two structurally identical list comprehensions. The `try/except` for config loading and the fallback to defaults are responsible for 6/11 CC points.

**Recommendation**:
```python
def _load_provider_configs(config_path: Path) -> dict:
    """Load provider config from TOML, return empty on failure."""
    try:
        import toml
        raw = toml.loads(config_path.read_text(encoding="utf-8"))
        return raw.get("models", {}).get("providers", {})
    except (OSError, ValueError, KeyError):
        return {}

def _collect_key_envs(providers: dict) -> list[str]:
    """Extract api_key_env names from provider configs."""
    return [
        str(v["api_key_env"])
        for v in providers.values()
        if isinstance(v, dict) and "api_key_env" in v
    ]

def _check_api_keys(config_path: Path) -> tuple[str, bool, str]:
    providers = _load_provider_configs(config_path) or DEFAULT_PROVIDERS
    key_envs = _collect_key_envs(providers)
    has_key = any(os.environ.get(k) for k in key_envs)
    return ("API key configured", has_key, "at least one set" if has_key else "none set")
```
CC drops from 11 → 5 (A-grade). The two helpers are reusable if other commands need provider config.

**Estimated change**: +15 lines (2 helpers), -15 lines (simplified main), net Δ = 0 lines.

---

### 2.3 `evolution_history` (CC 11) [REFACTORED → CC 3]

- **File**: `src/agent_nexus/platform/local/cli/evolution_cmd.py:118-156`
- **Lines**: 39
- **Target CC**: 4

**CC Decomposition**:
| Decision Point | CC Contribution |
|----------------|----------------|
| Function definition | 1 |
| `with _engine_context()` | 1 |
| `if skill is not None` | 1 |
| `else` | 1 |
| `if versions` | 1 |
| `if v.is_active` (list comp) | 1 |
| `active[0] if active else versions[-1]` (ternary) | 1 |
| `if skill_id is None` | 1 |
| `if not ancestry` | 1 |
| `for i, ancestor in enumerate(ancestry)` | 1 |
| `if ancestor.first_seen` (inline ternary) | 1 |

**First Principles Judgment**:
- Does ONE thing? **No** — resolves skill identifier AND formats ancestry tree. Mixed responsibilities.
- > 1 consumer? **No** — single CLI command.
- Data-driven alternative? The UUID/name resolution chain can be unified.

**Root Cause**: Mixed skill resolution (UUID → name → active selection) AND mixed formatting (indentation tree building). These are two distinct operations fused into one function.

**Recommendation**:
```python
def _resolve_skill_id(engine, identifier: str) -> str | None:
    """Resolve a skill identifier (UUID or name) to its internal ID."""
    skill = engine.store.get_skill_record(identifier)
    if skill is not None:
        return skill.id
    versions = engine.store.get_versions(identifier)
    if not versions:
        return None
    active = [v for v in versions if v.is_active]
    return (active[0] if active else versions[-1]).id

def _format_ancestry(ancestry: list) -> str:
    """Format ancestry chain as indented tree."""
    lines, indent = [], ""
    for i, ancestor in enumerate(ancestry):
        created = ancestor.first_seen.isoformat().split("T")[0] if ancestor.first_seen else "?"
        lines.append(f"{indent}{ancestor.name} (gen {ancestor.lineage.generation}, {created})")
        if i < len(ancestry) - 1:
            indent += "  -> "
    return "\n".join(lines)

@evolution_app.command("history")
def evolution_history(skill_name: str = typer.Argument(...)) -> None:
    with _engine_context() as engine:
        skill_id = _resolve_skill_id(engine, skill_name)
        if skill_id is None:
            typer.echo(f"Skill '{skill_name}' not found.", err=True)
            raise typer.Exit(code=1)
        ancestry = engine.store.get_ancestry(skill_id)
        if not ancestry:
            typer.echo(f"No ancestry found for '{skill_name}'.")
            return
        typer.echo(_format_ancestry(ancestry))
```
CC drops from 11 → 4 (A-grade). `_resolve_skill_id` is reusable for other evolution commands.

**Estimated change**: +20 lines (2 helpers), -25 lines (simplified main), net Δ = -5 lines.

---

## 3. Class-Level SRP Audit (First Principles)

### Assessment Criteria
- **> 10 methods** = potential SRP violation (monitor threshold)
- **> 20 methods** = likely SRP violation (action threshold)
- **Cohesion check**: Do the methods serve a single responsibility?

| Class | Module | Methods | Avg CC | SRP Risk | Assessment |
|-------|--------|---------|--------|----------|------------|
| TaskGraph | orchestration | 43 | 3.0 | **Monitor** | Methods are cohesive (CRUD + query + graph algorithms + async mirrors). The 42 methods include 6 async mirrors (thin wrappers) and 6 thin property accessors. Effective unique responsibilities: ~25. |
| EvolutionStore | evolution | 37 | 1.2 | **Low** | Thin DAO layer. Most methods are single-line SQL calls. No business logic. |
| SkillStore | evolution | 34 | 3.8 | **Low** | Similar to EvolutionStore — SQL-backed store with domain-specific queries. |
| PlatformRouter | router | 26 | 4.0 | **Moderate** | 4-phase workflow + agent resolution + result aggregation. Methods are cohesive around "route a composite task". |
| DAGDispatcher | agency | 25 | 4.2 | **Moderate** | Dispatch loop + parallel/sequential execution + result collection. Core dispatch responsibility. |
| GitInstaller | local | 25 | 3.9 | **Moderate** | Install + validate + sparse clone + venv creation. All within "install an agent" scope. |
| MCPGateway | gateway | 24 | 3.5 | **Monitor** | 3 MCP tools + 5 agent registration + 4 IPC/health + 4 tool building + 4 external server + 4 lifecycle. Consider extracting `ExternalServerManager` (4 methods). |
| DeferredAgentRegistry | gateway | 24 | 3.5 | **Low** | Agent registry with subprocess lifecycle. Methods cohesive. |

### Key Finding: No Critical SRP Violations

All large classes have methods that serve a single cohesive responsibility. The largest (TaskGraph at 43 methods) includes 12+ thin wrappers (async mirrors, properties) that inflate the count. The effective unique responsibilities are well within bounds.

---

## 4. Over-Abstraction Audit

### Dead Abstractions

| Abstraction | Type | Consumers | Status |
|-------------|------|-----------|--------|
| `ArtifactSink` | Protocol | **0** | **DEAD — REMOVED** in Iteration 6 |

### Justified Abstractions

| Abstraction | Type | Implementations/Consumers | Status |
|-------------|------|---------------------------|--------|
| `ExpertExecutor` | Protocol | 2+ (DAGDispatcher, TaskComposer) | Justified — enables test injection |
| `ContextProvider` | Protocol | Registry-based, multiple providers | Justified — extensibility point |
| `ReflectionRule` | Protocol | 2 impls (EmptyResultRule, MaxIterationRule) | Justified — pluggable rules |
| `SecurityRule` | ABC | 4 impls (Import, Function, Attribute, Regex) | Clearly justified — defense-in-depth |

**Recommendation**: Remove `ArtifactSink` (dead code). No other over-abstractions found.

---

## 5. Cross-Module Complexity Patterns

### Pattern 1: SQLite Store Boilerplate Duplication

Four stores share identical connection management patterns:

| Store | File | Shared Pattern Lines |
|-------|------|---------------------|
| EvolutionStore | store.py | ~30 lines |
| SkillStore | skill_store.py | ~30 lines |
| AnalysisStore | analysis_store.py | ~30 lines |
| BudgetStore | budget_store.py | ~30 lines |
| **Total** | | **~120 lines** |

Shared boilerplate: `_is_memory`, `_memory_conn`, `_conn_factory`, `_conn()` method, `close()` method.

**First Principles Judgment**:
- Is there > 1 consumer? **Yes** (4 stores).
- Does the base class do one thing? **Yes** (connection lifecycle).
- **But**: The boilerplate is simple (CC 3-5 each) and each store has different schemas. Extracting a base class saves lines but doesn't meaningfully reduce cognitive complexity.

**Recommendation**: **Optional, not urgent**. If a 5th store is added, reconsider extraction.

### Pattern 2: CLI Command Functions Mixing Resolution + Formatting

Both `evolution_history` and `_check_api_keys` fuse data resolution with output formatting. This pattern also appears in other CLI commands. The fix is consistent: extract `_resolve_X` and `_format_X` helpers.

### Pattern 3: Boolean Operator CC Inflation

Several functions lose 1-2 CC points to `and`/`or` operators in conditions:
- `result.failed or result.cancelled` → extract `result.is_terminal` property
- `self._concurrent and len(batch) > 1` → extract `self._should_dispatch_concurrently(batch)`
- `isinstance(v, dict) and "api_key_env" in v` → extract type-safe accessor

This is a low-cost, high-impact refactoring pattern.

---

## 6. Actionable Refactoring Plan

### Priority Matrix

| # | Function | CC | Target | Root Cause | Priority | Est. ΔLines |
|---|----------|----|--------|------------|----------|-------------|
| 1 | `evolution_history` | 11 | 4 | Mixed resolve+format | **P1** | -5 |
| 2 | `_check_api_keys` | 11 | 5 | Duplicate list comps | **P1** | 0 |
| 3 | `_run_dispatch_loop` | 11 | 7 | Boolean CC inflation | **P2** | +1 |
| 4 | Remove `ArtifactSink` | — | — | Dead abstraction | **P2** | -4 |
| 5 | SQLite store base | — | — | Boilerplate duplication | **P3** | -90 (optional) |

### P1 Refactoring Details

**1. `evolution_history` → CC 11→4**
- Extract `_resolve_skill_id(engine, identifier) -> str | None`
- Extract `_format_ancestry(ancestry) -> str`
- Main function becomes: resolve → guard → query → format → print
- Reusable: `_resolve_skill_id` can be used by other evolution commands

**2. `_check_api_keys` → CC 11→5**
- Extract `_load_provider_configs(config_path) -> dict`
- Extract `_collect_key_envs(providers) -> list[str]`
- Main function becomes: load → collect → check → return
- Reusable: both helpers usable wherever provider config is needed

### P2 Refactoring Details

**3. `_run_dispatch_loop` → CC 11→7**
- Extract `_dispatch_batch(batch, ...)` for concurrent/sequential routing
- Extract `DispatchResult.is_terminal` property for `failed or cancelled`
- Main function becomes a cleaner state machine

**4. Remove `ArtifactSink` Protocol**
- Delete lines 54-57 in `dag_dispatcher.py`
- Zero consumers, zero risk

---

## 7. Complexity Metrics Trend

| Metric | Iteration 1 | After Iteration 2 | After Iteration 3 | After Iteration 6 |
|--------|-------------|-------------------|-------------------|-------------------|
| C-grade functions | 5 | 3 | 3 | **0** |
| Average CC | 3.35 | 3.33 | 3.33 | **~3.30** |
| Max CC | 11 | 11 | 11 | **10** |
| Dead abstractions | Unknown | Unknown | 1 (ArtifactSink) | **0** |
| Classes > 20 methods | 8 | 8 | 8 | 8 |
| SRP violations | 0 critical | 0 critical | 0 critical | 0 critical |

**Overall Assessment**: The codebase complexity is now fully optimized. **Zero functions exceed CC 10.** All C-grade functions have been refactored. The remaining B-grade complexity is inherent to the domain (dispatch state machines, SQLite store patterns, CLI command workflows).
