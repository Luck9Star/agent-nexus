# Complexity Analysis Report

> Generated: 2026-05-10 (Iteration 10 — Cycle 2 First Principles Deep Audit)
> Scope: `src/agent_nexus/` | Baseline: radon 6.0.1

## 1. Overall Baseline

| Metric | Value | Delta from Cycle 1 |
|--------|-------|---------------------|
| Total blocks analyzed | 1,313 | +4 |
| Average complexity | **3.32 (Grade A)** | 3.33 → 3.32 |
| Max CC | **10 (B-grade)** | Unchanged |
| C-grade functions (CC ≥ 11) | **0** | Unchanged |
| B-grade functions (CC 6-10) | ~245 | +3 |
| Maintainability Index issues | **0 files** (all MI > 20) | — |

### CC Score Distribution

| CC | Count | Grade | Cumulative % |
|----|-------|-------|-------------|
| 1  | ~418  | A     | 31.8%       |
| 2  | ~175  | A     | 45.1%       |
| 3  | ~180  | A     | 58.9%       |
| 4  | ~163  | A     | 71.3%       |
| 5  | ~118  | A     | 80.3%       |
| 6  | ~98   | B     | 87.8%       |
| 7  | ~59   | B     | 92.3%       |
| 8  | ~49   | B     | 96.0%       |
| 9  | ~25   | B     | 97.9%       |
| 10 | 11    | B     | 98.7%       |
| 11+| 0     | —     | 100.0%      |

### Refactoring History Summary

| Iteration | Function | File | CC Before → After | Method |
|-----------|----------|------|--------------------|--------|
| 2 | `get_health_summary` | health.py | 10 → 7 | Counter replacement |
| 2 | `_no_more_work` | dag_dispatcher.py | 11 → 6 | Redundant branch merge |
| 2 | `run_composition` | cli.py | 11 → ~7 | 3 helper extraction |
| 6 | `evolution_history` | evolution_cmd.py | 11 → 3 | Extract resolve + format |
| 6 | `_check_api_keys` | init_cmd.py | 11 → 4 | Extract provider config helpers |
| 6 | `_run_dispatch_loop` | dag_dispatcher.py | 11 → 8 | Extract batch dispatch + is_terminal |
| 6 | ~~`ArtifactSink`~~ | dag_dispatcher.py | — | Removed dead Protocol |

---

## 2. CC=10 Boundary Functions — First Principles Root Cause Analysis

These 11 functions sit at the B/C-grade boundary. None require immediate action, but each carries risk of becoming C-grade if features are added.

### 2.1 `SkillStore._parse_snapshot` (CC 10)

- **File**: `src/agent_nexus/platform/evolution/skill_store.py:784-809`
- **Lines**: 26
- **Target CC**: 6
- **Root Cause**: **职责混合** — parsing + validation + error handling + logging in one function

**CC Decomposition**:
| Decision Point | CC |
|----------------|-----|
| Base | 1 |
| `if not raw or raw in (...)` | 2 (if + or) |
| `try/except` block | 1 (except) |
| `if not isinstance(loaded, dict) or not loaded` | 2 (if + or) |
| `if all(isinstance(v, str) ...)` | 1 (if) |
| Generator in `all()` | 1 (for) |
| List comp `if not isinstance(v, str)` | 1 (if) |
| **Total** | **10** |

**First Principles Check**:
- Does ONE thing? **No** — parses JSON, validates types, handles errors, logs warnings.
- Data-driven alternative? **Yes** — validation can be extracted.

**Recommendation**: Extract `_validate_snapshot_dict(loaded, skill_id)` to handle type validation + warning. CC → 6.

**Estimated change**: +10 lines (helper), -8 lines (simplified), net +2.

---

### 2.2 `EvolutionContextDescriber.l1_context` (CC 10)

- **File**: `src/agent_nexus/platform/evolution/context_describer.py:95-140`
- **Lines**: 46
- **Target CC**: 5
- **Root Cause**: **职责混合** — data filtering + health diagnosis + table formatting

