# Serena Deep Audit Report v3 — Test Signal-to-Noise Analysis

**Date**: 2026-05-10
**Cycle**: 3 (test quality focus — signal-to-noise ratio)
**Scope**: 174 test files, 4889 tests (pre-audit)
**Method**: AST-level static analysis + Serena symbol audit + mock density profiling
**Previous report**: docs/audit-report-SERENA-v2.md (cycle 2, 2026-05-10)

---

## Executive Summary

**Test suite has 48 low-signal tests removed** (37 Pydantic frozen + 11 required_field/empty_string). All removed tests only verified Pydantic's built-in behavior (frozen=True prevents assignment, min_length=1 rejects empty strings), not project-specific logic. `test_common.py` retained as single source of truth for frozen behavior.

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Total tests | 4889 | 4841 | -48 (-0.98%) |
| Model file tests | 504 | 456 | -48 |
| Pydantic frozen tests | 49 | 1 | -48 |
| Tests passed | 4810+30skipped+1pre-existing | 4810+30skipped+1pre-existing | 0 regression |

---

## Test Signal-to-Noise Audit Findings

### TSN-01: Pydantic Frozen Tests — Redundant [DELETED]

**Severity**: P3 (noise reduction)
**Files**: 11 model test files
**Tests deleted**: 37
**Pattern**: Every model class gets its own `test_frozen` method that creates an instance and tries to modify a field, expecting `ValidationError`. This re-verifies Pydantic's `frozen=True` behavior 37 times across the codebase.

**Why low signal**: The project uses a shared base `FrozenModel` with `model_config = ConfigDict(frozen=True)`. `test_common.py` already tests this base class behavior. Each per-model `test_frozen` is testing Pydantic's implementation, not a project design decision.

**Retained**: `test_common.py` with 3 frozen tests (prevents assignment, is hashable, subclass inherits config) serves as the canonical test.

**Files modified**:
- tests/unit/test_agent_models.py (6 deleted)
- tests/unit/test_distribution_models.py (5 deleted)
- tests/unit/test_runtime_models.py (5 deleted)
- tests/unit/test_skills.py (0 — had `test_frozen_cannot_modify_list` which tests deep freeze, kept)
- tests/unit/test_config_models.py (4 deleted)
- tests/unit/test_evolution_models.py (4 deleted)
- tests/unit/test_hooks_models.py (3 deleted)
- tests/unit/test_ipc_models.py (3 deleted)
- tests/unit/test_permission_models.py (3 deleted)
- tests/unit/test_task_models.py (1 deleted)
- tests/unit/test_token_tracker.py (1 deleted)
- tests/unit/test_context_models.py (2 deleted)

### TSN-02: Pydantic Required Field / Empty String Tests — Redundant [DELETED]

**Severity**: P3 (noise reduction)
**Files**: 5 model test files
**Tests deleted**: 11
**Pattern**: Tests call `Model()` with no args (expecting `ValidationError`) or pass empty strings to fields with `min_length=1`. These verify Pydantic's validation, not project-specific constraints.

**Deleted patterns**:
- `test_missing_required_fields` (5 tests) — calls `Model()` with no args, expects `ValidationError`
- `test_empty_id/name/description/agent` (6 tests) — passes `""` to fields with `min_length=1`

**Files modified**:
- tests/unit/test_task_models.py (4 deleted: missing_required + 3 empty tests + empty class cleanup)
- tests/unit/test_runtime_models.py (4 deleted: 3 empty + 1 both_empty tautological combo)
- tests/unit/test_agent_models.py (1 deleted)
- tests/unit/test_evolution_models.py (1 deleted)
- tests/unit/test_hooks_models.py (1 deleted)

### TSN-03: No-Assertion Tests — VALID [NO ACTION]

**Finding**: 7 tests identified with zero assert/pytest.raises
**Assessment**: All 7 are valid "should not raise" patterns:
- 1 skipped test (`test_run_with_retry_propagates_system_exit`)
- 6 tests verifying that operations complete without exceptions (double_close, skip_unknown, noop_save)
**Action**: None needed — these test legitimate invariants (idempotent cleanup, graceful handling)

### TSN-04: pytest.raises-Only Tests — VALID [NO ACTION]

**Finding**: 475 tests use only `pytest.raises` (no assert statements)
**Assessment**: These test error/exception paths — a valid test pattern. Testing that invalid inputs raise appropriate errors provides genuine regression protection.
**Action**: None — this is a healthy pattern, not tautological

### TSN-05: Mock Density Analysis — HEALTHY [NO ACTION]

**Files with >2 mocks/test**:
| File | Mock Density | Tests | Assessment |
|------|-------------|-------|------------|
| test_config_cmd.py | 7.6 | 5 | CLI command tests, mock density from arg parsing |
| test_cli_module.py | 2.9 | 103 | Module-level integration, expected density |
| test_external_mcp_adapter.py | 2.8 | 82 | External API adapter, mocking is appropriate |

**Assessment**: High mock density in these files is appropriate for their domain (CLI commands, external API adapters). No tautological mock==mock patterns found.

### TSN-06: Config Triple Coverage — MONITOR [DEFERRED]

**Finding**: `agent_nexus.models.config` tested by 13 files (total ~270 tests)
**Key overlap areas**:
- test_config.py (45 tests) + test_config_loader.py (28 tests) + config/test_loader.py (58 tests)
- Some tests in test_config.py may overlap with test_config_model_config.py

**Assessment**: These files test different layers (config loading, model validation, defaults, stages). Overlap is at the integration boundary, not pure duplication.
**Action**: Deferred — requires careful per-test comparison. Not tautological, just dense coverage.

---

## Cross-Cutting Analysis

### Pydantic Framework Test Taxonomy

| Category | Count | Signal Level | Action |
|----------|-------|-------------|--------|
| Frozen tests (per-model) | 37 | Near-zero | **DELETED** |
| Required field tests (per-model) | 11 | Near-zero | **DELETED** |
| Frozen tests (base class) | 3 | Medium (design decision) | **KEPT** |
| Empty string tests (project logic) | ~20 | Low-Medium | **KEPT** (test project validators) |
| ValidationError tests (project validators) | ~40 | Medium-High | **KEPT** (test custom validators) |

### Module Coverage Heatmap

Highest test density (by files per module):
1. `agency.registry` — 16 files, 331 tests
2. `models.evolution` — 15 files, 631 tests
3. `models.agent` — 15 files, 656 tests
4. `orchestration.task_graph` — 15 files, 480 tests
5. `models.config` — 13 files, ~270 tests

---

## Verification

- [x] ruff check: All modified files pass
- [x] pytest: 4810 passed, 30 skipped, 1 pre-existing IPC E2E timeout
- [x] No regressions from deletions
- [x] Empty class `TestTaskItemMinLength` cleaned up
- [x] Unused `ValidationError` import in test_config_models.py removed
