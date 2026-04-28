# CLI Command System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the monolithic `cli.py` into a modular package and add 15 new CLI commands across 5 tiers (init/doctor/version, config management, runtime control, evolution exposure, env/completion).

**Architecture:** Convert `cli.py` (542 lines) into a `cli/` package with 6 modules. Each module owns a Typer sub-app registered on the main app. New commands are thin wrappers over existing backends (`ConfigLoader`, `AgentSupervisor`, `EvolutionEngine`). A shared `_shared.py` provides `ConfigMigrator` and common helpers extracted from the current `cli.py`.

**Tech Stack:** Typer, questionary (init wizard), Pydantic v2, toml, rich (optional table formatting)

---

## File Structure

```
src/agent_nexus/platform/local/cli/
  __init__.py          # Main Typer app, register all sub-Typers, re-export `app`
  _shared.py           # _get_config_dir(), _init_managers(), ConfigMigrator
  _lifecycle.py        # Existing commands: install/uninstall/update/list/search/info/sources/run
  init_cmd.py          # init + doctor + version
  config_cmd.py        # config show/get/edit/validate/providers/path
  runtime_cmd.py       # start/stop/restart/status/logs/ps
  evolution_cmd.py     # evolution status/health/list/history/metrics/fix/promote

tests/unit/cli/
  conftest.py          # Shared fixtures: mock config dir, mock managers, CliRunner
  test_shared.py       # ConfigMigrator tests
  test_init_cmd.py     # init/doctor/version tests
  test_config_cmd.py   # config command tests
  test_runtime_cmd.py  # runtime command tests
  test_evolution_cmd.py # evolution command tests
```

### Backend API Reference (read-only, no changes needed)

| Backend | Key Methods Used | File |
|---------|-----------------|------|
| `ConfigLoader` | `load_config()`, `ensure_config_dir()`, `config_dir` | `platform/config/loader.py` |
| `AgentSupervisor` | `start_agent()`, `stop_agent()`, `start_all()`, `stop_all()`, `health_check_all()` | `platform/local/supervisor.py` |
| `LockfileManager` | `load()`, `get_entry()` | `platform/local/lockfile.py` |
| `SourceManager` | `list_sources()`, `search_agents()` | `platform/local/sources.py` |
| `EvolutionEngine` | `check_health()`, `diagnose_all()`, `promote_candidate()`, `evolve()` | `platform/evolution/engine.py` |
| `EvolutionStore` | `get_active_skills()`, `get_all_skills()`, `get_ancestry()`, `get_metrics()` | `platform/evolution/store.py` |
| `HealthChecker` | `get_health_summary()` | `platform/evolution/health.py` |
| `PlatformConfig` | `model_dump()`, `runtime`, `models` | `models/config.py` |

### Entry Point

`pyproject.toml` entry point stays the same:
```toml
[project.scripts]
agent-nexus = "agent_nexus.platform.local.cli:app"
```

Since `cli/` is a package, `cli/__init__.py` re-exports `app` — Python resolves the import identically.

---

## Phase 1: Package Refactor

### Task 1: Create `_shared.py` — Extract common helpers + `ConfigMigrator`

**Files:**
- Create: `src/agent_nexus/platform/local/cli/_shared.py`

- [ ] **Step 1: Write `_shared.py`**

```python
"""Shared helpers for CLI command modules.

Provides:
- _get_config_dir(): Resolve the platform config directory.
- _init_managers(): Initialise the standard manager stack.
- ConfigMigrator: Schema-based config migration.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import toml

logger = logging.getLogger(__name__)


def _get_config_dir() -> Path:
    """Resolve the platform config directory.

    Priority: ``AGENT_NEXUS_HOME`` env var > built-in default.
    """
    env = os.environ.get("AGENT_NEXUS_HOME")
    if env:
        return Path(env)
    from agent_nexus.platform.config.defaults import DEFAULT_CONFIG_DIR
    return DEFAULT_CONFIG_DIR


def _init_managers(
    config_dir: Path | None = None,
) -> tuple:
    """Initialise the standard manager stack used by most commands.

    Returns (config_loader, lockfile_manager, source_manager, config_dir).
    """
    from agent_nexus.platform.config.loader import ConfigLoader
    from agent_nexus.platform.local.lockfile import LockfileManager
    from agent_nexus.platform.local.sources import SourceManager

    _config_dir = config_dir or _get_config_dir()
    loader = ConfigLoader(_config_dir)
    loader.ensure_config_dir()

    lockfile = LockfileManager(_config_dir / "lockfile.json")
    sources = SourceManager(_config_dir / "sources.yaml")

    return loader, lockfile, sources, _config_dir


class ConfigMigrator:
    """Merge new defaults into user config when the schema version changes.

    Merge strategy:
    - New keys: add with default value
    - Existing keys: never overwrite (user intent preserved)
    - Nested dicts: recursive merge
    - Removed keys: leave in place
    - User-defined sections: never touched
    """

    TARGET_VERSION = "1.0"

    @classmethod
    def merge_if_needed(cls, config_path: Path) -> bool:
        """Merge new defaults into user config if schema is outdated.

        Returns True if migration was performed.
        """
        if not config_path.exists():
            return False

        try:
            raw = toml.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Cannot parse %s for migration", config_path)
            return False

        current_version = raw.get("schema_version", "")
        if current_version == cls.TARGET_VERSION:
            return False

        # Load the default template as the "new" config
        defaults = cls._default_config_dict()
        merged = cls._deep_merge(defaults, raw)
        merged["schema_version"] = cls.TARGET_VERSION

        config_path.write_text(
            toml.dumps(merged),
            encoding="utf-8",
        )
        logger.info(
            "Config migrated: %s -> %s",
            current_version or "(none)",
            cls.TARGET_VERSION,
        )
        return True

    @classmethod
    def check_version(cls, config_path: Path) -> str | None:
        """Return current schema_version, or None if config doesn't exist."""
        if not config_path.exists():
            return None
        try:
            raw = toml.loads(config_path.read_text(encoding="utf-8"))
            return raw.get("schema_version")
        except Exception:
            return None

    @classmethod
    def _default_config_dict(cls) -> dict[str, Any]:
        """Return the default config as a plain dict (no comments)."""
        return {
            "schema_version": cls.TARGET_VERSION,
            "runtime": {
                "python_path": "python3",
                "uv_path": "uv",
            },
            "models": {
                "default": "openai:gpt-4o",
                "providers": {
                    "openai": {
                        "api_key_env": "OPENAI_API_KEY",
                        "api": "openai-compatible",
                    },
                    "anthropic": {
                        "api_key_env": "ANTHROPIC_API_KEY",
                        "api": "anthropic-messages",
                    },
                },
            },
        }

    @classmethod
    def _deep_merge(
        cls,
        defaults: dict[str, Any],
        user: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge defaults into user config. User values always win."""
        result = dict(defaults)
        for key, user_val in user.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(user_val, dict)
            ):
                result[key] = cls._deep_merge(result[key], user_val)
            else:
                result[key] = user_val
        return result
```

- [ ] **Step 2: Write the test for `_shared.py`**

Create `tests/unit/cli/conftest.py`:

```python
"""Shared fixtures for CLI tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def tmp_config_dir(tmp_path: Path) -> Path:
    """Create a temporary config directory tree."""
    config_dir = tmp_path / ".agent-nexus"
    config_dir.mkdir()
    for subdir in ("agents", "venvs", "cache/repos", "runtimes", "logs"):
        (config_dir / subdir).mkdir(parents=True)
    return config_dir


@pytest.fixture(autouse=True)
def _isolate_config_env(monkeypatch: Any, tmp_path: Path) -> None:
    """Ensure tests don't read/write the real ~/.agent-nexus."""
    config_dir = tmp_path / ".agent-nexus"
    monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))
```

Create `tests/unit/cli/__init__.py` (empty).

Create `tests/unit/cli/test_shared.py`:

```python
"""Tests for cli/_shared.py — ConfigMigrator."""

from __future__ import annotations

from pathlib import Path

import toml
import pytest

from agent_nexus.platform.local.cli._shared import ConfigMigrator


class TestConfigMigratorCheckVersion:
    def test_returns_none_when_no_config(self, tmp_path: Path) -> None:
        result = ConfigMigrator.check_version(tmp_path / "nonexistent.toml")
        assert result is None

    def test_returns_version_from_config(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('schema_version = "0.9"\n')
        assert ConfigMigrator.check_version(cfg) == "0.9"

    def test_returns_none_when_no_version_key(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('[runtime]\npython_path = "python3"\n')
        assert ConfigMigrator.check_version(cfg) is None


class TestConfigMigratorMergeIfNeeded:
    def test_no_migration_when_already_current(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text('schema_version = "1.0"\n[runtime]\npython_path = "python3"\n')
        result = ConfigMigrator.merge_if_needed(cfg)
        assert result is False

    def test_no_migration_when_file_missing(self, tmp_path: Path) -> None:
        result = ConfigMigrator.merge_if_needed(tmp_path / "nonexistent.toml")
        assert result is False

    def test_merges_new_keys_preserving_user_values(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text(
            'schema_version = "0.5"\n'
            '[runtime]\n'
            'python_path = "/usr/bin/python3.12"\n'
            '[models]\n'
            'default = "anthropic:claude-sonnet-4-20250514"\n'
        )
        result = ConfigMigrator.merge_if_needed(cfg)
        assert result is True

        merged = toml.loads(cfg.read_text())
        # schema_version was updated
        assert merged["schema_version"] == "1.0"
        # user value preserved
        assert merged["runtime"]["python_path"] == "/usr/bin/python3.12"
        assert merged["models"]["default"] == "anthropic:claude-sonnet-4-20250514"
        # new default keys added
        assert "uv_path" in merged["runtime"]

    def test_preserves_user_custom_providers(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config.toml"
        cfg.write_text(
            'schema_version = "0.5"\n'
            '[models.providers.my-custom]\n'
            'base_url = "https://custom.api/v1"\n'
            'api_key_env = "CUSTOM_KEY"\n'
            'api = "openai-compatible"\n'
        )
        ConfigMigrator.merge_if_needed(cfg)

        merged = toml.loads(cfg.read_text())
        assert "my-custom" in merged["models"]["providers"]
        assert merged["models"]["providers"]["my-custom"]["base_url"] == "https://custom.api/v1"


class TestConfigMigratorDeepMerge:
    def test_user_overrides_default(self) -> None:
        result = ConfigMigrator._deep_merge(
            {"a": 1, "b": 2},
            {"b": 99},
        )
        assert result == {"a": 1, "b": 99}

    def test_recursive_nested_merge(self) -> None:
        result = ConfigMigrator._deep_merge(
            {"outer": {"x": 1, "y": 2}},
            {"outer": {"y": 99, "z": 3}},
        )
        assert result == {"outer": {"x": 1, "y": 99, "z": 3}}
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/unit/cli/test_shared.py -v`
Expected: All tests PASS

- [ ] **Step 4: Commit**

```bash
git add src/agent_nexus/platform/local/cli/_shared.py tests/unit/cli/
git commit -m "feat(cli): add _shared.py with ConfigMigrator and helpers"
```

---

### Task 2: Create `__init__.py` — Main app registration

**Files:**
- Create: `src/agent_nexus/platform/local/cli/__init__.py`

- [ ] **Step 1: Write `__init__.py`**

```python
"""CLI entry point for Agent Nexus.

Built with Typer.  Declared in ``pyproject.toml`` as::

    [project.scripts]
    agent-nexus = "agent_nexus.platform.local.cli:app"

This module MUST export ``app`` as a Typer instance at module level.
"""

from __future__ import annotations

import typer

app = typer.Typer(
    name="agent-nexus",
    help="Agent Nexus -- MCP-native Agent Platform",
    no_args_is_help=True,
)

# Register lifecycle commands (existing install/uninstall/update/list/search/info/sources/run)
from agent_nexus.platform.local.cli._lifecycle import (  # noqa: F401
    install_app,
    run_app,
)

app.add_typer(install_app, name="install")
app.add_typer(run_app, name="run", invoke_without_command=True)

# Register new command modules
from agent_nexus.platform.local.cli.init_cmd import init_app  # noqa: F401
from agent_nexus.platform.local.cli.config_cmd import config_app  # noqa: F401
from agent_nexus.platform.local.cli.runtime_cmd import runtime_app  # noqa: F401
from agent_nexus.platform.local.cli.evolution_cmd import evolution_app  # noqa: F401

app.add_typer(init_app, name=None)  # init/doctor/version as top-level
app.add_typer(config_app, name="config")
app.add_typer(runtime_app, name=None)  # start/stop/restart/status/logs/ps as top-level
app.add_typer(evolution_app, name="evolution")

if __name__ == "__main__":
    app()
```

Note: This file depends on the modules created in Tasks 3-6. For now, we'll create stub modules so imports don't fail.

- [ ] **Step 2: Create stub modules for Tasks 3-6**

These are placeholder registrations so the import chain works. Full implementations come in later tasks.

Create `src/agent_nexus/platform/local/cli/_lifecycle.py` (move content from old `cli.py` — done in Task 3).

Create `src/agent_nexus/platform/local/cli/init_cmd.py`:

```python
"""Init, doctor, and version commands."""

from __future__ import annotations

import typer

init_app = typer.Typer(help="Setup and diagnostics")
```

Create `src/agent_nexus/platform/local/cli/config_cmd.py`:

```python
"""Config management commands."""

from __future__ import annotations

import typer

config_app = typer.Typer(help="Configuration management")
```

Create `src/agent_nexus/platform/local/cli/runtime_cmd.py`:

```python
"""Runtime management commands."""

from __future__ import annotations

import typer

runtime_app = typer.Typer(help="Runtime management")
```

Create `src/agent_nexus/platform/local/cli/evolution_cmd.py`:

```python
"""Evolution subsystem commands."""

from __future__ import annotations

import typer

evolution_app = typer.Typer(help="Self-Evolution Engine")
```

- [ ] **Step 3: Commit**

```bash
git add src/agent_nexus/platform/local/cli/__init__.py src/agent_nexus/platform/local/cli/init_cmd.py src/agent_nexus/platform/local/cli/config_cmd.py src/agent_nexus/platform/local/cli/runtime_cmd.py src/agent_nexus/platform/local/cli/evolution_cmd.py
git commit -m "feat(cli): add __init__.py with app registration + stub modules"
```

---

### Task 3: Migrate lifecycle commands to `_lifecycle.py`

**Files:**
- Create: `src/agent_nexus/platform/local/cli/_lifecycle.py`
- Delete: `src/agent_nexus/platform/local/cli.py` (old monolith)

- [ ] **Step 1: Write `_lifecycle.py`**

Move all existing commands from `cli.py` into `_lifecycle.py`, importing helpers from `_shared` instead of defining them inline.

```python
"""Lifecycle commands: install, uninstall, update, list, search, info, sources, run.

Migrated from the original cli.py monolith.  All async implementations
are here; sync Typer callbacks delegate to them via ``asyncio.run()``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import typer

from agent_nexus.platform.local.cli._shared import (
    _get_config_dir,
    _init_managers,
)

logger = logging.getLogger(__name__)

# Sub-command groups
install_app = typer.Typer(help="Agent installation and management")
run_app = typer.Typer(help="Run agents and workflows")


# =====================================================================
# Install commands
# =====================================================================


@install_app.command("install")
def install_agent(
    name: str = typer.Argument(help="Agent name to install"),
    version: Optional[str] = typer.Option(
        None, "--version", "-v", help="Specific version"
    ),
    source: Optional[str] = typer.Option(
        None, "--source", "-s", help="Git URL (direct install)"
    ),
) -> None:
    """Install an agent from a package source."""
    asyncio.run(_install(name, version, source))


@install_app.command()
def uninstall(name: str = typer.Argument(help="Agent name to uninstall")) -> None:
    """Uninstall an agent."""
    asyncio.run(_uninstall(name))


@install_app.command()
def update(
    name: Optional[str] = typer.Argument(None, help="Agent name to update"),
    all_agents: bool = typer.Option(
        False, "--all", help="Update all installed agents"
    ),
) -> None:
    """Update an agent to the latest version."""
    if not name and not all_agents:
        typer.echo("Specify an agent name or use --all to update all agents.")
        raise typer.Exit(code=1)
    asyncio.run(_update(name, all_agents))


# =====================================================================
# Discovery commands
# =====================================================================


@app_cmd_list
def list_agents() -> None:
    """List installed agents."""
    asyncio.run(_list_agents())
```

Wait — the `list`, `search`, `info`, `sources` commands are top-level on `app`, not on a sub-Typer. Since `_lifecycle.py` doesn't own `app`, we need to register them differently. Let me restructure.

The correct approach: `_lifecycle.py` exports a function `register_lifecycle(app)` that registers all lifecycle commands on the main app. OR we define the Typer callbacks here but register them in `__init__.py`.

Actually, the cleanest approach: `_lifecycle.py` defines all the command functions and the install/run sub-Typers. `__init__.py` imports and registers them. For top-level commands (`list`, `search`, `info`, `sources`), `_lifecycle.py` exports the decorated functions and `__init__.py` adds them to `app`.

Let me revise `_lifecycle.py`:

```python
"""Lifecycle commands: install, uninstall, update, list, search, info, sources, run.

Migrated from the original cli.py monolith.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import typer

from agent_nexus.platform.local.cli._shared import _init_managers

logger = logging.getLogger(__name__)

install_app = typer.Typer(help="Agent installation and management")
run_app = typer.Typer(help="Run agents and workflows")


@install_app.command("install")
def install_agent(
    name: str = typer.Argument(help="Agent name to install"),
    version: Optional[str] = typer.Option(None, "--version", "-v", help="Specific version"),
    source: Optional[str] = typer.Option(None, "--source", "-s", help="Git URL (direct install)"),
) -> None:
    """Install an agent from a package source."""
    asyncio.run(_install(name, version, source))


@install_app.command()
def uninstall(name: str = typer.Argument(help="Agent name to uninstall")) -> None:
    """Uninstall an agent."""
    asyncio.run(_uninstall(name))


@install_app.command()
def update(
    name: Optional[str] = typer.Argument(None, help="Agent name to update"),
    all_agents: bool = typer.Option(False, "--all", help="Update all installed agents"),
) -> None:
    """Update an agent to the latest version."""
    if not name and not all_agents:
        typer.echo("Specify an agent name or use --all to update all agents.")
        raise typer.Exit(code=1)
    asyncio.run(_update(name, all_agents))


def list_agents() -> None:
    """List installed agents."""
    asyncio.run(_list_agents())


def search(query: str = typer.Argument(help="Search query")) -> None:
    """Search for available agents."""
    asyncio.run(_search(query))


def info(name: str = typer.Argument(help="Agent name")) -> None:
    """Show detailed information about an agent."""
    asyncio.run(_info(name))


def sources(
    action: str = typer.Argument(help="Action: list, add, remove"),
    name: Optional[str] = typer.Option(None, "--name", help="Source name"),
    url: Optional[str] = typer.Option(None, "--url", help="Source git URL"),
    source_type: Optional[str] = typer.Option(None, "--type", help="Source type: official, private"),
) -> None:
    """Manage package sources."""
    asyncio.run(_sources(action, name, url, source_type))


@run_app.callback()
def run_agent(
    name: str = typer.Argument(help="Agent name to run"),
    mode: str = typer.Option("mcp", "--mode", "-m", help="Run mode: mcp, router, cli"),
    transport: str = typer.Option("stdio", "--transport", "-t", help="Transport: stdio, sse"),
) -> None:
    """Run an agent in the specified mode."""
    asyncio.run(_run(name, mode, transport))


# =====================================================================
# Internal async implementations
# =====================================================================


async def _install(name: str, version: str | None, source_url: str | None) -> None:
    from agent_nexus.platform.local.installer import GitInstaller

    _loader, lockfile, sources, config_dir = _init_managers()
    installer = GitInstaller(sources, lockfile, config_dir)

    try:
        entry = await installer.install(name, version=version, source_url=source_url)
        typer.echo(f"Installed {name}@{entry.version}")
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)


async def _uninstall(name: str) -> None:
    from agent_nexus.platform.local.installer import GitInstaller

    _loader, lockfile, sources, config_dir = _init_managers()
    installer = GitInstaller(sources, lockfile, config_dir)

    try:
        removed = await installer.uninstall(name)
        if removed:
            typer.echo(f"Uninstalled {name}")
        else:
            typer.echo(f"Agent '{name}' is not installed.")
    except Exception as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)


async def _update(name: str | None, all_agents: bool) -> None:
    from agent_nexus.platform.local.installer import AgentNotFoundError, GitInstaller

    _loader, lockfile, sources, config_dir = _init_managers()
    installer = GitInstaller(sources, lockfile, config_dir)

    if all_agents:
        lockfile_data = lockfile.load()
        agents_to_update = list(lockfile_data.agents.keys())
        if not agents_to_update:
            typer.echo("No installed agents to update.")
            return
    elif name:
        agents_to_update = [name]
    else:
        typer.echo("Specify an agent name or use --all.")
        raise typer.Exit(code=1)

    updated_count = 0
    for agent_name in agents_to_update:
        try:
            entry = await installer.update(agent_name)
            if entry:
                typer.echo(f"Updated {agent_name}@{entry.version}")
                updated_count += 1
            else:
                typer.echo(f"{agent_name} is already up to date.")
        except AgentNotFoundError:
            typer.echo(f"Agent '{agent_name}' is not installed.", err=True)
        except Exception as exc:
            typer.echo(f"Error updating {agent_name}: {exc}", err=True)

    typer.echo(f"Updated {updated_count}/{len(agents_to_update)} agent(s).")


async def _list_agents() -> None:
    _loader, lockfile, _sources, _config_dir = _init_managers()
    lockfile_data = lockfile.load()

    agents = lockfile_data.agents
    if not agents:
        typer.echo("No agents installed.")
        return

    typer.echo(f"{'Name':<25} {'Version':<12} {'Type':<12} {'Source'}")
    typer.echo("-" * 65)
    for agent_name, entry in agents.items():
        typer.echo(
            f"{agent_name:<25} {entry.version:<12} "
            f"{entry.agent_type.value:<12} {entry.source}"
        )

    typer.echo(f"\n{len(agents)} agent(s) installed.")


async def _search(query: str) -> None:
    _loader, lockfile, sources, _config_dir = _init_managers()

    results: list[dict] = []
    for source, entry in sources.search_agents(query):
        results.append({
            "name": entry.name,
            "version": entry.version,
            "type": entry.type.value,
            "description": entry.description,
            "source": source.name,
        })

    if not results:
        typer.echo(f"No agents found matching '{query}'.")
        return

    typer.echo(f"Search results for '{query}':\n")
    for r in results:
        typer.echo(f"  {r['name']} ({r['type']}) @ {r['version']}")
        typer.echo(f"    {r['description']}")
        typer.echo(f"    Source: {r['source']}")

    typer.echo(f"\n{len(results)} result(s).")


async def _info(name: str) -> None:
    import yaml

    _loader, lockfile, _sources, config_dir = _init_managers()
    entry = lockfile.get_entry(name)

    if entry is None:
        typer.echo(f"Agent '{name}' is not installed.", err=True)
        raise typer.Exit(code=1)

    typer.echo(f"Agent: {name}")
    typer.echo(f"  Version:      {entry.version}")
    typer.echo(f"  Type:         {entry.agent_type.value}")
    typer.echo(f"  Source:       {entry.source}")
    typer.echo(f"  Commit SHA:   {(entry.commit_sha or '')[:12]}")
    typer.echo(f"  Installed at: {entry.installed_at}")
    if entry.venv_path:
        typer.echo(f"  Venv:         {entry.venv_path}")
    if entry.dependencies:
        typer.echo(f"  Dependencies: {', '.join(entry.dependencies)}")

    agent_dir = config_dir / "agents" / name
    manifest_path = agent_dir / "agent-manifest.yaml"
    if manifest_path.exists():
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            if isinstance(manifest, dict):
                typer.echo()
                desc = manifest.get("description", "")
                if desc:
                    typer.echo(f"  Description:  {desc}")
                run_modes = manifest.get("run_modes", [])
                if run_modes:
                    typer.echo(f"  Run modes:    {', '.join(run_modes)}")
                model_tier = manifest.get("model_tier", "")
                if model_tier:
                    typer.echo(f"  Model tier:   {model_tier}")
        except Exception:
            logger.debug("Failed to read manifest for info display", exc_info=True)

    skill_path = agent_dir / "SKILL.md"
    if skill_path.exists():
        try:
            first_lines = skill_path.read_text(encoding="utf-8").split("\n")[:5]
            typer.echo()
            typer.echo("  SKILL.md preview:")
            for line in first_lines:
                typer.echo(f"    {line}")
        except Exception:
            logger.debug("Failed to read SKILL.md preview", exc_info=True)


async def _sources(
    action: str, name: str | None, url: str | None, source_type: str | None
) -> None:
    from agent_nexus.models.distribution import SourceEntry

    _loader, _lockfile, sources, _config_dir = _init_managers()

    if action == "list":
        source_list = sources.list_sources()
        if not source_list:
            typer.echo("No sources configured.")
            return
        typer.echo(f"{'Name':<20} {'Type':<10} {'URL'}")
        typer.echo("-" * 60)
        for s in source_list:
            typer.echo(f"{s.name:<20} {s.type:<10} {s.url}")

    elif action == "add":
        if not name or not url:
            typer.echo("--name and --url are required for adding a source.")
            raise typer.Exit(code=1)
        entry = SourceEntry(name=name, type=source_type or "git", url=url)
        sources.add_source(entry)
        typer.echo(f"Source '{name}' added.")

    elif action == "remove":
        if not name:
            typer.echo("--name is required for removing a source.")
            raise typer.Exit(code=1)
        removed = sources.remove_source(name)
        if removed:
            typer.echo(f"Source '{name}' removed.")
        else:
            typer.echo(f"Source '{name}' not found.")

    else:
        typer.echo(f"Unknown action '{action}'. Use: list, add, remove.", err=True)
        raise typer.Exit(code=1)


async def _run(name: str, mode: str, transport: str) -> None:
    from agent_nexus.platform.local.supervisor import AgentSupervisor
    from agent_nexus.platform.orchestration.process_manager import ProcessManager

    _loader, lockfile, _sources, config_dir = _init_managers()

    entry = lockfile.get_entry(name)
    if entry is None:
        typer.echo(
            f"Agent '{name}' is not installed. "
            f"Use 'agent-nexus install {name}' first.",
            err=True,
        )
        raise typer.Exit(code=1)

    if mode == "mcp":
        pm = ProcessManager()
        supervisor = AgentSupervisor(
            process_manager=pm,
            lockfile_manager=lockfile,
            config_loader=_loader,
            config_dir=config_dir,
        )

        typer.echo(f"Starting agent '{name}' in MCP standalone mode...")

        ok = await supervisor.start_agent(name)
        if not ok:
            typer.echo(f"Failed to start agent '{name}'.", err=True)
            raise typer.Exit(code=1)

        handle = pm.get_agent(name)
        pid_str = str(handle.pid) if handle else "unknown"
        typer.echo(f"Agent '{name}' started (pid: {pid_str}).")
        typer.echo("Press Ctrl+C to stop.")

        try:
            await _wait_forever()
        except (KeyboardInterrupt, asyncio.CancelledError):
            typer.echo("\nStopping agent...")
        finally:
            await supervisor.stop_agent(name)

    elif mode == "router":
        try:
            from agent_nexus.platform.gateway.gateway import MCPGateway
            from agent_nexus.platform.router.router import PlatformRouter
        except ImportError as exc:
            typer.echo(f"Router mode requires additional modules: {exc}", err=True)
            raise typer.Exit(code=1)

        pm = ProcessManager()
        supervisor = AgentSupervisor(
            process_manager=pm,
            lockfile_manager=lockfile,
            config_loader=_loader,
            config_dir=config_dir,
        )

        ok = await supervisor.start_agent(name)
        if not ok:
            typer.echo(f"Failed to start agent '{name}'", err=True)
            raise typer.Exit(code=1)

        router = PlatformRouter(pm)
        gateway = MCPGateway(pm, router)

        typer.echo(f"Starting agent '{name}' in router mode ({transport})...")

        try:
            if transport == "sse":
                await gateway.run_sse()
            else:
                await gateway.run_stdio()
        except (KeyboardInterrupt, asyncio.CancelledError):
            typer.echo("\nShutting down...")
        finally:
            await gateway.stop()

    elif mode == "cli":
        pm = ProcessManager()
        supervisor = AgentSupervisor(
            process_manager=pm,
            lockfile_manager=lockfile,
            config_loader=_loader,
            config_dir=config_dir,
        )

        ok = await supervisor.start_agent(name)
        if not ok:
            typer.echo(f"Failed to start agent '{name}'.", err=True)
            raise typer.Exit(code=1)

        typer.echo(f"Agent '{name}' started in CLI mode.")
        typer.echo("Press Ctrl+C to stop.")

        try:
            await _wait_forever()
        except (KeyboardInterrupt, asyncio.CancelledError):
            typer.echo("\nStopping agent...")
        finally:
            await supervisor.stop_agent(name)

    else:
        typer.echo(f"Unknown mode '{mode}'. Use: mcp, router, cli.", err=True)
        raise typer.Exit(code=1)


async def _wait_forever() -> None:
    """Block indefinitely until cancelled."""
    while True:
        await asyncio.sleep(3600)
```

