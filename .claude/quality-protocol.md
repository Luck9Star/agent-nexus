# Quality Protocol (v2)

> **v1 Lessons (88→133, 76 entries, 2600+ tests)**
>
> | Problem | Evidence | v2 Fix |
> |---------|----------|--------|
> | Exit conditions satisfied at iter89, but P0/P1 kept appearing to iter133 | 6/6 checkboxes checked at iter89, yet 44 more iterations of real work | Three-layer exit model: grep baseline + density metrics + coverage completeness |
> | No convergence detection; test growth slowed from +40/iter to +3/iter without triggering stop | iter127-133: +4 tests/iter avg, 1 real fix in 3 rounds, 14/16 fp in iter131 | Quantitative convergence ratio with forced-stop rule |
> | Same module revisited 3-5x without knowing it was "done" | store.py ×5, ipc.py ×4, installer.py ×5 | Module heatmap with HOT/WARM/COLD status tracking |
> | Blue-team fp rate uncontrolled | iter131: 14/16 fp (87%), wasted verification time | Exploitability gate: every finding must pass 4-question verification |

---

## Protocol Architecture

```
Phase A: Systematic Pattern Elimination
  Goal: Exhaust P0-P7 patterns across entire codebase
  Exit: All 8 patterns cleared with zero-residue verification

Phase B: Adversarial Audit
  Goal: Deep logic defects that grep cannot find
  Exit: Convergence dashboard triggers Phase C

Phase C: Convergence Verification
  Goal: Independent validation of A+B quality
  Exit: All checks pass → protocol complete; new P0/P1 → back to Phase B
```

---

## Phase A: Systematic Pattern Elimination

### Key Differences from v1

| Dimension | v1 Problem | v2 Improvement |
|-----------|-----------|----------------|
| Exhaustiveness | "Batch fix hits" but only fixed 2-3 grep results | After fixing, **re-scan to verify zero residue** |
| Cross-pattern | Each pattern scanned independently | After fixing one pattern, **check if other patterns were introduced** |
| Completion | "cleared" = grep finds 0 | "cleared" = grep 0 + tests green + no new patterns |

### Priority Queue

Scan P0→P7 in order. Each pattern must pass exhaustion verification before marking complete.

| Priority | Pattern | Exhaustion Verification |
|----------|---------|------------------------|
| P0 | Security bypass (import/eval/exec/sandbox/cmd injection) | Security checker unit tests + AST exhaustive forbidden list |
| P1 | Data consistency (FK/counter race/TOCTOU) | SQL schema audit + concurrency tests + state machine verification |
| P2 | Race conditions (lock/async race/event-loop) | asyncio Lock audit + concurrent scenario enumeration |
| P3 | Silent failures (except pass/error swallowing/missing error_type) | `grep -rn "except.*:" src/` + every hit must have log/warn/raise |
| P4 | Type safety (Pyright/Optional) | `pyright src/` zero HIGH/CRITICAL |
| P5 | Code hygiene (TODO/dead code/unused imports) | `grep -rn "TODO\|FIXME\|HACK" src/` + ruff check |
| P6 | Performance (N+1/O(n²)/unbounded memory) | EXPLAIN ANALYZE key queries + algorithm complexity annotation |
| P7 | API design (input validation/path guards/boundaries) | Full API boundary audit (input range for every public method) |

### Phase A SOP

1. **Scan**: Full-repo grep for pattern, output complete hit list (line numbers + context)
2. **Classify**: Each hit tagged [REAL] / [ALREADY_FIXED] / [FALSE_POSITIVE(reason)]
3. **Batch fix**: Fix ALL [REAL] hits at once (not just current file)
4. **Exhaustion verification**: Re-grep after fix, confirm 0 new hits (if leaks remain, go to step 3)
5. **Cross-check**: Did the fix introduce problems from other patterns? (grep for new TODO/except:pass)
6. **Regression**: `uv run pytest tests/ -x -q` all green
7. **Mark complete**: Pattern marked "cleared" with exhaustion evidence

### Phase A Exit Conditions

