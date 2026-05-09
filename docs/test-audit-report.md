# Test Audit Report: Gap & Redundancy Analysis (Cycle 5)

**Date**: 2026-05-10
**Branch**: nf/serena-using-superpo-ff6f4b
**Iteration**: 27 (Cycle 5 — full re-analysis after Cycle 4 async_safety focus)
**Total Tests**: 4,739 across 163 files

---

## 1. Overall Statistics

| Metric | Cycle 4 (Iter 20) | Current (Cycle 5) | Delta |
|--------|--------------------|--------------------|-------|
| Test files (excl. conftest/__init__) | 174 | 163 | -11 (iter 25 cleanup) |
| Unit test files | 129 | 129 | 0 |
| E2E test files | 18 | 18 | 0 |
| Integration test files | 11 | 10 | -1 |
| Capability test files | 6 | 6 | 0 |
| **Total test functions** | **4,841** | **4,739** | **-102** |
| No-assertion tests | 1 (skipped) | 4 (LOW severity) | +3 (stricter detection) |
| Async safety test grade | B+ | B+ | Stable |
| Cyclomatic complexity | avg CC 3.28 | avg CC 3.28 | Stable |

---

## 2. Gap Analysis: Module Coverage

### 2.1 Module Coverage Distribution

83 source modules analyzed. Coverage classification:

| Classification | Count | Percentage | Description |
|---------------|-------|------------|-------------|
| HIGH (≥70%) | 46 | 55% | Well-tested, maintenance only |
| MEDIUM (40-69%) | 32 | 39% | Targeted gap-filling needed |
| LOW (10-39%) | 4 | 5% | Significant gaps, priority action |
| ZERO (0%) | 1 | 1% | Completely untested |

### 2.2 ZERO Coverage

| Module | Functions | Issue |
|--------|-----------|-------|
| `models/errors.py` | 7 error classes | Zero tests. All custom exceptions (SkillLoadError, AgentRuntimeError, etc.). May be acceptable if only used as exception hierarchy, but `__str__` / `__repr__` and any custom methods should be tested. |

### 2.3 P0 Gaps — Critical

| # | Module | Coverage | Functions | Issue |
|---|--------|----------|-----------|-------|
| 1 | `agency/cli.py` | ~30% | CLI backend commands | Most CLI backend functions untested. `run_composition`, `_execute_pipeline`, and 7+ CLI command handlers have zero direct coverage. |
| 2 | `evolution/engine.py` | ~40% | Evolution orchestration | `EvolutionEngine` high-level orchestration methods undertested. Skill analysis → evolve → promote cycle coverage gaps. |
| 3 | `models/composition.py` | ~30% | Composition models | Composite agent models with complex validation logic. Multiple validators and computed fields untested. |
| 4 | `agency/dag_dispatcher.py` | Partial | Core dispatch functions | `_run_dispatch_loop`, `_drain_single_future`, `_collect_futures` still lack direct unit tests (flagged since Cycle 4). |

### 2.4 P1 Gaps — Moderate Risk

| # | Module | Coverage | Issue |
|---|--------|----------|-------|
| 1 | `platform/local/_lifecycle.py` | ~30% | Agent lifecycle management. Start/stop/restart flows partially tested. |
| 2 | `local/installer.py` | ~55% | `install_local` still entirely untested (flagged since Cycle 4). Update happy path untested. |
| 3 | `local/supervisor.py` | ~55% | `_build_command`, `_try_venv_command`, `_try_system_command` untested. |
| 4 | `local/sources.py` | ~55% | BaseException cleanup handlers untested (flagged since Cycle 4). |
| 5 | `agency/llm_integrator.py` | ~50% | Semantic synthesis logic. Integration with quality gate partially covered. |
| 6 | `agency/executor.py` | ~60% | `LLMExecutor.close` httpx cleanup, network timeout scenarios. |
| 7 | `task_graph.py` | ~65% | CancelledError during async operation, concurrent write asyncio.Lock, `aclose` racing. |
| 8 | `process_manager.py` | ~70% | `stop_agent` PermissionError (macOS SIP), `_cleanup_dead` racing with `start_agent`. |
| 9 | `external_mcp_adapter.py` | ~60% | BaseException disconnect handler untested. |
| 10 | `hooks/executor.py` | ~70% | `_execute_http` CancelledError, `close` when httpx client mid-request. |

### 2.5 P2 Gaps — Low Priority