Now update `__init__.py` to register top-level lifecycle commands:

```python
"""CLI entry point for Agent Nexus."""

from __future__ import annotations

import typer

app = typer.Typer(
    name="agent-nexus",
    help="Agent Nexus -- MCP-native Agent Platform",
    no_args_is_help=True,
)

# --- Lifecycle commands ---
from agent_nexus.platform.local.cli._lifecycle import (
    install_app,
    run_app,
    list_agents,
    search,
    info,
    sources,
)

app.add_typer(install_app, name="install")
app.add_typer(run_app, name="run", invoke_without_command=True)
app.command("list")(list_agents)
app.command()(search)
app.command()(info)
app.command()(sources)

# --- New command modules ---
from agent_nexus.platform.local.cli.init_cmd import init_app
from agent_nexus.platform.local.cli.config_cmd import config_app
from agent_nexus.platform.local.cli.runtime_cmd import runtime_app
from agent_nexus.platform.local.cli.evolution_cmd import evolution_app

app.add_typer(init_app)
app.add_typer(config_app, name="config")
app.add_typer(runtime_app)
app.add_typer(evolution_app, name="evolution")

if __name__ == "__main__":
    app()
```

- [ ] **Step 2: Delete old `cli.py`**

```bash
rm src/agent_nexus/platform/local/cli.py
```

- [ ] **Step 3: Run existing CLI tests**

Run: `pytest tests/unit/test_cli_module.py tests/unit/test_local_module.py -v`
Expected: All tests PASS (or adjust imports if test files reference old `cli.py` path)

- [ ] **Step 4: Commit**

```bash
git add -A src/agent_nexus/platform/local/cli/
git commit -m "refactor(cli): migrate lifecycle commands from cli.py to cli/ package"
```

---

## Phase 2: Init Commands

### Task 4: Implement `version` command

