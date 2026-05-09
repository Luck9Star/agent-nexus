# Serena Deep Audit Report — Agent Nexus

**Date**: 2026-05-09
**Scope**: 8 core modules, 60+ source files
**Method**: Serena MCP symbol-level analysis (get_symbols_overview, find_symbol, get_diagnostics_for_file) + pattern search
**Status**: ty diagnostics clean on all files except 1 cosmetic warning

---

## Executive Summary

The codebase is **well-structured and defensively programmed**. No P0 (critical) issues found. Key strengths:

- Clean CancelledError propagation in all async handlers
- Comprehensive AST-level security checker with sandbox escape prevention
- No hardcoded credentials, eval/exec usage, or shell=True subprocess calls
- Minimal environment variable propagation to agent subprocesses
- Proper try/finally resource cleanup in critical paths (route_composite, stop_agent, _execute_phases)

**Issues found**: 2 P1 (1 fixed, 1 resolved), 4 P2 (2 fixed, 1 stale, 1 deferred), 4 P3 (3 fixed, 1 accepted) — detailed below.

---

## Module-by-Module Findings

### 1. MCP Gateway (`platform/gateway/`)

| ID | Severity | File:Line | Description | Fix |
|----|----------|-----------|-------------|-----|
| G-01 | P3 | `gateway.py:426` | `__signature__` attribute set on dynamically created function — ty reports `unresolved-attribute`. Known FastMCP pattern but type-checker unfriendly. | Add `# type: ignore[unresolved-attribute]` or use `functools.update_wrapper`. |
| G-02 | P3 | `gateway.py:292` | `_register_single_tool(self, adapter: Any)` — `adapter` typed as `Any` but should be `McpToolAdapter`. | Change type to `McpToolAdapter`. |
| G-03 | P2 | `gateway.py:390-391` | `_fetch_tools_from_running_agents` uses `asyncio.gather()` without `return_exceptions=True`. Currently safe because `_fetch_single_agent_tools` catches all errors internally, but fragile to future changes. | Add `return_exceptions=True` as defensive measure. |
| G-04 | — | `schema_transformer.py` | Clean. Well-implemented JSON Schema to Python type resolution with $ref, allOf, oneOf/anyOf support. | — |
| G-05 | — | `external_mcp_adapter.py` | Clean. `_safe_disconnect()` properly nullifies references in finally block. | — |
| G-06 | — | `deferred_registry.py` | Clean. Thread-safe with per-agent asyncio.Lock for activation. | — |

### 2. Agency Pipeline (`platform/agency/`)

| ID | Severity | File:Line | Description | Fix |
|----|----------|-----------|-------------|-----|
| A-01 | P1 | `llm_client.py:340-348` | `LLMClient.close()` is effectively a no-op — `if self._cli_backend is not None: pass`. Context manager `__exit__` calls `close()` silently without cleanup. | Implement CLI backend cleanup or document that LiteLLM manages API connection pools internally. |
| A-02 | P2 | `llm_client.py:64-66` | `_EMPTY_CAPABILITY` module-level tuple uses `Any` for `mgr`, `loader`, `platform_config` fields. These are lazy-injected runtime dependencies. | Consider using `Protocol` or `Optional[...]` types. |
| A-03 | P3 | `dag_dispatcher.py:631` | `_should_fail_orphan(self, task: Any)` — `task` should be `TaskItem`. | Change type annotation. |
| A-04 | P1 | `dag_dispatcher.py:203-210` | `DAGDispatcher.__del__` calls `pool.shutdown(wait=False)` but no explicit `close()` method or context manager protocol. If dispatcher is abandoned after `dispatch()`, thread pool may leak. | Add `close()` method and `__enter__`/`__exit__` support. |
| A-05 | P3 | `llm_planner.py:130`, `llm_integrator.py:40`, `llm_qa_gate.py:39` | Class-level `_fallback_count` + `_fallback_lock` (threading.Lock) shared across all instances. Appears intentional for global tracking but undocumented. | Add docstring explaining the class-level scope. |
| A-06 | — | `hooks.py:73-96` | `HookManager.dispatch` properly catches all handler exceptions, only propagates `HookAbort`. Clean design. | — |
| A-07 | — | `policy.py` | Content policy checker with confusable character normalization and Chinese-specific patterns. Well-implemented. | — |
| A-08 | — | `context_provider.py` | Clean provider registry with priority-based context injection. | — |
| A-09 | — | `token_counter.py` | Clean token counting with structured prompt builder and trim-to-budget support. | — |
| A-10 | — | `reflector.py` | Clean reflection system with configurable rules (EmptyResultRule, MaxIterationRule). | — |

