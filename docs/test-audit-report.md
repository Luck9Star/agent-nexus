# Test Audit Report: Gap & Redundancy Analysis (Cycle 4 — Iteration 34)

**Date**: 2026-05-10
**Branch**: nf/serena-using-superpo-ff6f4b
**Iteration**: 34 (Cycle 4 — full re-analysis after Iter 32 Serena audit + Iter 33 complexity)
**Total Tests**: 4,816 across 166 files
**Baseline**: 1,333 blocks, avg CC 3.28, 0 C-grade functions

---

## 1. Overall Statistics

| Metric | Cycle 5 (Iter 27) | Current (Cycle 4) | Delta |
|--------|--------------------|--------------------|-------|
| Test files | 163 | 166 | +3 |
| Unit test files | 129 | 129 | 0 |
| E2E test files | 18 | 21 | +3 |
| Integration test files | 10 | 10 | 0 |
| Capability test files | 6 | 0 | (removed) |
| **Total test functions** | **4,739** | **4,816** | **+77** |
| No-assertion tests | 4 (LOW) | 2 (verified) | -2 |
| Public API symbols | ~834 | ~834 | Stable |
| Avg CC / max CC | 3.28 / 10 | 3.28 / 10 | Stable |

---

## 2. Gap Analysis: Module Coverage

### 2.1 Module Coverage Distribution

83 source modules analyzed across 102 Python source files (~834 public symbols).

| Classification | Count | Percentage | Description |
|---------------|-------|------------|-------------|
| HIGH (≥70%) | 46 | 55% | Well-tested, maintenance only |
| MEDIUM (40-69%) | 32 | 39% | Targeted gap-filling needed |
| LOW (10-39%) | 4 | 5% | Significant gaps, priority action |
| ZERO (0%) | 1 | 1% | Completely untested |

### 2.2 ZERO Coverage

| Module | Symbols | Issue |
|--------|---------|-------|
| `models/errors.py` | 1 (`AgentNexusError`) | Zero tests. Single custom exception hierarchy. Acceptable if only used as base class. |

### 2.3 P0 Gaps — Critical Untested Paths

| # | Area | Function/Path | Risk |
|---|------|---------------|------|
| 1 | hooks | `_execute_http` swallows `CancelledError` — `except Exception` catches BaseException subclass, preventing task cancellation propagation through HTTP hooks | **Bug** — cancellation broken |
| 2 | hooks | `_execute_http` httpx per-request timeout (`httpx.Timeout(hook.timeout_seconds)`) never tested | Hangs on slow HTTP |
| 3 | gateway | `additionalProperties` schema pattern never tested in `_build_params_from_schema` or `SchemaTransformer` | Invalid param generation |
| 4 | gateway | `_disambiguate_tool_name` max-100 overflow guard (`suffix > 100 → ValueError`) never tested | Crash on many agents |
| 5 | evolution | `_generate_init_py`, `_generate_mcp_adapter`, `_generate_pyproject` have **zero direct tests** | Broken promoted agents |
| 6 | gateway | Nested `$ref` chains (3+ levels) only tested indirectly | Infinite recursion / wrong types |
| 7 | agency/cli.py | Most CLI backend command functions have zero direct unit test coverage | ~70% of CLI untested |
| 8 | dag_dispatcher | `_run_dispatch_loop`, `_drain_single_future`, `_collect_futures` still lack direct unit tests (flagged since Cycle 4) | Core dispatch untested |

### 2.4 P1 Gaps — Moderate Risk

| # | Area | Gap |
|---|------|-----|
| 1 | hooks | `_execute_prompt` / `_execute_agent` zero direct tests (placeholder implementations) |
| 2 | hooks | SSRF protection missing `169.254.169.254` (cloud metadata), IPv6 loopback, `0.0.0.0` |
| 3 | skills | `parse_file` `UnicodeDecodeError` on non-UTF-8 files |
| 4 | skills | `parse_file` `PermissionError` on unreadable files |
| 5 | evolution | `_evolve_captured` with nonexistent / unwritable `capture_directory` |
| 6 | evolution | `promote()` partial file write failure (atomic_write) only tested for mkdir |
| 7 | config | `_build_providers` crashes on non-dict values in user_providers |
| 8 | config | `load_merged_config` conflicting provider API types between global/project |
| 9 | gateway | `oneOf + anyOf` combination at same schema level untested |
| 10 | gateway | `_merge_properties` conflicting type definitions for same property name |

### 2.5 P2 Gaps — Low Priority