**CC Decomposition**:
| Decision Point | CC |
|----------------|-----|
| Base | 1 |
| `if skill_ids is not None` (filter 1) | 1 |
| `if not active` (empty check) | 1 |
| `sorted(...)` lambda | 1 |
| `for skill in active` | 1 |
| `rates = SkillRates.from_record(skill)` + conditional | 1 |
| `report = reports.get(skill.id)` + conditional | 1 |
| Ternary `rates.effective_rate if rates is not None else 0.0` | 1 |
| Ternary health status | 1 |
| `if report and report.is_healthy` | 1 |
| **Total** | **10** |

**First Principles Check**:
- Does ONE thing? **No** — acquires data, filters, diagnoses health, formats markdown table.
- > 1 consumer? **Yes** — called by evolution context system.
- Data-driven alternative? Formatting can be extracted.

**Recommendation**: Extract `_format_skill_table(active, reports) -> str`. Main becomes: fetch → filter → diagnose → format. CC → 5.

**Estimated change**: +18 lines (helper), -12 lines (simplified), net +6.

---

### 2.3 `EvolutionContextDescriber._build_judgment_history` (CC 10)

- **File**: `src/agent_nexus/platform/evolution/context_describer.py:279-303`
- **Lines**: 25
- **Target CC**: 4
- **Root Cause**: **手动计数 (Manual Counting)** — three identical `sum(1 for j in ...)` patterns

**CC Decomposition**:
The CC inflation comes from 3 generator expressions with inline conditionals:
```python
applied_count = sum(1 for j in judgments if j["applied"])
completed_count = sum(1 for j in judgments if j["completed"])
fell_back_count = sum(1 for j in judgments if j["fell_back"])
```
Each contributes 2 CC points (for + if). Total from counting: 6 CC points.

**First Principles Check**:
- Does ONE thing? **Yes** — builds judgment history summary.
- Data-driven alternative? **Yes** — `collections.Counter` with a single pass.

**Recommendation**:
```python
status_counts = Counter(
    "applied" if j["applied"] else "completed" if j["completed"] else "fell_back"
    for j in judgments
)
applied_count = status_counts["applied"]
completed_count = status_counts["completed"]
fell_back_count = status_counts["fell_back"]
```
CC drops from 10 → 4 (A-grade). Single-pass, more readable.

**Estimated change**: +3 lines, -3 lines, net 0.

---

### 2.4 `SchemaTransformer._resolve_one_of_any_of` (CC 10)

- **File**: `src/agent_nexus/platform/gateway/schema_transformer.py:209-237`
- **Lines**: 29
- **Target CC**: 6
- **Root Cause**: **条件爆炸** — null detection + variant iteration + union construction

**CC Decomposition**:
| Decision Point | CC |
|----------------|-----|
| Base | 1 |
| `if not variants` | 1 |
| `for variant in variants` | 1 |
| `if not isinstance(variant, dict)` | 1 |
| `if vtype == "null"` | 1 |
| `if not resolved_variants` | 1 |
| `if len(resolved_variants) == 1` | 1 |
| Ternary `... if has_null else ...` (×2) | 2 |
| `for v in resolved_variants[1:]` | 1 |
| **Total** | **10** |

**First Principles Check**:
- Does ONE thing? **Yes** — resolves oneOf/anyOf schema to Python types.
- Data-driven alternative? **Limited** — the null/non-null branch logic is inherent to JSON Schema semantics.
- Is this reducible? The CC is proportional to the number of distinct branches in JSON Schema oneOf/anyOf, which is a fixed domain requirement.

**Recommendation**: **Accept as-is**. The CC accurately reflects the inherent complexity of oneOf/anyOf resolution. Extracting helpers would create artificial indirection without reducing actual complexity.

---

### 2.5 `ExecutionAnalyzer._generate_suggestions` (CC 10)

- **File**: `src/agent_nexus/platform/evolution/analyzer.py:208-256`
- **Lines**: 49
- **Target CC**: 5
- **Root Cause**: **职责混合** — suggestion generation + CAPTURED special case + deduplication

**First Principles Check**:
- Does ONE thing? **No** — generates suggestions per skill, handles a special CAPTURED case, then deduplicates.
- > 1 consumer? **Yes** — called by `analyze_execution`.
- Data-driven alternative? The deduplication logic can be extracted.

**Recommendation**: Extract `_deduplicate_suggestions(suggestions) -> list[EvolutionSuggestion]`. Main becomes: generate + CAPTURED special case + deduplicate. CC → 5.