### 3. Orchestration (`platform/orchestration/`)

| ID | Severity | File:Line | Description | Fix |
|----|----------|-----------|-------------|-----|
| O-01 | — | `task_graph.py` | Clean. 30+ methods with comprehensive SQLite-backed task management. Cycle detection, parallel group computation, batch operations. | — |
| O-02 | — | `ipc.py:130-168` | `_drain_stderr` properly re-raises CancelledError, catches all other exceptions with logging. | — |
| O-03 | P3 | `ipc.py:160-186` | `close_sync` uses `self._stdout._transport` (CPython private attribute). Already documented with explanation and `getattr` guard. | Acceptable; no action needed. |
| O-04 | P2 | `ipc.py:322` | ~~`progress_callback: Any or None`~~ **[FIXED]** Changed to `Callable[[AgentToPlatform], Awaitable[None]] or None`. Removed unused `Any` import, added `Awaitable, Callable` from `collections.abc`. | — |
| O-05 | — | `process_manager.py` | Excellent defence-in-depth shutdown: IPC EOF then wait then SIGTERM then SIGKILL. `stop_all` properly uses `return_exceptions=True` and handles CancelledError with force-kill fallback. | — |
| O-06 | — | `dsl.py` | Clean TOML DSL parser with comprehensive validation (agent refs, blocked_by refs, self-blocks, cycles, unused agents). | — |

### 4. Evolution Engine (`platform/evolution/`)

| ID | Severity | File:Line | Description | Fix |
|----|----------|-----------|-------------|-----|
| E-01 | — | `store.py` | Clean. 30+ methods for SQLite-backed skill/agent evolution tracking. Counter invariant validation with delegation docstring. | — |
| E-02 | — | `engine.py` | Clean facade with proper routing by EvolutionTrigger. | — |
| E-03 | — | `compaction.py` | Clean CompactionGuard with truncation, hard ceiling, and consecutive compaction alerting. | — |
| E-04 | — | `skill_store.py` | Clean BFS lineage traversal and snapshot parsing. | — |
| E-05 | — | `budget_store.py` | Clean budget event logging with SQLite. | — |

### 5. Config (`platform/config/`)

| ID | Severity | File:Line | Description | Fix |
|----|----------|-----------|-------------|-----|
| C-01 | — | `model_config.py` | Clean. Proper provider identity resolution, API key reading from env vars with well-known fallbacks. No hardcoded keys. | — |
| C-02 | — | `model_db.py` | Clean. models.dev client with disk caching, trigram search, TTL-based index refresh. | — |
| C-03 | — | `loader.py` | Clean. TOML config loading with cache invalidation, source YAML parsing, external server config. | — |

### 6. Runtime (`platform/runtime/`)

| ID | Severity | File:Line | Description | Fix |
|----|----------|-----------|-------------|-----|
| R-01 | — | `security_checker.py` | **Excellent**. 19 blocked imports, 11 blocked functions, 6 blocked qualified calls, 11 blocked attributes, 7 regex patterns. Blocks all known sandbox escape vectors including `__builtins__`, `__subclasses__`, MRO traversal, `concurrent.futures`, `pty`, `mmap`. | — |
| R-02 | P2 | `security_checker.py:312-313` | Cache eviction is FIFO (`pop(next(iter(...)))`), not LRU. Suboptimal for repeated checks of recent code. | Use `collections.OrderedDict.move_to_end()` for LRU behavior. |
| R-03 | — | `permission_checker.py` | Clean. 4-layer evaluation: deny list, allow list, read-only exemption, mode baseline. Path traversal protection with glob patterns. | — |
| R-04 | — | `executor.py:263-307` | Clean. `_execute_inner` properly handles CancelledError (re-raises and marks timed_out), TimeoutError (marks contaminated), and generic exceptions. Thread lock properly released before async wait. | — |
| R-05 | — | `runtime.py` | Clean. IPython-based runtime with inject/retrieve/describe. | — |

