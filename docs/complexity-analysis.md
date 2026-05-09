# Complexity Analysis Report

> Generated: 2026-05-09 | Tool: radon 6.0.1 | Scope: `src/agent_nexus/`

## 1. Overall Baseline

| Metric | Value |
|--------|-------|
| Total blocks analyzed | 1,306 |
| Average complexity | **3.35 (Grade A)** |
| Grade A (1-5) | 1,058 (81.0%) |
| Grade B (6-10) | 243 (18.6%) |
| Grade C (11-20) | 5 (0.4%) |
| Grade D-F (>20) | 0 |

### CC Score Distribution

| CC | Count | Grade | Cumulative % |
|----|-------|-------|-------------|
| 1  | 414   | A     | 31.7%       |
| 2  | 178   | A     | 45.3%       |
| 3  | 182   | A     | 59.3%       |
| 4  | 165   | A     | 71.9%       |
| 5  | 119   | A     | 81.0%       |
| 6  | 98    | B     | 88.5%       |
| 7  | 59    | B     | 93.0%       |
| 8  | 49    | B     | 96.8%       |
| 9  | 25    | B     | 98.7%       |
| 10 | 12    | B     | 99.6%       |
| 11 | 5     | C     | 100.0%      |

**Assessment**: The codebase is in good shape overall. No function exceeds CC 11, and the average is well within the acceptable range. The 5 C-grade functions and 12 CC=10 functions are the primary refactoring targets.

---

## 2. C-Grade Functions (CC = 11) — Detailed Analysis

### 2.1 `DAGDispatcher._no_more_work` (CC 11)

- **File**: `src/agent_nexus/platform/agency/dag_dispatcher.py:300-327`
- **Lines**: 28
- **Target CC**: 5

**Root Cause**: Condition explosion — the function has multiple sequential filter/classify steps on the same list (`all_specialists` → `pending_or_running` → `in_progress`). Each list comprehension with a conditional adds CC.

**Analysis**:
- Does ONE thing? Partially — it classifies remaining tasks AND makes termination decisions AND performs side effects (`_safe_fail`). Mixed responsibilities.
- Can branches be data-driven? No — the logic is inherently sequential state classification. However, the two list comprehensions can be consolidated.

**Recommendation**:
```
1. Extract task classification into a single pass:
   - `_classify_remaining(specialist_ids, graph) -> (pending, in_progress, completed)`
2. Move _safe_fail side effects into a dedicated `_fail_batch()` helper
3. Main logic becomes: classify → dispatch based on classification → return
```
**Estimated change**: 28 lines → ~15 lines in main function + 10 lines in helpers.

---

### 2.2 `DAGDispatcher._run_dispatch_loop` (CC 11)

- **File**: `src/agent_nexus/platform/agency/dag_dispatcher.py:338-369`
- **Lines**: 32
- **Target CC**: 6

**Root Cause**: Nested conditionals — deadline check, empty-list check, concurrent-vs-sequential dispatch, and failure/cancellation checks form 4 levels of decision.

**Analysis**:
- Does ONE thing? Yes — it is a dispatch loop controller. The complexity comes from the inherent state machine of the loop.
- Can branches be data-driven? Partially — the concurrent/sequential branch can be a strategy dispatch.
- > 1 consumer? Yes — called by `dispatch()` and `adispatch()`.

**Recommendation**:
```
1. Extract the "dispatch batch" step:
   if self._concurrent and len(batch) > 1:
       self._dispatch_parallel(...)
   else:
       self._dispatch_sequential(...)
   → self._dispatch_batch(batch, task_description, deadline, result)
2. The early-return on failure can stay — it's a guard clause, not complexity
3. After extraction, CC drops to ~6 (loop + deadline guard + no-more-work + batch-dispatch + failure-check)
```
**Estimated change**: 32 lines → ~20 lines + 5-line wrapper.

---

### 2.3 `run_composition` (CC 11)

- **File**: `src/agent_nexus/platform/agency/cli.py:595-787`
- **Lines**: 193 (including 55 lines of Click decorators)
- **Target CC**: 4