| Module | Gap |
|--------|-----|
| `task_graph.py` | `_would_create_cycle` concurrent pre-check race, `_detect_cycles_conn` large graph performance |
| `ipc.py` | `receive` cancelled between buffered reads, `close` racing with concurrent `send` |
| `dag_dispatcher.py` | `adispatch` via `Task.cancel()` |
| `task_composer.py` | `_check_deadline`, `_should_skip_task`, `_dispatch_legacy` path |
| `cli.py` | `run_composition`, `_execute_pipeline` end-to-end |
| `model_db.py` | `_trigram_candidates` fuzzy matching, `_build_index` pipeline |
| `router.py` | `_execute_parallel_agents` CancelledError propagation |
| `gateway.py` | `_stop_external_servers` mid-connect, `_cleanup_agent_registration` name collision |
| `config/config_templates.py` | Template rendering edge cases |

### 2.6 Module Coverage Matrix

| Module | Symbols | Covered | Gaps | Coverage | Trend |
|--------|---------|---------|------|----------|-------|
| hooks/ | 6 | 6 | 0 | 100% | Stable |
| skills/ | 16 | 16 | 0 | 100% | Stable |
| models/ | ~95 | ~88 | ~7 | 93% | -5% (stricter measurement) |
| runtime/ | ~50 | ~47 | 3 | 94% | Stable |
| orchestration/ | ~75 | ~70 | 5 | 93% | -3% |
| gateway/ | ~65 | ~61 | 4 | 94% | -3% |
| evolution/ | ~110 | ~100 | 10 | 91% | -6% (engine/cli gaps) |
| config/ | 44 | 40 | 4 | 91% | -4% |
| agency/ | ~120 | ~90 | 30 | 75% | -18% (cli.py, dag gaps) |
| router/ | ~20 | ~18 | 2 | 90% | -5% |
| local/ | ~50 | ~40 | 10 | 80% | -12% (installer/supervisor gaps) |

---

## 3. Redundancy Analysis

### 3.1 Summary

| Category | Count | Severity | Action |
|----------|-------|----------|--------|
| Pydantic framework tests | ~200 | Low | Delete construction/defaults/serialization boilerplate |
| Duplicate gateway file | ~20 | Medium | Merge `test_gateway_tool_adapter.py` into `test_gateway_module.py` |
| Frozen/stdlib behavior tests | ~12 | Low | Delete |
| No-assertion tests (verified) | 4 | Low | Retain (valid "should not raise" patterns) |
| **Total removable** | **~236** | | |

### 3.2 Pydantic Framework Tests (~200)

Tests that verify Pydantic's own behavior rather than project logic:

| File | Pattern | Count | Example |
|------|---------|-------|---------|
| `test_agent_model.py` | Constructor defaults, field validation | ~25 | `assert AgentConfig(role="x").role == "x"` |
| `test_capability_model.py` | Enum values, frozen checks | ~20 | `assert CapabilityType.TOOL.value == "tool"` |
| `test_composition_model.py` | Nested model construction | ~20 | `assert CompositeAgent(...).name == "x"` |
| `test_config_model.py` | TOML parsing, defaults | ~25 | `assert Config().agents == []` |
| `test_context_model.py` | Token budget defaults | ~20 | `assert ContextTier.SYSTEM.value == "system"` |
| `test_evolution_model.py` | Evolution record construction | ~20 | `assert EvolutionRecord(...).skill_id` |
| `test_hooks_model.py` | Hook type enum values | ~15 | `assert HookType.PRE.value == "pre"` |
| `test_ipc_model.py` | Message construction | ~15 | `assert IPCMessage(...).type` |
| `test_runtime_model.py` | Runtime config defaults | ~20 | `assert RuntimeConfig().timeout` |
| `test_task_model.py` | Task status enum, priority | ~20 | `assert TaskStatus.PENDING.value == "pending"` |

**Deletion criteria**: Any test that only verifies Pydantic construction, field defaults, enum values, or serialization without project-specific validation logic.

**Retention criteria**: Tests with custom validators, `@model_validator`, computed fields, or cross-field invariants.

### 3.3 Duplicate File Pairs