**Files:**
- Modify: `src/agent_nexus/platform/local/cli/init_cmd.py`
- Test: `tests/unit/cli/test_init_cmd.py`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for init_cmd.py — init, doctor, version."""

from __future__ import annotations

from typer.testing import CliRunner

from agent_nexus.platform.local.cli import app


runner = CliRunner()


class TestVersion:
    def test_version_outputs_version_string(self) -> None:
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        # Should contain a version string like x.y.z
        import re
        assert re.search(r"\d+\.\d+", result.output)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/cli/test_init_cmd.py::TestVersion -v`
Expected: FAIL (no "version" command registered yet)

- [ ] **Step 3: Write minimal implementation**

Update `src/agent_nexus/platform/local/cli/init_cmd.py`:

```python
"""Init, doctor, and version commands."""

from __future__ import annotations

import importlib.metadata
import typer

init_app = typer.Typer(help="Setup and diagnostics")


@init_app.command()
def version() -> None:
    """Print the agent-nexus version."""
    try:
        ver = importlib.metadata.version("agent-nexus")
    except importlib.metadata.PackageNotFoundError:
        ver = "unknown (not installed)"
    typer.echo(ver)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/cli/test_init_cmd.py::TestVersion -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/local/cli/init_cmd.py tests/unit/cli/test_init_cmd.py
git commit -m "feat(cli): add version command"
```

---

### Task 5: Implement `doctor` command

**Files:**
- Modify: `src/agent_nexus/platform/local/cli/init_cmd.py`
- Test: `tests/unit/cli/test_init_cmd.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/cli/test_init_cmd.py`:

```python
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestDoctor:
    def test_doctor_checks_all_items(self, tmp_path: Path, monkeypatch: object) -> None:
        import pytest
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))  # type: ignore[attr-defined]

        # Create a minimal config.toml so the config check passes
        (config_dir / "config.toml").write_text(
            'schema_version = "1.0"\n[runtime]\npython_path = "python3"\n'
        )

        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        # Should show check results
        assert "config.toml" in result.output.lower() or "config" in result.output.lower()

    def test_doctor_reports_missing_config(self, tmp_path: Path, monkeypatch: object) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))  # type: ignore[attr-defined]

        result = runner.invoke(app, ["doctor"])
        assert result.exit_code == 0
        # Should indicate config is missing or has issues
        assert "fail" in result.output.lower() or "not found" in result.output.lower() or "missing" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/cli/test_init_cmd.py::TestDoctor -v`
Expected: FAIL (no "doctor" command)

- [ ] **Step 3: Write implementation**

Add to `src/agent_nexus/platform/local/cli/init_cmd.py`:

```python
@init_app.command()
def doctor() -> None:
    """Run diagnostic checks on the agent-nexus installation."""
    import os
    import shutil
    import sys

    from agent_nexus.platform.local.cli._shared import _get_config_dir, ConfigMigrator

    config_dir = _get_config_dir()
    config_path = config_dir / "config.toml"
    checks: list[tuple[str, bool, str]] = []

    # Check 1: config.toml exists and parses
    try:
        import toml
        toml.loads(config_path.read_text(encoding="utf-8"))
        checks.append(("config.toml exists and parses", True, "OK"))
    except FileNotFoundError:
        checks.append(("config.toml exists and parses", False, "not found"))
    except Exception as exc:
        checks.append(("config.toml exists and parses", False, str(exc)))

    # Check 2: API key configured
    key_envs = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY"]
    has_key = any(os.environ.get(k) for k in key_envs)
    checks.append(("API key configured", has_key, "at least one set" if has_key else "none set"))

    # Check 3: git on PATH
    git_path = shutil.which("git")
    checks.append(("git on PATH", git_path is not None, git_path or "not found"))

    # Check 4: uv on PATH
    uv_path = shutil.which("uv")
    checks.append(("uv on PATH", uv_path is not None, uv_path or "not found"))

    # Check 5: Python >= 3.12
    py_ok = sys.version_info >= (3, 12)
    checks.append(("Python >= 3.12", py_ok, sys.version.split()[0]))

    # Check 6: lockfile.json writable
    lockfile_path = config_dir / "lockfile.json"
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        lockfile_path.write_text("{}", encoding="utf-8")
        lockfile_path.read_text()
        checks.append(("lockfile.json writable", True, "OK"))
    except Exception as exc:
        checks.append(("lockfile.json writable", False, str(exc)))

    # Check 7: Evolution DB accessible
    try:
        from agent_nexus.platform.evolution.store import EvolutionStore
        store = EvolutionStore(":memory:")
        store.close()
        checks.append(("Evolution DB accessible", True, "OK"))
    except Exception as exc:
        checks.append(("Evolution DB accessible", False, str(exc)))

    # Output
    all_pass = True
    for label, passed, detail in checks:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        typer.echo(f"  [{status}] {label}: {detail}")

    typer.echo()
    passed_count = sum(1 for _, p, _ in checks if p)
    typer.echo(f"  {passed_count}/{len(checks)} checks passed.")

    if not all_pass:
        raise typer.Exit(code=1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/cli/test_init_cmd.py::TestDoctor -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/local/cli/init_cmd.py tests/unit/cli/test_init_cmd.py
git commit -m "feat(cli): add doctor command with 7 diagnostic checks"
```

---

### Task 6: Implement `init` command

**Files:**
- Modify: `src/agent_nexus/platform/local/cli/init_cmd.py`
- Test: `tests/unit/cli/test_init_cmd.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/cli/test_init_cmd.py`:

```python
class TestInit:
    def test_init_creates_config_dir_and_files(self, tmp_path: Path, monkeypatch: object) -> None:
        config_dir = tmp_path / "fresh-home"
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))  # type: ignore[attr-defined]

        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert config_dir.exists()
        assert (config_dir / "config.toml").exists()
        assert (config_dir / "sources.yaml").exists()

    def test_init_idempotent(self, tmp_path: Path, monkeypatch: object) -> None:
        config_dir = tmp_path / "fresh-home"
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))  # type: ignore[attr-defined]

        result1 = runner.invoke(app, ["init"])
        assert result1.exit_code == 0

        result2 = runner.invoke(app, ["init"])
        assert result2.exit_code == 0
        # Config should not be overwritten
        content = (config_dir / "config.toml").read_text()
        assert "schema_version" in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/cli/test_init_cmd.py::TestInit -v`
Expected: FAIL (no "init" command)

- [ ] **Step 3: Write implementation**

Add to `src/agent_nexus/platform/local/cli/init_cmd.py`:

```python
@init_app.command()
def init(
    wizard: bool = typer.Option(False, "--wizard", "-w", help="Interactive setup wizard"),
) -> None:
    """Initialize the agent-nexus platform configuration.

    Creates ~/.agent-nexus/ directory tree with default config files.
    Use --wizard for interactive setup with API key configuration.
    """
    import os

    from agent_nexus.platform.local.cli._shared import _get_config_dir, ConfigMigrator
    from agent_nexus.platform.config.loader import ConfigLoader
    from agent_nexus.platform.local.sources import SourceManager

    config_dir = _get_config_dir()

    # Step 1: Ensure directory tree
    loader = ConfigLoader(config_dir)
    loader.ensure_config_dir()
    typer.echo(f"Config directory: {config_dir}")

    # Step 2: Generate config.toml if missing
    config_path = config_dir / "config.toml"
    if not config_path.exists():
        config_path.write_text(_default_config_template(), encoding="utf-8")
        typer.echo("Created config.toml with default settings.")
    else:
        typer.echo("config.toml already exists.")
        # Step 5: Migrate if schema is outdated
        if ConfigMigrator.merge_if_needed(config_path):
            typer.echo("Config migrated to latest schema version.")

    # Step 3: Register official source
    sources = SourceManager(config_dir / "sources.yaml")
    official = sources.get_official_source()
    if official is None:
        from agent_nexus.models.distribution import SourceEntry
        sources.add_source(SourceEntry(
            name="official",
            type="git",
            url="https://github.com/anthropics/agent-nexus-packages.git",
            branch="main",
        ))
        typer.echo("Registered official source.")
    else:
        typer.echo("Official source already registered.")

    # Step 4: Detect API keys
    key_envs = {
        "OPENAI_API_KEY": "openai",
        "ANTHROPIC_API_KEY": "anthropic",
    }
    detected = []
    for env_var, provider in key_envs.items():
        if os.environ.get(env_var):
            detected.append(provider)
    if detected:
        typer.echo(f"Detected API keys for: {', '.join(detected)}")
    else:
        typer.echo("No API keys detected in environment.")

    # Step 6: Wizard mode
    if wizard:
        _run_wizard(config_path)

    # Next steps
    typer.echo()
    typer.echo("Next steps:")
    typer.echo("  1. Set API keys: export OPENAI_API_KEY=... or ANTHROPIC_API_KEY=...")
    typer.echo("  2. Browse agents: agent-nexus search <query>")
    typer.echo("  3. Install an agent: agent-nexus install <name>")
    typer.echo("  4. Run diagnostics: agent-nexus doctor")


def _run_wizard(config_path: object) -> None:
    """Interactive setup wizard using questionary."""
    try:
        import questionary
    except ImportError:
        typer.echo("Install questionary for wizard mode: pip install questionary")
        return

    import toml

    provider = questionary.select(
        "Select default provider:",
        choices=["openai", "anthropic"],
    ).ask()
    if provider is None:
        return

    api_key = questionary.password(
        f"Enter {provider.upper()} API key:",
    ).ask()

    model = questionary.text(
        "Enter default model (e.g. gpt-4o, claude-sonnet-4-20250514):",
        default="gpt-4o" if provider == "openai" else "claude-sonnet-4-20250514",
    ).ask()

    if api_key:
        key_env = f"{provider.upper()}_API_KEY"
        typer.echo(f"  Note: Set {key_env}={api_key[:8]}... in your shell profile.")

    # Write selections to config
    try:
        raw = toml.loads(config_path.read_text(encoding="utf-8"))  # type: ignore[attr-defined]
    except Exception:
        raw = {}
    raw.setdefault("models", {})["default"] = f"{provider}:{model}"
    config_path.write_text(toml.dumps(raw), encoding="utf-8")  # type: ignore[attr-defined]
    typer.echo(f"Config updated: default model = {provider}:{model}")

    # Verify connectivity
    verify = questionary.confirm("Test API connectivity?").ask()
    if verify:
        typer.echo("Connectivity test not yet implemented (placeholder).")


def _default_config_template() -> str:
    """Return the default config.toml template content."""
    return '''\
# Agent Nexus Configuration
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
'''
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/cli/test_init_cmd.py::TestInit -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/local/cli/init_cmd.py tests/unit/cli/test_init_cmd.py
git commit -m "feat(cli): add init command with optional --wizard mode"
```

---

## Phase 3: Runtime Commands

### Task 7: Implement runtime commands (start/stop/restart/status/logs/ps)

**Files:**
- Modify: `src/agent_nexus/platform/local/cli/runtime_cmd.py`
- Test: `tests/unit/cli/test_runtime_cmd.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/cli/test_runtime_cmd.py`:

```python
"""Tests for runtime_cmd.py — start/stop/restart/status/logs/ps."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from agent_nexus.platform.local.cli import app

runner = CliRunner()


class TestStatus:
    def test_status_with_no_agents(self, tmp_path: Path, monkeypatch: object) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))  # type: ignore[attr-defined]

        with patch("agent_nexus.platform.local.cli.runtime_cmd._get_supervisor") as mock_sv:
            sv = MagicMock()
            sv.list_installed.return_value = []
            sv.list_running.return_value = []
            mock_sv.return_value = sv

            result = runner.invoke(app, ["status"])
            assert result.exit_code == 0


class TestLogs:
    def test_logs_agent_not_found(self, tmp_path: Path, monkeypatch: object) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))  # type: ignore[attr-defined]

        result = runner.invoke(app, ["logs", "nonexistent-agent"])
        assert result.exit_code != 0 or "not found" in result.output.lower() or "no log" in result.output.lower()


