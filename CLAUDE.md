# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Agent Nexus is an MCP-native Agent Platform with self-built orchestration. It provides Git-based Agent distribution, Python Runtime execution layer, and Self-Evolution Engine. Agents are distributed via Git repos (Homebrew tap model), run locally, and use user-configured models. **Dual implementation**: Python platform complete (Phases 1-6), Rust platform rewrite in progress (6 crates, ~18K LOC).

- **Tech stack**: Python (production platform) + Rust (platform rewrite in progress), MCP protocol, self-built orchestration
- **License**: MIT
- **Language**: Documentation is primarily in Chinese

## Commands

```bash
# Python platform
uv sync                        # Install dependencies
uv run pytest tests/           # Run all Python tests
uv run pytest tests/ -x        # Stop on first failure
uv run pytest tests/ -m unit   # Unit tests only
uv run pytest tests/ -m integration  # Integration tests only
uv run pytest tests/ -m e2e    # E2E tests only
uv run ruff check src/ agents/ # Lint
uv run ruff check --fix src/   # Auto-fix lint issues
uv run ruff format src/ agents/ # Format
uv run ty check src/           # Type check (ty, installed separately)

# Agency pipeline (LLM-powered expert orchestration)
uv run python -m agent_nexus.platform.agency.cli run-composition \
  --task "..." --vendor-path <path> --allowlist <path> \
  --use-llm --temperature 0.7 --max-parallel 3

# Rust platform (workspace at repo root)
cargo build                   # Build all 6 crates
cargo test                    # Test all crates
cargo test -p ap-core         # Test single crate
cargo clippy                  # Lint
cargo clippy -p ap-cli        # Lint single crate
```

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
| Capability testing (contract-driven, 80 tests) | `docs/capability-testing.md` |

## Architecture Summary

Four-layer architecture (top to bottom):
1. **MCP Exposure Layer** — FastMCP Server per Agent, MCP Gateway for routing/discovery
2. **Orchestration Layer (self-built)** — TaskGraph (SQLite + blocked_by + cycle detection), IPC (stdin/stdout JSON-lines), ProcessManager (asyncio.subprocess + health check), OrchestrationDSL (TOML DAG)
3. **Python Runtime Layer** — CaveAgent-based IPythonRuntime (in-process, since Agents are already subprocesses). SecurityChecker at AST level
4. **Self-Evolution Engine** — OpenSpace-based. Three layers: Atomic Skill Evolution → Composite Orchestration Evolution → Agent Promotion

**Agent types**: Atomic (11, e.g. doc-filler, code-reviewer) and Composite (5, e.g. feature-delivery-pipeline). Three run modes: MCP standalone / Platform Router / CLI standalone.

**Agency Pipeline** — LLM-powered expert orchestration: LLMPlanner (task decomposition) → LLMExecutor (per-expert LLM calls) → LLMIntegrator (semantic synthesis) → LLMQualityGate (quality evaluation). All stages share a ModelCapabilityRegistry that provides per-model max_tokens, temperature range, and vision support data.

**Model Capability System** — Three-layer: built-in data (17 models in `models/capability.py`) → optional models.dev enrichment (`config/model_db.py`) → consumption in LLMClient (dynamic max_tokens, temperature clamping, supports_temperature gate). Model string format: `provider:model_name` (e.g. `anthropic:claude-sonnet-4-20250514`).

## Key Design Decisions