**Estimated change**: +12 lines (helper), -8 lines (simplified), net +4.

---

### 2.6 `HealthChecker.diagnose_skills` (CC 10)

- **File**: `src/agent_nexus/platform/evolution/health.py:193-251`
- **Lines**: 59
- **Target CC**: 5
- **Root Cause**: **职责混合** — data acquisition + filtering + per-skill analysis + metrics construction

**CC Decomposition**:
| Decision Point | CC |
|----------------|-----|
| Base | 1 |
| `if skills is not None` | 1 |
| `if skill_ids is not None` (filter 1) | 1 |
| `else` branch + `if skill_ids is not None` (filter 2) | 2 |
| `for skill in active_skills` | 1 |
| `if rates is not None` | 1 |
| `else` branch (4 metrics assignments) | 1 |
| `len(suggestions) == 0` (health check) | 1 |
| `rates is not None` ternary | 1 |
| **Total** | **10** |

**First Principles Check**:
- Does ONE thing? **No** — acquires skills, filters, computes metrics, builds reports.
- Data-driven alternative? **Yes** — metrics construction can be a factory method.

**Recommendation**: Extract `_build_health_metrics(skill, rates) -> dict[str, float]`. CC → 5.

**Estimated change**: +15 lines (helper), -10 lines (simplified), net +5.

---

### 2.7 `SkillLoader._split_body_resources` (CC 10)

- **File**: `src/agent_nexus/platform/skills/loader.py:184-217`
- **Lines**: 34
- **Target CC**: 6
- **Root Cause**: **状态机 (State Machine)** — fence-tracking state machine with line iteration

**CC Decomposition**:
| Decision Point | CC |
|----------------|-----|
| Base | 1 |
| `for line in content.splitlines()` | 1 |
| `if in_fence` | 1 |
| `if line.lstrip().startswith("```")` (inside fence) | 1 |
| `else` (outside fence) | 1 |
| `if line.lstrip().startswith("```")` (enter fence) | 1 |
| `elif _RESOURCES_SPLIT_RE.match(line)` | 1 |
| `if resources_start is None` | 1 |
| `if body` (ternary) | 1 |
| `if resources` (ternary) | 1 |
| **Total** | **10** |

**First Principles Check**:
- Does ONE thing? **Yes** — parses markdown into body/resources sections.
- Data-driven alternative? **No** — state machine is the natural pattern for fence tracking.
- Is this reducible? The CC is proportional to the state machine states, which is minimal (2 states: in_fence, not in_fence).

**Recommendation**: **Accept as-is**. This is a correctly implemented state machine with minimal states. No refactoring would improve readability.

---

### 2.8 `ProfileBasedExecutor._resolve_section` (CC 10)

- **File**: `src/agent_nexus/platform/agency/executor.py:117-148`
- **Lines**: 32
- **Target CC**: 2
- **Root Cause**: **Radon CC Inflation** — lambdas inside dict literal counted as decision points

**Analysis**: This function is **already optimally structured** using the data-driven pattern (dict of generators). The CC=10 is an artifact of radon counting lambda expressions as decision points within the enclosing function. The actual control flow is:
```python
gen = generators.get(section)  # dict lookup
if gen is not None:
    return gen()
return default
```
Effective CC: **2** (A-grade). The dict literal with 13 lambdas inflates the radon score.

**Recommendation**: **Accept as-is**. This is a model example of data-driven design replacing a chain of if/elif. No refactoring needed.

---

### 2.9 `_detect_risk_conflicts` (CC 10)

- **File**: `src/agent_nexus/platform/agency/integrator.py:349-381`
- **Lines**: 33
- **Target CC**: 5
- **Root Cause**: **防御性返回 (Defensive Returns)** — 6 early-exit guards

**CC Decomposition**:
| Decision Point | CC |
|----------------|-----|
| Base | 1 |
| `if len(risk_sets) < 2` | 1 |
| `if _has_similar_risks(risk_sets)` | 1 |
| `if not all(len(v) > 0 for v in ...)` | 2 (if + generator for) |
| `if a.source_agent in risk_sets` (list comp filter) | 1 |
| `if not section_sets` | 1 |
| `for s in section_sets[1:]` | 1 |
| `if not shared` | 1 |
| `all(len(v) > 0 ...)` generator | 1 |
| **Total** | **10** |

