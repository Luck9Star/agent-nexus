# CLI Reference

## Overview

Agent Nexus provides a unified CLI for all platform operations. Available as Python (`python -m agent_nexus.platform.local.cli`) and Rust (`agent-nexus`) binaries with near-identical command surfaces.

## Global Flags

| Flag | Description |
|------|-------------|
| `--version`, `-v` | Print version and exit |
| `--json` | Output as JSON (Rust CLI; also available on select Python commands: `config show`, `search`) |
| `--follow` | Follow log output (Rust CLI; also available on `runtime logs`) |

## Commands

### Setup & Diagnostics

#### `init`

Initialize Agent Nexus configuration.

```bash
agent-nexus init                # Create default config in ~/.agent-nexus/
agent-nexus init --wizard       # Interactive setup wizard
```

Creates:
- `~/.agent-nexus/config.toml` with default providers and official source
- `~/.agent-nexus/` directory tree (agents, venvs, cache, runtimes, logs)

#### `doctor`

Run 7 diagnostic checks on the installation.

```bash
agent-nexus doctor
```

Checks: config.toml validity, API key presence, git/uv on PATH, Python >= 3.11, config dir writable, Evolution DB accessible.

#### `env`

Print resolved environment snapshot (config dir, Python version, provider status).

```bash
agent-nexus env
```

#### `version`

Print the installed version.

```bash
agent-nexus version
```

---

### Agent Discovery & Management

#### `search`

Search available agents by keyword.

```bash
agent-nexus search code-review
agent-nexus search security --capability code-review
agent-nexus search --category software-engineering --sort downloads
agent-nexus search api --json
```

| Flag | Description |
|------|-------------|
| `--capability`, `-c` | Filter by capability tag |
| `--category`, `-C` | Filter by category |
| `--sort`, `-s` | Sort by: `relevance` (default), `downloads`, `name`, `rating` |
| `--json` | Output as JSON |

#### `list`

List installed agents.

```bash
agent-nexus list
agent-nexus list --json           # JSON output
```

#### `info`

Show detailed information about an agent.

```bash
agent-nexus info code-reviewer
```

#### `install`

Install an agent package.

```bash
agent-nexus install code-reviewer
agent-nexus install code-reviewer --version 1.2.0
agent-nexus install code-reviewer --source https://github.com/my/agents.git
agent-nexus install code-reviewer --local
```

| Flag | Description |
|------|-------------|
| `--version`, `-v` | Install a specific version |
| `--source`, `-s` | Git URL for direct install |
| `--local`, `-l` | Install from local project `agents/` directory |

#### `uninstall`

Remove an installed agent.

```bash
agent-nexus uninstall code-reviewer
```

#### `update`

Update installed agents.

```bash
agent-nexus update                 # Update all
agent-nexus update code-reviewer   # Update specific agent
```

---

### Source Management

#### `sources list`

List configured package sources.

```bash
agent-nexus sources list
```

#### `sources add`

Add a new package source.

```bash
agent-nexus sources add --name my-source --url https://github.com/my/agents.git
agent-nexus sources add --name private --url git@git.internal:agents.git --type git
```

#### `sources remove`

Remove a package source.

```bash
agent-nexus sources remove my-source
```

Sources are stored in `~/.agent-nexus/config.toml` under the `sources` array.

---

### Runtime

#### `check`

Validate an agent package for completeness and correctness.

```bash
agent-nexus check ./my-agent
agent-nexus check ./my-agent --verbose
```

Checks: manifest fields, SKILL.md presence, pyproject.toml validity, composition.toml DAG cycle detection (composite agents).

#### `run`

Run an agent.

```bash
agent-nexus run code-reviewer
agent-nexus run code-reviewer --mode mcp           # MCP mode (default)
agent-nexus run code-reviewer --mode router        # Platform Router mode
agent-nexus run code-reviewer --mode cli           # CLI standalone mode
agent-nexus run code-reviewer --transport sse      # SSE transport (default: stdio)
```

| Flag | Description |
|------|-------------|
| `--mode`, `-m` | Run mode: `mcp` (default), `router`, `cli` |
| `--transport`, `-t` | Transport: `stdio` (default), `sse` |

