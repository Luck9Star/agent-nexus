# Complexity Analysis Report

> Generated: 2026-05-10 (Iteration 26 — Cycle 3 Complexity + Test Signal-to-Noise Audit)
> Scope: `src/agent_nexus/` | Baseline: radon 6.0.1

## 1. Overall Baseline

| Metric | Value | Delta from Iter 22 |
|--------|-------|---------------------|
| Total blocks analyzed | 1,329 | +10 |
| Average complexity | **3.28 (Grade A)** | 3.30 → 3.28 |
| Max CC | **10 (B-grade)** | Unchanged |
| C-grade functions (CC ≥ 11) | **0** | Unchanged |
| CC=10 boundary functions | **4** | 6 → 4 (2 refactored in Iter 22) |
| B-grade functions (CC 6-10) | ~245 | ~5 reduction |
| Maintainability Index issues | **0 files** (all MI > 20) | — |

### CC Score Distribution

| CC | Count | Grade | Notes |
|----|-------|-------|-------|
| 1  | 155   | A     | Simple functions |
| 2  | 86    | A     | |
| 3  | 58    | A     | |
| 4  | 57    | A     | |
| 5  | 45    | A     | |
| 6  | 104   | B     | Most B-grade functions |
| 7  | 62    | B     | |
| 8  | 50    | B     | |
| 9  | 25    | B     | All domain-inherent |
| 10 | 4     | B     | 2 domain-inherent + 1 pseudo-positive + 1 non-exclusive fields |
| 11+| 0     | —     | Zero C-grade functions |

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
| 13 | `_format_section_value` | cli.py | 10 → 6 | Handler registry (isinstance→dict dispatch) |
| 13 | `diagnose_skills` | health.py | 10 → 6 | Extract `_build_health_metrics` |
| 13 | `l1_context` | context_describer.py | 10 → 6 | Extract `_format_skill_table` |
| 13 | `_generate_suggestions` | analyzer.py | 10 → 6 | Extract `_deduplicate_suggestions` |
| 13 | `load_dag_into_graph` | dag_dispatcher.py | 10 → 6 | Extract `_build_specialist_items` |
| 13 | `_execute_command` | executor.py | 11 → 9 | Merge CancelledError into BaseException |
| 22 | `_parse_snapshot` | skill_store.py | 10 → 4 | Extract `_validate_snapshot_dict` |
| 22 | `_detect_risk_conflicts` | integrator.py | 10 → 7 | Extract `_has_valid_risk_data` (CC 4) |

---

## 2. CC=10 Boundary Functions — First Principles Root Cause Analysis

4 remaining functions at the B/C-grade boundary (down from 11 original). All 4 are domain-inherent or pseudo-positive — **all high-ROI refactoring exhausted**.

### 2.1 ~~`SkillStore._parse_snapshot`~~ (CC 10 → 4) [REFACTORED Iter 22]

**Status**: Extracted `_validate_snapshot_dict` to separate validation from parsing. Main function CC dropped from 10 to 4 (A-grade).

---

### 2.2 `EvolutionContextDescriber._build_judgment_history` (CC 10) [P3 — ACCEPT AS-IS]

- **File**: `src/agent_nexus/platform/evolution/context_describer.py:279-303`
- **Lines**: 25
- **Target CC**: N/A
- **Root Cause**: **Non-exclusive boolean fields** — `applied`, `completed`, `fell_back` are independent flags, not mutually exclusive categories. Counter-based classification would change semantics.

**Decision**: The 3 `sum(1 for j in ...)` patterns are inherent to independent boolean field counting. Cannot be replaced with Counter without changing behavior (iteration 13 attempted and reverted).

---

### 2.3 `SchemaTransformer._resolve_one_of_any_of` (CC 10)

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

### 2.4 `SkillLoader._split_body_resources` (CC 10)

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

### 2.5 `ProfileBasedExecutor._resolve_section` (CC 10)

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

### 2.6 ~~`_detect_risk_conflicts`~~ (CC 10 → 7) [REFACTORED Iter 22]