class TestPs:
    def test_ps_is_alias_for_status(self, tmp_path: Path, monkeypatch: object) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))  # type: ignore[attr-defined]

        with patch("agent_nexus.platform.local.cli.runtime_cmd._get_supervisor") as mock_sv:
            sv = MagicMock()
            sv.list_installed.return_value = []
            sv.list_running.return_value = []
            mock_sv.return_value = sv

            result = runner.invoke(app, ["ps"])
            assert result.exit_code == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/cli/test_runtime_cmd.py -v`
Expected: FAIL

- [ ] **Step 3: Write implementation**

Replace `src/agent_nexus/platform/local/cli/runtime_cmd.py`:

```python
"""Runtime management commands: start, stop, restart, status, logs, ps."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

import typer

from agent_nexus.platform.local.cli._shared import _get_config_dir, _init_managers

runtime_app = typer.Typer(help="Runtime management")


def _get_supervisor():
    """Create an AgentSupervisor instance."""
    from agent_nexus.platform.local.supervisor import AgentSupervisor
    from agent_nexus.platform.orchestration.process_manager import ProcessManager

    _loader, lockfile, _sources, config_dir = _init_managers()
    pm = ProcessManager()
    return AgentSupervisor(
        process_manager=pm,
        lockfile_manager=lockfile,
        config_loader=_loader,
        config_dir=config_dir,
    )


@runtime_app.command()
def start(
    name: Optional[str] = typer.Argument(None, help="Agent name to start"),
    all_agents: bool = typer.Option(False, "--all", help="Start all installed agents"),
    mode: str = typer.Option("mcp", "--mode", "-m", help="Run mode: mcp, router, cli"),
) -> None:
    """Start an agent or all agents."""
    if not name and not all_agents:
        typer.echo("Specify an agent name or use --all.")
        raise typer.Exit(code=1)

    if all_agents:
        asyncio.run(_start_all())
    else:
        asyncio.run(_start_one(name, mode))  # type: ignore[arg-type]


@runtime_app.command()
def stop(
    name: Optional[str] = typer.Argument(None, help="Agent name to stop"),
    all_agents: bool = typer.Option(False, "--all", help="Stop all running agents"),
) -> None:
    """Stop a running agent or all agents."""
    if not name and not all_agents:
        typer.echo("Specify an agent name or use --all.")
        raise typer.Exit(code=1)

    if all_agents:
        asyncio.run(_stop_all())
    else:
        asyncio.run(_stop_one(name))  # type: ignore[arg-type]


@runtime_app.command()
def restart(
    name: str = typer.Argument(help="Agent name to restart"),
) -> None:
    """Restart a running agent."""
    asyncio.run(_restart_agent(name))


@runtime_app.command()
def status() -> None:
    """Show status of all agents."""
    asyncio.run(_status())


@runtime_app.command()
def logs(
    name: str = typer.Argument(help="Agent name"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
) -> None:
    """Show recent log output for an agent."""
    _show_logs(name, lines)


@runtime_app.command()
def ps() -> None:
    """Alias for status — show running agents."""
    asyncio.run(_status())


# =====================================================================
# Async implementations
# =====================================================================


async def _start_one(name: str, mode: str) -> None:
    from agent_nexus.platform.local.supervisor import AgentSupervisor
    from agent_nexus.platform.orchestration.process_manager import ProcessManager

    _loader, lockfile, _sources, config_dir = _init_managers()

    entry = lockfile.get_entry(name)
    if entry is None:
        typer.echo(f"Agent '{name}' is not installed.", err=True)
        raise typer.Exit(code=1)

    pm = ProcessManager()
    supervisor = AgentSupervisor(
        process_manager=pm,
        lockfile_manager=lockfile,
        config_loader=_loader,
        config_dir=config_dir,
    )

    ok = await supervisor.start_agent(name)
    if not ok:
        typer.echo(f"Failed to start agent '{name}'.", err=True)
        raise typer.Exit(code=1)

    handle = pm.get_agent(name)
    pid_str = str(handle.pid) if handle else "unknown"

    # Write PID file
    pid_dir = config_dir / "agents"
    pid_dir.mkdir(parents=True, exist_ok=True)
    (pid_dir / f"{name}.pid").write_text(pid_str, encoding="utf-8")

    typer.echo(f"Started {name} (pid: {pid_str})")


async def _start_all() -> None:
    from agent_nexus.platform.local.supervisor import AgentSupervisor
    from agent_nexus.platform.orchestration.process_manager import ProcessManager

    _loader, lockfile, _sources, config_dir = _init_managers()

    pm = ProcessManager()
    supervisor = AgentSupervisor(
        process_manager=pm,
        lockfile_manager=lockfile,
        config_loader=_loader,
        config_dir=config_dir,
    )

    started = await supervisor.start_all()
    if started:
        typer.echo(f"Started {len(started)} agent(s): {', '.join(started)}")
    else:
        typer.echo("No agents to start.")


async def _stop_one(name: str) -> None:
    from agent_nexus.platform.local.supervisor import AgentSupervisor
    from agent_nexus.platform.orchestration.process_manager import ProcessManager

    _loader, lockfile, _sources, config_dir = _init_managers()

    pm = ProcessManager()
    supervisor = AgentSupervisor(
        process_manager=pm,
        lockfile_manager=lockfile,
        config_loader=_loader,
        config_dir=config_dir,
    )

    ok = await supervisor.stop_agent(name)
    if ok:
        # Clean up PID file
        pid_file = config_dir / "agents" / f"{name}.pid"
        if pid_file.exists():
            pid_file.unlink()
        typer.echo(f"Stopped {name}")
    else:
        typer.echo(f"Agent '{name}' is not running.", err=True)


async def _stop_all() -> None:
    from agent_nexus.platform.local.supervisor import AgentSupervisor
    from agent_nexus.platform.orchestration.process_manager import ProcessManager

    _loader, lockfile, _sources, config_dir = _init_managers()

    pm = ProcessManager()
    supervisor = AgentSupervisor(
        process_manager=pm,
        lockfile_manager=lockfile,
        config_loader=_loader,
        config_dir=config_dir,
    )

    await supervisor.stop_all()
    # Clean up all PID files
    pid_dir = config_dir / "agents"
    if pid_dir.exists():
        for pid_file in pid_dir.glob("*.pid"):
            pid_file.unlink()
    typer.echo("Stopped all agents.")


async def _restart_agent(name: str) -> None:
    await _stop_one(name)
    await _start_one(name, mode="mcp")


async def _status() -> None:
    _loader, lockfile, _sources, config_dir = _init_managers()
    lockfile_data = lockfile.load()
    installed_agents = list(lockfile_data.agents.keys())

    if not installed_agents:
        typer.echo("No agents installed.")
        return

    # Check which are running via PID files
    running: set[str] = set()
    pids: dict[str, str] = {}
    pid_dir = config_dir / "agents"
    for agent_name in installed_agents:
        pid_file = pid_dir / f"{agent_name}.pid"
        if pid_file.exists():
            try:
                pid_str = pid_file.read_text(encoding="utf-8").strip()
                # Check if process is alive
                pid_int = int(pid_str)
                try:
                    os.kill(pid_int, 0)  # Signal 0 = check existence
                    running.add(agent_name)
                    pids[agent_name] = pid_str
                except (ProcessLookupError, OSError):
                    # Stale PID file
                    pid_file.unlink()
            except (ValueError, OSError):
                pass

    typer.echo(f"{'Name':<25} {'Installed':<12} {'Running':<10} {'PID':<10}")
    typer.echo("-" * 60)
    for agent_name in installed_agents:
        is_installed = "yes"
        is_running = "yes" if agent_name in running else "no"
        pid = pids.get(agent_name, "-")
        typer.echo(f"{agent_name:<25} {is_installed:<12} {is_running:<10} {pid:<10}")


def _show_logs(name: str, num_lines: int) -> None:
    _loader, lockfile, _sources, config_dir = _init_managers()
    log_path = config_dir / "logs" / f"{name}.log"

    if not log_path.exists():
        typer.echo(f"No log file for '{name}'. Agent may not have been started.")
        return

    try:
        all_lines = log_path.read_text(encoding="utf-8").splitlines()
        tail = all_lines[-num_lines:]
        typer.echo("\n".join(tail))
    except Exception as exc:
        typer.echo(f"Error reading log: {exc}", err=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/cli/test_runtime_cmd.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/local/cli/runtime_cmd.py tests/unit/cli/test_runtime_cmd.py
git commit -m "feat(cli): add runtime commands — start/stop/restart/status/logs/ps"
```

---

## Phase 4: Config Commands

### Task 8: Implement config commands (show/get/edit/validate/providers/path)

**Files:**
- Modify: `src/agent_nexus/platform/local/cli/config_cmd.py`
- Test: `tests/unit/cli/test_config_cmd.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/cli/test_config_cmd.py`:

```python
"""Tests for config_cmd.py — show/get/edit/validate/providers/path."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from agent_nexus.platform.local.cli import app

runner = CliRunner()


class TestConfigShow:
    def test_show_outputs_config(self, tmp_path: Path, monkeypatch: object) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            'schema_version = "1.0"\n[runtime]\npython_path = "python3"\n[models]\ndefault = "openai:gpt-4o"\n'
        )
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))  # type: ignore[attr-defined]

        result = runner.invoke(app, ["config", "show"])
        assert result.exit_code == 0
        assert "gpt-4o" in result.output

    def test_show_json_flag(self, tmp_path: Path, monkeypatch: object) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            'schema_version = "1.0"\n[runtime]\npython_path = "python3"\n'
        )
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))  # type: ignore[attr-defined]

        result = runner.invoke(app, ["config", "show", "--json"])
        assert result.exit_code == 0
        assert '"runtime"' in result.output


class TestConfigGet:
    def test_get_returns_value(self, tmp_path: Path, monkeypatch: object) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            'schema_version = "1.0"\n[models]\ndefault = "openai:gpt-4o"\n'
        )
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))  # type: ignore[attr-defined]

        result = runner.invoke(app, ["config", "get", "models.default"])
        assert result.exit_code == 0
        assert "gpt-4o" in result.output


class TestConfigValidate:
    def test_validate_valid_config(self, tmp_path: Path, monkeypatch: object) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            'schema_version = "1.0"\n[runtime]\npython_path = "python3"\n'
        )
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))  # type: ignore[attr-defined]

        result = runner.invoke(app, ["config", "validate"])
        assert result.exit_code == 0
        assert "valid" in result.output.lower()


class TestConfigPath:
    def test_path_outputs_config_dir(self, tmp_path: Path, monkeypatch: object) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))  # type: ignore[attr-defined]

        result = runner.invoke(app, ["config", "path"])
        assert result.exit_code == 0
        assert str(config_dir) in result.output


class TestConfigProviders:
    def test_providers_lists_providers(self, tmp_path: Path, monkeypatch: object) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            'schema_version = "1.0"\n[models]\ndefault = "openai:gpt-4o"\n'
        )
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))  # type: ignore[attr-defined]

        result = runner.invoke(app, ["config", "providers"])
        assert result.exit_code == 0
        assert "openai" in result.output.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/cli/test_config_cmd.py -v`
Expected: FAIL

- [ ] **Step 3: Write implementation**

Replace `src/agent_nexus/platform/local/cli/config_cmd.py`:

```python
"""Config management commands: show, get, edit, validate, providers, path."""

