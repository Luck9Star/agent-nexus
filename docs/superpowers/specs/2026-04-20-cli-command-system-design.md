# CLI Command System Design

Date: 2026-04-20
Status: Approved
Author: Claude Code (from brainstorming session)

## Overview

Design the complete CLI command system for Agent Nexus platform. Current CLI has 12 commands (install/uninstall/update/list/search/info/sources/run + sub-commands). This spec adds 15 new commands across 5 tiers to expose all platform capabilities: first-time setup, config management, runtime daemon control, evolution subsystem, and diagnostics.

Command count: 12 existing (preserved in `_lifecycle.py`) + 15 new = **27 total**. All organized in a modular package structure.

## Design Decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Init interaction | `questionary`-based wizard | Rich interactive UX (select, path input, autocomplete), same pattern as nanobot onboard |
| 2 | Runtime daemon | PID file + ProcessManager | Aligns with existing asyncio.subprocess architecture, no system-level daemon needed |
| 3 | Evolution scope | Full exposure (7 commands) | Backend is complete; expose all capabilities from day one |
| 4 | Config write strategy | show/get/edit/validate only (no `set`) | Avoid TOML serialization edge cases; `$EDITOR` is safer for writes |
| 5 | Provider template | Only openai + anthropic in default config | DeepSeek/Qwen/MiniMax as comment examples, not hardcoded entries |
| 6 | Config migration | `ConfigMigrator` with `schema_version` field | Merge new defaults without overwriting user values on platform upgrades |
| 7 | CLI architecture | Modular package (6 files) | Avoid single-file bloat (would exceed 1500 lines); each tier ~200-300 lines |

## File Structure

```
src/agent_nexus/platform/local/cli/
  __init__.py          # Main Typer app, register all sub-Typers
  _shared.py           # _get_config_dir(), _init_managers(), ConfigMigrator
  _lifecycle.py        # Existing: install/uninstall/update/list/search/info/sources/run
  init_cmd.py          # init + doctor + version
  config_cmd.py        # config show/get/edit/validate/providers/path
  runtime_cmd.py       # start/stop/restart/status/logs/ps
  evolution_cmd.py     # evolution status/health/list/history/metrics/fix/promote
```

Migration: existing `cli.py` (542 lines) becomes `cli/__init__.py` + `cli/_lifecycle.py`.
Entry point in pyproject.toml stays `agent_nexus.platform.local.cli:app`.

## Dependency Changes

```toml
[project.dependencies]
# Add:
"questionary>=2.0",    # Interactive init wizard
```

## Command Reference

Breakdown: 12 existing commands (moved to `_lifecycle.py`) + 15 new commands = 27 total.

### Tier 1: Core Onboarding (3 commands)

#### `agent-nexus init [--wizard]`

Default mode (no flags):
1. Create `~/.agent-nexus/` directory tree (via `ConfigLoader.ensure_config_dir()`)
2. Generate `config.toml` from template with openai + anthropic defaults and comment examples for custom providers
3. Register official source in `sources.yaml`
4. Detect API keys from environment (OPENAI_API_KEY, ANTHROPIC_API_KEY, etc.)
5. If `ConfigMigrator.merge_if_needed()` detects stale config, perform migration
6. Print "next steps" guidance

Wizard mode (`--wizard`):
1. All default steps above
2. Interactive `questionary` prompts:
   - Select default Provider (openai/anthropic/custom)
   - Enter API Key (masked input)
   - Select default model (with autocomplete from provider)
   - Verify connectivity (test API call with 1-token completion)
3. Write selections to config.toml

#### `agent-nexus doctor`

Run 7 diagnostic checks, output per-line pass/fail:

| # | Check | Pass condition |
|---|-------|---------------|
| 1 | config.toml exists and parses | `ConfigLoader.load_config()` succeeds |
| 2 | API key configured | At least 1 provider key env var is non-empty |
| 3 | `git` on PATH | `shutil.which("git")` is not None |
| 4 | `uv` on PATH | `shutil.which("uv")` is not None |
| 5 | Python >= 3.12 | `sys.version_info >= (3, 12)` |
| 6 | lockfile.json writable | Can create/write/read `~/.agent-nexus/lockfile.json` |
| 7 | Evolution DB accessible | `EvolutionStore(":memory:")` initializes |

#### `agent-nexus version`

Read from `importlib.metadata.version("agent-nexus")`. Print single line.

### Tier 2: Config Management (6 commands)

All commands under `config` sub-Typer.

#### `agent-nexus config show [--json]`

Load config via `ConfigLoader.load_config()` (full merge: defaults + config.toml + env vars).
Print formatted output. `--json` outputs raw JSON dump of `PlatformConfig.model_dump()`.

#### `agent-nexus config get <key>`

Dot-path lookup on merged PlatformConfig. Examples:
- `agent-nexus config get models.default` -> `openai:gpt-4o`
- `agent-nexus config get runtime.python_path` -> `python3`