**First Principles Check**:
- Does ONE thing? **Yes** — detects if expert risk findings are completely disjoint.
- Data-driven alternative? The early-return guards are necessary safety checks.
- Is this reducible? Some guards could be combined.

**Recommendation**: Combine related guards into a single `_has_valid_risk_data(risk_sets, artifacts)` helper. CC → 5. Low priority — function is correct and readable.

**Estimated change**: +8 lines (helper), -6 lines (simplified), net +2.

---

### 2.10 `load_dag_into_graph` (CC 10)

- **File**: `src/agent_nexus/platform/agency/dag_dispatcher.py:116-152`
- **Lines**: 37
- **Target CC**: 5
- **Root Cause**: **职责混合** — DAG task filtering + TaskItem construction + graph insertion

**CC Decomposition**:
| Decision Point | CC |
|----------------|-----|
| Base | 1 |
| `for dag_task in dag.tasks` | 1 |
| `if dag_task.id not in specialist_ids` | 1 |
| `for dep in dag_task.blocked_by if dep in specialist_ids` | 2 (for + if) |
| `if items` | 1 |
| `for item in items if graph.get_task(item.id) is None` | 2 (for + if) |
| `if new_items` | 1 |
| `graph.add_tasks(new_items)` conditional call | 1 |
| **Total** | **10** |

**First Principles Check**:
- Does ONE thing? **No** — filters specialist tasks, builds TaskItems, inserts into graph.
- Data-driven alternative? TaskItem construction can be extracted.

**Recommendation**: Extract `_build_specialist_items(dag, task_description, specialist_ids) -> list[TaskItem]`. CC → 5.

**Estimated change**: +12 lines (helper), -8 lines (simplified), net +4.

---

### 2.11 `_format_section_value` (CC 10)

- **File**: `src/agent_nexus/platform/agency/cli.py:389-401`
- **Lines**: 13
- **Target CC**: 4
- **Root Cause**: **类型分派 (Type Dispatch)** — isinstance chain + list comprehension CC

**CC Decomposition**:
| Decision Point | CC |
|----------------|-----|
| Base | 1 |
| `if isinstance(value, list)` | 1 |
| `if not value` (list empty) | 1 |
| `for item in value` (list comp) | 1 |
| `if isinstance(value, dict)` | 1 |
| `if not value` (dict empty) | 1 |
| `for k, v in value.items()` (dict comp) | 1 |
| `if value is None or (isinstance(value, str) and not value.strip())` | 3 (if + or + and) |
| **Total** | **10** |

**First Principles Check**:
- Does ONE thing? **Yes** — formats a value as markdown lines.
- Data-driven alternative? **Yes** — handler registry.

**Recommendation**: Replace isinstance chain with handler dispatch:
```python
_HANDLERS = [
    (list, lambda v: [f"- {item}" for item in v] if v else []),
    (dict, lambda v: [f"- **{k}**: {v}" for k, v in v.items()] if v else []),
]

def _format_section_value(value: object) -> list[str]:
    for typ, handler in _HANDLERS:
        if isinstance(value, typ):
            return handler(value)
    if value is None or (isinstance(value, str) and not value.strip()):
        return []
    return [str(value)]
```
CC drops from 10 → 4.

**Estimated change**: +5 lines (registry), -10 lines (simplified), net -5.

---

## 3. Focus Module Deep Analysis

### 3.1 MCP Gateway (`gateway.py`, 680 LOC)

| Metric | Value |
|--------|-------|
| Total blocks | 26 |
| Max CC | 8 (B-grade) |
| Class CC | 4 (A-grade) |
| Methods | 24 |
| B-grade methods | 5 |

**Assessment**: Healthy. The 5 B-grade methods are all in the 7-8 CC range (agent registration, tool building). The module's complexity is proportional to its domain (MCP protocol handling). No SRP violation — all methods serve "gateway tool registration and dispatch".

**Key B-grade functions**:
- `_agent_info` (CC 8): Builds agent info dict with health/status
- `_register_agent_tools` (CC 8): Tool registration with deduplication
- `_list_agents` (CC 7): Agent listing with filters
- `_build_params_from_schema` (CC 7): JSON Schema to inspect.Parameter
- `register_external_server` (CC 7): External MCP server lifecycle

