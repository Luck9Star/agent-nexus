# Agent Nexus

<p align="center">
  <strong>MCP-native Agent Platform | Self-built Multi-Agent Orchestration | Git-based Distribution</strong>
</p>

---

Agent Nexus is an MCP-native agent platform providing self-built multi-agent orchestration infrastructure, a Python runtime execution layer, and a self-evolution engine. Agents are distributed via Git repositories (Homebrew tap model), run locally, and use user-configured models.

## Key Features

- **Self-built Orchestration** — Built by referencing ClawTeam's proven patterns (TaskStore, Mailbox, SpawnBackend), adapted and simplified. TaskGraph (SQLite + DAG + cycle detection), IPC (JSON-lines), ProcessManager (async subprocess + health checks)
- **MCP-native** — Each Agent ships with a FastMCP Server; MCP Gateway handles unified routing and discovery. MCP protocol boundary = language boundary
- **Git-based Distribution** — Official monorepo + private repos + direct URL. No cloud infrastructure needed. Homebrew tap model
- **Dual Implementation** — Python platform complete (Phases 1-6), Rust platform rewrite in progress (6 crates, ~18K LOC)
- **Self-Evolution Engine** — Based on OpenSpace design, three-tier progression: Atomic Skill Evolution → Composite Orchestration Evolution → Agent Promotion
- **User-configured Models** — Supports OpenAI, Anthropic, Ollama, and more. Free to use (bring your own API key)

## Architecture Overview

Four-layer architecture (top to bottom):

```
┌─────────────────────────────────────────────────┐
│  Layer 1: MCP Exposure (FastMCP per Agent)       │
│  MCP Gateway → routing, discovery, tool agg.     │
├─────────────────────────────────────────────────┤
│  Layer 2: Orchestration (self-built)              │
│  TaskGraph (SQLite DAG) + IPC (JSON-lines)        │
│  ProcessManager + OrchestrationDSL (TOML)         │
├─────────────────────────────────────────────────┤
│  Layer 3: Python Runtime (CaveAgent-based)        │
│  IPythonRuntime + SecurityChecker (AST)           │
├─────────────────────────────────────────────────┤
│  Layer 4: Self-Evolution Engine (OpenSpace-based) │
│  Skill → Orchestration → Agent Promotion          │
└─────────────────────────────────────────────────┘
```

## Agent System

| Type | Count | Examples |
|------|-------|---------|
| **Atomic Agent** | 11 | doc-filler, code-reviewer, security-scanner, test-suite-generator |
| **Composite Agent** | 5 | feature-delivery-pipeline, product-documentation-suite |

Three run modes: **MCP standalone** / **Platform Router dispatch** / **CLI standalone**

## Quick Start

### Prerequisites

- Python 3.11+
- [hatch](https://hatch.pypa.io/) (`pip install hatch`)
- Optional: Rust toolchain (for Rust platform development)

### Install & Run

```bash
# Clone the repo
git clone https://github.com/user/agent-nexus.git
cd agent-nexus

# Python platform
hatch env create          # Create dev environment
hatch run test            # Run tests

# Rust platform (optional)
cargo build               # Build all crates
cargo test                # Run Rust tests

# CLI usage
agent-nexus init          # Initialize config
agent-nexus install <agent>  # Install an Agent
agent-nexus run <agent>   # Run an Agent
agent-nexus status        # Check status
```

### Environment Variables

```bash
# Model config (priority: env > agent config > defaults)
export AGENT_MODEL=gpt-4o           # Default agent model
export DEFAULT_MODEL=gpt-4o         # Global default model
export OPENAI_API_KEY=sk-...        # OpenAI
export ANTHROPIC_API_KEY=sk-ant-... # Anthropic
export OLLAMA_BASE_URL=http://...   # Ollama local models
```

## Project Structure

```
agent-nexus/
├── src/agent_nexus/              # Python platform core (hatch editable install)
│   ├── platform/
│   │   ├── orchestration/        # TaskGraph, ProcessManager, IPC, DSL
│   │   ├── router/               # Platform Router (4-Phase Workflow)
│   │   ├── gateway/              # MCP Gateway
│   │   ├── config/               # Model config + Provider registry
│   │   ├── local/                # CLI + Git Installer + Supervisor
│   │   ├── skills/               # Skill Loader
│   │   ├── evolution/            # Self-Evolution Engine
│   │   └── runtime/              # Python Runtime
│   └── models/                   # Shared data models
├── agents/                       # Official Agent packages (independent pyproject.toml each)
│   ├── atomic/                   # 11 Atomic Agents
│   └── composite/                # 5 Composite Agents
├── crates/                       # Rust platform rewrite (in progress)
│   ├── ap-core/                  # Core: TaskGraph, StateMachine, IPC, Hooks
│   ├── ap-cli/                   # CLI: clap derive, 9 commands
│   ├── ap-gateway/               # MCP Gateway
│   ├── ap-fetcher/               # Git-based Agent distribution
│   ├── ap-evolution/             # Self-Evolution Engine (SQLite)
│   └── ap-runtime/               # Python subprocess bridge
├── tests/                        # Tests
│   ├── unit/                     # Unit tests
│   ├── integration/              # Integration tests
│   └── e2e/                      # End-to-end tests
├── templates/                    # OrchestrationDSL TOML templates
├── docs/                         # Design documents
├── Cargo.toml                    # Rust workspace
└── pyproject.toml                # Python package config
```

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Python Platform | Python 3.11+, Pydantic, FastMCP, Typer, asyncio |
| Rust Platform | Rust 2021, Tokio, Axum, Rusqlite, Clap, Git2 |
| Protocol | MCP (stdio/SSE), JSON-lines IPC, TOML DSL |
| Storage | SQLite (TaskGraph + Evolution), TOML (config) |
| Distribution | Git (Homebrew tap model) |

## Security Architecture (Defense-in-Depth)

1. **Process boundary** — Agents run as independent subprocesses
2. **PermissionChecker** — Pre-execution permission check (DEFAULT / PLAN / FULL_AUTO modes)
3. **SecurityChecker** — Runtime AST-level code safety analysis

## Documentation

Full design docs are in `docs/`. See `docs/README.md` for the navigation index.

| Document | Location |
|----------|----------|
| Product positioning & core architecture | `docs/01-overview.md` |
| Orchestration layer design | `docs/02-clawteam-integration.md` |
| Python Runtime | `docs/03-python-runtime.md` |
| Self-Evolution Engine | `docs/04-self-evolution.md` |
| Agent system | `docs/05-agent-system.md` |
| MCP communication matrix | `docs/06-mcp-communication.md` |
| Git distribution & quality gates | `docs/07-marketplace.md` |
| Constraints & decisions | `docs/08-constraints-decisions.md` |
| Implementation plan | `docs/09-implementation-plan.md` |

## License

MIT License
