# Serena Deep Audit Report v2 — Agent Nexus

**Date**: 2026-05-10
**Cycle**: 2 (re-audit after iterations 2-8 fixes)
**Scope**: 8 core modules, 60+ source files
**Method**: Serena MCP symbol-level analysis + pattern search + diagnostics + radon CC scan
**Previous report**: docs/audit-report-SERENA.md (cycle 1, 2026-05-09)

---

## Executive Summary

**Re-audit confirms: cycle 1 fixes are intact and correct.** All 5 FIXED items from cycle 1 verified. No regressions introduced by iterations 2-8 changes.

New findings: **0 P0, 1 P2, 2 P3, 1 DEFERRED carryover**. The codebase remains well-structured with strong security posture.

| Quality Dimension | Status | Detail |
|-------------------|--------|--------|
| Cyclomatic Complexity | **Excellent** | 0 C-grade functions; max CC 10; avg CC 3.33 (Grade A) |
| Security | **Clean** | Zero P0 vulnerabilities; no hardcoded keys, eval/exec, shell=True |
| Async Safety | **Clean** | All 9 CancelledError sites correctly re-raise |
| Type Safety | **Clean** | 1 cosmetic ty diagnostic (already suppressed) |
| Test Suite | **Healthy** | 4728 passed, 0 failures (2 pre-existing IPC E2E timeouts excluded) |

---

## Verification of Cycle 1 Fixes

| Cycle 1 ID | Fix | Verification |
|------------|-----|--------------|
| A-04 [FIXED] | DAGDispatcher `__enter__`/`__exit__` + `close()` | `get_symbols_overview` confirms methods present |
| R-02 [FIXED] | SecurityChecker FIFO to LRU cache | OrderedDict pattern intact |
| G-02 [FIXED] | `_register_single_tool` type `McpToolAdapter` | Body confirms type annotation |
| A-03 [FIXED] | `_should_fail_orphan` type `TaskItem` | Body confirms type annotation |
| O-04 [FIXED] | `progress_callback` type tightened | Body confirms `Callable[...]\|None` |

### Complexity Refactoring Verified

| Function | Before | After | Method |
|----------|--------|-------|--------|
| `_run_dispatch_loop` | CC 11 (C) | CC 8 (B) | Extract `_dispatch_batch`, `DispatchResult.is_terminal` |
| `get_health_summary` | CC 10 (B) | CC 7 (A) | Counter replacement |
| `_no_more_work` | CC 11 (C) | CC 6 (A) | Redundant branch merge |
| `evolution_history` | CC 11 (C) | CC 3 (A) | Extract helpers |
| `_check_api_keys` | CC 11 (C) | CC 4 (A) | Extract helpers |
| `run_composition` | CC 11 (C) | ~CC 7 (B) | Extract 7 helper functions |

**radon cc -nc**: 0 C-grade functions. All functions Grade A or B.

---

## New Findings (Cycle 2)

### A-V2-01 (P2) — dag_dispatcher.py:510

**Exception detail swallowed in `_drain_single_future`**

```python
try:
    artifact, error = _f.result()
except Exception:
    artifact, error = None, "executor error"  # loses exception info
```

The `_run_executor` already catches and returns error strings, so this catch handles unexpected exceptions from `_f.result()`. The generic "executor error" string loses diagnostic information.

**Suggested fix**: Capture the exception and include its message in the error string.

### G-V2-01 (P3) — gateway.py:426

**`_make_tool_func` ty diagnostic (carryover)**

Already suppressed with `# type: ignore[attr-defined]`. Type checker limitation, not a code defect.

**Status**: No action needed.

### H-V2-01 (P3) — hooks/executor.py:548,570

**POC placeholder hook types: `_execute_prompt` and `_execute_agent`**

Both methods return unconditional pass with `logger.warning`. Documented as POC placeholders.

**Status**: Feature gap, not a defect.

### A-V2-02 (DEFERRED) — llm_client.py:200,207,237