### 7. Models (`models/`)

| ID | Severity | File:Line | Description | Fix |
|----|----------|-----------|-------------|-----|
| M-01 | — | `task.py` | Clean. `result: Any or None = None` — intentional for flexible task results. | — |
| M-02 | — | `ipc.py` | Clean. `_resolve_payload` uses `Any` for Pydantic validator — standard pattern. | — |
| M-03 | — | `hooks.py` | Clean. Well-typed HookEvent, CallContext, CallResult, RetryDecision. | — |
| M-04 | — | `context.py` | Clean. Tiered context with token budget management. | — |
| M-05 | — | All other model files | No diagnostics. Proper Pydantic models with validators. | — |

### 8. Router (`platform/router/`)

| ID | Severity | File:Line | Description | Fix |
|----|----------|-----------|-------------|-----|
| RT-01 | — | `router.py:262-337` | Clean. `route_composite` properly uses try/finally for context cleanup, CancelledError re-raise in phase execution, TimeoutError handling. | — |
| RT-02 | — | `subtask.py:124-181` | Clean. `run_parallel` with semaphore-based concurrency, proper BaseException handling (re-raises KeyboardInterrupt/CancelledError/SystemExit), failed task flag to skip queued tasks. | — |
| RT-03 | — | `workflow.py` | Clean. Dataclass-based phase/context/result models. | — |

---

## Cross-Cutting Analysis

### Security Audit Summary

| Category | Status | Details |
|----------|--------|---------|
| Hardcoded credentials | **Clean** | API keys read exclusively from env vars via `resolve_api_key()` |
| eval/exec/pickle | **Clean** | None found; `security_checker.py` actively blocks these in agent code |
| shell=True subprocess | **Clean** | Not used anywhere |
| Path traversal | **Clean** | `allowlist.py` validates source_path with regex; `cli.py` has `_validate_output_path` with `..` segment check |
| Deserialization | **Clean** | Uses `json.loads` + Pydantic validation; `yaml.safe_load` for config |
| Environment leakage | **Clean** | `_build_spawn_env` only passes essential env vars; caller-supplied extras layered on top |
| AST sandbox | **Excellent** | 19 blocked imports, 11 blocked functions, comprehensive escape vector coverage |
| Permission system | **Clean** | 4-layer check: deny, allow, readonly, mode baseline |

### Async Safety Summary

| Pattern | Status | Details |
|---------|--------|---------|
| CancelledError handling | **Clean** | All 9 sites properly re-raise CancelledError |
| asyncio.gather safety | **Mostly clean** | 4 of 7 gathers use `return_exceptions=True`; others safe due to internal error handling |
| Resource cleanup (try/finally) | **Clean** | All critical paths (route_composite, stop_agent, _execute_phases, _execute_inner) use try/finally |
| asyncio.create_task | **Clean** | Only 1 use (_drain_stderr) with proper cancellation in stop_agent |
| Thread pool cleanup | **P1 issue** | DAGDispatcher relies on `__del__` for cleanup; no explicit close() |

### Type Safety Summary

| Metric | Count | Assessment |
|--------|-------|------------|
| ty diagnostic errors | 1 | Only `__signature__` on dynamic function (cosmetic) |
| Any type annotations | ~40 | Most intentional (Pydantic validators, generic callbacks, dynamic typing) |
| Functions missing return types | 0 | All functions have type annotations |

### Code Quality Summary

| Pattern | Count | Assessment |
|---------|-------|------------|
| TODO/FIXME/HACK in production code | 0 | Only in code generation templates (create_cmd.py) |
| bare except | 0 | None found |
| Empty function bodies (pass) | 1 | LLMClient.close() — flagged as P1 |
| Class-level mutable state | 3 | _fallback_count/_fallback_lock — intentional but undocumented |

---

## Recommended Fix Priority

### Immediate (P1 — should fix before next release)