### 3.2 LLM Client (`llm_client.py`, 660 LOC)

| Metric | Value |
|--------|-------|
| Total blocks | 24 |
| Max CC | 9 (B-grade) |
| Class CC | 4 (A-grade) |
| Methods | 18 |
| B-grade methods | 4 |

**Assessment**: Healthy. The 3 CC-9 methods (`from_config`, `_build_litellm_kwargs`, `_call_cli`) handle multi-provider adaptation, which inherently requires branching. The module correctly isolates provider-specific logic.

### 3.3 Task Graph (`task_graph.py`, 855 LOC)

| Metric | Value |
|--------|-------|
| Total blocks | 43 |
| Max CC | 9 (B-grade) |
| Class CC | 3 (A-grade) |
| Methods | 43 |
| B-grade methods | 6 |

**Assessment**: Healthy but large. 43 methods is the highest count in the codebase. However:
- 6 are async mirrors (thin `await self._..._conn(...)` wrappers)
- 4 are thin property accessors
- 8 are single-line SQL operations
- **Effective unique responsibilities**: ~25, all cohesive around "task DAG CRUD and queries"

### 3.4 DAG Dispatcher (`dag_dispatcher.py`, 647 LOC)

| Metric | Value |
|--------|-------|
| Total blocks | 25 |
| Max CC | 9 (B-grade) |
| B-grade methods | 6 |

**Assessment**: Healthy post-refactoring. `_run_dispatch_loop` was successfully reduced from CC 11 → 8. The module's complexity reflects the inherent complexity of parallel task dispatch with failure handling.

### 3.5 Evolver (`evolver.py`, ~430 LOC)

| Metric | Value |
|--------|-------|
| Total blocks | 14 |
| Max CC | 9 (B-grade) |
| B-grade methods | 3 |

**Assessment**: Healthy. `process_tool_degradation` (CC 9) and `_evolve_derived` (CC 9) handle the most complex evolution types. The module is well-focused on the single responsibility of skill evolution.

---

## 4. Class-Level SRP Audit

### Assessment Criteria
- **> 10 methods**: monitor threshold
- **> 20 methods**: action threshold (requires cohesion justification)
- **Method cohesion**: Do methods serve a single responsibility?

| Class | Module | Methods | Max Method CC | SRP Risk | Assessment |
|-------|--------|---------|---------------|----------|------------|
| TaskGraph | orchestration | 43 | 9 | **Monitor** | Cohesive: DAG CRUD + queries. 18 thin wrappers inflate count. Effective responsibilities: ~25. |
| SkillStore | evolution | 34 | 10 | **Low** | SQL-backed store. Methods are domain queries. Cohesive. |
| ProcessManager | orchestration | 21 | 9 | **Low** | Subprocess lifecycle: spawn, health, cleanup. Cohesive. |
| MCPGateway | gateway | 24 | 8 | **Monitor** | Consider extracting `ExternalServerManager` (4 methods). |
| DeferredAgentRegistry | gateway | ~24 | 7 | **Low** | Agent registry + subprocess lifecycle. Cohesive. |
| ConfigLoader | config | 18 | 9 | **Low** | Config loading from TOML/YAML. Cohesive. |
| DAGDispatcher | agency | 25 | 9 | **Low** | Task dispatch + parallel execution. Cohesive. |

**Key Finding**: No critical SRP violations. All large classes have cohesive method sets. The inflated method counts come from thin wrappers (async mirrors, properties, single-line SQL calls).

---

## 5. Cross-Module Complexity Patterns

### Pattern 1: Manual Counting (3 occurrences)

Three functions use `sum(1 for x in items if condition)` patterns that inflate CC:
- `EvolutionContextDescriber._build_judgment_history`: 3× sum → replace with Counter
- `HealthChecker.get_health_summary`: **Already fixed** (Iteration 2)

**Remaining**: 1 occurrence in `_build_judgment_history`. Easy fix: single-pass Counter.

### Pattern 2: Resolution + Formatting Fusion (2 occurrences)

CLI context functions that fuse data resolution with output formatting:
- `EvolutionContextDescriber.l1_context`: acquire → filter → diagnose → format
- `HealthChecker.diagnose_skills`: acquire → filter → analyze → report

**Pattern fix**: Extract formatting helpers. The resolution/analysis logic stays; only formatting separates.