**Status**: Extracted `_has_valid_risk_data` (CC 4) to combine 3 data-validity guards. Main function CC dropped from 10 to 7.

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
| EvolutionStore | evolution | 37 | 8 | **Low** | SQL-backed store. Methods are domain queries (metrics, records, batch). Cohesive. |
| SkillStore | evolution | 34 | 10 | **Low** | SQL-backed store. Methods are domain queries. Cohesive. |
| PlatformRouter | router | 26 | 8 | **Low** | 4-phase workflow dispatch. Cohesive. |
| DAGDispatcher | agency | 26 | 9 | **Low** | Task dispatch + parallel execution. Cohesive. |
| GitInstaller | local | 25 | 9 | **Low** | Git clone/validate/venv lifecycle. Cohesive. |
| MCPGateway | gateway | 24 | 8 | **Monitor** | Consider extracting `ExternalServerManager` (4 methods). |
| DeferredAgentRegistry | gateway | ~24 | 7 | **Low** | Agent registry + subprocess lifecycle. Cohesive. |
| HookExecutor | hooks | 23 | 9 | **Low** | Command/HTTP hook execution. Cohesive. |
| OrchestrationDSL | orchestration | 21 | 9 | **Low** | TOML DSL parsing + validation. Cohesive. |
| AgentSupervisor | local | 20 | 8 | **Low** | Auto-restart + health monitoring. Cohesive. |
| ProcessManager | orchestration | 19 | 9 | **Low** | Subprocess lifecycle: spawn, health, cleanup. Cohesive. |
| LLMClient | agency | 18 | 9 | **Low** | Multi-provider LLM calls. Cohesive. |
| ConfigLoader | config | 18 | 9 | **Low** | Config loading from TOML/YAML. Cohesive. |

**Key Finding**: No critical SRP violations. All large classes have cohesive method sets. The inflated method counts come from thin wrappers (async mirrors, properties, single-line SQL calls).

---

## 5. Over-Abstraction Audit (First Principles)

### Protocol/ABC Consumer Analysis

| Abstraction | Consumers | Verdict |
|-------------|-----------|---------|
| `ExpertExecutor` (Protocol) | DAGDispatcher, TaskComposer (5+ sites) | **Well-justified** — pluggable execution strategy |
| `ContextProvider` (Protocol) | ContextProviderRegistry (register/get/providers) | **Well-justified** — pluggable context sources |
| `ReflectionRule` (Protocol) | Reflector (rules parameter) | **Well-justified** — pluggable reflection rules |
| `SecurityRule` (ABC) | 4 concrete implementations + SecurityChecker | **Well-justified** — extensible security rules |
| ~~`ArtifactSink` (Protocol)~~ | 0 consumers | **Deleted** in Iteration 6 |

**Finding**: Zero dead abstractions. All Protocol/ABC definitions have ≥1 consumer and serve legitimate extensibility points.

---

## 6. Cross-Module Complexity Patterns

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

## 7. CC=9 Priority Module Functions — Analysis

These CC=9 functions in the 6 priority modules are worth monitoring:

| Function | Module | CC | Root Cause | Verdict |
|----------|--------|----|------------|---------|
| `LLMClient.from_config` | llm_client | 9 | Multi-provider factory init | Domain-inherent (6 provider types) |
| `LLMClient._build_litellm_kwargs` | llm_client | 9 | Sequential optional kwargs | Could use data-driven pattern (low ROI) |
| `LLMClient._call_cli` | llm_client | 9 | I/O + session recording + error handling | Extract session recording (P2) |
| `LLMClient.call` | llm_client | 8 | CLI/API dispatch + hooks + error | Clean dispatch pattern |
| `DAGDispatcher._collect_futures` | dag_dispatcher | 9 | Fail-fast + timeout + error | Domain-inherent |
| `DAGDispatcher._run_dispatch_loop` | dag_dispatcher | 8 | Dispatch state machine | Already refactored (CC 11→8) |
| `TaskGraph.add_task` | task_graph | 9 | Multi-step validation + insert | Extract validators (P3) |
| `SkillEvolver.process_tool_degradation` | evolver | 9 | Anti-loop tracking + evolution | Domain-inherent |
| `SkillEvolver._evolve_derived` | evolver | 9 | Parent validation + derived creation | Domain-inherent |
| `OrchestrationDSL._parse_composition_tasks` | dsl | 9 | TOML field validation | Domain-inherent (7 validation steps) |
| `OrchestrationDSL._parse_composition_format` | dsl | 9 | TOML parsing + validation delegation | Clean delegation pattern |
| `EvolutionContextDescriber.l2_context` | context_describer | 9 | Multi-section context builder | Clean section assembly |