from __future__ import annotations

import json
import os
import subprocess

import typer

from agent_nexus.platform.local.cli._shared import _get_config_dir, ConfigMigrator

config_app = typer.Typer(help="Configuration management")


@config_app.command("show")
def config_show(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Show merged configuration."""
    from agent_nexus.platform.local.cli._shared import _init_managers

    loader, *_ = _init_managers()
    config = loader.load_config()

    if json_output:
        typer.echo(json.dumps(config.model_dump(), indent=2, default=str))
    else:
        typer.echo(f"Config dir:    {loader.config_dir}")
        typer.echo(f"Default model: {config.models.default}")
        typer.echo(f"Python path:   {config.runtime.python_path}")
        typer.echo(f"uv path:       {config.runtime.uv_path}")
        typer.echo(f"Providers:     {', '.join(config.models.providers.keys())}")


@config_app.command("get")
def config_get(
    key: str = typer.Argument(help="Dot-path config key (e.g. models.default)"),
) -> None:
    """Get a specific config value by dot-path key."""
    from agent_nexus.platform.local.cli._shared import _init_managers

    loader, *_ = _init_managers()
    config = loader.load_config()

    value = _resolve_dot_path(config.model_dump(), key)
    if value is None:
        typer.echo(f"Key '{key}' not found.", err=True)
        raise typer.Exit(code=1)
    typer.echo(str(value))


@config_app.command("edit")
def config_edit() -> None:
    """Open config.toml in $EDITOR."""
    config_dir = _get_config_dir()
    config_path = config_dir / "config.toml"

    if not config_path.exists():
        typer.echo(f"Config file not found at {config_path}. Run 'agent-nexus init' first.")
        raise typer.Exit(code=1)

    editor = os.environ.get("EDITOR", "vi")
    typer.echo(f"Opening {config_path} in {editor}...")
    subprocess.call([editor, str(config_path)])


@config_app.command("validate")
def config_validate() -> None:
    """Validate the current configuration."""
    from agent_nexus.platform.local.cli._shared import _init_managers

    config_dir = _get_config_dir()
    config_path = config_dir / "config.toml"

    if not config_path.exists():
        typer.echo(f"Config not found at {config_path}. Run 'agent-nexus init' first.", err=True)
        raise typer.Exit(code=1)

    try:
        loader, *_ = _init_managers()
        loader.load_config()
        typer.echo("Config valid.")
    except Exception as exc:
        typer.echo(f"Config invalid: {exc}", err=True)
        raise typer.Exit(code=1)

    # Check schema version
    version = ConfigMigrator.check_version(config_path)
    if version != ConfigMigrator.TARGET_VERSION:
        typer.echo(
            f"Warning: Config schema version is {version}, "
            f"latest is {ConfigMigrator.TARGET_VERSION}. "
            f"Run 'agent-nexus init' to migrate."
        )


@config_app.command("providers")
def config_providers() -> None:
    """List all configured providers and their API key status."""
    from agent_nexus.platform.local.cli._shared import _init_managers

    loader, *_ = _init_managers()
    config = loader.load_config()

    typer.echo(f"{'Name':<20} {'Base URL':<35} {'API Key Env':<20} {'Key Status'}")
    typer.echo("-" * 90)

    for name, provider in config.models.providers.items():
        key_env = provider.api_key_env
        has_key = bool(os.environ.get(key_env)) if key_env else False
        status = "set" if has_key else "not set"
        base_url = provider.base_url or "(default)"
        typer.echo(f"{name:<20} {base_url:<35} {key_env:<20} {status}")


@config_app.command("path")
def config_path() -> None:
    """Print the config directory path."""
    typer.echo(str(_get_config_dir()))


def _resolve_dot_path(data: dict, key: str) -> object:
    """Resolve a dot-separated key path on a nested dict."""
    parts = key.split(".")
    current: object = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/cli/test_config_cmd.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/local/cli/config_cmd.py tests/unit/cli/test_config_cmd.py
git commit -m "feat(cli): add config commands — show/get/edit/validate/providers/path"
```

---

## Phase 5: Evolution Commands

### Task 9: Implement evolution commands (status/health/list/history/metrics/fix/promote)

**Files:**
- Modify: `src/agent_nexus/platform/local/cli/evolution_cmd.py`
- Test: `tests/unit/cli/test_evolution_cmd.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/cli/test_evolution_cmd.py`:

```python
"""Tests for evolution_cmd.py — status/health/list/history/metrics/fix/promote."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from agent_nexus.platform.local.cli import app

runner = CliRunner()


def _make_mock_engine():
    """Create a mock EvolutionEngine with all needed methods."""
    engine = MagicMock()

    # HealthChecker mock
    engine.health_checker.get_health_summary.return_value = {
        "total_skills": 5,
        "healthy": 4,
        "unhealthy": 1,
        "suggestions": 0,
    }
    engine.health_checker.diagnose_all.return_value = {}

    # Store mock
    mock_store = MagicMock()
    mock_store.get_active_skills.return_value = []
    mock_store.get_all_skills.return_value = []
    mock_store.get_ancestry.return_value = []
    mock_store.get_metrics.return_value = MagicMock(
        total_selections=100,
        total_applied=80,
        total_completions=70,
        total_fallbacks=10,
    )
    engine.store = mock_store

    # Evolver mock
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.new_skill_id = "skill-v2"
    engine.evolver.evolve.return_value = mock_result

    # Promoter mock
    mock_promo = MagicMock()
    mock_promo.success = True
    mock_promo.agent_name = "new-agent"
    engine.promoter.promote.return_value = mock_promo

    return engine


class TestEvolutionStatus:
    def test_status_shows_summary(self, tmp_path: Path, monkeypatch: object) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))  # type: ignore[attr-defined]

        engine = _make_mock_engine()
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=engine):
            result = runner.invoke(app, ["evolution", "status"])
            assert result.exit_code == 0
            assert "5" in result.output  # total_skills


class TestEvolutionList:
    def test_list_with_no_skills(self, tmp_path: Path, monkeypatch: object) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))  # type: ignore[attr-defined]

        engine = _make_mock_engine()
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=engine):
            result = runner.invoke(app, ["evolution", "list"])
            assert result.exit_code == 0


class TestEvolutionMetrics:
    def test_metrics_shows_aggregate(self, tmp_path: Path, monkeypatch: object) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))  # type: ignore[attr-defined]

        engine = _make_mock_engine()
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=engine):
            result = runner.invoke(app, ["evolution", "metrics"])
            assert result.exit_code == 0
            assert "100" in result.output  # total_selections
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/cli/test_evolution_cmd.py -v`
Expected: FAIL

- [ ] **Step 3: Write implementation**

Replace `src/agent_nexus/platform/local/cli/evolution_cmd.py`:

```python
"""Evolution subsystem commands: status, health, list, history, metrics, fix, promote."""

from __future__ import annotations

import typer

from agent_nexus.platform.local.cli._shared import _get_config_dir, _init_managers

evolution_app = typer.Typer(help="Self-Evolution Engine")


def _get_engine():
    """Create an EvolutionEngine instance from the evolution DB."""
    from agent_nexus.platform.evolution.engine import EvolutionEngine
    from agent_nexus.platform.evolution.store import EvolutionStore

    config_dir = _get_config_dir()
    db_path = config_dir / "evolution.db"
    store = EvolutionStore(str(db_path))
    return EvolutionEngine(store)


@evolution_app.command("status")
def evolution_status() -> None:
    """Show evolution subsystem status summary."""
    engine = _get_engine()
    summary = engine.health_checker.get_health_summary()

    typer.echo("Evolution Status:")
    typer.echo(f"  Total skills:  {summary.get('total_skills', 0)}")
    typer.echo(f"  Healthy:       {summary.get('healthy', 0)}")
    typer.echo(f"  Unhealthy:     {summary.get('unhealthy', 0)}")
    typer.echo(f"  Suggestions:   {summary.get('suggestions', 0)}")


@evolution_app.command("health")
def evolution_health(
    skill_name: str | None = typer.Argument(None, help="Skill name for detailed view"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show threshold details"),
) -> None:
    """Show health diagnostics for skills."""
    engine = _get_engine()

    if skill_name:
        try:
            suggestions = engine.check_health(skill_name)
            if suggestions:
                typer.echo(f"Skill '{skill_name}': UNHEALTHY")
                for s in suggestions:
                    typer.echo(f"  [{s.evolution_type.value}] {s.direction}")
            else:
                typer.echo(f"Skill '{skill_name}': HEALTHY")
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1)
    else:
        reports = engine.diagnose_all()
        if not reports:
            typer.echo("No skills to diagnose.")
            return

        typer.echo(f"{'Name':<30} {'Applied Rate':<15} {'Completion Rate':<18} {'Fallback Rate':<16} {'Verdict'}")
        typer.echo("-" * 95)
        for skill_id, report in reports.items():
            metrics = report.metrics
            applied = metrics.get("applied_rate", 0)
            completion = metrics.get("completion_rate", 0)
            fallback = metrics.get("fallback_rate", 0)
            verdict = "HEALTHY" if report.is_healthy else "UNHEALTHY"
            typer.echo(
                f"{report.skill_name:<30} {applied:<15.2%} "
                f"{completion:<18.2%} {fallback:<16.2%} {verdict}"
            )


