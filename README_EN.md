# Agent Nexus

English | **[中文](README.md)**

> MCP-native Agent Platform -- Self-Built Orchestration, Git Distribution, Self-Evolution Engine

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests: 1793](https://img.shields.io/badge/tests-1793_passing-brightgreen.svg)]()

Agent Nexus is an **MCP-native** intelligent agent platform built on a four-layer architecture. Users run Agents locally with their own model configuration (OpenAI / Anthropic / Ollama / Chinese providers all supported).

**Key highlights:**

- **Self-Built Orchestration** -- TaskGraph (SQLite WAL) + IPC (JSON-lines) + ProcessManager (asyncio.subprocess) + OrchestrationDSL (TOML DAG), referencing ClawTeam for proven patterns
- **Git-Based Distribution** -- Homebrew Tap model: official monorepo + private repos + direct URLs. No cloud infrastructure required
- **Python Runtime** -- IPython kernel execution + AST-level security checks. Agents are always Python internally; platform layer is planned for Rust rewrite
- **Self-Evolution Engine** -- FIX / DERIVED / CAPTURED skill evolution + CompactionGuard context protection + Agent Promotion

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Configuration Guide](#configuration-guide)
- [CLI Commands](#cli-commands)
- [Agent Catalog](#agent-catalog)
- [Agent Development Guide](#agent-development-guide)
- [Self-Evolution Engine](#self-evolution-engine)
- [Security Architecture](#security-architecture)
- [Testing](#testing)
- [Tech Stack](#tech-stack)
- [Design Documents](#design-documents)
- [License](#license)

---

## Architecture Overview

Agent Nexus uses a four-layer architecture, from external communication down to the evolution engine:

```
+---------------------------------------------+
|          MCP Exposure Layer                  |  FastMCP Server per Agent
|          MCP Gateway aggregation & routing   |  DeferredAgentRegistry lazy loading
+---------------------------------------------+
|          Orchestration Layer                 |  TaskGraph (SQLite WAL + blocked_by + cycle detection)
|          Self-Built Orchestration            |  IPC (stdin/stdout JSON-lines)
|                                              |  ProcessManager (asyncio.subprocess)
|                                              |  OrchestrationDSL (TOML DAG)
+---------------------------------------------+
|          Python Runtime Layer                |  IPython InteractiveShell kernel execution
|          CaveAgent-based                     |  SecurityChecker AST-level code safety analysis
|                                              |  L0-L3 four-tier progressive context loading
+---------------------------------------------+
|          Self-Evolution Engine               |  Atomic Skill Evolution (FIX/DERIVED/CAPTURED)
|          OpenSpace-based                     |  Composite Orchestration Evolution
|                                              |  Agent Promotion (skill -> standalone agent)
+---------------------------------------------+
```

**Layer responsibilities:**

| Layer | Role | Key Components |
|-------|------|---------------|
| **MCP Exposure** | External communication | FastMCP Server per Agent, MCP Gateway for routing/discovery, DeferredAgentRegistry |
| **Orchestration** | Multi-Agent coordination | TaskGraph (DAG + state machine + cycle detection), IPC (JSON-lines), ProcessManager, OrchestrationDSL (TOML) |
| **Python Runtime** | In-process code execution | IPython InteractiveShell, SecurityChecker (AST), Variables/Functions/Types persistence |
| **Self-Evolution** | Skill and orchestration improvement | ExecutionAnalyzer, SkillEvolver, SkillStore, Agent Promotion pipeline |

**Communication matrix:**

| Scenario | Protocol |
|----------|----------|
| Agent internal code execution | Python Runtime (IPython) |
| Agent-to-Agent | IPC (stdin/stdout JSON-lines), Platform Router mediates |
| Agent-to-external frameworks | MCP Server (stdio / SSE) |
| Agent-to-external APIs | MCP Tool Call |
| Platform Router to Agent | stdin/stdout JSON-lines |
| Remote Agent (future) | MCP SSE |

---

## Installation

### Prerequisites

- Python 3.11 or later
- [uv](https://docs.astral.sh/uv/) (recommended) or pip
- Git

### From Source (Development)

```bash
# Clone the repository
git clone https://github.com/anthropics/agent-nexus.git
cd agent-nexus

# Create virtual environment and install with dev dependencies
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Via pip

```bash
pip install -e ".[dev]"
```

### Verify Installation

```bash
agent-nexus --help
agent-nexus list
```

---

## Quick Start

### 1. Configure a model provider

Set at least one API key as an environment variable:

```bash
export OPENAI_API_KEY="sk-..."
# or
export ANTHROPIC_API_KEY="sk-ant-..."
# or use a local model
export OLLAMA_BASE_URL="http://localhost:11434"
```

### 2. Install an Agent

```bash
agent-nexus install doc-filler
```

### 3. Run the Agent

```bash
# MCP standalone mode (default)
agent-nexus run doc-filler --mode mcp

# CLI interactive mode (for testing)
agent-nexus run doc-filler --mode cli

# Router mode (for orchestration)
agent-nexus run doc-filler --mode router
```

---

## Configuration Guide

### Configuration Priority

Settings are resolved in the following order (highest priority first):

1. **Environment variables** -- `AGENT_MODEL`, `DEFAULT_MODEL`, etc.
2. **config.toml** -- `~/.agent-nexus/config.toml`
3. **Built-in defaults** -- See `src/agent_nexus/platform/config/defaults.py`

### Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `OPENAI_API_KEY` | OpenAI API key | `sk-...` |
| `ANTHROPIC_API_KEY` | Anthropic API key | `sk-ant-...` |
| `DEEPSEEK_API_KEY` | DeepSeek API key | `sk-...` |
| `DASHSCOPE_API_KEY` | Alibaba Qwen API key | `sk-...` |
| `MINIMAX_API_KEY` | MiniMax API key | `...` |
| `OLLAMA_BASE_URL` | Ollama local endpoint | `http://localhost:11434` |
| `AGENT_MODEL` | Override default model (highest priority) | `anthropic:claude-sonnet-4-20250514` |
| `DEFAULT_MODEL` | Default model (second priority) | `openai:gpt-4o` |
| `AGENT_NEXUS_HOME` | Platform config directory | `~/.agent-nexus` |
| `AGENT_MCP_MODE` | MCP transport mode | `sse` (default: stdio) |

### config.toml

The main configuration file lives at `~/.agent-nexus/config.toml`:

```toml
[models]
default = "openai:gpt-4o"

[models.providers.openai]
api_key_env = "OPENAI_API_KEY"

[models.providers.anthropic]
api_key_env = "ANTHROPIC_API_KEY"
api = "anthropic-messages"

[models.providers.ollama]
base_url = "http://localhost:11434/v1"

[models.providers.deepseek]
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"

[models.providers.qwen]
base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
api_key_env = "DASHSCOPE_API_KEY"

[runtime]
python_path = "python3"
uv_path = "uv"
```

**Built-in provider presets** (can be overridden in config.toml):

| Provider | API Type | Key Env Var | Default Base URL |
|----------|----------|-------------|-----------------|
| openai | openai-compatible | `OPENAI_API_KEY` | (SDK default) |
| anthropic | anthropic-messages | `ANTHROPIC_API_KEY` | (SDK default) |
| deepseek | openai-compatible | `DEEPSEEK_API_KEY` | `https://api.deepseek.com/v1` |
| minimax | anthropic-messages | `MINIMAX_API_KEY` | `https://api.minimax.chat/v1` |
| qwen | openai-compatible | `DASHSCOPE_API_KEY` | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| ollama | openai-compatible | (none needed) | `http://localhost:11434/v1` |

### Model Tiers

Agents declare a recommended model tier. The platform maps tiers to concrete model strings:

| Tier | Default Model | Use Case | Example Agents |
|------|--------------|----------|---------------|
| **lightweight** | `openai:gpt-4o-mini` | Fast tasks, extraction | doc-filler (filling) |
| **standard** | `openai:gpt-4o` | General tasks | api-doc-generator, security-scanner |
| **powerful** | `anthropic:claude-sonnet-4-20250514` | Complex reasoning | requirements-analyzer |
| **premium** | `anthropic:claude-opus-4-20250116` | Deep analysis, code review | code-reviewer, contract-analyzer |

Override the tier-to-model mapping in config.toml:

```toml
[models.tiers]
lightweight = "ollama:llama3"
standard = "openai:gpt-4o"
powerful = "anthropic:claude-sonnet-4-20250514"
premium = "anthropic:claude-opus-4-20250116"
```

### sources.yaml

Agent package sources are configured in `~/.agent-nexus/sources.yaml`:

```yaml
sources:
  - name: official
    type: git
    url: https://github.com/agent-nexus/official-packages
    branch: main

  - name: team-tap
    type: git
    url: git@github.com:my-team/agent-tap.git
    branch: main

  - name: experimental
    type: git
    url: https://github.com/agent-nexus/experimental
    branch: dev
```

### Config Directory Layout

`~/.agent-nexus/` (or `$AGENT_NEXUS_HOME`):

```
~/.agent-nexus/
  config.toml         # Platform configuration
  sources.yaml        # Package source registry
  lockfile.json       # Installed agent tracking (commit SHA, version)
  agents/             # Installed agent packages
  venvs/              # Per-agent virtual environments
  cache/repos/        # Cached git repositories
  runtimes/           # Runtime state
  logs/               # Platform and agent logs
```

---

## CLI Commands

### Install and Manage Agents

```bash
# Install from official source
agent-nexus install doc-filler

# Install a specific version
agent-nexus install doc-filler --version 1.2.0

# Install from a direct git URL
agent-nexus install my-agent --source https://github.com/org/agent-repo.git

# Uninstall an agent
agent-nexus uninstall doc-filler

# Update a single agent to latest version
agent-nexus update doc-filler

# Update all installed agents
agent-nexus update --all
```

### Discovery

```bash
# List installed agents
agent-nexus list

# Search for agents across all sources
agent-nexus search "security"
agent-nexus search "document"

# Show detailed info about an agent
agent-nexus info doc-filler
```

Output of `agent-nexus info`:

```
Agent: doc-filler
  Version:      1.0.0
  Type:         atomic
  Source:       official
  Commit SHA:   a1b2c3d4e5f6
  Installed at: 2025-04-18T10:30:00
  Venv:         ~/.agent-nexus/venvs/doc-filler

  Description:  Word document template filling specialist

  SKILL.md preview:
    # doc-filler -- Word document template filling specialist
    ## Role
    ...
```

### Package Sources

```bash
# List configured sources
agent-nexus sources list

# Add a private source
agent-nexus sources add --name internal --url https://github.com/myorg/agents.git

# Add a source with explicit type
agent-nexus sources add --name staging --url git@github.com:org/staging.git --type private

# Remove a source
agent-nexus sources remove internal
```

### Run Agents

```bash
# MCP standalone mode (runs as MCP Server, stdio transport)
agent-nexus run doc-filler --mode mcp

# MCP standalone with SSE transport
agent-nexus run doc-filler --mode mcp --transport sse

# CLI standalone mode (direct interaction, for development)
agent-nexus run doc-filler --mode cli

# Router mode (via Platform Router + MCP Gateway)
agent-nexus run doc-filler --mode router

# Router mode with SSE transport
agent-nexus run doc-filler --mode router --transport sse
```

| Mode | `--mode` | `--transport` | Description |
|------|----------|--------------|-------------|
| MCP Standalone | `mcp` (default) | `stdio` (default) or `sse` | Agent runs as standalone MCP Server |
| Platform Router | `router` | `stdio` or `sse` | Orchestrated by Platform Router, exposed via MCP Gateway |
| CLI Standalone | `cli` | n/a | Direct CLI interaction for development/testing |

### Quality Check (for Agent developers)

```bash
# Validate an agent package before publishing
agent-nexus check ./my-agent
```

---

## Agent Catalog

### 10 Atomic Agents

Each Atomic Agent is a single-purpose specialist with deep domain optimization:

| Agent | Domain | Model Tier | Key Differentiator |
|-------|--------|-----------|-------------------|
| **doc-filler** | Document / Template Automation | Lightweight/Standard | Two-stage pipeline (analyze + fill), style inheritance chain processing |
| **requirements-analyzer** | Software Engineering - Requirements | Powerful | Multi-turn dialogue with tracked questioning strategy |
| **code-reviewer** | Software Engineering - Code Quality | Premium | Per-language rule database, cross-file reasoning |
| **api-doc-generator** | Software Engineering - Documentation | Standard | OpenAPI 3.1 standard generation |
| **security-scanner** | Quality/Security - AppSec | Standard | OWASP Top 10 pattern matching |
| **accessibility-auditor** | Quality/Security - Accessibility | Lightweight/Standard | WCAG 2.2 AA 87-criteria compliance |
| **localization-specialist** | Document/Content - Localization | Standard | Glossary management, register detection |
| **contract-analyzer** | Document/Content - Legal Analysis | Premium | Cross-clause dependency understanding, multi-jurisdiction compliance |
| **market-intelligence-analyst** | Research/Analysis - Market Research | Standard | Porter/SWOT/PESTEL methodology |
| **test-suite-generator** | Software Engineering - Testing | Standard | AST parsing + per-paradigm test strategy |

### 5 Composite Agents

Composite Agents orchestrate multiple Atomic Agents via TOML DAG:

| Agent | Orchestration Pattern | Dependencies (Atomic Agents) |
|-------|----------------------|------------------------------|
| **feature-delivery-pipeline** | Sequential then Parallel | requirements-analyzer then [api-doc-generator + test-suite-generator + code-reviewer] |
| **document-compliance-gateway** | Full Parallel | [contract-analyzer + accessibility-auditor + localization-specialist] then conflict detection |
| **cicd-quality-gate** | Full Parallel | [security-scanner + code-reviewer + test-suite-generator] then quality decision |
| **competitive-intelligence-briefing** | Sequential Chain | market-intelligence-analyst then doc-filler then localization-specialist |
| **product-documentation-suite** | Parallel then Sequential | [api-doc-generator + code-reviewer] then localization-specialist |

---

## Agent Development Guide

### Agent Package Structure

**Atomic Agent:**

```
my-agent/
  agent-manifest.yaml    # Metadata, permissions, model config
  SKILL.md               # Three-tier behavioral definition
  agent.py               # PydanticAI core logic
  tools/                 # Domain-specific tools
  hooks/                 # Lifecycle hooks (hooks.yaml)
  mcp_servers/           # External MCP Server dependencies
  mcp_adapter.py         # MCP Server adapter
  local_adapter.py       # Local mode adapter (stdin/stdout JSON-lines)
  main.py                # Entry point (auto-detects run mode)
  models.py              # Pydantic data models
  pyproject.toml         # Package configuration
  tests/
    test_agent.py
```

**Composite Agent:**

```
my-composite/
  agent-manifest.yaml    # Metadata + dependency declarations
  SKILL.md               # Includes orchestration description
  composition.toml       # Orchestration DAG definition
  hooks/
    hooks.yaml
  mcp_adapter.py
  main.py
  pyproject.toml
  tests/
    test_composition.py
```

### agent-manifest.yaml

The manifest declares an agent's identity, permissions, model preferences, and dependencies:

```yaml
name: doc-filler
version: 1.0.0
type: atomic              # atomic | composite
description: Word document template filling specialist

model_config:
  recommended: "standard" # lightweight/standard/powerful/premium
  fallback: "lightweight"

permissions:
  mode: default           # default | plan | full_auto
  allowed_tools: [file_read, file_write]
  denied_tools: [bash]
  path_rules:
    - pattern: "*.docx"
      access: read-write

# Composite Agents must declare atomic_agents dependencies
# dependencies:
#   atomic_agents:
#     - requirements-analyzer
#     - api-doc-generator

# External MCP Server dependencies
mcp_servers:
  filesystem:
    transport: stdio
    command: "uvx"
    args: ["mcp-server-filesystem"]

# Lifecycle hooks
hooks:
  pre_execution:
    - type: prompt
      prompt: "Verify input file exists and is .docx format"
      block_on_failure: true
  post_execution:
    - type: command
      command: "notify-send 'Document filled'"
      block_on_failure: false
```

### SKILL.md (Three-Tier Behavioral Definition)

SKILL.md follows a progressive loading pattern inspired by deer-flow:

| Tier | Content | Loaded When |
|------|---------|-------------|
| **Metadata** | name, agent_type, triggers, capabilities | Immediately (YAML frontmatter) |
| **Body** | role, workflow, constraints | Before first interaction |
| **Resources** | examples, templates, references | On demand |

Example:

```markdown
---
name: requirements-analyzer
agent_type: atomic
description: Multi-turn dialogue requirement analysis
triggers:
  - requirement analysis
  - extract requirements
capabilities: [requirements-analysis, structured-output, web-search]
model_config:
  recommended: "powerful"
  fallback: "default"
---

# Requirements Analyzer Agent

## Role
You are a professional requirements analyst...

## Workflow
1. Receive initial user requirements
2. Multi-turn clarification (one question at a time)
3. Optional: search industry background
4. Generate structured requirements specification

## Constraints
- Ask one question at a time
- Max 12 questions, auto-summarize at threshold
- Mark unverified content as "pending confirmation"
```

### composition.toml (Orchestration DSL)

Composite Agents define their DAG via TOML. Tasks with empty `blocked_by` run immediately; tasks blocked on others run in parallel once their dependencies complete:

```toml
[composition]
name = "feature-delivery-pipeline"
description = "Requirement-driven parallel generation of API docs, test suites, and code reviews"

[tasks.task1]
name = "requirements-analysis"
agent = "requirements-analyzer"
blocked_by = []

[tasks.task2]
name = "api-doc-generation"
agent = "api-doc-generator"
blocked_by = ["task1"]

[tasks.task3]
name = "test-suite-generation"
agent = "test-suite-generator"
blocked_by = ["task1"]

[tasks.task4]
name = "code-review"
agent = "code-reviewer"
blocked_by = ["task1"]
```

Full TOML schema reference:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `[composition]` | section | Yes | Composition metadata |
| `composition.name` | string | Yes | Name of the Composite Agent |
| `composition.description` | string | Yes | Description |
| `[[tasks]]` | array | Yes | Task list |
| `tasks[].id` | string | Yes | Task ID (TOML key) |
| `tasks[].name` | string | Yes | Task display name |
| `tasks[].agent` | string | Yes | Agent to assign |
| `tasks[].blocked_by` | array | No | Blocking task IDs |
| `tasks[].vars` | object | No | Task variables |
| `[tool_loading]` | section | No | Global tool loading strategy |
| `tool_loading.strategy` | string | No | eager / lazy / manifest_only |
| `tool_loading.preload_agents` | array | No | Agents to preload in eager mode |

### Run Mode Entry Point

Each Agent's `main.py` auto-detects the run mode:

```python
import os

def main():
    mode = os.getenv("AGENT_MODE", "mcp")

    if mode == "local":
        # Platform Router mode: stdin/stdout JSON-lines
        asyncio.run(serve(my_agent))
    elif mode == "cli":
        # CLI Standalone mode
        asyncio.run(run_cli(my_agent))
    else:
        # MCP Standalone mode (default)
        mcp_serve(my_agent)
```

### Agent Roles (Optional)

Agents can declare a role for preset tool constraints:

| Role | Tools | Recommended Model |
|------|-------|------------------|
| **explore** | glob, grep, file_read, web_fetch/search | lightweight |
| **plan** | file_read, glob, grep (read-only) | standard |
| **worker** | all tools | inherit |
| **verification** | file_read, file_write (temp only), glob, grep | standard |

### Lifecycle Hooks

Hooks inject custom logic at key execution points. Four hook types are supported:

| Type | Execution | Latency | Use Case |
|------|----------|---------|----------|
| **command** | Shell subprocess | Low | File validation, script checks |
| **http** | HTTP POST | Medium | CI/CD triggers, notifications |
| **prompt** | LLM short call (small model) | Medium | Quick validation, format checks |
| **agent** | LLM deep call (large model) | High | Complex reasoning, quality review |

Supported events: `pre_execution`, `post_execution`, `pre_tool_use`, `post_tool_use`, `on_error`, `on_evolution`.

### MCP Tool Naming Convention

When exposed via MCP Gateway, tools follow: `{agent-name}__{tool-name}`

| Agent | Tool | MCP Full Name |
|-------|------|--------------|
| doc-filler | analyze_template | `doc-filler__analyze_template` |
| doc-filler | fill_template | `doc-filler__fill_template` |
| code-reviewer | review_diff | `code-reviewer__review_diff` |

External MCP tools are bridged as: `mcp__{server_name}__{tool_name}`

### Testing Conventions

```bash
# Run tests for a specific agent
pytest agents/atomic/my-agent/tests/ -v

# Run all tests
pytest tests/ agents/ -v

# Quality check before publishing
agent-nexus check ./my-agent
```

---

## Self-Evolution Engine

Agent Nexus has a built-in three-tier self-evolution system inspired by OpenSpace:

### Tier 1: Atomic Skill Evolution

Runtime metric-driven skill-level evolution within each Atomic Agent:

| Mode | Trigger | Result |
|------|---------|--------|
| **FIX** | Skill selected but fails | In-place update of SKILL.md (same name, same directory) |
| **DERIVED** | Successful pattern can be enhanced | New skill (new directory, new name, supports merging) |
| **CAPTURED** | Task succeeds without any skill | Brand new skill extracted from the successful interaction |

Key metrics tracked per skill: `applied_rate`, `completion_rate`, `fallback_rate`, `effective_rate`.

### Tier 2: Composite Orchestration Evolution

Optimizes the DAG topology of Composite Agents based on execution history:

- Analyzes call chain efficiency, parallelization opportunities, and missing steps
- `DERIVED`: Optimizes TOML templates (adjust agent order, parallel strategies)
- `CAPTURED`: Creates new Composite Agents from successful orchestration patterns

### Tier 3: Agent Promotion

Promotes high-performing skills to standalone Agents:

- Conditions: `effective_rate > 0.8` + `total_selections > 50` + independent workflow
- Auto-generates: `SKILL.md` + `agent.py` + `agent-manifest.yaml`
- Registers as MCP Server and publishes to Git source

### Health Threshold Rules

| Trigger Condition | Evolution Type | Description |
|-------------------|---------------|-------------|
| `fallback_rate > 0.4` | FIX | Skill frequently selected but not applied |
| `applied_rate > 0.4` AND `completion_rate < 0.35` | FIX | High application rate but low completion |
| `effective_rate < 0.55` AND `applied_rate > 0.25` | DERIVED | Moderate effectiveness, needs enhancement |
| `effective_rate > 0.8` AND `selections > 50` | Promotion | Ready for promotion to standalone Agent |

### Anti-Loop Safeguards

- Three evolution triggers with built-in anti-loop: Post-Analysis, Tool Degradation, Periodic Metric Check
- Apply-Retry limit: max 5 rounds per evolution cycle
- CompactionGuard: `min_turns_between_compactions=5` to prevent positive feedback loops

---

## Security Architecture

Defense-in-depth with three independent security layers:

### 1. Process Boundary

Agents run as independent subprocesses managed by ProcessManager (asyncio.subprocess). Each agent has its own virtual environment. No shared memory between agents.

### 2. PermissionChecker (Pre-Execution)

Three permission modes control what agents can do before any execution:

| Mode | Behavior |
|------|----------|
| **default** | Prompts user for approval on sensitive operations |
| **plan** | Plans and presents actions for approval before executing |
| **full_auto** | Executes without prompts (use with caution) |

Configured in `agent-manifest.yaml` via `permissions.mode` and `permissions.allowed_tools` / `denied_tools`.

### 3. SecurityChecker (Runtime AST-Level)

Analyzes generated Python code at the AST level before execution:

| Rule Type | What It Checks | Example |
|-----------|---------------|---------|
| `ImportRule` | Blocked module imports | `os`, `subprocess`, `sys`, `socket` |
| `FunctionRule` | Blocked function calls | `eval()`, `exec()`, `compile()` |
| `AttributeRule` | Blocked attribute access | `__import__`, `__builtins__` |
| `RegexRule` | Pattern-based blocking | Shell injection patterns |

---

## Testing

```bash
# Run all tests (platform + agents)
pytest tests/ agents/ -v

# Platform tests only
pytest tests/ -v

# Single agent tests
pytest agents/atomic/doc-filler/tests/ -v

# Coverage report
pytest tests/ --cov=agent_nexus --cov-report=html

# Run specific test categories
pytest tests/unit/ -v
pytest tests/integration/ -v
pytest tests/e2e/ -v
```

Current test coverage: **1793 tests all passing**, covering all platform modules and Agent packages.

---

## Tech Stack

| Component | Technology | Notes |
|-----------|-----------|-------|
| Platform Core | Python 3.11+ | POC phase |
| Data Models | Pydantic v2 (frozen) | All-immutable models |
| Agent Framework | PydanticAI | Agent logic and tool definitions |
| MCP Server | FastMCP | per-Agent MCP exposure |
| CLI | Typer | install/run/list/search/info/sources |
| Persistence | SQLite WAL | TaskGraph concurrent safety |
| Runtime | IPython InteractiveShell | Kernel execution |
| Config | TOML + YAML | config.toml + sources.yaml |
| Production Rewrite | Rust | Upper layers only (Gateway/Fetcher/Supervisor/CLI), Agent Runtime stays Python |

---

## Project Structure

```
agent-nexus/
+-- src/agent_nexus/          # Platform core
|   +-- models/               # Shared data models (10 files, 58 Pydantic types)
|   +-- platform/
|       +-- router/           # Platform Router (4-Phase Workflow)
|       +-- orchestration/    # TaskGraph + IPC + ProcessManager + DSL
|       +-- gateway/          # MCP Gateway aggregation + DeferredRegistry
|       +-- config/           # Model config + Provider registry
|       +-- local/            # CLI + Git Installer + Supervisor
|       +-- skills/           # Skill Loader
|       +-- evolution/        # Self-Evolution Engine (6 modules)
|       +-- runtime/          # Python Runtime (IPython + SecurityChecker)
+-- agents/                   # Agent packages
|   +-- atomic/               # 10 Atomic Agents
|   +-- composite/            # 5 Composite Agents
+-- tests/                    # Platform tests (unit + integration + e2e)
+-- templates/                # OrchestrationDSL TOML templates
+-- docs/                     # Design documents (POC v5.2, Chinese)
+-- crates/                   # Rust rewrite (future)
+-- pyproject.toml
```

---

## Design Documents

All design documents are in `docs/`, POC v5.2 (Chinese):

| Topic | File |
|-------|------|
| Product positioning and core architecture | `docs/01-overview.md` |
| Self-built orchestration layer | `docs/02-clawteam-integration.md` |
| Python Runtime | `docs/03-python-runtime.md` |
| Self-Evolution Engine | `docs/04-self-evolution.md` |
| Agent system | `docs/05-agent-system.md` |
| MCP exposure and communication | `docs/06-mcp-communication.md` |
| Agent distribution and quality gates | `docs/07-marketplace.md` |
| Constraints and decisions | `docs/08-constraints-decisions.md` |
| 7-phase implementation plan | `docs/09-implementation-plan.md` |
| Git distribution and local architecture | `docs/10-cloud-local-architecture.md` |
| TOML Schema and references | `docs/appendix.md` |

---

## License

[MIT](LICENSE)