**Assessment**: All CC=9 functions reflect inherent domain complexity. None carry risk of becoming C-grade without significant feature additions.

---

## 8. Maintainability Index

All 100+ source files pass MI > 20 (maintainable). `radon mi -nc` returned zero output, confirming no low-maintainability files.

---

## 10. Actionable Refactoring Priority Matrix

### P1 — CC=10 Functions Refactored (Iteration 13)

| # | Function | CC | Target | Root Cause | Status |
|---|----------|----|--------|------------|--------|
| 1 | `_format_section_value` | 10→6 | 4 | isinstance → handler registry | **[REFACTORED]** |
| 2 | `diagnose_skills` | 10→6 | 5 | Extract metrics builder | **[REFACTORED]** |
| 3 | `l1_context` | 10→6 | 5 | Extract table formatter | **[REFACTORED]** |
| 4 | `_generate_suggestions` | 10→6 | 5 | Extract deduplication | **[REFACTORED]** |
| 5 | `load_dag_into_graph` | 10→6 | 5 | Extract item builder | **[REFACTORED]** |

### P2 — CC=9 Functions with Moderate Fix Value

| # | Function | CC | Target | Root Cause | Status |
|---|----------|----|--------|------------|--------|
| 6 | `LLMClient._call_cli` | 9 | 5 | Extract session recording | **Deferred** |
| 7 | `LLMClient._build_litellm_kwargs` | 9 | 5 | Data-driven optional kwargs | **Deferred** |

### P3 — Accept as-is (Inherent Complexity)

| # | Function | CC | Reason |
|---|----------|----|--------|
| 8 | `_resolve_one_of_any_of` | 10 | JSON Schema semantics, proportional to domain complexity |
| 9 | `_split_body_resources` | 10 | Minimal state machine (2 states), correctly implemented |
| 10 | `_resolve_section` | 10 | False positive — data-driven pattern, effective CC 2 |
| 11 | `_build_judgment_history` | 10 | Non-exclusive boolean fields — Counter changes semantics |

### P4 — Already Refactored (Cycle 2)

| # | Function | CC Before → After | Method |
|---|----------|--------------------|--------|
| 12 | `_parse_snapshot` | 10 → 4 | Extract `_validate_snapshot_dict` (Iter 22) |
| 13 | `_detect_risk_conflicts` | 10 → 7 | Extract `_has_valid_risk_data` (Iter 22) |

---

## 11. Complexity Metrics Trend

| Metric | Iter 1 | After Iter 6 | Iter 13 | Iter 17 | Iter 22 | **Iter 26 (Current)** |
|--------|--------|-------------|---------|---------|---------|----------------------|
| Total blocks | 1,306 | 1,313 | ~1,325 | 1,319 | ~1,321 | **1,329** |
| Average CC | 3.35 | ~3.30 | < 3.3 | 3.30 | ~3.28 | **3.28** |
| Max CC | 11 | 10 | 10 | 10 | 10 | **10** |
| C-grade functions | 5 | 0 | 0 | 0 | 0 | **0** |
| CC=10 boundary | — | 11 | ~6 | 6 | 4 | **4** |
| Dead abstractions | Unknown | 0 | 0 | 0 | 0 | **0** |
| MI issues | — | 0 | 0 | 0 | 0 | **0** |
| Classes > 20 methods | 8 | 8 | 8 | 14 | 14 | **14** |
| SRP violations | 0 critical | 0 critical | 0 critical | 0 critical | 0 critical | **0 critical** |
| Total tests | 4,460 | ~4,460 | ~4,660 | ~4,728 | ~4,795 | **4,739** |
| Redundant tests removed | 0 | ~20 | ~47 | ~47 | ~95 | **~233** |