| Area | Gap |
|------|-----|
| hooks | `__aenter__` / `__aexit__` context manager protocol no direct tests |
| hooks | `close()` exception propagation when `aclose()` raises |
| skills | `_parse_frontmatter` YAML anchors/aliases edge case |
| config | `load_cli_routing` no dedicated unit tests |
| evolution | `EvolutionEngine.evolve` `affected_skill_ids` filter in TOOL_DEGRADATION path |
| gateway | `_capitalize` helper no direct tests |
| gateway | `_navigate_ref` with array-valued intermediate nodes |
| gateway | `_cleanup_agent_registration` concurrent with active tool invocation (race) |
| local | `_lifecycle.py` `ConfigMigrator` no dedicated test |

### 2.6 Module Coverage Matrix

| Module | Symbols | Covered | Gaps | Coverage | Key Risk |
|--------|---------|---------|------|----------|----------|
| hooks/ | 16 | 14 | 2 | 88% | CancelledError in _execute_http |
| skills/ | 13 | 12 | 1 | 92% | parse_file encoding errors |
| models/ | ~78 | ~72 | ~6 | 92% | errors.py zero; composition.py validators |
| runtime/ | ~58 | ~55 | 3 | 95% | Excellent coverage |
| orchestration/ | ~96 | ~90 | 6 | 94% | task_graph concurrent writes |
| gateway/ | ~72 | ~64 | 8 | 89% | additionalProperties, nested $ref |
| evolution/ | ~150 | ~130 | 20 | 87% | _generate_* files, capture directory |
| config/ | 38 | 35 | 3 | 92% | _build_providers crash |
| agency/ | ~278 | ~210 | 68 | 76% | cli.py commands, dag dispatch core |
| router/ | ~32 | ~30 | 2 | 94% | Stable |
| local/ | ~103 | ~85 | 18 | 82% | _lifecycle, sources edge cases |

---

## 3. Redundancy Analysis

### 3.1 Summary

| Category | Count | Severity | Action |
|----------|-------|----------|--------|
| Pydantic framework tests | 79 | Low | Delete — constructor defaults, serialization round-trips, enum values |
| Duplicate counter validation | 9 | Low | Delete — same validator logic tested twice |
| No-assertion tests | 2 | Medium | Fix or delete — stub/empty test bodies |
| Semantic overlap (planner) | ~4-5 | Low | Merge — structured vs legacy fallback tests |
| Tautological assertion | 1 | Trivial | Remove assertion line (keep mock.verify) |
| **Total removable** | **~91-96** | | |

### 3.2 Pydantic Framework Tests (79 tests)

Tests that only verify Pydantic's own behavior without project-specific validation logic:

| File | Framework Tests | Total | Pattern |
|------|----------------|-------|---------|
| `test_agent_models.py` | 24 | 62 | Constructor defaults, field access, enum iteration, serialization |
| `test_evolution_models.py` | 14 | 55 | Constructor defaults, serialization round-trips, field access |
| `test_distribution_models.py` | 14 | 48 | Constructor defaults, serialization round-trips, field access |
| `test_runtime_models.py` | 17 | 26 | Constructor defaults, serialization round-trips, field access |
| `test_context_models.py` | 10 | 64 | Constructor defaults, serialization round-trips |

**Deletion criteria**: Any test that only verifies `model_construct()→field == value`, `model_dump()→model_validate()→equal`, or `Enum.X.value == "x"` without custom validators, computed fields, or cross-field invariants.

**Retention criteria**: Tests with `@model_validator`, custom `__init__`, computed properties, or business logic assertions.

### 3.3 Duplicate Counter Validation (9 tests)

| File | Class | Tests | Duplicate Of |
|------|-------|-------|-------------|
| `test_evolution_models.py` | `TestSkillRecordCounterInvariant` | 9 | `TestSkillRecordCounterValidation` — same `applied <= selections`, `fallbacks <= applied` invariants |
| `test_evolution_models.py` | `TestEvolutionMetricsCounterInvariant` | 6 | Same validator pattern for different model class — partially unique |

### 3.4 No-Assertion Tests (2 tests)

| File | Test | Issue | Verdict |
|------|------|-------|---------|
| `test_process_manager_unit.py:144` | `test_skips_unknown_agent_names` | No assertion — only checks "should not raise" | Add `assert ... not in pm._agents` or delete |
| `test_router_module.py:1361` | `test_run_with_retry_propagates_system_exit` | Empty body — only docstring, zero test logic | Delete stub or implement |

### 3.5 Semantic Overlap: Planner Files

