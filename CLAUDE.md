# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Agent Nexus is an MCP-native Agent Platform with self-built orchestration. It provides Git-based Agent distribution, Python Runtime execution layer, and Self-Evolution Engine. Agents are distributed via Git repos (Homebrew tap model), run locally, and use user-configured models. Currently in POC v5.2 documentation phase — no code exists yet.

- **Tech stack**: Python (POC) → Rust (production rewrite), MCP protocol, self-built orchestration (referencing ClawTeam)
- **License**: MIT
- **Language**: Documentation is primarily in Chinese

## Documentation Index

All design docs live in `docs/`. POC.md is the master index. Key documents:

| Topic | File |
|-------|------|
| Product positioning & core architecture (4-layer) | `docs/01-overview.md` |
| Self-built orchestration layer (ref ClawTeam) | `docs/02-clawteam-integration.md` |
| Python Runtime (CaveAgent-based) | `docs/03-python-runtime.md` |
| Self-Evolution Engine (OpenSpace-based) | `docs/04-self-evolution.md` |
| Agent system (Atomic/Composite, 3 run modes) | `docs/05-agent-system.md` |
| MCP exposure & communication matrix | `docs/06-mcp-communication.md` |
| Agent distribution & quality gates | `docs/07-marketplace.md` |
| Constraints, security, Rust rewrite scope | `docs/08-constraints-decisions.md` |
| 7-phase implementation plan | `docs/09-implementation-plan.md` |
| Git-based distribution, local architecture, Python POC code, Rust traits | `docs/10-cloud-local-architecture.md` |
| TOML schemas, model tiers, reference projects | `docs/appendix.md` |

## Architecture Summary

Four-layer architecture (top to bottom):
1. **MCP Exposure Layer** — FastMCP Server per Agent, MCP Gateway for routing/discovery
2. **Orchestration Layer (self-built)** — TaskGraph (SQLite + blocked_by + cycle detection), IPC (stdin/stdout JSON-lines), ProcessManager (asyncio.subprocess + health check), OrchestrationDSL (TOML DAG)
3. **Python Runtime Layer** — CaveAgent-based IPythonRuntime (in-process, since Agents are already subprocesses). SecurityChecker at AST level
4. **Self-Evolution Engine** — OpenSpace-based. Three layers: Atomic Skill Evolution → Composite Orchestration Evolution → Agent Promotion

**Agent types**: Atomic (10, e.g. doc-filler, code-reviewer) and Composite (5, e.g. feature-delivery-pipeline). Three run modes: MCP standalone / Platform Router / CLI standalone.

## Key Design Decisions

- **Self-built orchestration** — Reference ClawTeam's proven patterns (TaskStore, MailboxManager, SpawnBackend), build simplified versions. No external pip dependency.
- **Runtime-First Hybrid** — Python Runtime is primary execution, MCP for external communication
- **MCP protocol boundary = language boundary** — Rust platform communicates with Python Agent subprocesses via MCP stdio/SSE. Agent internals stay Python forever.
- **Git-based distribution** — Agents distributed via Git repos (Official monorepo + Private repos + Direct URL). No cloud infrastructure needed. Homebrew tap model.
- **Rust rewrite scope** — Only upper layers (Gateway, Fetcher, Supervisor, CLI). Agent Runtime stays Python. Interfaces must remain format-compatible during migration.

## Reference Projects (local clones)

| Project | Local Path | What to reference |
|---------|-----------|-------------------|
| ClawTeam | `/Users/yangyitian/Documents/dev/Agents/ClawTeam/` | Orchestration reference: `clawteam/store/` TaskGraph, `clawteam/team/mailbox.py` IPC, `clawteam/spawn/` ProcessManager |
| OpenSpace | `/Users/yangyitian/Documents/dev/Agents/OpenSpace/` | `openspace/skill_engine/` |
| CaveAgent | `/Users/yangyitian/Documents/dev/Agents/cave-agent/` | `src/cave_agent/runtime/`, `src/cave_agent/security/` |
| deer-flow | `/Users/yangyitian/Documents/dev/Agents/deer-flow/` | `packages/harness/deerflow/skills/`, `packages/harness/deerflow/subagents/` |

## Project Structure (current)