| Pair | Overlap | Redundant Tests | Merge Recommendation |
|------|---------|-----------------|---------------------|
| `test_gateway_tool_adapter.py` ⊂ `test_gateway_module.py` | McpToolAdapter/SchemaTransformer tests | ~20 | Delete `test_gateway_tool_adapter.py`, keep `test_gateway_module.py` (superset) |
| `test_config.py` + `test_config_loader.py` + `config/test_loader.py` | Empty config, invalid_api_type, TOML errors | ~15-20 | Consolidate to 2 files (model tests + loader tests) |
| `test_permission_checker.py` vs `test_permission_checker_unit.py` | 9 shared concepts | ~15 | Keep unit file, remove integration duplicates |
| `test_process_manager.py` vs `test_process_manager_unit.py` | 10 similar tests | ~10 | Keep unit file, remove integration duplicates |
| `test_llm_planner.py` vs `test_llm_planner_structured.py` | Fallback/parse paths | ~5-7 | Keep structured, remove legacy |

### 3.4 No-Assertion Tests (Verified)

4 tests flagged as having no direct `assert` statement. All are valid "should not raise" patterns using `pytest.raises` context managers or implicit pass-on-no-exception:

| File | Test | Severity | Verdict |
|------|------|----------|---------|
| `test_router_module.py:1361` | `test_run_with_retry_propagates_system_exit` | LOW | `@pytest.mark.skip` — intentional |
| Various | 3 "should not raise" patterns | LOW | Valid — testing that code paths don't throw |

### 3.5 Mega-File Anti-Pattern

Two test files exceed 2500 lines, indicating insufficient modularization:

| File | Lines | Recommendation |
|------|-------|----------------|
| `test_gateway_module.py` | 2,597 | Split by concern: adapter tests, lifecycle tests, tool call tests |
| `test_local_module.py` | 3,363 | Split by module: installer tests, supervisor tests, sources tests, CLI tests |

### 3.6 Redundancy Trend

| Cycle | Action | Tests Affected |
|-------|--------|---------------|
| Cycle 2 (Iter 7) | Deleted 47 redundant tests | +71 net |
| Cycle 3 (Iter 16) | Deleted 48 zero-signal Pydantic tests | -48 |
| Cycle 3 (Iter 25) | Removed 138 tests (2 file deletions, 7 class removals) | -138 |
| **Cycle 5 identified** | **~236 removable** | **Pending action** |
| **Cumulative removed** | **233 across 3 cycles** | **+222 P0 added** |

---

## 4. E2E Test Quality Assessment

### 4.1 Classification

| Classification | Files | Tests | Files |
|----------------|-------|-------|-------|
| **TRUE_E2E** | 5 | ~91 | hooks_lifecycle, ipc_async_safety, ipc_real_subprocess, process_manager_async_safety, process_manager_cancel |
| **INTEGRATION** | 11 | ~248 | agency, agency_pipeline, config, dag_dispatcher, dsl_toml, evolution_*, task_graph_*, runtime, gateway_tool_call (partial) |
| **MISCLASSIFIED_UNIT** | 2 | ~73 | test_runtime_security_e2e (~59 unit tests), test_gateway_tool_call_e2e (~14 unit tests) |

### 4.2 TRUE_E2E Details

These 5 files use real OS resources (subprocess, pipes, filesystem) and verify actual side effects:

| File | Tests | What It Tests |
|------|-------|---------------|
| `test_hooks_lifecycle_e2e.py` | ~18 | Hook execution with real subprocess |
| `test_ipc_async_safety_e2e.py` | ~12 | IPC with real pipe I/O under concurrent access |
| `test_ipc_real_subprocess_e2e.py` | ~15 | IPC with real subprocess stdin/stdout |
| `test_process_manager_async_safety_e2e.py` | ~25 | Process lifecycle with real OS processes |
| `test_process_manager_cancel_e2e.py` | ~21 | Cancel/kill with real subprocess |

### 4.3 MISCLASSIFIED_UNIT Details

| File | Problem | Recommendation |
|------|---------|----------------|
| `test_runtime_security_e2e.py` | ~59 tests mock all I/O — no real subprocess, no real AST execution | Re-classify as unit tests. Add 3-5 TRUE_E2E tests with real runtime. |
| `test_gateway_tool_call_e2e.py` | ~14 tests use mock adapters — no real MCP connection | Re-classify as unit tests. Keep existing TRUE_E2E subset. |

### 4.4 Missing E2E Scenarios