**All 8 patterns cleared + baseline checks pass:**

- [ ] `grep -rn "except.*:\s*pass" src/` zero hits (bare pass without logging)
- [ ] `grep -rn "TODO\|FIXME\|HACK" src/` zero hits
- [ ] `pyright src/` zero HIGH/CRITICAL
- [ ] `uv run pytest tests/ -x -q` all green
- [ ] Security forbidden_functions + forbidden_attributes fully covered
- [ ] Every `close()`/`__del__`/`cleanup` path has a regression test

---

## Phase B: Adversarial Audit

### Key Differences from v1

| Dimension | v1 Problem | v2 Improvement |
|-----------|-----------|----------------|
| Convergence | None. 87% fp rate still running | Dashboard: 2 consecutive rounds zero P0/P1 → Phase C |
| False positive control | Every finding gets fixed | Every finding must pass **exploitability gate** before fix |
| Repeated patterns | Same category fixed 7-11 times | Same sub-category max 2 fixes; 3rd triggers **root cause analysis** |

### Exploitability Gate

Every blue-team finding must answer 4 questions. ALL must be YES to proceed to fix:

1. **Triggerable?** — Can you construct specific input/state that triggers this in production?
2. **Impactful?** — What is the consequence? (data loss / security bypass / service unavailable / or just an extra log line?)
3. **Not framework-protected?** — asyncio single-thread safety? Pydantic type guard? Framework already handles? → If yes, tag as [FRAMEWORK_PROTECTED], do not fix
4. **Not duplicate?** — Has this site been fixed for the same class of issue in a previous iteration?

**Findings that don't pass the gate**: Tag as [EVALUATED-NOT_DEFECT], record in log, no fix, no test, no iteration count consumed.

### Convergence Dashboard

```
┌─ Convergence Dashboard ───────────────────────────────────┐
│ Round │ P0/P1 found │ P2/P3 found │ FP rate │ Action      │
├───────┼─────────────┼─────────────┼─────────┼─────────────┤
│ N     │ ≥1          │ any         │ any     │ Continue    │
│ N+1   │ 0           │ ≥1          │ <50%    │ Continue    │
│ N+1   │ 0           │ any         │ ≥50%    │ ⚠ Warning   │
│ N+2   │ 0           │ 0           │ —       │ → Phase C   │
│ N+2   │ 0           │ ≥1          │ ≥50%    │ → Phase C   │
│ N+k   │ 0           │ 0           │ —       │ Terminate   │
└───────┴─────────────┴─────────────┴─────────┴─────────────┘
```

**Trigger Phase C**: 2 consecutive rounds with P0/P1=0, OR 1 round with all findings fp rate ≥50%.

### Module Heatmap (prevents redundant scanning)

Before each round, check module status:

| Status | Condition | Action |
|--------|-----------|--------|
| **HOT** | P0/P1 found in last 3 rounds | Skip (already deeply scanned) |
| **WARM** | Scanned in last 5 rounds, 0 defects | Optional scan |
| **COLD** | Not scanned in 10+ rounds | Priority scan |
| **UNSCANNED** | Never in progress table | Must scan |

### Phase B SOP

1. **Check heatmap**: Select COLD/UNSCANNED modules, skip HOT modules
2. **Choose audit angle** (each angle used at most once, no repeats):
   - State machine violations (object lifecycle transitions)
   - Error propagation gaps (exceptions reaching callers correctly)
   - Resource leak paths (every acquire has a release)
   - Concurrency safety (shared state access ordering)
   - Input boundaries (public API parameter range validation)
   - Security escapes (sandbox/permissions/path traversal)
3. **Blue-team 2-3 agents scan in parallel, cross-validate**
4. **Each finding passes exploitability gate**
5. **Fix only gated findings**
6. **Update convergence dashboard**
7. **Decide: continue or trigger Phase C**

### Repeated Pattern Root Cause Analysis

When the same sub-category (e.g., "FD leak", "missing error_type") is fixed for the 3rd time, **stop fixing bugs and produce a root cause analysis report**:

```markdown
## Root Cause Analysis: [sub-category name]

### Pattern Description
[Bug pattern, 1-2 sentences]

### Fix History
| Round | File | What was fixed |
|-------|------|---------------|
| iterX | file.py | What was done |

### Root Cause
[Why does this keep recurring? Missing abstraction? Missing code standard? Missing static check?]

### Recommendations
- [ ] Short-term: Fix remaining sites
- [ ] Medium-term: Add lint rule / extract common pattern
- [ ] Long-term: Architectural fix (e.g., unified resource manager)
```

---

## Phase C: Convergence Verification

### Independent Verification

Phase C is executed by an independent agent (not one that participated in Phase B fixes):

1. **Regression**: `uv run pytest tests/ -x -q` all green
2. **Baseline**: Phase A's 6 exit conditions all pass
3. **Fix spot-check**: Randomly sample 3 Phase B fixes, verify:
   - Fix correctly resolves the original problem
   - Fix does not introduce new problems
   - Regression test actually tests the fix point
4. **Miss scan**: One final scan from a novel angle (unused in Phase B)

### Phase C Exit

- All pass → **Protocol complete**, output final report
- New P0/P1 found → Back to Phase B with new findings as input
- New P2-P3 found → Record as [KNOWN-ACCEPTED], no Phase B re-entry

---

## Exit Conditions (v2 Three-Layer Model)

### Layer 1: Baseline Health (all must pass)

- [ ] `uv run pytest tests/ -x -q` all green
- [ ] `uv run pyright src/agent_nexus/` zero HIGH/CRITICAL
- [ ] Zero `except.*:\s*pass` silent exceptions in src/
- [ ] Zero TODO/FIXME/HACK in src/

### Layer 2: Defect Density (quantitative metrics)

- [ ] Zero P0 found in last 3 rounds
- [ ] Zero P1 found in last 3 rounds
- [ ] All P2 findings in last 5 rounds are false positive or documented as accepted risk
- [ ] Convergence ratio < 0.15 and zero real bugs in last 3 rounds

### Layer 3: Coverage Completeness

- [ ] All src/ modules marked WARM or HOT in module heatmap (no UNSCANNED)
- [ ] docs/ Phase-to-module mapping complete
- [ ] No known security bypass vectors (import/function/attribute/regex four-rule coverage)

> **Key difference from v1**: v1's exit conditions were satisfied at iter89, but P0/P1 defects kept appearing through iter133. v2's Layer 2 adds quantitative metrics, Layer 3 ensures coverage completeness.

---

## Anti-Patterns (v1 + v2 Combined)

| # | Anti-Pattern | v2 Control Mechanism |
|---|-------------|---------------------|
| 1 | Fix 5 unrelated bugs in one round | Phase A: one pattern per round; Phase B: one angle per round |
| 2 | Skip full test suite after fixing | Mandatory after every Phase |
| 3 | Fix same pattern repeatedly without marking | 3rd occurrence triggers root cause analysis |
| 4 | Blue-team high fp rate without stopping | Dashboard auto-triggers Phase C |
| 5 | Exit conditions too shallow | Phase A baseline + Phase B depth + Phase C independence |
| 6 | Random audit not following queue | Phase A follows priority queue; Phase B follows angle list, each used once |
| 7 | Fix one file without grepping others | Phase A step 4 enforces re-scan for zero residue |
| 8 | Add Pydantic constraints without checking constructors | Phase A step 5 cross-checks impacted callers |
| 9 | Re-scan HOT modules | Heatmap prevents redundant deep scans |
| 10 | Blue-team findings without cross-validation | Exploitability gate: 4 questions, all YES required |

---

## Test Rules

- Test files named by module: `test_{module_name}.py`
- Mock read/readline must return `b""` to prevent infinite loops
- IPython InteractiveShell: session-scoped fixtures only
- Single test file execution ≤ 30s, full suite ≤ 180s
- Monitor Python process memory; excessive usage suggests memory leak risk

---

## Progress Tracking (v2 Format)

### Phase A Cleared Patterns