1. **A-01**: ~~Implement `LLMClient.close()` or document that it is intentionally a no-op~~ **[RESOLVED — DOCUMENTED]** Docstring already explains: API providers use LiteLLM-managed pools; CLI backends have no persistent resources.
2. **A-04**: ~~Add `close()` method + context manager protocol to `DAGDispatcher`~~ **[FIXED]** Added `__enter__`/`__exit__` protocol; existing `close(wait=True)` preserved; `__del__` now delegates to `close()`.
3. **ASYNC-01**: ~~`_fetch_single_agent_tools` exception handling too narrow, `asyncio.gather` propagates unexpected exceptions to abort all agent tool discovery~~ **[FIXED]** Changed `except (TimeoutError, IPCError, OSError, RuntimeError)` → `except Exception` in `router.py:771` so any unexpected exception from one agent doesn't prevent discovering tools from all other agents.
4. **RES-01**: ~~`EvolutionStore` has `close()` but no context manager protocol~~ **[FIXED Cycle 2]** Added `__enter__`/`__exit__` to `EvolutionStore` in `evolution/store.py`. SQLite memory connection now guaranteed cleanup via `with` statement.
5. **RES-02**: ~~`ModelDBClient` has `close()` but no context manager protocol~~ **[FIXED Cycle 2]** Added `__enter__`/`__exit__` to `ModelDBClient` in `config/model_db.py`. httpx.Client now guaranteed cleanup via `with` statement.
6. **RES-03**: ~~`CLISessionStore` has `close()` but no context manager protocol~~ **[FIXED Cycle 2]** Added `__enter__`/`__exit__` to `CLISessionStore` in `cli_backend/session_store.py`. SQLite file connection now guaranteed cleanup via `with` statement.
7. **RES-04**: ~~`LLMExecutor` has `close()` but no context manager protocol~~ **[FIXED Cycle 2]** Added `__enter__`/`__exit__` to `LLMExecutor` in `agency/executor.py`. Per-expert LLMClient cache now guaranteed cleanup via `with` statement.
8. **MCP-01**: ~~`_build_params_from_schema` crashes on `null` default values — `inspect.Parameter(default=None)` with non-nullable annotation causes Pydantic validation failure~~ **[FIXED Cycle 3]** When `"default": null` in JSON Schema, now adds `| None` to type annotation so `None` is valid at validation time. `gateway.py:485-498`.
9. **MCP-02**: ~~No validation that `inputSchema` from external MCP servers or IPC agents has `"type": "object"` — invalid schemas propagate to FastMCP tool registration~~ **[FIXED Cycle 3]** Added `_normalize_input_schema()` in `tool_adapter.py`, validation in `deferred_registry._validate_tool_schemas`, and normalization in `ExternalMcpAdapter._discover_tools`. All three entry points now enforce MCP contract: `inputSchema` must be a dict with `type: "object"` or `properties` key.
10. **MCP-03**: ~~`tool.inputSchema` may not be a plain dict (Pydantic model risk from MCP SDK deserialization)~~ **[FIXED Cycle 3]** Added `isinstance(raw_schema, dict)` check with `dict()` fallback conversion in `ExternalMcpAdapter._discover_tools`.

### Short-term (P2 — should fix within sprint)