| File | Tests | Unique | Overlapping |
|------|-------|--------|-------------|
| `test_llm_planner.py` | 26 | 21 | — |
| `test_llm_planner_structured.py` | 13 | 8-9 | ~4-5 (JSON fallback, partial parsing) |

Recommendation: Keep structured file for Pydantic-specific tests. Remove overlapping fallback tests from one file.

### 3.6 Redundancy Trend

| Cycle | Action | Tests Affected | Cumulative |
|-------|--------|---------------|------------|
| Cycle 2 (Iter 7) | Deleted 47 redundant tests | -47 | 47 |
| Cycle 3 (Iter 16) | Deleted 48 Pydantic tests | -48 | 95 |
| Cycle 3 (Iter 25) | Removed 138 tests | -138 | 233 |
| **Cycle 4 identified** | **~91-96 removable** | **Pending** | **~324-329** |
| **P0 tests added** | 222 across 5 cycles | +222 | |

---

## 4. E2E Test Quality Assessment

### 4.1 Classification Summary

| Classification | Files | Tests | Percentage |
|----------------|-------|-------|------------|
| **TRUE_E2E** | 17 | ~424 | 81% |
| **INTEGRATION** | 5 | ~90 | 14% |
| **MISCLASSIFIED_UNIT** | 9 | ~89 | 5% |

### 4.2 TRUE_E2E Files (17)

All use real OS resources (subprocess, pipes, SQLite, filesystem) with zero or near-zero mocking:

evolution_e2e, agency_e2e, agency_pipeline_e2e, config_e2e, dsl_toml_e2e, evolution_async_safety, ipc_async_safety, ipc_real_subprocess, process_manager_async_safety, process_manager_cancel, runtime_e2e, task_graph_async_safety, task_graph_concurrent, hooks_lifecycle, evolution_lifecycle, evolution_engine_lifecycle, runtime_security_e2e

### 4.3 MISCLASSIFIED_UNIT (9 files, recommend reclassification)

| File | Mock Refs | Problem |
|------|-----------|---------|
| `test_gateway_lifecycle_e2e.py` | 23 | Heavy `AsyncMock`/`MagicMock`/`@patch`, `_make_mock_handle()` factory. No real subprocess/DB. |
| `test_cli_backend_integration.py` | 13 | `@patch("subprocess.Popen")`, `@patch("subprocess.run")`. No real subprocess. |
| `test_llm_pipeline_e2e.py` | 3 | `@patch("...LLMClient")` + MagicMock. Single test, no real OS. |
| `test_mcp_ecosystem_integration.py` | 23 | Mock-saturated LLMClient/IPC paths despite real adapter instances. |
| `test_reasoning_protocol_integration.py` | 10 | All 4 tests `@patch("...LLMClient")`. No real OS. |
| `test_reflect_loop.py` | 9 | Real Reflector rules but LLMReflector uses MagicMock for LLMClient. |
| `test_agency_hooks_token_context.py` | 5 | Pure in-memory module wiring, no real OS. |

### 4.4 Missing E2E Scenarios

| Priority | Scenario | Description |
|----------|----------|-------------|
| **P0** | Gateway full lifecycle | Register agent → start subprocess → discover tools → MCP call → get result → cleanup |
| **P0** | CLI init + install + run | `agent-nexus init` → `install` → `run` complete pipeline |
| **P1** | External MCP adapter real connection | Connect stdio MCP server → discover tools → call → disconnect |
| **P1** | Router composite 4-phase flow | Load DSL → create TaskGraph → execute with echo agent → aggregate |
| **P2** | Evolution autonomous cycle | Seed skills → analyze → evolve → promote → verify availability |

---

## 5. High-Risk Area Deep Dives

### 5.1 hooks/executor.py

| Path | Status | Risk |
|------|--------|------|
| `_execute_http` CancelledError swallowed by `except Exception` | **BUG** | Task cancellation broken via HTTP hooks |
| `_execute_http` httpx per-request timeout | Untested | Hangs on slow HTTP |
| `_execute_prompt` / `_execute_agent` | Zero direct tests | Placeholder always returns passed=True |
| `_is_private_url` cloud metadata IPs | Partial coverage | 169.254.169.254 not blocked |
| `close()` aclose() exception | Untested | Unhandled exception propagation |

### 5.2 skills/loader.py

| Path | Status | Risk |
|------|--------|------|
| `parse_file` UnicodeDecodeError | Untested | Crash on non-UTF-8 SKILL.md |
| `parse_file` PermissionError | Untested | Crash on unreadable files |
| `_parse_frontmatter` YAML anchors/aliases | Untested | Unexpected metadata |
| `_normalize_triggers` boolean values | Untested | `triggers: true` behavior unknown |

