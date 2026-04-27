# Quick Start Guide

Get Agent Nexus running in 5 minutes.

## 1. Install

```bash
# Via uv (recommended)
uv tool install agent-nexus

# Or via pip
pip install agent-nexus
```

## 2. Initialize

```bash
agent-nexus init
```

This creates `~/.agent-nexus/config.toml` with default settings.

## 3. Configure an API Key

```bash
# Choose your provider:
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
export DEEPSEEK_API_KEY="sk-..."
```

Or add it to `~/.agent-nexus/.env` for persistence:

```
OPENAI_API_KEY=sk-...
```

## 4. Verify

```bash
agent-nexus doctor
```

All 7 checks should pass.

## 5. Browse & Install an Agent

```bash
# See what's available
agent-nexus search security

# Install one
agent-nexus install security-scanner
```

## 6. Run Your First Agent

```bash
agent-nexus run security-scanner --file src/main.py
```

## Next Steps

- [Configuration Reference](configuration.md) — All config options and env vars
- [CLI Reference](cli.md) — Complete command documentation
- [Architecture Overview](01-overview.md) — Platform architecture

## Project-Level Setup (Optional)

For Agency pipeline or project-specific model config, create `./agent-nexus.toml` in your project root:

```toml
[models]
default = "anthropic:claude-sonnet-4-20250514"

[models.stages]
planning = "anthropic:claude-opus-4-20250116"
execution = "anthropic:claude-sonnet-4-20250514"
```

Project config overrides global config automatically when running CLI commands from the project directory.