**Root Cause**: Orchestrator pattern — the function is a 5-step pipeline setup function. The CC comes from `try/except` around expert loading, `if use_llm` branching, and `try/finally` for cleanup. It's a "wiring function" with mixed setup, execution, and teardown.

**Analysis**:
- Does ONE thing? No — it sets up logging, loads .env, loads experts, initializes LLM components, creates executor, runs pipeline, handles output. Classic "main function" syndrome.
- Can branches be data-driven? The `use_llm` branch creates different executor paths — this is inherent, not data-driven.
- > 1 consumer? No — single CLI entry point.

**Recommendation**:
```
1. Extract step 1 (expert loading) → `_load_experts(vendor_path, allowlist) -> ExpertRegistry`
2. Extract step 2 (LLM init) → already done (_setup_llm_components), keep
3. Extract step 3 (executor creation) → already done (_create_executor), keep
4. Extract step 4 (pipeline execution) → `_execute_pipeline(composer_input, registry, executor, ...) -> ComposerResult`
5. Extract step 5 (output) → already done (_handle_output), keep
6. Main function becomes a 15-line orchestrator calling the 5 steps
```
**Estimated change**: 193 lines → ~40 lines in main + existing extracted helpers.

---

### 2.4 `_check_api_keys` (CC 11)

- **File**: `src/agent_nexus/platform/local/cli/init_cmd.py:96-119`
- **Lines**: 25
- **Target CC**: 4

**Root Cause**: Condition explosion with double-fallback pattern — tries loading from config file, then falls back to DEFAULT_PROVIDERS, then checks environment variables. Each branch in the list comprehensions adds CC.

**Analysis**:
- Does ONE thing? Yes — checks if API keys exist.
- Can branches be data-driven? Yes — the "try config, fall back to defaults" pattern can be a single helper.
- > 1 consumer? Need to verify — likely only called from init command.

**Recommendation**:
```
1. Extract: `_collect_key_envs(config_path) -> list[str]` — handles config vs defaults
2. Main function becomes: collect envs → check if any set → return tuple
```
**Estimated change**: 25 lines → ~8 lines in main + 12 lines in helper.

---

### 2.5 `evolution_history` (CC 11)

- **File**: `src/agent_nexus/platform/local/cli/evolution_cmd.py:119-156`
- **Lines**: 38
- **Target CC**: 4

**Root Cause**: Nested conditional branching — tries UUID lookup, then name lookup, then active selection, with error handling at each step. The `for i, ancestor in enumerate(ancestry)` with conditional indentation formatting adds CC.

**Analysis**:
- Does ONE thing? Partially — it resolves a skill identifier AND formats ancestry output.
- Can branches be data-driven? The UUID-vs-name resolution can be a single lookup function.
- > 1 consumer? No — single CLI command.

**Recommendation**:
```
1. Extract: `_resolve_skill_id(engine, identifier) -> str | None` — handles UUID/name/active resolution
2. Extract: `_format_ancestry(ancestry) -> str` — handles the tree formatting
3. Main function becomes: resolve → query ancestry → format → print
```
**Estimated change**: 38 lines → ~12 lines in main + 15 lines in helpers.

---

## 3. CC = 10 Functions (Near Threshold)

### 3.1 `_detect_risk_conflicts` (CC 10)

- **File**: `src/agent_nexus/platform/agency/integrator.py:350-381`
- **Lines**: 33
- **Target CC**: 5

**Root Cause**: Early-return cascade — 4 guard clauses followed by the actual computation. The guards are simple but each adds +1 CC.

**Recommendation**: Consolidate guards into a single validation function `_validate_risk_analysis_prereqs(artifacts) -> RiskAnalysisInput | None`.

---

### 3.2 `load_dag_into_graph` (CC 10)

- **File**: `src/agent_nexus/platform/agency/dag_dispatcher.py:118-153`
- **Lines**: 36
- **Target CC**: 4

**Root Cause**: Mixed I/O + transformation — the function simultaneously filters DAG tasks, transforms them to TaskItems, and inserts into the database. The two-stage filter (specialist check + existing-in-graph check) adds branches.