#### `runtime`

Manage running agent processes.

```bash
agent-nexus runtime start <name>       # Start an agent process
agent-nexus runtime stop <name>        # Stop gracefully (SIGTERM → SIGKILL)
agent-nexus runtime restart <name>     # Restart with state recovery
agent-nexus runtime status             # Show all running agents
agent-nexus runtime ps                 # Alias for status
agent-nexus runtime logs <name>        # View agent logs
agent-nexus runtime logs <name> --follow  # Stream logs
```

---

### Configuration

#### `config`

View or edit configuration.

```bash
agent-nexus config show                # Show resolved config with sources
agent-nexus config get <key>           # Get a specific config value
agent-nexus config edit                # Open config.toml in $EDITOR
agent-nexus config validate            # Validate config.toml
agent-nexus config providers           # List configured providers
agent-nexus config path                # Print config directory path
```

---

### Development

#### `create-agent`

Capability-taxonomy aware agent scaffolding (top-level command).

```bash
agent-nexus create-agent my-agent --type atomic
agent-nexus create-agent my-pipeline --type composite
```

Generates a complete agent package with manifest, SKILL.md, pyproject.toml, and capability-aware tool templates.

#### `create`

Scaffold a new agent package (subcommand group).

```bash
agent-nexus create agent my-agent --type atomic
agent-nexus create agent my-agent --wizard    # Interactive mode
```

#### `evolution`

Manage the self-evolution engine.

```bash
agent-nexus evolution status           # Show evolution engine status
agent-nexus evolution health           # Health check of evolution store
agent-nexus evolution health my-skill --verbose  # Detailed skill health
agent-nexus evolution list             # List active evolved skills
agent-nexus evolution list --all       # Include inactive skills
agent-nexus evolution history my-skill # Show version lineage for a skill
agent-nexus evolution metrics          # Show evolution quality metrics
agent-nexus evolution metrics --agent code-reviewer  # Filter by agent
agent-nexus evolution fix <skill-id>   # Trigger FIX evolution on unhealthy skill
agent-nexus evolution promote <skill-id>  # Promote a skill to standalone agent
```

---

## Agency Pipeline

The Agency pipeline requires a project-level `./agent-nexus.toml` (or global config) with model stages configured. Invoked via Python CLI:

```bash
python -m agent_nexus.platform.agency.cli run-composition \
  --task "Add input validation to the login endpoint" \
  --vendor-path ./vendor \
  --allowlist config/agency-agents.allowlist.yaml \
  --use-llm \
  --temperature 0.7 \
  --max-parallel 3
```

### Agency Subcommands

| Command | Description |
|---------|-------------|
| `import-experts` | Import expert profiles from vendor path |
| `plan-composition` | Plan an expert composition for a task |
| `run-composition` | Execute a composition (full pipeline) |
| `validate-output` | Validate expert output against required sections |
| `list-experts` | List available expert profiles |
| `check-profiles` | Validate imported expert profiles |

### run-composition Flags

| Flag | Description |
|------|-------------|
| `--task` / `-m` / `--message` | The task description (required) |
| `--vendor-path` | Path to vendor agents |
| `--allowlist` | Allowlist YAML for agency agents |
| `--use-llm` | Enable LLM-powered planning/integration/QA |
| `--temperature` | LLM temperature (0.0-1.0) |
| `--max-parallel` | Max parallel experts (default: 3) |
| `--model` | Override model for all stages |
| `--config-dir` | Config directory path |
| `--timeout` | Overall pipeline timeout (seconds) |
| `--reasoning-protocol` | Enable structured reasoning (`<thinking>`/`<summary>` tags) |
| `--enable-evolution` | Enable self-evolution post-analysis |

## Environment Variables

See [Configuration Reference](configuration.md) for the full env var table.

## Rust vs Python CLI

The Rust and Python CLIs are functionally equivalent. Differences:

- Rust CLI uses `--json` for machine-readable output
- Rust CLI uses `--follow` for log streaming
- Python-only: `agency` subcommand (Agency pipeline)

Prefer the Rust binary (`agent-nexus`) for production use; use Python (`python -m agent_nexus.platform.local.cli`) for development and agency workflows.