#### `agent-nexus config edit`

Open config.toml in `$EDITOR` (fallback: `vi`). Uses `subprocess.call([editor, path])`.

#### `agent-nexus config validate`

Attempt `ConfigLoader.load_config()`. If success: print "Config valid." If error: print specific error (TOML parse error / Pydantic validation error / file not found). Also run `ConfigMigrator.check_version()` and warn if config schema is outdated.

#### `agent-nexus config providers`

Table output of all providers (built-in 6 + user-defined from config.toml):

| Name | Base URL | API Key Env | Key Status |
|------|----------|-------------|------------|
| openai | (default) | OPENAI_API_KEY | set |
| anthropic | (default) | ANTHROPIC_API_KEY | not set |
| custom-xyz | https://... | CUSTOM_KEY | set |

Key status: check `os.environ.get(api_key_env, "")` -> "set" / "not set" (never reveal key value).

#### `agent-nexus config path`

Print `~/.agent-nexus/` absolute path. Useful for scripting.

### Tier 3: Runtime Management (8 commands)

All commands use existing `AgentSupervisor` and `ProcessManager` backend. No new backend code needed.

#### `agent-nexus start <name> [--mode mcp|router|cli]`

1. Verify agent is installed (lockfile lookup)
2. Call `AgentSupervisor.start_agent(name)`
3. Write PID to `~/.agent-nexus/agents/<name>.pid`
4. Redirect stdout/stderr to `~/.agent-nexus/logs/<name>.log`
5. Print "Started <name> (pid: <pid>)"

#### `agent-nexus start --all [--mode]`

Iterate lockfile entries, start each. Parallel startup via `AgentSupervisor.start_all()`.

#### `agent-nexus stop <name>`

1. Read PID from `~/.agent-nexus/agents/<name>.pid`
2. Send SIGTERM
3. Wait up to 5s for process exit
4. If still alive: SIGKILL
5. Delete PID file
6. Print "Stopped <name>"

#### `agent-nexus stop --all`

Stop all running agents sequentially (reverse start order).

#### `agent-nexus restart <name>`

stop + start. Preserve the original `--mode`.

#### `agent-nexus status`

Table output using `AgentSupervisor.list_running()` + `health_check_all()`:

| Name | Installed | Running | PID | Health |
|------|-----------|---------|-----|--------|
| doc-filler | yes | yes | 12345 | alive |
| code-reviewer | yes | no | - | - |
| security-scanner | no | - | - | - |

#### `agent-nexus logs <name> [--lines N]`

Read last N lines from `~/.agent-nexus/logs/<name>.log`. Default N=50.

#### `agent-nexus ps`

Alias for `agent-nexus status`. Same implementation, different command name.

### Tier 4: Evolution (7 commands)

All commands under `evolution` sub-Typer. Backend: `EvolutionEngine` (facade over EvolutionStore + HealthChecker + SkillEvolver + AgentPromoter).

#### `agent-nexus evolution status`

Call `HealthChecker.get_health_summary()`. Output:

```
Evolution Status:
  Total skills: 42
  Healthy: 38
  Unhealthy: 3
  Suggestions: 1
```

#### `agent-nexus evolution health [skill-name] [--verbose]`

Without skill-name: call `HealthChecker.diagnose_all()`, show table of all skills.
With skill-name: call `HealthChecker.check_health(skill_name)`, show detailed metrics.

`--verbose`: include threshold details and per-metric breakdown.

Table columns: Name | Applied Rate | Completion Rate | Fallback Rate | Verdict

#### `agent-nexus evolution list [--all]`

Default: `EvolutionStore.get_active_skills()`.
`--all`: `EvolutionStore.get_all_skills()`.

Table columns: Name | Version | Generation | Status | Created

#### `agent-nexus evolution history <skill-name>`

Call `EvolutionStore.get_ancestry(skill_id)`. Display version lineage:

```
skill-template-v1 (gen 0, 2026-04-10)
  -> skill-template-v2 (gen 1, 2026-04-12, FIX)
    -> skill-template-v3 (gen 2, 2026-04-15, DERIVED)
```

#### `agent-nexus evolution metrics [--agent <name>]`

Call `EvolutionStore.get_metrics(agent_name)`.
Without `--agent`: aggregate across all agents.

Table: Agent | Skills | Total Applied | Success Rate | Avg Fallback Rate

#### `agent-nexus evolution fix <skill-id>`

Call `SkillEvolver.evolve(trigger=EvolutionTrigger.FIX, skill_id=skill_id)`.
Print result: success/failure + new skill version if created.

#### `agent-nexus evolution promote <skill-id>`

Call `AgentPromoter.promote(candidate_skill_id)`.
Print result: promoted/not promoted + new agent name if created.

### Tier 5: Auxiliary (2 commands)

#### `agent-nexus env`

Print resolved environment snapshot:

```
Config dir:    ~/.agent-nexus/
Python:        3.12.4
Git:           2.44.0
uv:            0.4.0
Providers:     openai (key: set), anthropic (key: set), deepseek (key: not set)
Schema:        1.0
```

#### `agent-nexus completion [shell]`

Typer built-in shell completion. Generates completion script for bash/zsh/fish/powershell.

## Config Migration System

### Schema Version

New field in `PlatformConfig`:

```python
class PlatformConfig(BaseModel):
    schema_version: str = "1.0"
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    models: ModelConfig = Field(default_factory=ModelConfig)
```

Current default: `"1.0"`. Future platform updates bump `TARGET_SCHEMA_VERSION` in `defaults.py`.

### ConfigMigrator

Located in `cli/_shared.py`. Two methods:

```python
class ConfigMigrator:
    TARGET_VERSION = "1.0"  # Bumped on schema changes

    @classmethod
    def merge_if_needed(cls, config_path: Path) -> bool:
        """Merge new defaults into user config if schema is outdated.
        Returns True if migration was performed."""

    @classmethod
    def check_version(cls, config_path: Path) -> str | None:
        """Return current schema_version, or None if config doesn't exist."""
```

Merge strategy:
- **New keys**: Add with default value
- **Existing keys**: Never overwrite (user intent preserved)
- **Nested dicts**: Recursive merge
- **Removed keys**: Leave in place (forward compatibility)
- **User-defined sections** (e.g. `[models.providers.custom-xyz]`): Never touched

### Trigger points

1. `agent-nexus init` (non-first-run): auto-merge before proceeding
2. `agent-nexus config validate`: warn if schema_version < TARGET_VERSION
3. `agent-nexus doctor`: check schema_version as diagnostic item

## Default config.toml Template

Generated by `agent-nexus init` (non-wizard mode):

```toml
# Agent Nexus Configuration
# Docs: https://github.com/anthropics/agent-nexus
# Schema version: 1.0

schema_version = "1.0"

[runtime]
python_path = "python3"
uv_path = "uv"

[models]
default = "openai:gpt-4o"

[models.providers.openai]
api_key_env = "OPENAI_API_KEY"
api = "openai-compatible"

[models.providers.anthropic]
api_key_env = "ANTHROPIC_API_KEY"
api = "anthropic-messages"

# --- Custom Provider Examples ---
# Uncomment and edit to add your own providers:
#
# [models.providers.deepseek]
# base_url = "https://api.deepseek.com/v1"
# api_key_env = "DEEPSEEK_API_KEY"
# api = "openai-compatible"
#
# [models.providers.qwen]
# base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
# api_key_env = "DASHSCOPE_API_KEY"
# api = "openai-compatible"
#
# [models.providers.minimax]
# base_url = "https://api.minimax.chat/v1"
# api_key_env = "MINIMAX_API_KEY"
# api = "openai-compatible"
#
# [models.providers.ollama]
# base_url = "http://localhost:11434/v1"
# api_key_env = ""
# api = "openai-compatible"
```

## Implementation Phases

| Phase | Module | Commands | Backend Dependency | Est. Lines |
|-------|--------|----------|-------------------|------------|
| 1 | Package refactor + `_shared.py` | 0 (restructure) | None | ~80 |
| 2 | `init_cmd.py` | 3 | ConfigLoader, SourceManager, ConfigMigrator | ~300 |
| 3 | `runtime_cmd.py` | 8 | AgentSupervisor, ProcessManager | ~250 |
| 4 | `config_cmd.py` | 6 | ConfigLoader, ModelConfigManager | ~200 |
| 5 | `evolution_cmd.py` | 7 | EvolutionEngine, HealthChecker | ~250 |
| 6 | `env` + `completion` | 2 | None | ~50 |
| | **Total** | **15 new** (+ 12 existing in `_lifecycle.py`) | | **~1130** |

Phase ordering rationale:
- Phase 1 first: all subsequent phases depend on the package structure
- Phase 2 next: `init` is the first-run experience, blockers for all users
- Phase 3: Supervisor backend is complete, thin wrapper = fastest delivery
- Phase 4: Config management supports debugging of Phase 2-3 issues
- Phase 5: Evolution is advanced, no dependency on other phases
- Phase 6: Minor polish, can be done anytime

## Testing Strategy

Each command module gets a corresponding test file:

```
tests/unit/cli/
  test_init_cmd.py
  test_config_cmd.py
  test_runtime_cmd.py
  test_evolution_cmd.py
  conftest.py              # Shared fixtures: mock config dir, mock managers
```

Test approach:
- Use `typer.testing.CliRunner` for CLI invocation
- Mock `_init_managers()` and backend managers (ConfigLoader, Supervisor, EvolutionEngine)
- Test both success and error paths for each command
- Test `ConfigMigrator.merge_if_needed()` with various schema versions
- Test init wizard flow with mocked `questionary` inputs