```
agent-nexus/
├── src/agent_nexus/          # Platform core (editable install via hatch)
│   ├── platform/
│   │   ├── router/           # Platform Router (4-Phase Workflow)
│   │   ├── orchestration/    # TaskGraph, ProcessManager, IPC, OrchestrationDSL
│   │   ├── gateway/          # MCP Gateway aggregation
│   │   ├── config/           # Model Config + Provider Registry
│   │   ├── local/            # CLI + Git Installer + Supervisor
│   │   ├── skills/           # Skill Loader
│   │   ├── evolution/        # Self-Evolution Engine
│   │   └── runtime/          # Python Runtime (CaveAgent-based)
│   └── models/               # Shared data models
├── agents/                   # Official Agent packages (independent pyproject.toml each)
│   ├── atomic/               # 10 Atomic Agents
│   └── composite/            # 5 Composite Agents
├── tests/                    # Platform tests
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── templates/                # OrchestrationDSL TOML templates
├── docs/                     # Design documents (POC v5.2)
├── crates/                   # Rust rewrite (future): ap-core, ap-fetcher, ap-runtime, ap-gateway, ap-cli
└── pyproject.toml            # Platform package config
```

## Security Architecture (Defense-in-Depth)

1. Process boundary — Agents run as independent subprocesses
2. PermissionChecker — Pre-execution permission check (DEFAULT/PLAN/FULL_AUTO modes)
3. SecurityChecker — Runtime AST-level code safety analysis

## Implementation Phases

Phase 1 (W1-2): Self-built orchestration basics + first Agent → Phase 2 (W3-4): Platform Router → Phase 3 (W5): MCP Gateway → Phase 4 (W6-7): Git-based Distribution + CLI → Phase 5 (W8-10): Runtime + Evolution → Phase 6 (W11-12): Polish → Phase 7 (W13-20): Rust rewrite

See `docs/09-implementation-plan.md` for full phase details and risk matrix.

## Conventions

- All Agents must have SKILL.md before implementation code
- Model config priority: env vars > Agent config > defaults
- Environment variables: `AGENT_MODEL`, `DEFAULT_MODEL`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL`
- Config file: `config.toml` (TOML format)
- Lock file: `lockfile.json` (JSON format)
- All reference project licenses are MIT or Apache-2.0 — preserve original copyright notices

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **agent-nexus** (8525 symbols, 28825 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## When Debugging

1. `gitnexus_query({query: "<error or symptom>"})` — find execution flows related to the issue
2. `gitnexus_context({name: "<suspect function>"})` — see all callers, callees, and process participation
3. `READ gitnexus://repo/agent-nexus/process/{processName}` — trace the full execution flow step by step
4. For regressions: `gitnexus_detect_changes({scope: "compare", base_ref: "main"})` — see what your branch changed

## When Refactoring

- **Renaming**: MUST use `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` first. Review the preview — graph edits are safe, text_search edits need manual review. Then run with `dry_run: false`.
- **Extracting/Splitting**: MUST run `gitnexus_context({name: "target"})` to see all incoming/outgoing refs, then `gitnexus_impact({target: "target", direction: "upstream"})` to find all external callers before moving code.
- After any refactor: run `gitnexus_detect_changes({scope: "all"})` to verify only expected files changed.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Tools Quick Reference

| Tool | When to use | Command |
|------|-------------|---------|
| `query` | Find code by concept | `gitnexus_query({query: "auth validation"})` |
| `context` | 360-degree view of one symbol | `gitnexus_context({name: "validateUser"})` |
| `impact` | Blast radius before editing | `gitnexus_impact({target: "X", direction: "upstream"})` |
| `detect_changes` | Pre-commit scope check | `gitnexus_detect_changes({scope: "staged"})` |
| `rename` | Safe multi-file rename | `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` |
| `cypher` | Custom graph queries | `gitnexus_cypher({query: "MATCH ..."})` |

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d=1 | WILL BREAK — direct callers/importers | MUST update these |
| d=2 | LIKELY AFFECTED — indirect deps | Should test |
| d=3 | MAY NEED TESTING — transitive | Test if critical path |

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/agent-nexus/context` | Codebase overview, check index freshness |
| `gitnexus://repo/agent-nexus/clusters` | All functional areas |
| `gitnexus://repo/agent-nexus/processes` | All execution flows |
| `gitnexus://repo/agent-nexus/process/{name}` | Step-by-step execution trace |

## Self-Check Before Finishing

Before completing any code modification task, verify:
1. `gitnexus_impact` was run for all modified symbols
2. No HIGH/CRITICAL risk warnings were ignored
3. `gitnexus_detect_changes()` confirms changes match expected scope
4. All d=1 (WILL BREAK) dependents were updated

## Keeping the Index Fresh

After committing code changes, the GitNexus index becomes stale. Re-run analyze to update it:

```bash
npx gitnexus analyze
```

If the index previously included embeddings, preserve them by adding `--embeddings`:

```bash
npx gitnexus analyze --embeddings
```

To check whether embeddings exist, inspect `.gitnexus/meta.json` — the `stats.embeddings` field shows the count (0 means no embeddings). **Running analyze without `--embeddings` will delete any previously generated embeddings.**

> Claude Code users: A PostToolUse hook handles this automatically after `git commit` and `git merge`.

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
