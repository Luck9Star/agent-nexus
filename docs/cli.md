# CLI Reference

## Overview

Agent Nexus provides a unified CLI for all platform operations. Available as Python (`python -m agent_nexus.platform.local.cli`) and Rust (`agent-nexus`) binaries with near-identical command surfaces.

## Global Flags

| Flag | Description |
|------|-------------|
| `--version`, `-v` | Print version and exit |
| `--json` | Output as JSON (Rust CLI only) |
| `--follow` | Follow log output (Rust CLI only) |

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
agent-nexus search security
```

#### `list`

List installed agents.

```bash
agent-nexus list
agent-nexus list --all            # Include available (not installed)
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
agent-nexus install --source official code-reviewer
```

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

#### `run`

Run an agent.

```bash
agent-nexus run code-reviewer --file src/main.py
agent-nexus run code-reviewer --mcp    # Run in MCP mode
```

#### `runtime`

Manage running agent processes.

```bash
agent-nexus runtime list
agent-nexus runtime stop <id>
agent-nexus runtime logs <id> --follow
```

---

### Configuration

#### `config`

View or edit configuration.

```bash
agent-nexus config                  # Show merged config
agent-nexus config edit             # Open config.toml in $EDITOR
agent-nexus config show             # Show resolved config with sources
```

---

### Development

#### `create`

Scaffold a new agent package.

```bash
agent-nexus create my-agent --type atomic
agent-nexus create my-pipeline --type composite
```

#### `evolution`

Manage the self-evolution engine.

```bash
agent-nexus evolution status
agent-nexus evolution run
agent-nexus evolution history
```

---

## Agency Pipeline

The Agency pipeline requires a project-level `./agent-nexus.toml` (or global config) with model stages configured:

```bash
python -m agent_nexus.platform.agency.cli run-composition \
  --task "Add input validation to the login endpoint" \
  --vendor-path ./vendor \
  --allowlist config/agency-agents.allowlist.yaml \
  --use-llm \
  --temperature 0.7 \
  --max-parallel 3
```

| Flag | Description |
|------|-------------|
| `--task` | The task description |
| `--vendor-path` | Path to vendor agents |
| `--allowlist` | Allowlist YAML for agency agents |
| `--use-llm` | Enable LLM-powered planning/integration/QA |
| `--temperature` | LLM temperature (0.0-1.0) |
| `--max-parallel` | Max parallel experts |

## Environment Variables

See [Configuration Reference](configuration.md) for the full env var table.

## Rust vs Python CLI

The Rust and Python CLIs are functionally equivalent. Differences:

- Rust CLI uses `--json` for machine-readable output
- Rust CLI uses `--follow` for log streaming
- Python-only: `agency` subcommand (Agency pipeline)

Prefer the Rust binary (`agent-nexus`) for production use; use Python (`python -m agent_nexus.platform.local.cli`) for development and agency workflows.