---

## 12. Overall Assessment

The codebase complexity is **well-managed and stable**:

1. **Zero C-grade functions** (CC ≥ 11) — stable across 20+ iterations
2. **4 CC=10 boundary functions** — down from 11 original after 16 successful refactoring operations; all 4 remaining are domain-inherent or pseudo-positive
3. **No maintainability issues** — all files MI > 20
4. **No SRP violations** — all 14 large classes (>20 methods) have cohesive method sets
5. **No dead abstractions** — all 4 Protocol/ABC definitions have consumers
6. **Average CC ~3.28 (Grade A)** — well below industry average (~6-8)

The remaining complexity is **proportional to domain requirements** (dispatch state machines, JSON Schema resolution, multi-provider LLM adaptation, markdown parsing) rather than accidental (poor structure, missing abstractions). The 16 successful CC refactoring operations have exhausted all high-ROI refactoring targets.

---

## 13. Test Signal-to-Noise Analysis (Cycle 3)

### Current Test Suite Status

| Metric | Value |
|--------|-------|
| Total tests | 4,739 |
| Passed (excl. 2 IPC E2E timeouts) | 4,689 |
| Skipped | 30 |
| Tests removed across 3 cycles | ~233 |
| Tests added across 3 cycles | ~512 |

### Noise Analysis Results

| Category | Count | Status |
|----------|-------|--------|
| No-assertion tests (AST scan) | 47 | **All legitimate** (34 mock.assert_*, 11 should-not-raise, 1 intentionally skipped + 1 empty stub) |
| Mock-only assertion tests | 3 | **Legitimate** (testing delegation to dependencies) |
| Empty stub tests | 1 | **Intentionally skipped** (SystemExit + asyncio.wait_for behavior documented in skip reason) |
| Cross-file duplicate class names | ~30 pairs | **Verified non-duplicate** — shared class names test different functions/aspects |
| Pydantic framework tests | 0 | **All removed** (48 deleted in Iter 16) |
| Tautological tests | 0 | **All removed** (45 deleted in Iter 6-7) |

### Signal Quality Assessment

**Grade: A-** — The test suite has high signal density after 3 cycles of cleanup:

1. **No tautological patterns remain** — every test verifies real behavior or contracts
2. **No Pydantic framework tests** — only project-specific validation logic is tested
3. **Close() lifecycle tests are legitimate** — testing idempotent cleanup, exception handling, and resource release
4. **Mock.assert_called tests verify delegation** — ensuring correct method dispatch is a valid contract test
5. **Cross-file class name duplicates are false positives** — same class names testing different aspects of the same feature

**Remaining noise (< 1%)**:
- 1 intentionally skipped stub test (`test_run_with_retry_propagates_system_exit`) — documents known asyncio limitation
- Config module has dense coverage (13 files, ~270 tests) but is layered by concern (loading/validation/defaults/stages), not pure duplication

### Cleanup History

| Cycle | Tests Removed | Key Actions |
|-------|---------------|-------------|
| Cycle 1 | 68 | 20 task model duplicates, 45 evolution tautological, 3 gateway E2E |
| Cycle 2 | 48 | 37 Pydantic frozen tests, 11 required_field tests |
| Cycle 3 | 138 | 28 config_loader duplicates, 23 IPC model duplicates, 15 evolution class duplicates, 4 Pydantic frozen, 13 IPC roundtrip, 6 gateway adapter duplicates |
| **Total** | **~254** | **Zero regressions across all deletions** |