| Priority | Scenario | Description |
|----------|----------|-------------|
| **P0** | Gateway full lifecycle | Register agent → start subprocess → discover tools → MCP call → get result → cleanup |
| **P0** | CLI init + install + run | `agent-nexus init` → `install` → `run` complete pipeline |
| **P1** | Router composite 4-phase flow | Load DSL → create TaskGraph → execute with echo agent → aggregate results |
| **P1** | External MCP adapter real connection | Connect stdio MCP server → discover tools → call → disconnect |
| **P2** | Evolution autonomous cycle | Seed skills → analyze → evolve → promote → verify post-promotion availability |

---

## 5. Async Safety Assessment (Carried Forward)

### 5.1 Grade Summary

| Dimension | Grade | Change from Cycle 4 |
|-----------|-------|---------------------|
| CancelledError handling | B+ | Stable |
| Resource cleanup | B | Stable |
| Concurrent access | C+ | Stable |
| BaseException coverage | C | Stable |
| gather safety | A | Stable |
| Timeout coverage | A- | Stable |
| **Overall** | **B+** | **Stable** |

### 5.2 Outstanding Async Safety Gaps

Unchanged from Cycle 4 P0/P1 list. Key items:

- `TaskGraph` concurrent SQLite write tests (asyncio.Lock serialization) — **D grade**
- `Router/subtask.py` BaseException re-raise — **F grade** (test is skipped)
- `ExternalMcpAdapter` BaseException disconnect — **no test**
- `evolution/store.py` `evolve_skill` UPSERT race — **no test**

---

## 6. Priority Actions (Cycle 5)

### P0 — Immediate

| # | Action | Impact | Est. Tests |
|---|--------|--------|------------|
| 1 | Delete ~200 Pydantic framework tests | Remove low-signal noise | -200 |
| 2 | Add tests for `agency/cli.py` commands | Cover 70% of CLI backend | +30 |
| 3 | Add `evolution/engine.py` orchestration tests | Cover evolution cycle | +15 |
| 4 | Add `models/composition.py` validator tests | Cover complex validation | +10 |
| 5 | Re-classify `test_runtime_security_e2e.py` as unit | Fix E2E classification accuracy | 0 |
| 6 | Add Gateway full lifecycle E2E test | Cover P0 E2E gap | +5 |

### P1 — Medium Priority

| # | Action | Impact |
|---|--------|--------|
| 1 | Merge `test_gateway_tool_adapter.py` into `test_gateway_module.py` | Eliminate ~20 duplicate tests |
| 2 | Split mega-files (gateway 2597L, local 3363L) into focused test files | Improve maintainability |
| 3 | Add TaskGraph concurrent write tests | Verify asyncio.Lock serialization |
| 4 | Add `install_local` tests (flagged since Cycle 4) | Cover critical install path |
| 5 | Consolidate config test triple → 2 files | Remove ~15-20 duplicates |

### P2 — Long Term

| # | Action |
|---|--------|
| 1 | Add BaseException tests for sources.py, lockfile.py, utils.py, _shared.py |
| 2 | Create CLI pipeline E2E test (init → install → run) |
| 3 | Add LLMExecutor network timeout + CancelledError tests |
| 4 | Unskip `test_run_with_retry_propagates_system_exit` or add alternative |
| 5 | Add ExternalMcpAdapter BaseException disconnect test |

---

## 7. Iteration History

| Iteration | Action | Tests Affected |
|-----------|--------|---------------|
| v1 (Iter 1) | Initial coverage analysis | Baseline: 4,437 tests |
| v2 (Iter 4) | Deep audit: corrections, redundancy | Identified ~96 removable |
| Iter 7 | Deleted 47 redundant + added 128 P0 tests | +71 net (4,437→4,508→4,778) |
| Cycle 3 (Iter 11) | Full re-analysis: no-assertion debunked | Claimed 176 no-assertion (later debunked) |
| Iter 14 | Added 94 P0 tests + E2E rewrites | +94 new, 37 reclassified |
| Iter 16 | Deleted 48 zero-signal Pydantic tests | -48 frozen/required_field |
| Cycle 4 (Iter 20) | async_safety focused re-audit | Corrected 176→1 no-assertion; 7 P0 async gaps |
| Iter 25 | Removed 138 tests (2 deletions, 7 class removals) | -138 |
| **Cycle 5 (Iter 27)** | **Full re-analysis: module coverage + redundancy + E2E** | **4,739 tests; ~236 removable identified** |

---

*Report generated by 3 parallel agents (E2E quality analysis, module coverage scan, redundancy detection). All findings cross-referenced against source code and verified with `pytest --co`.*