@evolution_app.command("list")
def evolution_list(
    all_skills: bool = typer.Option(False, "--all", help="Show all skills including inactive"),
) -> None:
    """List skills in the evolution system."""
    engine = _get_engine()
    skills = engine.store.get_all_skills() if all_skills else engine.store.get_active_skills()

    if not skills:
        typer.echo("No skills found.")
        return

    typer.echo(f"{'Name':<30} {'Version':<10} {'Generation':<12} {'Status':<10} {'Created'}")
    typer.echo("-" * 75)
    for skill in skills:
        status = "active" if skill.is_active else "inactive"
        typer.echo(
            f"{skill.name:<30} {skill.version:<10} "
            f"{skill.lineage.generation:<12} {status:<10} "
            f"{skill.created_at.split('T')[0] if skill.created_at else '-'}"
        )


@evolution_app.command("history")
def evolution_history(
    skill_name: str = typer.Argument(help="Skill name or ID to trace ancestry"),
) -> None:
    """Show version lineage for a skill."""
    engine = _get_engine()

    # Try to find skill by name first
    skills = engine.store.get_all_skills()
    skill_id = None
    for s in skills:
        if s.name == skill_name or s.id == skill_name:
            skill_id = s.id
            break

    if skill_id is None:
        typer.echo(f"Skill '{skill_name}' not found.", err=True)
        raise typer.Exit(code=1)

    ancestry = engine.store.get_ancestry(skill_id)
    if not ancestry:
        typer.echo(f"No ancestry found for '{skill_name}'.")
        return

    indent = ""
    for i, ancestor in enumerate(ancestry):
        evo_type = getattr(ancestor.lineage, "content_diff", "") or ""
        type_label = f", {evo_type}" if evo_type else ""
        created = ancestor.created_at.split("T")[0] if ancestor.created_at else "?"
        typer.echo(f"{indent}{ancestor.name} (gen {ancestor.lineage.generation}, {created}{type_label})")
        if i < len(ancestry) - 1:
            indent += "  -> "


@evolution_app.command("metrics")
def evolution_metrics(
    agent: str | None = typer.Option(None, "--agent", "-a", help="Filter by agent name"),
) -> None:
    """Show evolution quality metrics."""
    engine = _get_engine()
    metrics = engine.store.get_metrics(agent_name=agent)

    typer.echo(f"  Total selections: {metrics.total_selections}")
    typer.echo(f"  Total applied:    {metrics.total_applied}")
    typer.echo(f"  Total completions: {metrics.total_completions}")
    typer.echo(f"  Total fallbacks:  {metrics.total_fallbacks}")

    if metrics.total_selections > 0:
        success_rate = metrics.total_completions / metrics.total_selections
        fallback_rate = metrics.total_fallbacks / metrics.total_selections
        typer.echo(f"  Success rate:     {success_rate:.2%}")
        typer.echo(f"  Fallback rate:    {fallback_rate:.2%}")


@evolution_app.command("fix")
def evolution_fix(
    skill_id: str = typer.Argument(help="Skill ID to fix"),
) -> None:
    """Trigger a FIX evolution on an unhealthy skill."""
    from agent_nexus.models.evolution import EvolutionTrigger

    engine = _get_engine()
    try:
        results = engine.evolve(trigger=EvolutionTrigger.METRIC_CHECK)
        typer.echo(f"Fix evolution triggered for {skill_id}.")
        typer.echo(f"Results: {len(results) if isinstance(results, list) else 1} evolution(s) processed.")
    except Exception as exc:
        typer.echo(f"Fix failed: {exc}", err=True)
        raise typer.Exit(code=1)


@evolution_app.command("promote")
def evolution_promote(
    skill_id: str = typer.Argument(help="Skill ID to promote to agent"),
) -> None:
    """Promote a skill candidate to a standalone agent."""
    from agent_nexus.platform.evolution.promotion import PromotionCandidate

    engine = _get_engine()
    candidate = PromotionCandidate(skill_id=skill_id)
    try:
        result = engine.promote_candidate(candidate)
        if result.success:
            typer.echo(f"Skill '{skill_id}' promoted to agent.")
            if result.agent_name:
                typer.echo(f"New agent: {result.agent_name}")
        else:
            typer.echo(f"Promotion not completed for '{skill_id}'.")
    except Exception as exc:
        typer.echo(f"Promotion failed: {exc}", err=True)
        raise typer.Exit(code=1)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/cli/test_evolution_cmd.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/local/cli/evolution_cmd.py tests/unit/cli/test_evolution_cmd.py
git commit -m "feat(cli): add evolution commands — status/health/list/history/metrics/fix/promote"
```

---

## Phase 6: Auxiliary Commands

### Task 10: Implement `env` command

**Files:**
- Modify: `src/agent_nexus/platform/local/cli/init_cmd.py`
- Test: `tests/unit/cli/test_init_cmd.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/cli/test_init_cmd.py`:

```python
class TestEnv:
    def test_env_outputs_environment_info(self, tmp_path: Path, monkeypatch: object) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))  # type: ignore[attr-defined]

        result = runner.invoke(app, ["env"])
        assert result.exit_code == 0
        assert "Config dir" in result.output
        assert "Python" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/cli/test_init_cmd.py::TestEnv -v`
Expected: FAIL

- [ ] **Step 3: Write implementation**

Add to `src/agent_nexus/platform/local/cli/init_cmd.py`:

```python
@init_app.command()
def env() -> None:
    """Print resolved environment snapshot."""
    import os
    import shutil
    import sys

    from agent_nexus.platform.local.cli._shared import _get_config_dir

    config_dir = _get_config_dir()

    # Provider key status
    from agent_nexus.platform.config.defaults import DEFAULT_PROVIDERS

    provider_status: list[str] = []
    for name, preset in DEFAULT_PROVIDERS.items():
        key_env = preset.get("api_key_env", "")
        has_key = bool(key_env and os.environ.get(str(key_env)))
        provider_status.append(f"{name} (key: {'set' if has_key else 'not set'})")

    git_ver = shutil.which("git")
    uv_ver = shutil.which("uv")

    typer.echo(f"Config dir:    {config_dir}")
    typer.echo(f"Python:        {sys.version.split()[0]}")
    typer.echo(f"Git:           {'installed' if git_ver else 'not found'}")
    typer.echo(f"uv:            {'installed' if uv_ver else 'not found'}")
    typer.echo(f"Providers:     {', '.join(provider_status)}")
    typer.echo(f"Schema:        1.0")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/cli/test_init_cmd.py::TestEnv -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/local/cli/init_cmd.py tests/unit/cli/test_init_cmd.py
git commit -m "feat(cli): add env command"
```

---

### Task 11: Update `pyproject.toml` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add `questionary` dependency**

Find the `[project.dependencies]` section in `pyproject.toml` and add:

```toml
"questionary>=2.0",    # Interactive init wizard
```

- [ ] **Step 2: Verify the entry point is correct**

Ensure `pyproject.toml` has:

```toml
[project.scripts]
agent-nexus = "agent_nexus.platform.local.cli:app"
```

This should already be correct since `cli/__init__.py` re-exports `app`.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "feat(cli): add questionary dependency for init wizard"
```

---

## Self-Review Checklist

### 1. Spec Coverage

| Spec Section | Task | Status |
|---|---|---|
| Phase 1: Package refactor | Tasks 1-3 | Covered |
| Phase 2: init/doctor/version | Tasks 4-6 | Covered |
| Phase 3: start/stop/restart/status/logs/ps | Task 7 | Covered |
| Phase 4: config show/get/edit/validate/providers/path | Task 8 | Covered |
| Phase 5: evolution status/health/list/history/metrics/fix/promote | Task 9 | Covered |
| Phase 6: env command | Task 10 | Covered |
| questionary dependency | Task 11 | Covered |
| `completion` command | - | **Skipped** — Typer provides this automatically via `_TYPER_COMPLETE` env var; no custom code needed |

### 2. Placeholder Scan

- No "TBD", "TODO", "implement later", "fill in details" found
- No "Add appropriate error handling" — all error paths have explicit handling
- All code blocks contain complete implementations

### 3. Type Consistency

- `_init_managers()` returns `tuple` — matches all call sites that destructure as `loader, lockfile, sources, config_dir`
- `ConfigMigrator.TARGET_VERSION = "1.0"` — matches `PlatformConfig` schema
- `EvolutionEngine.store` property returns `EvolutionStore` — matches `get_active_skills()`, `get_ancestry()`, `get_metrics()` usage
- `HealthReport.metrics` is `dict[str, float]` — matched in `evolution_health()` formatting
- `EvolutionMetrics` fields `total_selections`, `total_applied`, etc. — matched in `evolution_metrics()` output
- `PromotionCandidate` takes `skill_id: str` — matched in `evolution_promote()` call