**Recommendation**: Split into `_filter_specialist_tasks(dag) -> list[DAGTask]` and `_transform_to_items(tasks, description) -> list[TaskItem]`, then the main function becomes filter → transform → deduplicate → insert.

---

### 3.3 `_format_section_value` (CC 10)

- **File**: `src/agent_nexus/platform/agency/cli.py:390-401`
- **Lines**: 13
- **Target CC**: 4

**Root Cause**: Type-dispatch on `value` — isinstance checks for list, dict, None, str, and fallback. Classic "visitor without pattern matching" pattern.

**Recommendation**: Use a dispatch dict mapping `type(value)` to formatting functions, or a `match` statement (Python 3.10+). The function is already short, so this is low priority.

---

### 3.4 `ProfileBasedExecutor._resolve_section` (CC 10)

- **File**: `src/agent_nexus/platform/agency/executor.py:118-148`
- **Lines**: 31
- **Target CC**: 3

**Root Cause**: Data-driven pattern already implemented as a dict — the CC comes from the 14 lambda entries in the `generators` dict. Radon counts each lambda as a branch. **This is a false positive** — the function is already well-structured with a data-driven approach.

**Recommendation**: No action needed. The dict-based dispatch is the correct pattern. The CC is inflated by radon's counting of lambda expressions.

---

### 3.5 `HealthChecker.diagnose_skills` (CC 10)

- **File**: `src/agent_nexus/platform/evolution/health.py:193-250`
- **Lines**: 58
- **Target CC**: 5

**Root Cause**: Mixed responsibilities — the function handles skill filtering (with dual-path `skills` vs `active_skills`), per-skill health computation, metric construction (with conditional rates), and report assembly.

**Recommendation**:
```
1. Extract `_resolve_skill_list(skill_ids, skills, store) -> list[SkillRecord]` — handles the filtering logic
2. Extract `_build_metrics(skill, rates) -> dict[str, float]` — handles metric construction
3. Main function becomes: resolve list → iterate → compute health + metrics → assemble reports
```

---

### 3.6 `HealthChecker.get_health_summary` (CC 10)

- **File**: `src/agent_nexus/platform/evolution/health.py:266-296`
- **Lines**: 31
- **Target CC**: 4

**Root Cause**: Manual counting loop — iterates over all reports and suggestions to count by `EvolutionType`. Three sequential `if/elif` chains in the inner loop.

**Recommendation**: Use `collections.Counter`:
```python
counts = Counter(s.evolution_type for r in reports.values() for s in r.suggestions)
return {"fix_suggestions": counts[EvolutionType.FIX], ...}
```
CC drops to ~3.

---

### 3.7 `ExecutionAnalyzer._generate_suggestions` (CC 10)

- **File**: `src/agent_nexus/platform/evolution/analyzer.py:209-256`
- **Lines**: 48
- **Target CC**: 5

**Root Cause**: Mixed iteration + accumulation + deduplication — the function iterates skills, generates suggestions per skill, adds a special CAPTURED suggestion, then deduplicates by key. The dedup logic with `dict` tracking adds CC.

**Recommendation**: Extract deduplication into a `_deduplicate_suggestions(suggestions) -> list[EvolutionSuggestion]` helper. The CAPTURED special case can be appended before dedup. CC drops to ~6.

---

### 3.8 `SkillStore._parse_snapshot` (CC 10)

- **File**: `src/agent_nexus/platform/evolution/skill_store.py:785-809`
- **Lines**: 25
- **Target CC**: 4

**Root Cause**: Defensive parsing — multiple `if not` guards for empty inputs, type checking, non-string value checking, and exception handling. Each guard is simple but adds +1 CC.

**Recommendation**: Acceptable for a parsing/validation function. Low priority — defensive parsing inherently has many branches.

---

### 3.9 `EvolutionContextDescriber.l1_context` (CC 10)

- **File**: `src/agent_nexus/platform/evolution/context_describer.py:96-140`
- **Lines**: 45
- **Target CC**: 5