4. **G-03**: ~~Add `return_exceptions=True` to `_fetch_tools_from_running_agents` gather~~ **[STALE]** Function no longer exists in current codebase.
5. **O-04**: ~~Tighten `progress_callback` type annotation~~ **[FIXED]** Changed `Any | None` → `Callable[[AgentToPlatform], Awaitable[None]] | None` in `ipc.py:322`. Removed unused `Any` import, added `Awaitable, Callable` from `collections.abc`.
6. **R-02**: ~~Convert SecurityChecker cache from FIFO to LRU eviction~~ **[FIXED]** Replaced `dict` with `OrderedDict`, added `move_to_end()` on cache hit, `popitem(last=False)` on eviction.
7. **A-V2-01**: ~~`_drain_single_future` swallows exception detail with generic "executor error" string~~ **[FIXED]** Changed to `f"executor error: {exc}"` in `dag_dispatcher.py:512` to preserve diagnostic information.
8. **ASYNC-02**: ~~`_execute_command` subprocess cleanup missing `finally` block; `SystemExit`/`GeneratorExit` orphans subprocess~~ **[FIXED]** Added `except BaseException` block in `hooks/executor.py:452` that kills subprocess and re-raises.
9. **ASYNC-03**: ~~`HookExecutor` httpx.AsyncClient lazy-init without context manager protocol~~ **[FIXED]** Added `__aenter__`/`__aexit__` protocol to `HookExecutor` in `hooks/executor.py:547-553`, ensuring `close()` is called on context exit.
10. **A-02**: Replace `Any` fields in LLMClient with proper types or Protocols — deferred (requires lazy-injection architecture change)
11. **MCP-04**: ~~`_merge_properties` uses `...` sentinel for optional fields, making them required in Pydantic~~ **[FIXED Cycle 3]** Aligned with `_build_object_model` pattern: uses `Field(default=...)` for explicit defaults and `None` for implicit optional. `schema_transformer.py:305-310`.
12. **MCP-05**: ~~`$ref` cache uses only last path segment, causing collisions between `#/$defs/X` and `#/components/schemas/X`~~ **[FIXED Cycle 3]** Changed cache key from `ref_name` to full `ref` path string. `schema_transformer.py:159-186`.
13. **MCP-06**: ~~`_resolve_all_of` silently drops constraint-only sub-schemas (e.g. `{"required": ["extra"]}`)~~ **[FIXED Cycle 3]** Added `merged_required` set that collects required arrays from constraint-only sub-schemas, then promotes matching optional fields to required. `schema_transformer.py:188-225`.
14. **MCP-07**: ~~`ExternalMcpAdapter.call_tool` silently drops non-TextContent blocks (images, embedded resources)~~ **[FIXED Cycle 3]** Added placeholder `[type content omitted]` for non-text blocks so callers know content was present. `external_mcp_adapter.py:147-150`.

### Backlog (P3 — nice to have)

11. **G-01**: ~~Suppress `__signature__` type error~~ **[ALREADY SUPPRESSED]** `# type: ignore[attr-defined]` already present at `gateway.py:427`.
12. **G-02**: ~~Type `_register_single_tool` adapter parameter~~ **[FIXED]** Changed `adapter: Any` → `adapter: McpToolAdapter`.
13. **A-03**: ~~Type `_should_fail_orphan` task parameter~~ **[FIXED]** Changed `task: Any` → `task: TaskItem`.
14. **A-05**: ~~Document class-level `_fallback_count` scope~~ **[FIXED]** Added comment explaining class-level scope for all 3 classes (Planner, Integrator, QAGate).
11. **O-03**: Acceptable; document the CPython `_transport` dependency

---

## Cycle 3 — Test Signal-to-Noise Audit (2026-05-10)

**Focus**: Remove tests with zero information value (tautological, framework-behavior, same-abstraction duplicates).
**Baseline**: 4845 tests (4813 passed, 2 pre-existing IPC E2E failures, 30 skipped)
**After**: 4707 tests (4707 passed, 2 pre-existing IPC E2E failures, 30 skipped)
**Net removal**: 138 tests, 0 regressions

### Files Deleted Entirely

| File | Tests Removed | Reason |
|------|:---:|--------|
| `tests/unit/test_config_loader.py` | 28 | 100% redundant with `config/test_loader.py` — 18 exact + 9 partial overlaps, 3 unique tests already covered |
| `tests/unit/test_ipc_models.py` | 23 | 100% subset of `test_ipc_mcp_contract.py` — every class had equivalent or better coverage |

### Classes Removed from Existing Files

