# Rust Platform Rewrite — Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rewrite the entire Agent Nexus Platform layer from Python to Rust (17,076 lines → 6 crates).

**Architecture:** 6 crate workspace (ap-core → ap-runtime → ap-fetcher/ap-evolution/ap-gateway → ap-cli). Full replacement, no parallel Python/Rust running. Dual-channel IPC (MCP + raw JSON-lines).

**Tech Stack:** Rust (edition 2021), tokio, rmcp, axum, git2, rusqlite, clap, serde

**Design Spec:** `docs/superpowers/specs/2026-04-22-rust-rewrite-design.md`

---

## Build Order (严格按依赖图从底向上)

```
Phase 1: ap-core/models        ← 无依赖，所有 crate 的基础
Phase 2: ap-core/config        ← 依赖 models
Phase 3: ap-core/orchestration ← 依赖 models + config
Phase 4: ap-core/router        ← 依赖 orchestration
Phase 5: ap-core/hooks+skills  ← 依赖 models
Phase 6: ap-runtime            ← 依赖 ap-core
Phase 7: ap-fetcher            ← 依赖 ap-core
Phase 8: ap-evolution          ← 依赖 ap-core + ap-runtime
Phase 9: ap-gateway            ← 依赖 ap-core + ap-runtime
Phase 10: ap-cli               ← 依赖所有 crate
Phase 11: Integration Tests    ← 全量集成验证
```

## Sub-Plan Files

| Phase | Sub-Plan | Status |
|-------|----------|--------|
| 0 | Workspace + Cargo.toml setup | [ ] |
| 1 | `plans/rust-rewrite/01-ap-core-models.md` | [ ] |
| 2 | `plans/rust-rewrite/02-ap-core-config.md` | [ ] |
| 3 | `plans/rust-rewrite/03-ap-core-orchestration.md` | [ ] |
| 4 | `plans/rust-rewrite/04-ap-core-router.md` | [ ] |
| 5 | `plans/rust-rewrite/05-ap-core-hooks-skills.md` | [ ] |
| 6 | `plans/rust-rewrite/06-ap-runtime.md` | [ ] |
| 7 | `plans/rust-rewrite/07-ap-fetcher.md` | [ ] |
| 8 | `plans/rust-rewrite/08-ap-evolution.md` | [ ] |
| 9 | `plans/rust-rewrite/09-ap-gateway.md` | [ ] |
| 10 | `plans/rust-rewrite/10-ap-cli.md` | [ ] |
| 11 | `plans/rust-rewrite/11-integration-tests.md` | [ ] |

## Execution Notes

- Each sub-plan is self-contained and can be executed by a fresh subagent
- Sub-plans follow TDD: write test → verify fail → implement → verify pass → commit
- Sub-plans reference the design spec for context
- Commit after each completed sub-plan
- Run `cargo test` + `cargo clippy` at the end of each sub-plan
