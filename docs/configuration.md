# Configuration Reference

## Overview

Agent Nexus uses a two-level TOML configuration system:

```
~/.agent-nexus/config.toml        # Global (user-level) — always loaded
./agent-nexus.toml                # Project-level (optional) — overrides global
```

## Priority Chain

For each setting, the value from the highest-priority source wins:

```
CLI arguments > environment variables > .env file > project config > global config > built-in defaults
```

## Quick Start

```bash
# Generate default global config
agent-nexus init

# Set your API key
export OPENAI_API_KEY="sk-..."

# Verify
agent-nexus doctor
```

## Global Config: `~/.agent-nexus/config.toml`

### Full Schema

```toml
schema_version = "1.0"

[runtime]
python_path = "python3"          # Python binary path
uv_path = "uv"                   # uv package manager path

[models]
default = "openai:gpt-4o"        # Default model string (provider:model_name)

[models.stages]                   # Agency pipeline per-stage overrides
planning = "anthropic:claude-opus-4-20250116"
execution = "anthropic:claude-sonnet-4-20250514"
integration = "openai:gpt-4o"
qa = "anthropic:claude-sonnet-4-20250514"

[models.providers.openai]
api_key_env = "OPENAI_API_KEY"    # Env var name for API key
api = "openai-compatible"         # API type: openai-compatible | anthropic-messages | ollama

[models.providers.anthropic]
api_key_env = "ANTHROPIC_API_KEY"
api = "anthropic-messages"

sources = [
    {name = "official", type = "git", url = "https://github.com/anthropics/agent-nexus-packages.git", branch = "main"},
]
```

### Fields

#### `schema_version`
Version of the config schema. Currently `"1.0"`. Used by `agent-nexus init` for auto-migration.

#### `[runtime]`
| Field | Default | Description |
|-------|---------|-------------|
| `python_path` | `"python3"` | Path to Python 3.11+ binary |
| `uv_path` | `"uv"` | Path to the uv package manager |

#### `[models]`
| Field | Default | Description |
|-------|---------|-------------|
| `default` | `"openai:gpt-4o"` | Default model in `provider:model_name` format |

#### `[models.stages]`
Optional. Per-stage model overrides for the Agency pipeline:

| Stage | Purpose |
|-------|---------|
| `planning` | Task decomposition (LLMPlanner) |
| `execution` | Per-expert LLM calls (LLMExecutor) |
| `integration` | Semantic synthesis (LLMIntegrator) |
| `qa` | Quality evaluation (LLMQualityGate) |

#### `[models.providers.<name>]`
| Field | Default | Description |
|-------|---------|-------------|
| `api_key_env` | `""` | Environment variable holding the API key |
| `api` | `"openai-compatible"` | API protocol: `openai-compatible`, `anthropic-messages`, or `ollama` |
| `base_url` | `""` | Base URL override for the API endpoint |
| `streaming` | `true` | Enable streaming for this provider |

#### Streaming Configuration

`ModelConfig` supports `streaming_default` to set the global streaming preference. Individual `ProviderConfig` entries can override this with their `streaming` field.

```toml
[models]
streaming_default = true

[models.providers.anthropic]
streaming = true
```

#### Advanced Configuration Sections

| Section | Description |
|---------|-------------|
| `[cli_backends.*]` | Named CLI backend configurations for agent routing |
| `[cli_routing]` | CLI command routing rules |
| `[[mcp.external_servers]]` | External MCP server connections (array of tables) |

```toml
[cli_backends.my-backend]
type = "local"
command = "python"

[cli_routing]
default_backend = "my-backend"

[[mcp.external_servers]]
name = "external-tool"
transport = "stdio"
command = "npx"
args = ["-y", "some-mcp-server"]
```

#### `sources`
Array of agent package sources. Each entry:
| Field | Default | Description |
|-------|---------|-------------|
| `name` | required | Unique source identifier |
| `type` | `"git"` | Source type |
| `url` | `""` | Git repository URL |
| `branch` | `"main"` | Default branch |

## Project Config: `./agent-nexus.toml`

Optional. Place in your project root. Only the fields you want to override need to be present.

```toml
schema_version = "1.0"

[models]
default = "anthropic:claude-opus-4-20250116"

[models.stages]
planning = "anthropic:claude-opus-4-20250116"
execution = "anthropic:claude-sonnet-4-20250514"
```

All paths in project config are resolved relative to the config file's directory.

## Environment Variables

### API Keys (per-provider)

| Variable | Provider |
|----------|----------|
| `OPENAI_API_KEY` | OpenAI |
| `ANTHROPIC_API_KEY` | Anthropic |
| `ANTHROPIC_AUTH_TOKEN` | Anthropic (alt; used by `init` wizard key detection only) |
| `DEEPSEEK_API_KEY` | DeepSeek |
| `MINIMAX_API_KEY` | MiniMax |
| `DASHSCOPE_API_KEY` | Qwen (DashScope) |
| `OLLAMA_HOST` | Ollama host |

### Platform Settings

| Variable | Purpose |
|----------|---------|
| `AGENT_NEXUS_HOME` | Override default `~/.agent-nexus/` directory |
| `AGENT_MODEL` | Override default model (highest priority) |
| `DEFAULT_MODEL` | Override default model (falls back from AGENT_MODEL) |

### Runtime

| Variable | Purpose |
|----------|---------|
| `EDITOR` | Text editor for `config edit` command (default: vi) |
| `AGENT_NEXUS_PYTHON` | Python path for Rust runtime command |

> **Note**: `MCP_TRANSPORT`, `MCP_PORT`, and `MCP_HOST` are available in scaffold-generated agent templates (via `create-agent`), not as platform-level configuration. They control individual agent MCP server behavior.

## Model String Format

Models use the format `provider:model_name`:

```
anthropic:claude-sonnet-4-20250514
openai:gpt-4o
deepseek:deepseek-chat
ollama:llama3
api:MiniMax-M2.7-highspeed
```

The provider prefix maps to a `[models.providers.<name>]` section.

## .env File

Optional `~/.agent-nexus/.env` for Docker/deployment scenarios. Simple KEY=VALUE format. Only sets variables not already in the environment.

Priority: `existing env vars > .env > config.toml api_key values`

## Migration from Pre-1.0 Configs

Run `agent-nexus init` — it auto-detects outdated `schema_version` and merges new defaults, preserving all user settings. If you had `sources.yaml`, sources are automatically migrated to config.toml.