**Root Cause**: Mixed I/O + formatting — fetches skills, filters, sorts, calls `diagnose_skills`, then formats a markdown table. The table construction loop adds CC.

**Recommendation**: Extract `_format_skill_table(skills, reports) -> str` for the markdown table formatting. Keep the data-fetching in `l1_context`.

---

### 3.10 `EvolutionContextDescriber._build_judgment_history` (CC 10)

- **File**: `src/agent_nexus/platform/evolution/context_describer.py:280-303`
- **Lines**: 24
- **Target CC**: 4

**Root Cause**: Nested loop with accumulation — outer loop over skills, inner comprehension for counting applied/completed/fell_back. Three `sum(1 for ...)` comprehensions.

**Recommendation**: Replace three `sum()` calls with a single `Counter`:
```python
counts = Counter(j["status"] for j in judgments)  # if status field existed
```
Or use a helper `_count_judgment_outcomes(judgments) -> tuple[int, int, int]`.

---

### 3.11 `SkillLoader._split_body_resources` (CC 10)

- **File**: `src/agent_nexus/platform/skills/loader.py:186-217`
- **Lines**: 32
- **Target CC**: 4

**Root Cause**: State-machine parsing — tracks `in_fence` state while iterating lines, with two branches inside the loop. The offset tracking and conditional `break` add branches.

**Recommendation**: The function is a clean state-machine parser. Minor improvement: extract the line-processing into a generator `_find_resources_heading(content) -> int | None` that yields the offset. Low priority.

---

### 3.12 `SchemaTransformer._resolve_one_of_any_of` (CC 10)

- **File**: `src/agent_nexus/platform/gateway/schema_transformer.py:210-237`
- **Lines**: 28
- **Target CC**: 5

**Root Cause**: Type-dispatch with accumulation — iterates variants, classifies null vs non-null, resolves each, then handles Optional vs Union construction. The final Union-building loop adds CC.

**Recommendation**: Replace the manual Union construction with `Union[tuple(resolved_variants)]` or `functools.reduce(operator.or_, resolved_variants)`. The null/non-null split can use partition:
```python
null_variants, non_null = partition(lambda v: v.get("type") == "null", variants)
```

---

## 4. Focus Module Analysis

### 4.1 Gateway (`src/agent_nexus/platform/gateway/gateway.py`)

| Metric | Value |
|--------|-------|
| Lines | 680 |
| Methods | 24 |
| Avg CC | 3.48 |
| Max CC | 8 (`_agent_info`, `_register_agent_tools`) |

**Class size**: MCPGateway has 24 methods, which exceeds the SRP threshold of 10. However, the methods are well-decomposed:
- 3 core tools (search, list, info)
- 5 agent registration methods
- 4 IPC/health methods
- 4 tool building methods
- 4 external server methods
- 4 lifecycle methods (run_stdio, run_sse, stop, __init__)

**Risk**: Moderate. The class is large but methods are cohesive around the "MCP gateway" concept. The tool registration chain (`_register_agent_tools` → `_register_single_tool` → `_disambiguate_tool_name`) is well-factored.

**Recommendation**: Consider extracting `ExternalServerManager` for the 4 external server methods. The core agent methods can stay.

---

### 4.2 LLM Client (`src/agent_nexus/platform/agency/llm_client.py`)

| Metric | Value |
|--------|-------|
| Lines | 660 |
| Methods | 18 (in LLMClient) |
| Avg CC | 3.29 |
| Max CC | 9 (`from_config`, `_build_litellm_kwargs`, `_call_cli`) |

**Risk**: Moderate. Three B(9) methods, but they serve distinct responsibilities (factory, request building, CLI execution). The `from_config` method mixes factory logic with config resolution — a common pattern that's hard to avoid.