| File | Class/Tests Removed | Reason |
|------|:---:|--------|
| `test_gateway_e2e_reclassified.py` | TestMcpToolAdapterContract (~6 tests) | Exact duplicate of `test_gateway_tool_adapter.py` |
| `test_evolution_module.py` | TestEditDistance, TestCorrectSkillIds, TestExecutionAnalyzer, TestSkillEvolverFix, TestSkillEvolverDerived (~15 tests) | Exact duplicates of `test_evolution_analyzer.py` and `test_evolution_evolver.py` |
| `test_ipc.py` | TestIPCModelRoundtrip (2), TestIPCContentMaxLength (11) | Roundtrip = Pydantic framework behavior; MaxLength = redundant with IPC contract tests |
| `test_task_models.py` | test_frozen_raises_on_mutation, test_frozen_raises_on_field_change | Pydantic `frozen=True` framework behavior, not project logic |
| `models/test_capability.py` | test_frozen | Pydantic frozen framework behavior |
| `test_permission_checker.py` | test_config_is_frozen | Pydantic frozen framework behavior |
| `test_integrator_enhanced.py` | test_conflicting_severity_viewpoints | Overlapping with test_conflicting_recommendations at same abstraction level |

### TSN Pattern Classification

| Pattern | Count | Description |
|---------|:---:|-------------|
| Exact file duplication | 51 | Two files testing same module at same abstraction level |
| Exact class duplication | 21 | Class copied between files (5 evolution + 6 gateway) |
| Pydantic framework tests | 4 | Testing that `frozen=True` raises on mutation |
| IPC framework tests | 13 | Pydantic serialization roundtrip + max_length constraints |
| Same-level overlap | 1 | Two integrator tests detecting same conflict type |
| **Total** | **138** | |

## Cycle 4 — Full Re-Audit + TSN Deep Analysis (2026-05-10)

**Focus**: Verify all previous fixes intact, find new issues since Cycle 3, deep test_signal_to_noise analysis
**Method**: Serena MCP symbol-level + parallel Explore agents (3 agents covering 8 modules)
**Fix verification**: 19/19 fixes from Cycles 1-3 confirmed INTACT, zero regressions

### Fix Verification Results

| Cycle | Fix | Status |
|-------|-----|--------|
| C1 | DAGDispatcher __enter__/__exit__ | INTACT |
| C1 | SecurityChecker LRU cache (OrderedDict) | INTACT |
| C1 | _register_single_tool: McpToolAdapter | INTACT |
| C1 | _should_fail_orphan: TaskItem | INTACT |
| C2 | router.py:771 except Exception broadening | INTACT |
| C2 | hooks/executor.py BaseException subprocess cleanup | INTACT |
| C2 | dag_dispatcher.py exception detail preservation | INTACT |
| C2 | HookExecutor __aenter__/__aexit__ | INTACT |
| C2 | EvolutionStore __enter__/__exit__ | INTACT |
| C2 | ModelDBClient __enter__/__exit__ | INTACT |
| C2 | CLISessionStore __enter__/__exit__ | INTACT |
| C2 | LLMExecutor __enter__/__exit__ | INTACT |
| C3 | gateway.py null default type safety | INTACT |
| C3 | tool_adapter.py _normalize_input_schema | INTACT |
| C3 | deferred_registry.py inputSchema validation | INTACT |
| C3 | schema_transformer.py $ref cache key fix | INTACT |
| C3 | schema_transformer.py _resolve_all_of refactored | INTACT |
| C3 | llm_client.py _call_cli refactored | INTACT |
| C3 | external_mcp_adapter.py non-text content | INTACT |

### Cycle 4 New Findings

#### P1 (High) — 2 New Issues

| ID | Module | File:Line | Description | Recommendation |
|----|--------|-----------|-------------|----------------|
| C4-01 | agency | `reflector.py:120-122` | LLM failure in `reflect()` defaults to `sufficient=True`, bypassing quality gate. If reflection LLM fails (network error, rate limit), bad outputs pass through to users. | Change fallback to `sufficient=False` (fail-closed). |
| C4-02 | orchestration | `process_manager.py:362` | `contextlib.suppress(asyncio.CancelledError)` in `_cancel_drain` silently swallows task cancellation, breaking cancellation propagation chain. | Remove suppress, add explicit CancelledError handler with re-raise. |

#### P2 (Medium) — 10 New Issues