### Pattern 3: Radon CC Inflation from Lambdas (1 occurrence)

- `ProfileBasedExecutor._resolve_section`: CC 10 but effective CC 2 due to lambda-counting artifact.

This is a false positive — the function is optimally structured with data-driven design.

### Pattern 4: Type Dispatch via isinstance Chain (2 occurrences)

- `_format_section_value`: isinstance chain for list/dict/None/str
- `SchemaTransformer._resolve_by_type_str`: type string to Python type mapping

**Pattern fix**: Replace with handler registry or dispatch dict where possible.

### Pattern 5: SQLite Store Boilerplate (4 modules, ~120 lines)

Four stores share identical `_conn()/_memory_conn/close()` patterns. This is **boilerplate duplication**, not cognitive complexity. Fixing saves lines but doesn't meaningfully reduce bug surface. Defer until a 5th store is added.

---

## 6. Maintainability Index

All 100+ source files pass MI > 20 (maintainable). `radon mi -nc` returned zero output, confirming no low-maintainability files.

---

## 7. Actionable Refactoring Priority Matrix

### P1 — CC=10 Functions with Clear Fixes

| # | Function | CC | Target | Root Cause | Est. ΔLines |
|---|----------|----|--------|------------|-------------|
| 1 | `_build_judgment_history` | 10 | 4 | Manual counting → Counter | 0 |
| 2 | `_format_section_value` | 10 | 4 | isinstance → handler registry | -5 |

### P2 — CC=10 Functions with Moderate Fix Value

| # | Function | CC | Target | Root Cause | Est. ΔLines |
|---|----------|----|--------|------------|-------------|
| 3 | `diagnose_skills` | 10 | 5 | Extract metrics builder | +5 |
| 4 | `l1_context` | 10 | 5 | Extract table formatter | +6 |
| 5 | `_generate_suggestions` | 10 | 5 | Extract deduplication | +4 |
| 6 | `load_dag_into_graph` | 10 | 5 | Extract item builder | +4 |

### P3 — Accept as-is (Inherent Complexity)

| # | Function | CC | Reason |
|---|----------|----|--------|
| 7 | `_resolve_one_of_any_of` | 10 | JSON Schema semantics, proportional to domain complexity |
| 8 | `_split_body_resources` | 10 | Minimal state machine (2 states), correctly implemented |
| 9 | `_resolve_section` | 10 | False positive — data-driven pattern, effective CC 2 |
| 10 | `_parse_snapshot` | 10 | Validation + error handling, low risk of growth |
| 11 | `_detect_risk_conflicts` | 10 | Defensive guards, low risk of growth |

---

## 8. Complexity Metrics Trend

| Metric | Iter 1 | After Iter 2 | After Iter 6 | Cycle 2 (Current) |
|--------|--------|-------------|-------------|-------------------|
| Total blocks | 1,306 | 1,309 | 1,313 | **1,313** |
| Average CC | 3.35 | 3.33 | ~3.30 | **3.32** |
| Max CC | 11 | 11 | 10 | **10** |
| C-grade functions | 5 | 3 | 0 | **0** |
| CC=10 boundary | — | — | 11 | **11** |
| Dead abstractions | Unknown | Unknown | 0 | **0** |
| MI issues | — | — | 0 | **0** |
| Classes > 20 methods | 8 | 8 | 8 | **8** |
| SRP violations | 0 critical | 0 critical | 0 critical | **0 critical** |

---

## 9. Overall Assessment

The codebase complexity is **well-managed and stable**:

1. **Zero C-grade functions** (CC ≥ 11) — all previously identified have been refactored
2. **11 CC=10 boundary functions** — 6 have clear refactoring paths (P1/P2), 5 are inherent to their domain (P3)
3. **No maintainability issues** — all files MI > 20
4. **No SRP violations** — all large classes have cohesive method sets
5. **No dead abstractions** — ArtifactSink Protocol removed in Iteration 6
6. **Average CC 3.32 (Grade A)** — well below industry average (~6-8)

The remaining complexity is **proportional to domain requirements** (dispatch state machines, JSON Schema resolution, multi-provider LLM adaptation) rather than accidental (poor structure, missing abstractions). Further refactoring of P1/P2 items would improve the metrics but carries diminishing returns for bug prevention.