- **Self-built orchestration** — Reference ClawTeam's proven patterns (TaskStore, MailboxManager, SpawnBackend), build simplified versions. No external pip dependency.
- **Runtime-First Hybrid** — Python Runtime is primary execution, MCP for external communication
- **MCP protocol boundary = language boundary** — Rust platform communicates with Python Agent subprocesses via MCP stdio/SSE. Agent internals stay Python forever.
- **Git-based distribution** — Agents distributed via Git repos (Official monorepo + Private repos + Direct URL). No cloud infrastructure needed. Homebrew tap model.
- **Rust rewrite scope** — Upper layers only (Gateway, Fetcher, Evolution, CLI). Agent Runtime stays Python. 6 crates already implemented: ap-core, ap-runtime, ap-gateway, ap-fetcher, ap-evolution, ap-cli. Interfaces must remain format-compatible during migration.

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
├── src/agent_nexus/          # Platform core (editable install via uv)
│   ├── platform/
│   │   ├── agency/           # Agency pipeline (LLMPlanner → Executor → Integrator → QAGate)
│   │   ├── router/           # Platform Router (4-Phase Workflow)
│   │   ├── orchestration/    # TaskGraph, ProcessManager, IPC, OrchestrationDSL
│   │   ├── gateway/          # MCP Gateway aggregation
│   │   ├── config/           # Model Config + Provider Registry + models.dev client
│   │   ├── local/            # CLI + Git Installer + Supervisor
│   │   ├── skills/           # Skill Loader
│   │   ├── evolution/        # Self-Evolution Engine
│   │   └── runtime/          # Python Runtime (CaveAgent-based)
│   └── models/               # Shared data models + ModelCapability registry
├── agents/                   # Official Agent packages (independent pyproject.toml each)
│   ├── atomic/               # 11 Atomic Agents
│   └── composite/            # 5 Composite Agents
├── tests/                    # Platform tests
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── templates/                # OrchestrationDSL TOML templates
├── docs/                     # Design documents
├── crates/                   # Rust platform rewrite (in progress)
│   ├── ap-core/              # Core: TaskGraph, ProcessManager, StateMachine, DSL, IPC, Hooks, Skills
│   ├── ap-cli/               # CLI: clap derive, 9 commands (init, install, run, status, etc.)
│   ├── ap-gateway/           # MCP Gateway: deferred agent loading, tool aggregation
│   ├── ap-fetcher/           # Git-based agent distribution (clone, update, verify)
│   ├── ap-evolution/         # Self-Evolution Engine: SQLite store, analyzer, evolver, promotion
│   └── ap-runtime/           # Python subprocess bridge (spawn, IPC, health check)
├── Cargo.toml                # Rust workspace config
└── pyproject.toml            # Python platform config
```

## Security Architecture (Defense-in-Depth)

1. Process boundary — Agents run as independent subprocesses
2. PermissionChecker — Pre-execution permission check (DEFAULT/PLAN/FULL_AUTO modes)
3. SecurityChecker — Runtime AST-level code safety analysis

## Implementation Phases

Phase 1 (W1-2): Self-built orchestration basics + first Agent → Phase 2 (W3-4): Platform Router → Phase 3 (W5): MCP Gateway → Phase 4 (W6-7): Git-based Distribution + CLI → Phase 5 (W8-10): Runtime + Evolution → Phase 6 (W11-12): Polish → **Phase 7 (W13-20): Rust rewrite — IN PROGRESS** (ap-core ✅ ap-runtime ✅ ap-gateway ✅ ap-fetcher ✅ ap-evolution ✅ ap-cli ✅)

See `docs/09-implementation-plan.md` for full phase details and risk matrix.

## Conventions

- All Agents must have SKILL.md before implementation code
- Model config priority: env vars > Agent config > defaults
- Environment variables: `AGENT_MODEL`, `DEFAULT_MODEL`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL`
- Config file: `config.toml` (TOML format) — supports per-stage model config (`[models.stages]`) and per-provider API keys
- Lock file: `lockfile.json` (JSON format)
- Model string format: `provider:model_name` (e.g. `anthropic:claude-sonnet-4-20250514`, `api:MiniMax-M2.7-highspeed`)
- LLMClient lifecycle: use as context manager or call `.close()` to release the httpx connection pool
- Shared ModelCapabilityRegistry: pass `capability_registry=` to LLMClient to avoid duplicate models.dev fetches across pipeline stages
- Type checking: `ty check src/` (ty v0.0.32+, installed via Homebrew) — run before committing alongside ruff
- All reference project licenses are MIT or Apache-2.0 — preserve original copyright notices
