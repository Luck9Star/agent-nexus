# Agent Nexus

MCP-native Agent Platform with self-built orchestration and Git-based distribution. Provides agent lifecycle management, expert capability orchestration, Python runtime execution, and self-evolution engine.

## Architecture

Four-layer architecture (top to bottom):

```
┌─────────────────────────────────────────────┐
│           MCP Exposure Layer                │  FastMCP Server + Gateway routing
├─────────────────────────────────────────────┤
│           Orchestration Layer               │  TaskGraph · IPC · ProcessManager · DSL
├─────────────────────────────────────────────┤
│           Runtime Layer                     │  Python Runtime (CaveAgent IPython)
├─────────────────────────────────────────────┤
│           Evolution Engine                  │  Atomic Skill → Composite → Agent Promotion
└─────────────────────────────────────────────┘
```

- **Agent types**: Atomic (11) + Composite (5)
- **Run modes**: MCP standalone / Platform Router / CLI standalone
- **Dual implementation**: Python platform (production) + Rust platform rewrite (6 crates, in progress)

## Agency Expert Orchestration

Agent Nexus integrates the [agency-agents](https://github.com/nicepkg/agency-agents) expert pool with dynamic capability decomposition, specialist selection, and concurrent execution:

```
User Task → Capability Inference → Specialist Selection → DAG Build → Concurrent LLM Execution → Integration → QA Gate
```

### Quick Start

**1. Configure LLM API**

Edit `~/.agent-nexus/config.toml`:

```toml
[models]
default = "api:MiniMax-M2.7-highspeed"

[models.providers.api]
base_url = "http://your-api-endpoint:3006"
api_key_env = "API_API_KEY"
api = "anthropic-messages"   # or "openai-compatible"
```

Set API key in `~/.agent-nexus/.env`:

```
API_API_KEY="sk-your-api-key"
```

**2. Prepare expert repository**

```bash
# agency-agents as vendor dependency
git submodule add https://github.com/nicepkg/agency-agents.git vendor/agency-agents
```

**3. Run expert orchestration**

```bash
# List available experts
uv run python -m agent_nexus.platform.agency.cli list-experts \
  --vendor-path vendor/agency-agents \
  --allowlist config/agency-agents.allowlist.yaml

# Plan DAG (no LLM execution)
uv run python -m agent_nexus.platform.agency.cli plan-composition \
  --task "Review payment system security and architecture design" \
  --vendor-path vendor/agency-agents \
  --allowlist config/agency-agents.allowlist.yaml

# Full execution: orchestrate → LLM calls → integration → QA
uv run python -m agent_nexus.platform.agency.cli run-composition \
  --task "Review payment system security and architecture design" \
  --vendor-path vendor/agency-agents \
  --allowlist config/agency-agents.allowlist.yaml \
  --max-parallel 3
```

### Pipeline Stages

| Stage | Module | Description |
|-------|--------|-------------|
| Import | `AgencyImporter` | Import expert profiles from vendor repo with allowlist filtering |
| Registry | `ExpertRegistry` | Capability-indexed expert registry with set-cover selection |
| Inference | `infer_capabilities()` | Natural language → capability labels (EN + CN) |
| Selection | `SpecialistSelector` | Greedy set-cover for optimal expert team composition |
| Planning | `DynamicCompositePlanner` | DAG construction based on capability subset relationships |
| Execution | `LLMExecutor` + `DAGDispatcher` | Concurrent LLM calls via ThreadPoolExecutor |
| Integration | `Integrator` | Multi-expert result merging |
| Validation | `QAGate` | Output compliance checking |

### CLI Commands

| Command | Description |
|---------|-------------|
| `import-experts` | Import expert profiles (supports dry-run) |
| `list-experts` | Preview available experts |
| `plan-composition` | Plan orchestration DAG (no execution) |
| `run-composition` | Full orchestration execution (LLM + concurrent + QA) |
| `check-profiles` | Validate imported profiles |
| `validate-output` | Validate expert output compliance |

## Installation

```bash
# Python platform
git clone https://github.com/Luck9Star/agent-nexus.git
cd agent-nexus
uv sync

# Rust platform (optional)
cargo build --workspace
cargo test --workspace
```

## Development

```bash
# Testing
uv run pytest tests/               # All
uv run pytest tests/ -m unit       # Unit tests
uv run pytest tests/ -m e2e        # E2E tests

# Lint & Format
uv run ruff check src/ agents/
uv run ruff format src/ agents/

# Rust
cargo test          # All crates
cargo clippy        # Lint
```

## Project Structure

```
agent-nexus/
├── src/agent_nexus/          # Platform core
│   ├── platform/
│   │   ├── agency/           # Expert orchestration pipeline
│   │   ├── orchestration/    # TaskGraph · ProcessManager · IPC · DSL
│   │   ├── gateway/          # MCP Gateway
│   │   ├── config/           # Model config + Provider registry
│   │   ├── runtime/          # Python Runtime
│   │   └── evolution/        # Self-evolution engine
│   └── models/               # Shared data models
├── agents/                   # Agent packages (independent pyproject.toml each)
│   ├── atomic/               # 11 Atomic Agents
│   └── composite/            # 5 Composite Agents
├── crates/                   # Rust platform rewrite
│   ├── ap-core/              # TaskGraph · ProcessManager · StateMachine · DSL
│   ├── ap-cli/               # CLI (clap derive)
│   ├── ap-gateway/           # MCP Gateway
│   ├── ap-fetcher/           # Git agent distribution
│   ├── ap-evolution/         # Self-evolution engine
│   └── ap-runtime/           # Python subprocess bridge
├── tests/                    # Tests
├── docs/                     # Design documents
├── config/                   # Config examples
└── vendor/agency-agents/     # Expert repository (submodule)
```

## Configuration

- **Model priority**: env vars > agent config > defaults
- **Environment variables**: `AGENT_MODEL`, `DEFAULT_MODEL`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL`
- **Config file**: `~/.agent-nexus/config.toml`
- **Supported API formats**: `anthropic-messages`, `openai-compatible`, `ollama`

## Key Design Decisions

- **Self-built orchestration**: Reference ClawTeam patterns (TaskStore, MailboxManager, SpawnBackend) — no external pip dependencies
- **MCP boundary = language boundary**: Rust platform communicates with Python agent subprocesses via MCP stdio/SSE
- **Git-based distribution**: Homebrew tap model — no cloud infrastructure required
- **Rust rewrite scope**: Upper layers only (Gateway, Fetcher, Evolution, CLI). Agent Runtime stays Python

## License

MIT