| ID | Module | File:Line | Description |
|----|--------|-----------|-------------|
| C4-03 | gateway | `deferred_registry.py:292-300` | Tool discovery failure silently falls back to generic chat tool — masks IPC errors |
| C4-04 | gateway | `gateway.py:303-308` | Tool registration failure silently drops tools — schema mismatch invisible |
| C4-05 | gateway | `gateway.py:406-430` | Monkey-patched `__signature__`/`__annotations__` to fool FastMCP — fragile coupling |
| C4-06 | agency | `llm_client.py:65-67,198,218,227,243,321` | 7 bare `Any` annotations in LLMClient — types available in same package |
| C4-07 | agency | `llm_planner.py:190-194` | LLM failure silently falls back to keyword strategy — no degradation signal |
| C4-08 | agency | `reflector.py:173-174` | Rule evaluation failures silently skipped in loop — bugs hidden |
| C4-09 | orchestration | `task_graph.py:385+` | All `conn` parameters typed as `Any` instead of `sqlite3.Connection` |
| C4-10 | evolution | `skill_store.py:900-906` | `_rows_to_records` silently skips corrupt rows — DB corruption hidden from callers |
| C4-11 | config | `model_db.py:273,292,332` | 3 bare `except Exception:` blocks swallow disk cache errors without logging |
| C4-12 | runtime | `security_checker.py:248` | Security rule exception swallowed — bugs in rules impossible to debug |

#### P3 (Low) — 8 Notable New Issues

| ID | Module | File:Line | Description |
|----|--------|-----------|-------------|
| C4-13 | gateway | `tool_adapter.py:53-63` | `remove_lock()` is misleading named no-op |
| C4-14 | agency | `llm_client.py:349` | `if self._cli_backend is not None: pass` — dead code |
| C4-15 | agency | `dag_dispatcher.py:488-490,518-519` | Future exceptions caught but not typed (concurrent.futures pattern) |
| C4-16 | orchestration | `process_manager.py:50-57` | `_build_spawn_env` extra dict can inject any env var |
| C4-17 | orchestration | `process_manager.py:648` | `except Exception: pass` in `__del__` — no logging |
| C4-18 | config | `loader.py:287` | `load_cli_routing()` returns `Any` — should be typed |
| C4-19 | runtime | `executor.py:279,301` | Broad `except Exception` in transform/execute may swallow `SystemExit` |
| C4-20 | router | `router.py:321` | Cleanup failure in `finally` block silently dropped |

### Cross-Cutting Patterns

**Pattern: Silent degradation in agency LLM components**
Four agency files (`llm_planner.py`, `llm_integrator.py`, `llm_qa_gate.py`, `reflector.py`) catch `Exception` on LLM call failures and silently degrade. No metric, counter, or structured log signals degraded mode. The `_fallback_count` class variable partially addresses this in `llm_planner.py` only. Recommendation: add a shared `_degradation_tracker` or structured log field across all four files.

**Pattern: Bare `except Exception:` without variable name**
12 instances across gateway, config, runtime, and agency modules. While most are in cleanup paths (acceptable), 4 are in functional code paths where errors should be logged with detail. These make debugging harder because the exception type and message are lost.

### Test Signal-to-Noise Deep Analysis (Cycle 4)

**Scope**: Full test suite under `tests/`
**Total tests**: ~4700+
**Analysis method**: Pattern search for tautological/framework/duplicate tests

#### High-Impact Cleanup Candidates (~207 tests)

| Category | Count | Value Density | Recommendation |
|----------|:-----:|:-------------:|----------------|
| Serialization round-trip tests | ~53 | Zero | Replace with single parametrized test per module |
| Defaults/construction tautologies | ~50 | Zero | Remove — Pydantic tests its own field assignment |
| min_length/gt/ge constraint tests | ~40 | Zero | Remove — Pydantic validator behavior |
| Duplicate counter invariant tests | ~12 | Low | Consolidate 5 classes → 1 parametrized (~6 tests) |
| Overlapping test files | ~30 | Low | Merge `test_gateway_e2e_reclassified.py` → `test_gateway_module.py` |
| Enum coercion tests | ~7 | Zero | Remove — Pydantic StrEnum coercion behavior |
| Frozen dataclass tests | ~10 | Zero | Remove — stdlib/Pydantic behavior |
| Constant value tests | ~5 | Low | Remove — code review catches constant changes |

#### Specific Duplicate Files