**Recommendation**:
- `from_config` (CC 9): Already well-delegated to `_resolve_provider` and `_resolve_capability`. The CC comes from the resolution chain — acceptable.
- `_build_litellm_kwargs` (CC 9): The optional parameter application (`max_tokens`, `top_p`, `timeout`, `api_key`, `api_base`) is inherently conditional. Could use a dict-merge pattern but the clarity gain is marginal.
- `_call_cli` (CC 9): The session recording block adds branches. Extract `_record_cli_execution(result, backend, ...)` to reduce.

---

### 4.3 Planner (`src/agent_nexus/platform/agency/planner.py`)

| Metric | Value |
|--------|-------|
| Lines | 291 |
| Functions/Methods | 10 |
| Avg CC | ~4.1 |
| Max CC | 7 (`generate_toml`, `resolve_dependencies`) |

**Risk**: Low. The planner is well-structured with clear separation between validation, DAG construction, and TOML generation. The class `DynamicCompositePlanner` has only 2 methods.

---

### 4.4 Executor (`src/agent_nexus/platform/agency/executor.py`)

| Metric | Value |
|--------|-------|
| Lines | 467 |
| Max CC | 10 (`_resolve_section` — false positive) |

**Risk**: Low. The `_resolve_section` CC 10 is a false positive (data-driven dict dispatch). The actual logic complexity is CC 3.

---

### 4.5 Task Graph (`src/agent_nexus/platform/orchestration/task_graph.py`)

| Metric | Value |
|--------|-------|
| Lines | 855 |
| Methods | 42 |
| Avg CC | 2.95 |
| Max CC | 9 (`add_task`) |

**Class size**: TaskGraph has 42 methods, significantly exceeding SRP threshold. However, many are thin wrappers (async mirrors, property accessors, context managers).

**Decomposition**:
- 8 lifecycle/context methods
- 4 core CRUD methods (add_task, add_tasks, start_task, complete_task, fail_task)
- 6 query methods (get_task, get_ready, get_blocked, get_snapshot)
- 6 async mirrors
- 6 internal graph algorithms (_build_dep_map, _build_reverse_map, etc.)
- 6 batch/internal helpers
- 4 validation methods

**Risk**: Moderate. The class is large but methods are thin and cohesive. The graph algorithm methods are well-extracted.

**Recommendation**: No urgent action. Consider extracting async mirrors into a mixin if the class grows further.

---

### 4.6 Evolver (`src/agent_nexus/platform/evolution/evolver.py`)

| Metric | Value |
|--------|-------|
| Lines | 431 |
| Methods | 11 |
| Avg CC | 4.0 |
| Max CC | 9 (`process_tool_degradation`, `_evolve_derived`) |

**Risk**: Moderate. `process_tool_degradation` (CC 9) has a loop with conditional anti-loop tracking. `_evolve_derived` (CC 9) has many sequential field constructions for the new SkillRecord.

**Recommendation**:
- `process_tool_degradation`: Extract the anti-loop tracking into a dedicated `AntiLoopTracker` class. Main loop becomes: filter unaddressed → evolve → mark addressed.
- `_evolve_derived`: Extract `_create_derived_record(parents, suggestion) -> SkillRecord` for the record construction. The parent-loading loop can use `_load_parents(store, target_ids) -> list[SkillRecord]`.

---

## 5. Summary Table — High-CC Functions