| Pattern | Scope | Exhaustion Verification | Round |
|---------|-------|------------------------|-------|
| *(Migrated from v1 — all 8 patterns cleared in iter88-93)* | | Re-scan verified zero residue | A0 |
| *(No new pattern elimination needed for current cycle)* | | | |

### Phase B Convergence History

| Round | Audit Angle | P0/P1 | P2/P3 | FP Rate | Status |
|-------|-------------|-------|-------|---------|--------|
| *(Populate as Phase B runs)* | | | | | |

### Module Heatmap

| Module | Last Scanned | Status | Last P0/P1 |
|--------|-------------|--------|-----------|
| store.py | 2026-04-20 | HOT | iter132 |
| ipc.py | 2026-04-20 | HOT | iter129 |
| process_manager.py | 2026-04-20 | HOT | iter130 |
| task_graph.py | 2026-04-20 | WARM | iter98 |
| router.py | 2026-04-20 | HOT | iter131 |
| executor.py | 2026-04-20 | WARM | iter133b |
| lockfile.py | 2026-04-20 | WARM | iter123 |
| installer.py | 2026-04-20 | WARM | iter118 |
| gateway.py | 2026-04-20 | WARM | iter132 |
| supervisor.py | 2026-04-20 | WARM | iter130 |
| promotion.py | 2026-04-20 | WARM | iter129 |
| subtask.py | 2026-04-20 | WARM | iter121 |
| compaction.py | 2026-04-20 | WARM | iter91 |
| workflow.py | 2026-04-20 | WARM | iter127 |
| tool_adapter.py | 2026-04-20 | WARM | iter117 |
| sources.py | 2026-04-20 | WARM | iter122 |
| deferred_registry.py | 2026-04-20 | WARM | iter122 |
| security_rules.py | 2026-04-19 | WARM | iter88 |
| runtime.py | 2026-04-19 | WARM | iter88 |
| security_checker.py | 2026-04-19 | WARM | iter88 |

### Convergence Data

| Window | Test Delta | real_fix | fp | Convergence Ratio |
|--------|-----------|----------|-----|-------------------|
| iter131-133 | +4 | 1 | 14 | 0.04 |
| iter128-130 | +13 | 2 | 0 | 0.13 |
| iter125-127 | +7 | 2 | 0 | — |

**Current state**: Converging (ratio=0.04, real_fix=1/3 rounds). If next round zero real_fix → trigger stop.

---

## Migration Guide

From v1 to v2:
1. Archive v1 progress table in `iteration-protocol-v1-archive.md`
2. Populate module heatmap from v1's last 10 rounds of scan data
3. Calculate convergence data from v1's last 6 rounds
4. Phase A exit conditions already satisfied (confirmed iter89, re-verified iter133)
5. Phase B / Layer 2 / Layer 3 start from v2 activation

---

## v1 → v2 Improvement Summary

```
┌─ Problem 1: Exit conditions satisfied too early ────────────┐
│  Evidence: 6/6 checked at iter89, P0/P1 still at iter133     │
│  v2 Fix: 3-layer model — grep is just Layer 1 baseline       │
│          Layer 2 = density metrics, Layer 3 = coverage map    │
├─ Problem 2: Same module revisited without tracking ──────────┤
│  Evidence: store.py ×5, ipc.py ×4, installer.py ×5          │
│  v2 Fix: Module heatmap with HOT/WARM/COLD status             │
│          HOT modules auto-skipped for 3 rounds                │
├─ Problem 3: Diminishing returns with no stop signal ─────────┤
│  Evidence: iter127-133 avg +3 tests/iter, 1 fix/3 rounds     │
│  v2 Fix: Convergence ratio + forced-stop at <0.15            │
│          Dashboard makes state visible per-round              │
├─ Problem 4: Blue-team false positives uncontrolled ──────────┤
│  Evidence: iter131 14/16 fp (87%), wasted verification time  │
│  v2 Fix: Exploitability gate (4 questions, all YES required)  │
│          FP rate ≥50% auto-triggers Phase C exit              │
└──────────────────────────────────────────────────────────────┘
```
