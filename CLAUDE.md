# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Agent Nexus is an MCP-native Agent Platform with self-built orchestration. It provides Git-based Agent distribution, Python Runtime execution layer, and Self-Evolution Engine. Agents are distributed via Git repos (Homebrew tap model), run locally, and use user-configured models. Python implementation complete (Phases 1-6); Rust rewrite (Phase 7) pending.

- **Tech stack**: Python (production) → Rust (future platform rewrite), MCP protocol, self-built orchestration (referencing ClawTeam)
- **License**: MIT
- **Language**: Documentation is primarily in Chinese

## Documentation Index

All design docs live in `docs/`. See `docs/README.md` for the full navigation index. Key documents:

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
| Git-based distribution, local architecture, Python implementation, Rust traits | `docs/10-cloud-local-architecture.md` |
| TOML schemas, model tiers, reference projects | `docs/appendix.md` |
| Testing overview, coverage, conventions | `docs/testing.md` |

## Architecture Summary

Four-layer architecture (top to bottom):
1. **MCP Exposure Layer** — FastMCP Server per Agent, MCP Gateway for routing/discovery
2. **Orchestration Layer (self-built)** — TaskGraph (SQLite + blocked_by + cycle detection), IPC (stdin/stdout JSON-lines), ProcessManager (asyncio.subprocess + health check), OrchestrationDSL (TOML DAG)
3. **Python Runtime Layer** — CaveAgent-based IPythonRuntime (in-process, since Agents are already subprocesses). SecurityChecker at AST level
4. **Self-Evolution Engine** — OpenSpace-based. Three layers: Atomic Skill Evolution → Composite Orchestration Evolution → Agent Promotion

**Agent types**: Atomic (11, e.g. doc-filler, code-reviewer) and Composite (5, e.g. feature-delivery-pipeline). Three run modes: MCP standalone / Platform Router / CLI standalone.

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
│   ├── atomic/               # 11 Atomic Agents
│   └── composite/            # 5 Composite Agents
├── tests/                    # Platform tests
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── templates/                # OrchestrationDSL TOML templates
├── docs/                     # Design documents
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