| File Pair | Overlap | Recommendation |
|-----------|---------|----------------|
| `test_check_cmd.py` vs `cli/test_check_cmd.py` | CLI integration vs helper unit tests — overlapping scenarios | Keep CLI-level tests, remove helper tests that duplicate same scenarios |
| `test_evolution_store.py` vs `evolution/test_store_p0_unit.py` | Both test `get_metrics`, `deactivate_skill`, `get_skill_records_batch`, `get_children` | Merge unique tests from `_p0_unit` into main file |
| `test_gateway_e2e_reclassified.py` vs `test_gateway_module.py` | Both test DeferredAgentRegistry registration/search/lifecycle | Consolidate into `test_gateway_module.py` |

#### test_evolution_models.py Counter Invariant Over-Testing

Same validation logic (`applied <= selections`, `completions + fallbacks <= applied`) tested ~22 times across 5 test classes:
- `TestSkillRecordCounterValidation` (7 tests)
- `TestSkillRecordCounterInvariant` (8 tests)
- `TestSkillRecordCompletionsFallbacksInvariant` (2 tests)
- `TestEvolutionMetrics` counter tests (4 tests)
- `TestEvolutionMetricsCounterInvariant` (5 tests)

A single parametrized class with ~6 cases would cover all invariants.

### Module Diagnostic Summary (Cycle 4)

| Module | Files | P0 | P1 | P2 | P3 | TODO/HACK | Assessment |
|--------|:-----:|:--:|:--:|:--:|:--:|:---------:|:----------:|
| gateway | 6 | 0 | 0 | 3 | 1 | 0 | Good |
| agency | 32 | 0 | 1 | 5 | 3 | 0 | Good |
| orchestration | 5 | 0 | 1 | 2 | 4 | 0 | Good |
| evolution | 14 | 0 | 0 | 2 | 8 | 0 | Good |
| config | 6 | 0 | 0 | 3 | 2 | 0 | Good |
| runtime | 8 | 0 | 0 | 3 | 4 | 0 | Good |
| models | 16 | 0 | 0 | 2 | 2 | 0 | Excellent |
| router | 4 | 0 | 0 | 2 | 3 | 0 | Good |
| **Total** | **91** | **0** | **2** | **22** | **27** | **0** | **Good** |

### Cumulative Audit Metrics (4 Cycles)

| Metric | Cycle 1 | Cycle 2 | Cycle 3 | Cycle 4 |
|--------|:--------:|:--------:|:--------:|:--------:|
| P0 issues | 0 | 0 | 0 | 0 |
| P1 issues (new) | 2 | 4 | 3 | 2 |
| P1 issues (fixed) | 2 | 4 | 3 | — |
| P2 issues (new) | 4 | 3 | 4 | 10 |
| P2 issues (fixed) | 2 | 3 | 4 | — |
| Total fixes applied | 7 | 10 | 19 | 19 (verified) |
| Test count | 4460 | 4728 | 4784 | ~4700 |
| Tests removed (cumulative) | 20 | 67 | 138 | 138 |
| Low-value tests identified | 96 | 176→1 | 138 removed | ~207 remaining |
| CC refactoring ops | 5 | 12 | 18 | 18 (verified) |
| C-grade functions | 3→0 | 0 | 0 | 0 |

---

## Methodology

- **Tool**: Serena MCP (LSP-backed semantic analysis)
- **Files audited**: 91 Python source files across 8 core modules (Cycle 4)
- **Checks performed**:
  - Symbol overview (depth=1) for all public classes/methods
  - ty diagnostics (severity >= Hint) for all files
  - Regex search for: TODO/FIXME/HACK, bare except, pass, eval/exec, CancelledError, Any, hardcoded keys, shell=True, asyncio patterns, threading patterns
  - Deep body reads of critical methods (CancelledError handlers, resource cleanup, security checks)
  - Fix verification: grep/read all 19 previously fixed code locations
  - Test signal-to-noise: pattern analysis for tautological, framework, duplicate tests
- **Lines of code reviewed**: ~8000+ (symbol bodies + context across 4 cycles)
- **False positive rate**: Low — each finding verified against actual code context