| Function | File | CC | Target | Root Cause | Priority | Est. ΔLines |
|----------|------|----|--------|------------|----------|-------------|
| `DAGDispatcher._no_more_work` | dag_dispatcher.py:300 | 11 | 5 | Mixed responsibilities | **P1** | -3 |
| `DAGDispatcher._run_dispatch_loop` | dag_dispatcher.py:338 | 11 | 6 | Nested conditionals | **P2** | -7 |
| `run_composition` | cli.py:595 | 11 | 4 | Main function syndrome | **P1** | -100 (restructure) |
| `_check_api_keys` | init_cmd.py:96 | 11 | 4 | Double-fallback pattern | **P2** | -5 |
| `evolution_history` | evolution_cmd.py:119 | 11 | 4 | Mixed resolve+format | **P2** | -11 |
| `_detect_risk_conflicts` | integrator.py:350 | 10 | 5 | Guard cascade | P3 | -5 |
| `load_dag_into_graph` | dag_dispatcher.py:118 | 10 | 4 | Mixed I/O+transform | **P2** | -10 |
| `_format_section_value` | cli.py:390 | 10 | 4 | Type dispatch | P3 | 0 |
| `_resolve_section` | executor.py:118 | 10 | 3 | **False positive** (dict dispatch) | **Skip** | 0 |
| `diagnose_skills` | health.py:193 | 10 | 5 | Mixed responsibilities | **P2** | -15 |
| `get_health_summary` | health.py:266 | 10 | 4 | Manual counting loop | **P1** | -10 |
| `_generate_suggestions` | analyzer.py:209 | 10 | 5 | Mixed iterate+dedup | P3 | -8 |
| `_parse_snapshot` | skill_store.py:785 | 10 | 4 | Defensive parsing | P3 | 0 |
| `l1_context` | context_describer.py:96 | 10 | 5 | Mixed I/O+format | P3 | -10 |
| `_build_judgment_history` | context_describer.py:280 | 10 | 4 | Triple sum() in loop | P3 | -5 |
| `_split_body_resources` | loader.py:186 | 10 | 4 | State machine parsing | P3 | 0 |
| `_resolve_one_of_any_of` | schema_transformer.py:210 | 10 | 5 | Manual Union construction | P3 | -5 |

### Priority Classification

- **P1 (Do now)**: Functions with CC >= 11 AND clear refactoring path AND high impact on maintainability
  - `run_composition` — main function syndrome, 193 lines, easy wins
  - `get_health_summary` — trivial Counter replacement
  - `DAGDispatcher._no_more_work` — extract classification + side effects

- **P2 (Do soon)**: Functions with CC >= 11 OR CC = 10 with mixed responsibilities
  - `DAGDispatcher._run_dispatch_loop`
  - `_check_api_keys`
  - `evolution_history`
  - `load_dag_into_graph`
  - `diagnose_skills`

- **P3 (Nice to have)**: Functions with CC = 10 that are inherently complex or already well-structured
  - Guard cascades, defensive parsing, state machines
  - `_resolve_section` — skip entirely (false positive)

---

## 6. Cross-Cutting Patterns

### Pattern 1: "Main Function Syndrome"
`run_composition` is the most egregious example. The fix is always the same: extract steps into named functions. The Click decorators inflate the apparent size but don't affect CC.

### Pattern 2: Guard Cascades
Several functions (`_detect_risk_conflicts`, `_parse_snapshot`, `_check_api_keys`) use early-return guards. While each guard is simple, 4+ guards push CC above 10. **Mitigation**: Group related guards into a validation function that returns a result type.

### Pattern 3: Manual Counting/Type Dispatch
`get_health_summary` (manual EvolutionType counting) and `_format_section_value` (isinstance chain) can use `Counter` and `match`/dispatch-dict respectively. These are the easiest wins.

### Pattern 4: False Positives from Data-Driven Patterns
`_resolve_section` uses a dict of lambdas — this is the correct pattern, but radon counts each lambda as adding CC. **No action needed** for such functions.

---

## 7. Maintainability Index

All modules scored well on the Maintainability Index (radon mi). No modules fell below the "Medium" threshold. The codebase uses clear naming, reasonable function lengths, and consistent structure.

---

## 8. Recommendations Summary

1. **Immediate (P1)**: Refactor `run_composition` into 5 step functions. Replace manual counting in `get_health_summary` with `Counter`. Extract task classification in `_no_more_work`.
2. **Near-term (P2)**: Extract resolve/format helpers in `evolution_history`. Split `load_dag_into_graph` into filter/transform/insert. Extract `_resolve_skill_list` from `diagnose_skills`.
3. **Skip**: `_resolve_section` (false positive). `_parse_snapshot` (defensive parsing is appropriate). `_split_body_resources` (clean state machine).
4. **Architectural**: MCPGateway (24 methods) and TaskGraph (42 methods) are large classes. Monitor for growth, but no urgent refactoring needed — methods are cohesive and thin.
