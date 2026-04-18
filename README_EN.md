# Agent Nexus

English | **[中文](README.md)**

> MCP-native Agent Platform — Self-Built Orchestration · Git Distribution · Self-Evolution Engine

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests: 1793](https://img.shields.io/badge/tests-1793_passing-brightgreen.svg)]()

Agent Nexus is an **MCP-native** intelligent agent platform built on a four-layer architecture:

- **Self-Built Orchestration** — TaskGraph (SQLite WAL) + IPC (JSON-lines) + ProcessManager (asyncio.subprocess) + OrchestrationDSL (TOML DAG), referencing ClawTeam for proven patterns
- **Git-Based Distribution** — Homebrew Tap model: official monorepo + private repos + direct URLs. No cloud infrastructure required
- **Python Runtime** — IPython kernel execution + AST-level security checks. Agents are always Python internally; platform layer is planned for Rust rewrite
- **Self-Evolution Engine** — FIX / DERIVED / CAPTURED skill evolution + CompactionGuard context protection + Agent Promotion

Users run Agents locally with their own model configuration (OpenAI / Anthropic / Ollama / Chinese providers all supported).

---

## Four-Layer Architecture

```
┌─────────────────────────────────────────────┐
│          MCP Exposure Layer                 │  FastMCP Server per Agent
│          MCP Gateway aggregation & routing  │  DeferredAgentRegistry lazy loading
├─────────────────────────────────────────────┤
│          Orchestration Layer                │  TaskGraph (SQLite WAL + blocked_by + cycle detection)
│          Self-Built Orchestration           │  IPC (stdin/stdout JSON-lines)
│                                             │  ProcessManager (asyncio.subprocess)
│                                             │  OrchestrationDSL (TOML DAG)
├─────────────────────────────────────────────┤
│          Python Runtime Layer               │  IPython InteractiveShell kernel execution
│          CaveAgent-based                    │  SecurityChecker AST-level code safety analysis
│                                             │  L0-L3 four-tier progressive context loading
├─────────────────────────────────────────────┤
│          Self-Evolution Engine              │  Atomic Skill Evolution (FIX/DERIVED/CAPTURED)
│          OpenSpace-based                    │  Composite Orchestration Evolution
│                                             │  Agent Promotion (skill → standalone agent)
└─────────────────────────────────────────────┘
```

## Agent System

### 10 Atomic Agents (Single Specialized Capability)

| Agent | Domain | Model Tier | Key Differentiator |
|-------|--------|-----------|-------------------|
| **Doc Filler** | Document / Template Automation | Lightweight/Standard | Two-stage pipeline, style inheritance chain processing |
| **Requirements Analyzer** | Software Engineering - Requirements | Powerful | Multi-turn dialogue with tracked questioning strategy |
| **Code Reviewer** | Software Engineering - Code Quality | Premium | Per-language rule database, cross-file reasoning |
| **API Doc Generator** | Software Engineering - Documentation | Standard | OpenAPI 3.1 standard generation |
| **Security Scanner** | Quality/Security - AppSec | Standard | OWASP Top 10 pattern matching |
| **Accessibility Auditor** | Quality/Security - Accessibility | Lightweight/Standard | WCAG 2.2 AA 87-criteria compliance |
| **Localization Specialist** | Document/Content - Localization | Standard | Glossary management, register detection |
| **Contract Analyzer** | Document/Content - Legal Analysis | Premium | Cross-clause dependency understanding, multi-jurisdiction compliance |
| **Market Intelligence** | Research/Analysis - Market Research | Standard | Porter/SWOT/PESTEL methodology |
| **Test Suite Generator** | Software Engineering - Testing | Standard | AST parsing + per-paradigm test strategy |

### 5 Composite Agents (Multi-Agent Orchestration)

| Agent | Orchestration Pattern | Dependencies (Atomic Agents) |
|-------|----------------------|------------------------------|
| **Feature Delivery Pipeline** | Sequential → Parallel | Requirements → [API Doc + Test + Review] |
| **Document Compliance Gateway** | Full Parallel | [Legal + Accessibility + Localization] → Conflict Detection |
| **CI/CD Quality Gate** | Full Parallel | [Security + Code Review + Test] → Quality Decision |
| **Competitive Intel Briefing** | Sequential Chain | Market Intel → Doc Filler → Localization |
| **Product Documentation Suite** | Parallel → Sequential | [API Doc + Code Review] → Localization |

## Three Run Modes

| Mode | Description | Entry Point |
|------|-------------|-------------|
| **MCP Standalone** | Agent runs directly as an MCP Server | `uvx agent-name` |
| **Platform Router** | Orchestrated and managed via Platform Router | `agent-nexus run <name> --mode router` |
| **CLI Standalone** | Direct command-line interaction | `agent-nexus run <name> --mode cli` |

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/anthropics/agent-nexus.git
cd agent-nexus

# Create virtual environment and install
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Model Configuration

```bash
# Environment variables (highest priority)
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export OLLAMA_BASE_URL="http://localhost:11434"

# Or edit config file
mkdir -p ~/.agent-nexus
cat > ~/.agent-nexus/config.toml << 'EOF'
[models]
default = "gpt-4o"

[models.providers.openai]
api_key_env = "OPENAI_API_KEY"

[models.providers.ollama]
base_url = "http://localhost:11434"
EOF
```

### CLI Usage

```bash
# Install an Agent
agent-nexus install doc-filler

# Run an Agent
agent-nexus run doc-filler --mode mcp

# List installed Agents
agent-nexus list

# Search available Agents
agent-nexus search "security"

# Manage Agent sources
agent-nexus sources add --name internal --url https://github.com/myorg/agents.git
```

## Project Structure

```
agent-nexus/
├── src/agent_nexus/          # Platform core
│   ├── models/               # Shared data models (10 files, 58 Pydantic types)
│   ├── platform/
│   │   ├── router/           # Platform Router (4-Phase Workflow)
│   │   ├── orchestration/    # TaskGraph + IPC + ProcessManager + DSL
│   │   ├── gateway/          # MCP Gateway aggregation + DeferredRegistry
│   │   ├── config/           # Model config + Provider registry
│   │   ├── local/            # CLI + Git Installer + Supervisor
│   │   ├── skills/           # Skill Loader
│   │   ├── evolution/        # Self-Evolution Engine (6 modules)
│   │   └── runtime/          # Python Runtime (IPython + SecurityChecker)
├── agents/                   # Agent packages
│   ├── atomic/               # 10 Atomic Agents
│   └── composite/            # 5 Composite Agents
├── tests/                    # Platform tests (unit + integration + e2e)
├── templates/                # OrchestrationDSL TOML templates
├── docs/                     # Design documents (POC v5.2, Chinese)
└── pyproject.toml
```

## Self-Evolution Engine

Agent Nexus has built-in three-tier self-evolution capabilities:

1. **Atomic Skill Evolution** — Runtime metric-driven skill-level evolution
   - `FIX`: Repair broken/outdated skills (in-place, same name and directory)
   - `DERIVED`: Create enhanced versions (new directory, new name, supports multi-skill merging)
   - `CAPTURED`: Capture novel patterns as brand-new skills

2. **Composite Orchestration Evolution** — Orchestration-level optimization
   - Optimize DAG topology based on TaskGraph execution history
   - Automatically discover parallelizable bottleneck nodes

3. **Agent Promotion** — Promote skills to standalone Agents
   - Conditions: `effective_rate > 0.8` + `total_selections > 50` + independent workflow
   - Auto-generates `agent.toml` + `agent.py` + `SKILL.md`

### Health Threshold Rules

| Trigger Condition | Evolution Type | Description |
|-------------------|---------------|-------------|
| `fallback_rate > 0.4` | FIX | Skill frequently selected but not applied |
| `applied_rate > 0.4` AND `completion_rate < 0.35` | FIX | High application rate but low completion |
| `effective_rate < 0.55` AND `applied_rate > 0.25` | DERIVED | Moderate effectiveness, needs enhancement |
| `effective_rate > 0.8` AND `selections > 50` | Promotion | Ready for promotion to standalone Agent |

## Security Architecture (Defense-in-Depth)

1. **Process Boundary** — Agents run as independent subprocesses
2. **PermissionChecker** — Pre-execution permission checks (DEFAULT / PLAN / FULL_AUTO levels)
3. **SecurityChecker** — Runtime AST-level code safety analysis (import/function/attribute/regex rule categories)

## Testing

```bash
# Run all tests
pytest tests/ agents/ -v

# Platform tests only
pytest tests/ -v

# Single Agent tests
pytest agents/atomic/doc-filler/tests/ -v

# Coverage report
pytest tests/ --cov=agent_nexus --cov-report=html
```

Current test coverage: **1793 tests all passing**, covering all platform modules and Agent packages.

## Tech Stack

| Component | Technology | Notes |
|-----------|-----------|-------|
| Platform Core | Python 3.11+ | POC phase |
| Data Models | Pydantic v2 (frozen) | All-immutable models |
| MCP Server | FastMCP | per-Agent MCP exposure |
| CLI | Typer | install/run/list/search |
| Persistence | SQLite WAL | TaskGraph concurrent safety |
| Runtime | IPython InteractiveShell | Kernel execution |
| Config | TOML + YAML | config.toml + sources.yaml |
| Production Rewrite | Rust | Upper layers only (Gateway/Fetcher/Supervisor/CLI), Agent Runtime stays Python |

## Design Documents

All design documents are in `docs/`, POC v5.2 (Chinese):

| Topic | File |
|-------|------|
| Product positioning & core architecture | `docs/01-overview.md` |
| Self-built orchestration layer | `docs/02-clawteam-integration.md` |
| Python Runtime | `docs/03-python-runtime.md` |
| Self-Evolution Engine | `docs/04-self-evolution.md` |
| Agent system | `docs/05-agent-system.md` |
| MCP exposure & communication | `docs/06-mcp-communication.md` |
| Agent distribution & quality gates | `docs/07-marketplace.md` |
| Constraints & decisions | `docs/08-constraints-decisions.md` |
| 7-phase implementation plan | `docs/09-implementation-plan.md` |
| Git distribution & local architecture | `docs/10-cloud-local-architecture.md` |
| TOML Schema & references | `docs/appendix.md` |

## License

[MIT](LICENSE)