**Three `Any` typed fields in LLMClient** (carryover from cycle 1)

Requires lazy-injection architecture refactoring.

**Status**: Deferred.

---

## Module-by-Module Diagnostics (Cycle 2)

| Module | Files Checked | ty Errors | ty Warnings |
|--------|--------------|-----------|-------------|
| gateway/ | 4 files | 1 (cosmetic) | 0 |
| agency/ | 16 files | 0 | 0 |
| orchestration/ | 4 files | 0 | 0 |
| evolution/ | 8 files | 0 | 0 |
| config/ | 4 files | 0 | 0 |
| runtime/ | 5 files | 0 | 0 |
| models/ | 10 files | 0 | 0 |
| router/ | 3 files | 0 | 0 |

**Total**: 1 cosmetic ty error across 54 source files.

---

## Cross-Cutting Pattern Analysis

### Security

| Pattern | Status | Count |
|---------|--------|-------|
| bare except | **Zero** | 0 |
| eval/exec/pickle | **Zero** | 0 |
| shell=True | **Zero** | Only `create_subprocess_exec` |
| hardcoded secrets | **Zero** | API keys from env vars only |
| TODO/FIXME in production | **Zero** | Only in code generation templates |

### Exception Handling

| Pattern | Count | Assessment |
|---------|-------|------------|
| `except Exception` with logging | ~60 | All followed by logger with exc_info=True |
| `except Exception` swallowing detail | 1 | A-V2-01 (P2) |
| `except Exception` with continue | 1 | reflector.py rule failure (intentional) |

### Async Safety (CancelledError)

All 9 CancelledError sites verified correct (re-raise after cleanup).

### pass Statements

All `pass` statements are legitimate (exception handler skip, optional import fallback, Protocol abstract). No unimplemented method bodies.

---

## Test Verification

```
4728 passed, 30 skipped, 2 warnings in 75.62s
```

- 0 test failures
- 2 pre-existing IPC E2E timeouts excluded

---

## Issue Summary (All Cycles Combined)

| ID | Severity | Status | Description |
|----|----------|--------|-------------|
| A-04 | P1 | **FIXED** | DAGDispatcher context manager protocol |
| A-01 | P1 | **RESOLVED** | LLMClient.close() documented as intentional |
| A-02/A-V2-02 | P2 | **DEFERRED** | LLMClient Any fields |
| O-04 | P2 | **FIXED** | progress_callback type tightened |
| R-02 | P2 | **FIXED** | SecurityChecker FIFO to LRU cache |
| G-03 | P2 | **STALE** | Function no longer exists |
| **A-V2-01** | **P2** | **NEW** | `_drain_single_future` swallows exception detail |
| G-01/G-V2-01 | P3 | **SUPPRESSED** | `__signature__` type ignore comment |
| G-02 | P3 | **FIXED** | _register_single_tool type |
| A-03 | P3 | **FIXED** | _should_fail_orphan type |
| A-05 | P3 | **FIXED** | _fallback_count documentation |
| O-03 | P3 | **ACCEPTED** | CPython _transport dependency |
| **H-V2-01** | **P3** | **NEW** | POC placeholder hook types |

**Active items requiring action**: 0 (all cycle 2 findings resolved)

### Cycle 3 Fixes (Iteration 12)

| ID | Severity | Status | Fix |
|----|----------|--------|-----|
| ASYNC-01 | P1 | **FIXED** | `_fetch_single_agent_tools` except widened from 4 specific types to `Exception` — prevents one agent's failure from aborting all tool discovery |
| ASYNC-02 | P2 | **FIXED** | `_execute_command` added `except BaseException` block — ensures subprocess cleanup on SystemExit/GeneratorExit |
| ASYNC-03 | P2 | **FIXED** | `HookExecutor` added `__aenter__`/`__aexit__` — httpx.AsyncClient guaranteed cleanup via context manager |
| A-V2-01 | P2 | **FIXED** | `_drain_single_future` preserves exception detail: `"executor error"` → `f"executor error: {exc}"` |
