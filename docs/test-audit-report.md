# Test Audit Report: Gap & Redundancy Analysis

**Date**: 2026-05-09
**Branch**: nf/serena-using-superpo-ff6f4b
**Auditor**: Automated analysis via Serena LSP + grep/glob

---

## 1. Overall Statistics

| Metric | Count |
|--------|-------|
| Source modules (non-init) | 99 |
| Public classes | 219 |
| Public functions | 76 |
| **Total public symbols** | **295** |
| Unit test files | 122 |
| E2E test files | 20 |
| Integration test files | 9 |
| Capability test files | 6 |
| **Total test files** | **157** |
| Unit test functions | 3,888 |
| E2E test functions | 412 |
| Integration test functions | 143 |
| Capability test functions | 17 |
| **Total test functions** | **4,460** |

---

## 2. Coverage Matrix Summary

### Module Coverage Table

| Module | Source Files | Public Symbols | Test Files | Coverage |
|--------|-------------|----------------|------------|----------|
| **models/** | 13 | ~40 classes/enums | 12 | Good |
| **platform/gateway/** | 5 | MCPGateway, DeferredAgentRegistry, McpToolAdapter, SchemaTransformer, ExternalMcpAdapter | 6 | Good |
| **platform/orchestration/** | 4 | TaskGraph, ProcessManager, IPCProtocol/Stream, OrchestrationDSL | 10+ | Good |
| **platform/evolution/** | 11 | EvolutionEngine, EvolutionStore, SkillEvolver, etc. | 12 | Good |
| **platform/config/** | 5 | ConfigLoader, ModelConfigManager, ModelDBClient | 6+ | Good |
| **platform/runtime/** | 6 | PythonRuntime, IPythonExecutor, SecurityChecker, PermissionChecker, TokenTracker | 6 | Good |
| **platform/router/** | 3 | PlatformRouter, SubtaskController, WorkflowContext | 4 | Good |
| **platform/hooks/** | 2 | HookExecutor, HookManager | 2 | Moderate |
| **platform/skills/** | 2 | SkillLoader, ParsedSkill | 3 | Good |
| **platform/agency/** | 20+ | LLMClient, LLMPlanner, DAGDispatcher, TaskComposer, etc. | 30+ | Good |
| **platform/local/** | 6 | GitInstaller, LockfileManager, SourceManager, AgentSupervisor | 9 | Good |
| **platform/agency/cli_backend/** | 7 | GenericCLIBackend, CLIRouter, CLISessionStore, CLIBackendRegistry | 7 | Good |

### Source Modules with No Direct Test File

All 99 source modules have at least one test file that imports from them. No module is completely untested.

---

## 3. Gap Analysis: Untested Public Symbols (P0-P2)

### P0 -- Critical Gaps (Zero test coverage for key public methods)

| Module | Symbol | Risk | Notes |
|--------|--------|------|-------|
| `gateway/gateway.py` | `MCPGateway.run_stdio()` | High | Core server entry point, never tested |
| `gateway/gateway.py` | `MCPGateway.run_sse()` | High | Core server entry point, never tested |
| `gateway/gateway.py` | `MCPGateway.stop()` | Medium | Lifecycle cleanup |
| `gateway/gateway.py` | `MCPGateway._search_and_activate()` | High | Deferred loading trigger |
| `gateway/gateway.py` | `MCPGateway._check_agent_health()` | Medium | Health check during tool calls |
| `gateway/external_mcp_adapter.py` | `ExternalMcpAdapter._open_http_transport()` | Medium | HTTP transport path |
| `router/router.py` | `PlatformRouter.route_composite()` end-to-end | High | Core composite routing, only unit-tested with mocks |
| `router/router.py` | `PlatformRouter._topological_sort_tasks()` | Medium | Complex graph algorithm |
| `router/router.py` | `PlatformRouter._execute_parallel_agents()` | Medium | Parallel execution fan-out |
| `evolution/evolver.py` | `SkillEvolver.process_tool_degradation()` | Medium | Tool failure evolution path |
| `evolution/evolver.py` | `SkillEvolver.prune_recovered_tools()` | Medium | Recovery cleanup |
| `evolution/promotion.py` | `AgentPromoter._generate_entry_point()` | Medium | Code generation for promoted skills |
| `evolution/promotion.py` | `AgentPromoter._generate_mcp_adapter()` | Medium | MCP adapter generation |
| `evolution/promotion.py` | `AgentPromoter._generate_pyproject()` | Medium | pyproject.toml generation |
| `orchestration/process_manager.py` | `ProcessManager._force_kill_and_reap()` | High | Zombie process cleanup |
| `orchestration/process_manager.py` | `ProcessManager._cleanup_dead()` | High | Dead agent reaping |
| `hooks/executor.py` | `HookExecutor._execute_http()` | Medium | HTTP hook execution |
| `hooks/executor.py` | `HookExecutor._execute_agent()` | Medium | Agent hook execution |
| `hooks/executor.py` | `HookExecutor._execute_prompt()` | Medium | LLM prompt hook execution |
| `local/installaller.py` | `GitInstaller._sparse_clone()` | Medium | Sparse checkout for large repos |
| `local/supervisor.py` | `AgentSupervisor._find_dead_agents()` | Medium | Dead agent detection |
| `local/supervisor.py` | `AgentSupervisor.auto_restart_dead()` | High | Auto-restart logic |

### P1 -- Modules with Weak Boundary/Exception Testing

| Module | Gap | Notes |
|--------|-----|-------|
| `config/loader.py` | Invalid TOML with missing required fields | Only `TomlDecodeError` tested, not semantic validation |
| `config/loader.py` | `.env` file with malformed entries | Best-effort test exists but doesn't verify error handling |
| `config/model_db.py` | Network timeout / unreachable models.dev | `ModelDBClient` HTTP error paths not tested |
| `config/model_db.py` | Disk cache corruption | `_load_disk_index` with corrupt JSON |
| `evolution/store.py` | Concurrent SQLite write contention | Store is shared across threads, no contention tests |
| `evolution/compaction.py` | `CompactionGuard.needs_hard_ceiling()` | Hard ceiling enforcement path |
| `evolution/compaction.py` | `CompactionGuard.reinject_after_compaction()` | Context recovery after compaction |
| `orchestration/ipc.py` | `IPCStream.receive()` with oversized messages | `_MAX_MESSAGE_SIZE` enforcement |
| `orchestration/ipc.py` | `IPCProtocol.send_heartbeat()` timeout | Heartbeat timeout code path |
| `orchestration/task_graph.py` | `TaskGraph.detect_cycles()` with complex diamond deps | Only simple cycles tested |
| `runtime/security_checker.py` | `SecurityChecker.clear_cache()` | Cache invalidation not tested |
| `runtime/permission_checker.py` | `PermissionChecker.check_command()` with shell injection | Dangerous command patterns |
| `skills/loader.py` | `SkillLoader.load_agent_skills()` with missing directory | IOError path |
| `agency/llm_client.py` | `LLMClient._call_cli()` with non-zero exit code | CLI backend failure handling |
| `agency/dag_dispatcher.py` | `DAGDispatcher._dispatch_sequential()` | Sequential dispatch branch vs parallel |

### P2 -- Nice-to-Have Coverage Improvements

| Module | Gap |
|--------|-----|
| `platform/utils.py` | `resolve_composition_path()` with symlinks |
| `platform/utils.py` | `sqlite_connection()` with WAL mode |
| `models/_common.py` | `FrozenModel` serialization edge cases |
| `models/distribution.py` | `PackageSource` cache invalidation |
| `agency/json_parse.py` | `robust_json_parse()` with deeply nested malformed JSON |
| `agency/prompt_loader.py` | `render()` with missing template variables |
| `agency/allowlist.py` | `validate_allowlist_entry()` with duplicate tools |
| `agency/parser.py` | `parse_frontmatter()` with BOM / encoding issues |

---

## 4. E2E Test Quality Assessment

### E2E Tests Testing REAL End-to-End Flows

| Test File | What it Tests | Real E2E? |
|-----------|--------------|-----------|
| `test_runtime_e2e.py` | PythonRuntime execute/inject/retrieve lifecycle | **YES** -- uses real IPython kernel |
| `test_runtime_security_e2e.py` | SecurityChecker + SecurityRule integration | **YES** -- real AST checking |
| `test_task_graph_concurrent_e2e.py` | Concurrent SQLite writes to TaskGraph | **YES** -- real concurrency |
| `test_task_graph_async_safety_e2e.py` | Async TaskGraph wrappers | **YES** -- real async SQLite |
| `test_ipc_real_subprocess_e2e.py` | Real subprocess IPC via stdin/stdout | **YES** -- real processes |
| `test_ipc_async_safety_e2e.py` | IPC stream under concurrent access | **YES** -- real IPC |
| `test_process_manager_async_safety_e2e.py` | ProcessManager with real processes | **YES** -- real subprocess management |
| `test_process_manager_cancel_e2e.py` | Process cancellation and cleanup | **YES** -- real signal handling |
| `test_ipc_mcp_contract_e2e.py` | IPC message serialization contract | **YES** -- real Pydantic validation |
| `test_evolution_lifecycle_e2e.py` | Full evolution lifecycle in SQLite | **YES** -- real SQLite |
| `test_evolution_async_safety_e2e.py` | Concurrent evolution store access | **YES** -- real concurrency |
| `test_dsl_toml_e2e.py` | TOML parsing roundtrip | **YES** -- real file I/O |
| `test_config_e2e.py` | Config loading + env vars + caching | **YES** -- real TOML + file I/O |

### E2E Tests That Are Mostly Mocked (Should Be Reclassified or Enhanced)

| Test File | Issue | Recommendation |
|-----------|-------|----------------|
| `test_gateway_e2e.py` | Tests `TestGatewayE2E.test_tool_name_collision_handling` does pure dict/set manipulation without importing any gateway code. `TestDeferredRegistryE2E` uses mock `ProcessManager`. | **Reclassify 3 tests (lines 33-74) to unit** -- they test Python set logic, not gateway code. The DeferredRegistry tests are legitimate integration tests but should not be labeled E2E. |
| `test_agency_pipeline_e2e.py` | 64 mock references. Uses `MagicMock` for all LLM calls and agent execution. | **Reclassify to integration** -- pipeline stages are real but all I/O is mocked. Add 1-2 true E2E tests with a real echo agent. |
| `test_dag_dispatcher_e2e.py` | 59 mock references. Tests DAG dispatch with mocked executors. | Partially valid -- tests real TaskGraph + DAG logic. The mock-heavy executor tests should be unit tests. |
| `test_agency_e2e.py` | 5 mock references but tests real importer/registry/selector flow. | Acceptable as E2E -- the flow is real even if some components are mocked. |
| `test_cli_backend_e2e.py` | 11 mock references. Mocks `subprocess.Popen` but tests full config-to-response pipeline. | Acceptable -- mocking subprocess is necessary for deterministic E2E. |
| `test_router_e2e.py` | Tests `SubtaskController` only, which is a utility class. | **Reclassify to unit** -- does not test any router-to-agent flow. |

### Needed New E2E Test Scenarios

1. **Gateway full tool call flow**: Register agent -> start subprocess -> discover tools -> call tool via MCP -> get result -> cleanup. Requires a simple echo agent subprocess.
2. **Router composite 4-phase flow**: Load DSL -> create TaskGraph -> execute phases with echo agents -> aggregate results.
3. **Evolution full cycle**: Seed skills -> run analysis -> evolve -> promote -> verify promoted agent works.
4. **External MCP adapter**: Connect to a real stdio MCP server -> discover tools -> call tool -> disconnect.
5. **CLI init + install + run**: `agent-nexus init` -> `agent-nexus install <agent>` -> `agent-nexus run <agent>`.

---

## 5. Redundancy Analysis

### Duplicate Tests (Same Source, Same Assertions, Multiple Files)

| Files | Overlap | Action |
|-------|---------|--------|
| `test_task_model.py` (20 tests) + `test_task_models.py` (31 tests) | Both test `TaskState` enum values, `TaskItem` construction, self-reference validation. `test_task_models.py` is strictly more comprehensive (adds `TaskGraphSnapshot` tests). | **Delete `test_task_model.py`** -- all its coverage is a strict subset of `test_task_models.py` |
| `test_hooks.py` (12 tests) + `test_hooks_models.py` (46 tests) | `test_hooks.py` tests `HookManager` dispatch logic. `test_hooks_models.py` tests model validation. No direct overlap in assertions, but both import from the same modules. | **Keep both** -- they test different layers (models vs executor) |
| `test_executor.py` (54 tests) + `test_runtime.py` (58 tests) | `test_executor.py` tests `IPythonExecutor`. `test_runtime.py` tests `PythonRuntime` (which wraps `IPythonExecutor`). Different layers. | **Keep both** -- different abstraction levels |
| `test_config.py` (45 tests) + `test_config_loader.py` (28 tests) + `test_config_models.py` (23 tests) + `test_config_defaults.py` (26 tests) + `test_config_model_config.py` (20 tests) + `test_config_stages.py` (4 tests) | Six files for 5 source files. `test_config.py` tests `ConfigLoader` end-to-end. `test_config_loader.py` also tests `ConfigLoader` but with more granular TOML parsing. `test_config_defaults.py` tests constant values. | **Merge `test_config.py` into `test_config_loader.py`** -- both test `ConfigLoader.load_config()`. Keep model-specific files separate. |
| `test_dag_data_flow.py` (unit) + `test_dag_dispatcher_e2e.py` (e2e) | Both test DAG dispatch. Unit version uses `TaskGraph` directly. E2E version uses mocks but same flow. | **Keep both** -- different test tiers |

### Tautological Tests (Test Only Mock Behavior)

| File | Test | Issue |
|------|------|-------|
| `test_gateway_e2e.py::TestGatewayE2E::test_tool_name_collision_handling` | Pure set manipulation -- tests Python `while` loop adding items to a set. No gateway code involved. | **Delete or rewrite** to test `MCPGateway._disambiguate_tool_name()` |
| `test_gateway_e2e.py::TestGatewayE2E::test_gateway_cleanup_removes_tools` | Dict comprehension filtering strings. No gateway code involved. | **Delete or rewrite** to test `MCPGateway._cleanup_agent_registration()` |
| `test_gateway_e2e.py::TestGatewayE2E::test_namespaced_tool_roundtrip` | String `split("___")` test. No gateway code involved. | **Delete or rewrite** to test `McpToolAdapter` name formatting |
| `test_config_defaults.py` (26 tests) | Many tests assert constant values like `assert DEFAULT_OLLAMA_BASE_URL == "http://localhost:11434"`. Testing that a constant equals what it's defined as. | **Low value** -- keep if they guard against accidental changes, but most are tautological |

### No-Value Tests (Test Python/stdlib Behavior)

| File | Test | Issue |
|------|------|-------|
| `test_task_model.py::TestTaskState::test_from_string` | Tests that `TaskState("pending")` returns `TaskState.PENDING` -- this is standard Python enum behavior. | Low value -- covered by `test_values` |
| `test_task_models.py::TestTaskState::test_string_values` | Identical to above -- tests enum string equality. | Low value but acceptable as contract test |
| `test_config_defaults.py` (most tests) | Asserts hardcoded default strings match their values. | Low value but serves as change detection |

---

## 6. Summary of Recommendations

### Immediate Actions (P0)

1. **Delete `tests/unit/test_task_model.py`** -- strict subset of `test_task_models.py` (saves 186 lines, 20 redundant tests)
2. **Reclassify `test_gateway_e2e.py` pure-logic tests** (lines 33-74) as unit tests or delete them
3. **Reclassify `test_router_e2e.py`** as unit tests (tests `SubtaskController` only)
4. **Add gateway lifecycle tests**: `MCPGateway.run_stdio()`, `run_sse()`, `stop()` need at least smoke tests
5. **Add `ProcessManager._force_kill_and_reap()` test**: zombie cleanup is safety-critical

### Short-Term Actions (P1)

6. **Merge `test_config.py` into `test_config_loader.py`**: eliminate overlapping `ConfigLoader` tests
7. **Add config boundary tests**: invalid TOML fields, missing required sections, malformed `.env`
8. **Add IPC oversized message test**: verify `_MAX_MESSAGE_SIZE` enforcement
9. **Add evolution concurrency test**: `EvolutionStore` under parallel writes
10. **Add `PermissionChecker.check_command()` shell injection tests**

### Long-Term Actions (P2)

11. **Create 1-2 true E2E tests** with echo agent subprocess for gateway and router flows
12. **Add `ModelDBClient` network failure tests**: timeout, unreachable, corrupt response
13. **Add `SkillEvolver.process_tool_degradation()` tests**: tool failure evolution path
14. **Add `AgentPromoter` code generation tests**: verify generated files are valid Python/TOML

### Files to Review for Cleanup

| File | Action | Estimated Savings |
|------|--------|-------------------|
| `tests/unit/test_task_model.py` | Delete (covered by test_task_models.py) | 186 lines, 20 tests |
| `tests/e2e/test_gateway_e2e.py` lines 33-74 | Delete or rewrite (tautological) | ~40 lines, 3 tests |
| `tests/e2e/test_router_e2e.py` | Move to tests/unit/ | Reclassification only |
| `tests/unit/test_config_defaults.py` | Consider trimming constant-value tests | ~10 low-value tests |
| `tests/unit/test_config.py` | Merge into test_config_loader.py | Eliminate 5-8 overlapping tests |

---

## 7. Risk Assessment

### High-Risk Untested Paths

1. **Gateway server lifecycle** (`run_stdio`/`run_sse`/`stop`) -- the entire MCP server entry points have zero test coverage. Any regression here would break all MCP communication.
2. **ProcessManager zombie cleanup** (`_force_kill_and_reap`, `_cleanup_dead`) -- orphaned processes are a production reliability risk.
3. **Router composite execution** (`route_composite` with real agents) -- the core platform feature is only tested with mocks.
4. **Evolution promotion code generation** -- generated Python/TOML files are never validated.

### Medium-Risk Areas

5. **IPC message size limits** -- oversized message handling is untested.
6. **Config semantic validation** -- malformed but syntactically valid TOML paths are untested.
7. **External MCP HTTP transport** -- only stdio is tested in E2E.
8. **Hook HTTP/Agent/Prompt executors** -- three hook types have no execution tests.

---

*Report generated by Serena LSP + static analysis. All findings should be verified by running the test suite.*
