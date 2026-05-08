# Config Consolidation & Documentation Split — Design Spec

**Date**: 2026-04-27
**Status**: approved

## 1. Motivation

Config is scattered across 4 formats (TOML, YAML, JSON, .env) in 6+ files. Docs are similarly fragmented — no standalone CLI reference, no config reference, no quick-start guide. Users face an unnecessarily steep onboarding curve.

## 2. Design

### 2.1 Config File Layout

```
~/.agent-nexus/                    # Global (cross-project)
├── config.toml                    # All structured config + sources (was sources.yaml)
├── .env                           # Optional, env-specific overrides (preserved)
├── lockfile.json                  # Machine-generated, stays as-is
├── evolution.db                   # Machine-generated, stays as-is
└── agents/                        # Installed agent packages

./agent-nexus.toml                 # Project-level (optional, in repo root)
```

### 2.2 Priority Chain

```
CLI args > env vars > .env > project ./agent-nexus.toml > global ~/.agent-nexus/config.toml > built-in defaults
```

### 2.3 Path Resolution

All relative paths in a config file are resolved against the directory containing that config file (not cwd). Supports `~` expansion and absolute paths.

### 2.4 Global Config Schema (~/.agent-nexus/config.toml)

```toml
schema_version = "1.0"

[runtime]
python_path = "python3"
uv_path = "uv"

[models]
default = "anthropic:claude-sonnet-4-20250514"

[models.providers.<name>]
api_key = ""            # inline key, or...
api_key_env = ""        # read from env var
api = "openai-compatible" | "anthropic-messages" | "ollama"
base_url = ""

[models.stages]
planning = "..."
execution = "..."
integration = "..."
qa = "..."

[sources]
official = "https://github.com/agent-nexus/official-agents"
# additional user sources...
```

### 2.5 Project Config Schema (./agent-nexus.toml, optional)

```toml
schema_version = "1.0"

[models]
default = "anthropic:claude-opus-4-20250116"

[models.stages]
planning = "anthropic:claude-opus-4-20250116"
execution = "anthropic:claude-sonnet-4-20250514"
integration = "openai:gpt-4o"
qa = "anthropic:claude-sonnet-4-20250514"
```

Notes:
- All fields optional — missing fields fall through to global config
- `allowlist` defaults to `config/agency-agents.allowlist.yaml` relative to project root (not shown in config)
- `max_parallel` has a built-in default of 3

### 2.6 Config Consolidation Changes

| Before | After |
|--------|-------|
| `~/.agent-nexus/sources.yaml` (YAML) | `[sources]` section in `~/.agent-nexus/config.toml` (TOML) |
| `init` template: 2 providers | `init` template: all 6 providers (consistent with `DEFAULT_PROVIDERS`) |
| No project-level config | `./agent-nexus.toml` merges on top of global |
| `ConfigMigrator._default_config_dict()`: 2 providers | 6 providers, consistent with `defaults.py` |

### 2.7 What Does NOT Change

- `.env` preserved as optional override layer (useful for Docker/deployment)
- `lockfile.json` stays JSON (machine-generated)
- `evolution.db` stays SQLite
- Environment variable names unchanged
- CLI flag names unchanged

## 3. Documentation Split

### 3.1 New Documents

| File | Content | Target Length |
|------|---------|---------------|
| `docs/configuration.md` | Full config reference: schema, priority chain, every field, env var table, examples for common setups | ~300 lines |
| `docs/cli.md` | Complete CLI reference: 17 commands, arguments, usage examples, Python vs Rust parity notes | ~500 lines |
| `docs/quick-start.md` | 5-minute guide: install → init → config → run first agent | ~150 lines |

### 3.2 Existing Document Changes

| File | Change |
|------|--------|
| `README.md` | Slim down — keep overview, architecture diagram, quick links to new docs. Remove inline config/env tables, point to `docs/configuration.md`. Remove CLI table, point to `docs/cli.md`. |
| `README_EN.md` | Same slim-down treatment |
| `docs/README.md` | Add entries for configuration.md, cli.md, quick-start.md |

## 4. Implementation Order

1. **Config loader changes** (Python): merge sources.toml into config.toml, add project-level config loading, fix init template to 6 providers
2. **Rust config loader changes**: mirror Python changes (sources in config.toml, project-level config)
3. **CLI command updates**: `init` generates unified config, `sources` reads from config.toml `[sources]`, `config` shows merged view
4. **Documentation**: write configuration.md, cli.md, quick-start.md; slim down READMEs
5. **Tests**: update config loader tests, add project-level merge tests

## 5. Risks

- **Breaking change**: `sources.yaml` users need to migrate. Mitigation: `init --doctor` detects old `sources.yaml` and offers migration.
- **Rust parity**: Rust `ModelConfig` lacks `stages` field. Add it in this change.