### 5.3 evolution/engine.py + promotion.py

| Path | Status | Risk |
|------|--------|------|
| `_generate_init_py` | Zero tests | Invalid __init__.py breaks promoted agent |
| `_generate_mcp_adapter` | Zero tests | Invalid MCP adapter breaks tool registration |
| `_generate_pyproject` | Zero tests | Invalid pyproject.toml breaks installation |
| `_evolve_captured` unwritable dir | Untested | Silent failure |
| `promote()` atomic_write failure | Partial | Only mkdir tested, not individual file writes |

### 5.4 gateway/schema_transformer.py + gateway.py

| Path | Status | Risk |
|------|--------|------|
| `additionalProperties` in schema | Never tested | Invalid parameter generation |
| Nested $ref chains (3+ levels) | Only indirect | Infinite recursion or wrong types |
| `_disambiguate_tool_name` overflow (100+) | Never tested | ValueError crash |
| `oneOf + anyOf` combined | Never tested | Incorrect union handling |
| `_merge_properties` conflicting types | Never tested | Last-wins silently |

### 5.5 config/loader.py

| Path | Status | Risk |
|------|--------|------|
| `_build_providers` non-dict values | Untested | AttributeError crash |
| `load_merged_config` API type conflicts | Untested | Silent override with wrong type |
| `load_cli_routing` unit tests | None | Only integration coverage |

---

## 6. Priority Actions (Cycle 4 → Cycle 5)

### P0 — Immediate

| # | Action | Impact | Est. Tests |
|---|--------|--------|------------|
| 1 | Fix `_execute_http` CancelledError bug (except Exception → except Exception + re-raise CancelledError) | Fix task cancellation | 0 (code fix) |
| 2 | Add `_generate_*` file generation tests for promotion | Verify promoted agent files | +8 |
| 3 | Add `additionalProperties` + nested `$ref` schema tests | Fix gateway schema handling | +6 |
| 4 | Delete 79 Pydantic framework tests | Remove low-signal noise | -79 |
| 5 | Add `parse_file` encoding/permission error tests | Fix skills robustness | +4 |

### P1 — Medium Priority

| # | Action | Impact |
|---|--------|--------|
| 1 | Add `_execute_http` timeout + CancelledError propagation tests | Cover hook HTTP lifecycle |
| 2 | Reclassify 7 MISCLASSIFIED_UNIT files to unit/ | Fix test tier accuracy |
| 3 | Delete 9 duplicate counter validation tests | Reduce redundancy |
| 4 | Fix 2 no-assertion tests (add assertions or delete) | Remove stubs |
| 5 | Add `_disambiguate_tool_name` overflow test | Cover gateway crash guard |

### P2 — Long Term

| # | Action |
|---|--------|
| 1 | Add `_build_providers` non-dict crash test |
| 2 | Create Gateway full lifecycle E2E test |
| 3 | Create CLI pipeline E2E test (init → install → run) |
| 4 | Add `_evolve_captured` unwritable directory test |
| 5 | Merge planner files (remove ~4-5 semantic overlaps) |

---

## 7. Iteration History

| Iteration | Action | Tests Affected |
|-----------|--------|---------------|
| v1 (Iter 1) | Initial coverage analysis | Baseline: 4,437 tests |
| v2 (Iter 4) | Deep audit: corrections, redundancy | Identified ~96 removable |
| Iter 7 | Deleted 47 redundant + added 128 P0 tests | +71 net |
| Cycle 3 (Iter 11) | Full re-analysis: no-assertion debunked | 176→1 correction |
| Iter 14 | Added 94 P0 tests + E2E rewrites | +94 new |
| Iter 16 | Deleted 48 zero-signal Pydantic tests | -48 |
| Cycle 4 (Iter 20) | async_safety focused re-audit | 7 P0 async gaps |
| Iter 25 | Removed 138 tests (2 file deletions, 7 class removals) | -138 |
| Cycle 5 (Iter 27) | Full re-analysis: module coverage + redundancy + E2E | ~236 identified |
| **Cycle 4 (Iter 34)** | **Full re-analysis: 5 high-risk deep dives + Pydantic audit** | **~91-96 removable; 8 P0 gaps** |

---

*Report generated from 5 parallel analysis agents: source API scan, test inventory, E2E quality assessment, 5-area deep dive, redundancy detection. All findings cross-referenced against source code.*
