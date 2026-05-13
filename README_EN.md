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

## Model Capability System

Three-layer model capability resolution:

```
Built-in Data (17 models)  →  optional models.dev enrichment  →  LLMClient consumption
```

- **Dynamic max_tokens**: Reads from capability data instead of hardcoded 4096
- **Temperature clamping**: Clamps to model's `[temperature_min, temperature_max]` range
- **`supports_temperature` gate**: Skips temperature/top_p for models that don't support them
- **models.dev enrichment**: Auto-fetched on init, silent fallback to built-in data on failure
- **Auto model inference**: Extracts real model name from API response to self-correct capability data

## Agency Expert Orchestration

Agent Nexus integrates the [agency-agents](https://github.com/nicepkg/agency-agents) expert pool with dynamic capability decomposition, specialist selection, and concurrent execution:

```
User Task → Capability Inference → Specialist Selection → DAG Build → Concurrent LLM Execution → Integration → QA Gate
```

### Quick Start

See the [Quick Start Guide](docs/quick-start.md) for a 5-minute setup walkthrough. For full configuration options, see [Configuration Reference](docs/configuration.md).

### Pipeline Stages

| Stage | Module | Description |
|-------|--------|-------------|
| Import | `AgencyImporter` | Import expert profiles from vendor repo with allowlist filtering |
| Registry | `ExpertRegistry` | Capability-indexed expert registry with set-cover selection |
| Inference | `LLMPlanner` / `infer_capabilities()` | Natural language → capability labels (LLM + keyword fallback) |
| Selection | `SpecialistSelector` | Greedy set-cover for optimal expert team composition |
| Planning | `DynamicCompositePlanner` | DAG construction based on capability subset relationships |
| Execution | `LLMExecutor` + `DAGDispatcher` | Concurrent LLM calls via ThreadPoolExecutor |
| Integration | `LLMIntegrator` / `Integrator` | Multi-expert result synthesis (LLM + rule-based fallback) |
| Validation | `LLMQualityGate` / `QAGate` | Semantic quality evaluation + structural compliance |
| Capability | `ModelCapabilityRegistry` | Dynamic max_tokens/temperature/vision from built-in data + models.dev |

### CLI Commands

See [CLI Reference](docs/cli.md) for the complete command documentation (17 commands).

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
uv run ruff check --fix src/ agents/
uv run ruff format src/ agents/

# Type Check
uv run ty check src/              # ty v0.0.32+ (brew install ty)

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
│   ├── atomic/               # 20 Atomic Agents
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

- **Config file**: `~/.agent-nexus/config.toml` — supports per-stage model config (`[models.stages]`)
- **Model string format**: `provider:model_name` (e.g. `anthropic:claude-sonnet-4-20250514`)
- **Supported API formats**: `anthropic-messages`, `openai-compatible`, `ollama`
- **Model priority** (highest to lowest): Expert profile `model` field → CLI `--model` → `[models.stages]` → `AGENT_MODEL` env → `[models].default`
- **Environment variables**: `AGENT_MODEL`, `DEFAULT_MODEL`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OLLAMA_BASE_URL`

## Key Design Decisions

- **Self-built orchestration**: Reference ClawTeam patterns (TaskStore, MailboxManager, SpawnBackend) — no external pip dependencies
- **MCP boundary = language boundary**: Rust platform communicates with Python agent subprocesses via MCP stdio/SSE
- **Git-based distribution**: Homebrew tap model — no cloud infrastructure required
- **Rust rewrite scope**: Upper layers only (Gateway, Fetcher, Evolution, CLI). Agent Runtime stays Python

## License

MIT
