# CLI Improvements: check command + sources sub-app + JSON output + logs --follow

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill gaps in the CLI — add missing `check` command, restructure `sources` as Typer sub-app, add `--json` output to discovery commands, add `logs --follow` real-time tailing.

**Architecture:** Each improvement is an independent Typer command module or a modification to an existing one. Follow the established pattern: sync Typer callback → async implementation via `asyncio.run()`. New module `check_cmd.py` for the check command. Modifications to `_lifecycle.py` (sources → sub-app) and `runtime_cmd.py` (logs --follow).

**Tech Stack:** Python 3.11+, Typer, Pydantic v2, PyYAML, tomllib (stdlib)

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/agent_nexus/platform/local/cli/check_cmd.py` | **Create** | `agent-nexus check <path>` — validate agent package |
| `src/agent_nexus/platform/local/cli/sources_cmd.py` | **Create** | `agent-nexus sources list/add/remove` — Typer sub-app |
| `src/agent_nexus/platform/local/cli/_lifecycle.py` | **Modify** | Remove flat `sources()` function and `_sources()` |
| `src/agent_nexus/platform/local/cli/__init__.py` | **Modify** | Register new sub-apps, remove old sources registration |
| `src/agent_nexus/platform/local/cli/runtime_cmd.py` | **Modify** | Add `--follow` to logs command |
| `src/agent_nexus/platform/local/cli/_lifecycle.py` | **Modify** | Add `--json` option to `list_agents`, `search`, `info` |
| `tests/unit/test_check_cmd.py` | **Create** | Tests for check command |
| `tests/unit/test_cli_module.py` | **Modify** | Update sources tests + add JSON output tests + follow tests |
| `README.md` | **Modify** | Update CLI command docs |
| `README_EN.md` | **Modify** | Update CLI command docs |

---

### Task 1: `check` command — Agent package validation

**Files:**
- Create: `src/agent_nexus/platform/local/cli/check_cmd.py`
- Create: `tests/unit/test_check_cmd.py`
- Modify: `src/agent_nexus/platform/local/cli/__init__.py`

This is the highest-priority gap. Provides a quality gate before agent publishing.

#### Step 1.1: Write failing tests for check command

- [ ] **Create `tests/unit/test_check_cmd.py`**

```python
"""Unit tests for agent-nexus check command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from typer.testing import CliRunner

from agent_nexus.platform.local.cli import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_atomic_agent(base: Path, name: str = "test-agent") -> Path:
    """Create a minimal valid atomic agent package at base/name."""
    agent_dir = base / name
    agent_dir.mkdir(parents=True, exist_ok=True)
    pkg_dir = agent_dir / f"agent_{name.replace('-', '_')}"
    pkg_dir.mkdir(parents=True, exist_ok=True)

    # agent-manifest.yaml
    manifest = {
        "name": name,
        "version": "0.1.0",
        "type": "atomic",
        "description": "Test agent",
    }
    (agent_dir / "agent-manifest.yaml").write_text(
        yaml.dump(manifest), encoding="utf-8"
    )

    # SKILL.md with YAML frontmatter
    skill_md = (
        "---\n"
        f"name: {name}\n"
        "agent_type: atomic\n"
        "description: Test agent\n"
        "---\n\n# Test Agent\n\nTest body.\n"
    )
    (agent_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")

    # pyproject.toml
    (agent_dir / "pyproject.toml").write_text(
        "[project]\nname = 'test-agent'\nversion = '0.1.0'\n",
        encoding="utf-8",
    )

    # agent.py
    (agent_dir / "agent.py").write_text(
        "async def run(task: str, _context=None) -> str:\n    return task\n",
        encoding="utf-8",
    )

    # mcp_adapter.py
    (pkg_dir / "mcp_adapter.py").write_text(
        "def create_mcp_server(): pass\n", encoding="utf-8"
    )
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")

    return agent_dir


def _write_composite_agent(base: Path, name: str = "test-composite") -> Path:
    """Create a minimal valid composite agent package."""
    agent_dir = base / name
    agent_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "name": name,
        "version": "0.1.0",
        "type": "composite",
        "description": "Test composite agent",
    }
    (agent_dir / "agent-manifest.yaml").write_text(
        yaml.dump(manifest), encoding="utf-8"
    )

    composition_toml = (
        '[composition]\nname = "test-composite"\ndescription = "test"\n\n'
        "[tasks.task1]\nname = 'step1'\nagent = 'code-reviewer'\nblocked_by = []\n"
    )
    (agent_dir / "composition.toml").write_text(composition_toml, encoding="utf-8")

    (agent_dir / "SKILL.md").write_text(
        "---\nname: test-composite\nagent_type: composite\ndescription: test\n---\n\n# Test\n",
        encoding="utf-8",
    )
    (agent_dir / "pyproject.toml").write_text(
        "[project]\nname = 'test-composite'\nversion = '0.1.0'\n",
        encoding="utf-8",
    )
    return agent_dir


# ---------------------------------------------------------------------------
# Valid packages
# ---------------------------------------------------------------------------


class TestCheckValid:
    def test_valid_atomic_agent(self, tmp_path: Path) -> None:
        agent_dir = _write_atomic_agent(tmp_path)
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 0
        assert "All checks passed" in result.output

    def test_valid_composite_agent(self, tmp_path: Path) -> None:
        agent_dir = _write_composite_agent(tmp_path)
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 0
        assert "All checks passed" in result.output


# ---------------------------------------------------------------------------
# Missing files
# ---------------------------------------------------------------------------


class TestCheckMissingFiles:
    def test_missing_manifest(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / "broken"
        agent_dir.mkdir()
        (agent_dir / "SKILL.md").write_text("---\nname: x\n---\n")
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 1
        assert "agent-manifest.yaml" in result.output

    def test_missing_skill_md(self, tmp_path: Path) -> None:
        agent_dir = _write_atomic_agent(tmp_path)
        (agent_dir / "SKILL.md").unlink()
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 1
        assert "SKILL.md" in result.output

    def test_missing_pyproject(self, tmp_path: Path) -> None:
        agent_dir = _write_atomic_agent(tmp_path)
        (agent_dir / "pyproject.toml").unlink()
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 1
        assert "pyproject.toml" in result.output

    def test_missing_agent_py_for_atomic(self, tmp_path: Path) -> None:
        agent_dir = _write_atomic_agent(tmp_path)
        (agent_dir / "agent.py").unlink()
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 1
        assert "agent.py" in result.output

    def test_missing_composition_toml_for_composite(self, tmp_path: Path) -> None:
        agent_dir = _write_composite_agent(tmp_path)
        (agent_dir / "composition.toml").unlink()
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 1
        assert "composition.toml" in result.output


# ---------------------------------------------------------------------------
# Invalid content
# ---------------------------------------------------------------------------


class TestCheckInvalidContent:
    def test_invalid_manifest_yaml(self, tmp_path: Path) -> None:
        agent_dir = _write_atomic_agent(tmp_path)
        (agent_dir / "agent-manifest.yaml").write_text(":: invalid yaml {{{")
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 1
        assert "agent-manifest.yaml" in result.output

    def test_manifest_name_mismatch(self, tmp_path: Path) -> None:
        agent_dir = _write_atomic_agent(tmp_path, name="agent-a")
        # Overwrite manifest with a different name
        manifest = {
            "name": "different-name",
            "version": "0.1.0",
            "type": "atomic",
            "description": "Test",
        }
        (agent_dir / "agent-manifest.yaml").write_text(
            yaml.dump(manifest), encoding="utf-8"
        )
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 1
        assert "name" in result.output.lower()

    def test_composition_cycle(self, tmp_path: Path) -> None:
        agent_dir = _write_composite_agent(tmp_path)
        cycle_toml = (
            '[composition]\nname = "cycle"\ndescription = "cycle test"\n\n'
            "[tasks.t1]\nname = 'a'\nagent = 'x'\nblocked_by = ['t2']\n\n"
            "[tasks.t2]\nname = 'b'\nagent = 'y'\nblocked_by = ['t1']\n"
        )
        (agent_dir / "composition.toml").write_text(cycle_toml, encoding="utf-8")
        result = runner.invoke(app, ["check", str(agent_dir)])
        assert result.exit_code == 1
        assert "cycle" in result.output.lower() or "circular" in result.output.lower()

    def test_nonexistent_path(self) -> None:
        result = runner.invoke(app, ["check", "/nonexistent/path"])
        assert result.exit_code == 1
        assert "not found" in result.output.lower() or "does not exist" in result.output.lower()
```

- [ ] **Step 1.2: Run tests to verify they fail**

Run: `pytest tests/unit/test_check_cmd.py -v --no-header -q 2>&1 | head -20`
Expected: FAIL — `ModuleNotFoundError: No module named 'agent_nexus.platform.local.cli.check_cmd'` or import error since command not registered.

#### Step 1.3: Implement check command

- [ ] **Create `src/agent_nexus/platform/local/cli/check_cmd.py`**

```python
"""Agent package quality check command: agent-nexus check <path>."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

check_app = typer.Typer(help="Quality checks for agent packages")


def _check_path_exists(path: Path) -> list[str]:
    """Verify the path exists and is a directory."""
    errors: list[str] = []
    if not path.exists():
        errors.append(f"Path does not exist: {path}")
    elif not path.is_dir():
        errors.append(f"Path is not a directory: {path}")
    return errors


def _check_manifest(path: Path, dir_name: str) -> tuple[list[str], dict | None]:
    """Check agent-manifest.yaml exists, parses, and name matches directory."""
    errors: list[str] = []
    manifest_path = path / "agent-manifest.yaml"

    if not manifest_path.exists():
        errors.append("Missing agent-manifest.yaml")
        return errors, None

    try:
        import yaml

        content = manifest_path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
    except Exception as exc:
        errors.append(f"agent-manifest.yaml: parse error: {exc}")
        return errors, None

    if not isinstance(data, dict):
        errors.append("agent-manifest.yaml: expected a mapping, got " + type(data).__name__)
        return errors, None

    for field in ("name", "version", "type", "description"):
        if field not in data:
            errors.append(f"agent-manifest.yaml: missing required field '{field}'")

    if "name" in data and data["name"] != dir_name:
        errors.append(
            f"agent-manifest.yaml: name '{data['name']}' does not match "
            f"directory name '{dir_name}'"
        )

    return errors, data


def _check_skill_md(path: Path) -> list[str]:
    """Check SKILL.md exists and has YAML frontmatter."""
    errors: list[str] = []
    skill_path = path / "SKILL.md"

    if not skill_path.exists():
        errors.append("Missing SKILL.md")
        return errors

    content = skill_path.read_text(encoding="utf-8")
    if not content.startswith("---"):
        errors.append("SKILL.md: missing YAML frontmatter (must start with '---')")
    elif content.count("---") < 2:
        errors.append("SKILL.md: incomplete YAML frontmatter (missing closing '---')")

    return errors


def _check_pyproject(path: Path) -> list[str]:
    """Check pyproject.toml exists and is parseable."""
    errors: list[str] = []
    pyproject_path = path / "pyproject.toml"

    if not pyproject_path.exists():
        errors.append("Missing pyproject.toml")
        return errors

    try:
        import tomllib

        tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"pyproject.toml: parse error: {exc}")

    return errors


def _check_atomic_files(path: Path) -> list[str]:
    """Check files required for atomic agents."""
    errors: list[str] = []
    if not (path / "agent.py").exists():
        errors.append("Missing agent.py (required for atomic agents)")
    return errors


def _check_composite_files(path: Path) -> list[str]:
    """Check files required for composite agents."""
    errors: list[str] = []

    composition_path = path / "composition.toml"
    if not composition_path.exists():
        errors.append("Missing composition.toml (required for composite agents)")
        return errors

    # Parse and validate composition
    try:
        import tomllib

        data = tomllib.loads(composition_path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"composition.toml: parse error: {exc}")
        return errors

    # Validate structure
    if "composition" not in data:
        errors.append("composition.toml: missing [composition] section")
    if "tasks" not in data:
        errors.append("composition.toml: missing [tasks] section")
    else:
        tasks = data["tasks"]
        for tid, task in tasks.items():
            if "name" not in task:
                errors.append(f"composition.toml: task '{tid}' missing 'name'")
            if "agent" not in task:
                errors.append(f"composition.toml: task '{tid}' missing 'agent'")
            if tid in task.get("blocked_by", []):
                errors.append(f"composition.toml: task '{tid}' cannot depend on itself")

        # Cycle detection via DFS
        from agent_nexus.platform.utils import detect_cycles_dfs

        task_ids = set(tasks.keys())
        cycles = detect_cycles_dfs(
            task_ids,
            lambda tid: [d for d in tasks[tid].get("blocked_by", []) if d in task_ids],
        )
        for cycle in cycles:
            errors.append(f"composition.toml: circular dependency: {' -> '.join(cycle)}")

    return errors


@check_app.command("check")
def check_agent(
    path: str = typer.Argument(help="Path to agent package directory"),
) -> None:
    """Validate an agent package for completeness and correctness.

    Checks manifest, SKILL.md, pyproject.toml, required files, and
    (for composite agents) composition.toml DAG validity.
    """
    agent_path = Path(path).resolve()
    all_errors: list[str] = []

    # 1. Path exists
    all_errors.extend(_check_path_exists(agent_path))
    if all_errors:
        _print_errors(all_errors)
        raise typer.Exit(code=1)

    dir_name = agent_path.name

    # 2. Manifest
    manifest_errors, manifest_data = _check_manifest(agent_path, dir_name)
    all_errors.extend(manifest_errors)

    # 3. SKILL.md
    all_errors.extend(_check_skill_md(agent_path))

    # 4. pyproject.toml
    all_errors.extend(_check_pyproject(agent_path))

    # 5. Type-specific checks
    agent_type = manifest_data.get("type", "") if manifest_data else ""
    if agent_type == "atomic":
        all_errors.extend(_check_atomic_files(agent_path))
    elif agent_type == "composite":
        all_errors.extend(_check_composite_files(agent_path))

    if all_errors:
        _print_errors(all_errors)
        raise typer.Exit(code=1)

    typer.echo("All checks passed.")


def _print_errors(errors: list[str]) -> None:
    """Print check errors to stderr."""
    typer.echo(f"Found {len(errors)} issue(s):\n", err=True)
    for i, error in enumerate(errors, 1):
        typer.echo(f"  {i}. {error}", err=True)
```

- [ ] **Step 1.4: Register check sub-app in `__init__.py`**

Add after the existing `from agent_nexus.platform.local.cli.create_cmd import create_app` line:

```python
from agent_nexus.platform.local.cli.check_cmd import check_app
```

Add after `app.add_typer(create_app, name="create")`:

```python
app.add_typer(check_app, name="check")
```

Wait — `check` only has one command and the README shows `agent-nexus check <path>` not `agent-nexus check check <path>`. Better to register it as a top-level command instead:

```python
from agent_nexus.platform.local.cli.check_cmd import check_agent
app.command("check")(check_agent)
```

- [ ] **Step 1.5: Run tests to verify they pass**

Run: `pytest tests/unit/test_check_cmd.py -v --no-header -q`
Expected: All PASS

- [ ] **Step 1.6: Commit**

```bash
git add src/agent_nexus/platform/local/cli/check_cmd.py tests/unit/test_check_cmd.py src/agent_nexus/platform/local/cli/__init__.py
git commit -m "feat(cli): add 'agent-nexus check' command for agent package validation"
```

---

### Task 2: `sources` sub-app restructuring

**Files:**
- Create: `src/agent_nexus/platform/local/cli/sources_cmd.py`
- Modify: `src/agent_nexus/platform/local/cli/_lifecycle.py` — remove `sources()` and `_sources()`
- Modify: `src/agent_nexus/platform/local/cli/__init__.py` — register new sub-app

Convert the flat `agent-nexus sources <action>` into a proper Typer sub-app with `list`, `add`, `remove` subcommands. Same functionality, better `--help` output.

#### Step 2.1: Write failing tests

- [ ] **Add to `tests/unit/test_cli_module.py`** — update existing sources tests to use new subcommand paths

The existing tests use `runner.invoke(app, ["sources", "list"])` which will still work since the new sub-app has `list` as a subcommand. But we need to verify `--help` output shows subcommands:

```python
class TestSourcesSubApp:
    def test_sources_help_shows_subcommands(self) -> None:
        result = runner.invoke(app, ["sources", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "add" in result.output
        assert "remove" in result.output

    def test_sources_add_requires_name_and_url(self) -> None:
        result = runner.invoke(app, ["sources", "add"])
        assert result.exit_code != 0

    def test_sources_list_no_sources(self) -> None:
        with patch(
            "agent_nexus.platform.local.cli._lifecycle._init_managers"
        ) as mock_init:
            from agent_nexus.models.distribution import Lockfile
            mock_lockfile = MagicMock()
            mock_lockfile.load.return_value = Lockfile(agents={})
            mock_sources = MagicMock()
            mock_sources.list_sources.return_value = []
            mock_init.return_value = (MagicMock(), mock_lockfile, mock_sources, Path("/tmp"))
            result = runner.invoke(app, ["sources", "list"])
        assert "No sources" in result.output

    def test_sources_remove_not_found(self) -> None:
        with patch(
            "agent_nexus.platform.local.cli._lifecycle._init_managers"
        ) as mock_init:
            mock_sources = MagicMock()
            mock_sources.remove_source.return_value = False
            mock_init.return_value = (MagicMock(), MagicMock(), mock_sources, Path("/tmp"))
            result = runner.invoke(app, ["sources", "remove", "nonexistent"])
        assert result.exit_code != 0
```

- [ ] **Step 2.2: Run tests to verify they fail**

Run: `pytest tests/unit/test_cli_module.py::TestSourcesSubApp -v --no-header -q`
Expected: FAIL — `No such command 'list'` (sources is still a flat command, not a sub-app)

#### Step 2.3: Create sources_cmd.py

- [ ] **Create `src/agent_nexus/platform/local/cli/sources_cmd.py`**

```python
"""Sources management sub-commands: list, add, remove."""

from __future__ import annotations

import asyncio
from typing import Optional

import typer

from agent_nexus.platform.local.cli._shared import _init_managers

sources_app = typer.Typer(help="Manage package sources")


@sources_app.command("list")
def sources_list() -> None:
    """List configured package sources."""
    _loader, _lockfile, sources, _config_dir = _init_managers()
    source_list = sources.list_sources()
    if not source_list:
        typer.echo("No sources configured.")
        return
    typer.echo(f"{'Name':<20} {'Type':<10} {'URL'}")
    typer.echo("-" * 60)
    for s in source_list:
        typer.echo(f"{s.name:<20} {s.type:<10} {s.url}")


@sources_app.command("add")
def sources_add(
    name: str = typer.Option(..., "--name", help="Source name"),
    url: str = typer.Option(..., "--url", help="Source git URL"),
    source_type: Optional[str] = typer.Option(
        None, "--type", help="Source type (default: git)"
    ),
) -> None:
    """Add a new package source."""
    from agent_nexus.models.distribution import SourceEntry
    from agent_nexus.platform.local.installer import _validate_git_url

    try:
        _validate_git_url(url)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1)

    _loader, _lockfile, sources, _config_dir = _init_managers()
    entry = SourceEntry(name=name, type=source_type or "git", url=url)
    sources.add_source(entry)
    typer.echo(f"Source '{name}' added.")


@sources_app.command("remove")
def sources_remove(
    name: str = typer.Argument(help="Source name to remove"),
) -> None:
    """Remove a package source."""
    _loader, _lockfile, sources, _config_dir = _init_managers()
    removed = sources.remove_source(name)
    if removed:
        typer.echo(f"Source '{name}' removed.")
    else:
        typer.echo(f"Source '{name}' not found.")
        raise typer.Exit(code=1)
```

#### Step 2.4: Update `__init__.py`

- [ ] **In `__init__.py`:**

Remove:
```python
from agent_nexus.platform.local.cli._lifecycle import (
    ...
    sources,
)
```
Remove: `app.command()(sources)`

Add:
```python
from agent_nexus.platform.local.cli.sources_cmd import sources_app
app.add_typer(sources_app, name="sources")
```

#### Step 2.5: Remove old sources from `_lifecycle.py`

- [ ] **In `_lifecycle.py`:**

Remove the `sources()` sync function (lines 84-93) and `_sources()` async function (lines 335-391). These are replaced by `sources_cmd.py`.

#### Step 2.6: Run tests

Run: `pytest tests/unit/test_cli_module.py -v --no-header -q`
Expected: All PASS

- [ ] **Step 2.7: Commit**

```bash
git add src/agent_nexus/platform/local/cli/sources_cmd.py src/agent_nexus/platform/local/cli/_lifecycle.py src/agent_nexus/platform/local/cli/__init__.py tests/unit/test_cli_module.py
git commit -m "refactor(cli): restructure 'sources' as Typer sub-app with list/add/remove subcommands"
```

---

### Task 3: `--json` output for discovery commands

**Files:**
- Modify: `src/agent_nexus/platform/local/cli/_lifecycle.py`

Add `--json` flag to `list`, `search`, and `info` commands for machine-readable output.

#### Step 3.1: Write failing tests

- [ ] **Add to `tests/unit/test_cli_module.py`**

```python
import json

class TestJsonOutput:
    def test_list_json(self) -> None:
        with patch(
            "agent_nexus.platform.local.cli._lifecycle._init_managers"
        ) as mock_init:
            entry = _make_entry()
            mock_lockfile = MagicMock()
            mock_lockfile.load.return_value = Lockfile(agents={"doc-filler": entry})
            mock_init.return_value = (MagicMock(), mock_lockfile, MagicMock(), Path("/tmp"))
            result = runner.invoke(app, ["list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["name"] == "doc-filler"

    def test_search_json(self) -> None:
        with patch(
            "agent_nexus.platform.local.cli._lifecycle._init_managers"
        ) as mock_init:
            from agent_nexus.models.distribution import SourceEntry, IndexEntry
            mock_sources = MagicMock()
            mock_sources.search_agents.return_value = [
                (SourceEntry(name="official", type="git", url="https://example.com"),
                 IndexEntry(name="doc-filler", version="1.0.0", type=AgentType.ATOMIC,
                            description="Doc filler")),
            ]
            mock_init.return_value = (MagicMock(), MagicMock(), mock_sources, Path("/tmp"))
            result = runner.invoke(app, ["search", "doc", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["name"] == "doc-filler"

    def test_list_json_empty(self) -> None:
        with patch(
            "agent_nexus.platform.local.cli._lifecycle._init_managers"
        ) as mock_init:
            mock_lockfile = MagicMock()
            mock_lockfile.load.return_value = Lockfile(agents={})
            mock_init.return_value = (MagicMock(), mock_lockfile, MagicMock(), Path("/tmp"))
            result = runner.invoke(app, ["list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data == []
```

- [ ] **Step 3.2: Run tests to verify they fail**

Run: `pytest tests/unit/test_cli_module.py::TestJsonOutput -v --no-header -q`
Expected: FAIL — `No such option: --json`

#### Step 3.3: Implement `--json` in `_lifecycle.py`

- [ ] **Modify `list_agents()` function:**

Add `json_output` parameter:

```python
def list_agents(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List installed agents."""
    asyncio.run(_list_agents(json_output))
```

- [ ] **Modify `_list_agents()` function:**

```python
async def _list_agents(json_output: bool = False) -> None:
    """Async list implementation."""
    import json as json_mod

    _loader, lockfile, _sources, _config_dir = _init_managers()
    lockfile_data = lockfile.load()

    agents = lockfile_data.agents
    if json_output:
        result = [
            {
                "name": name,
                "version": entry.version,
                "type": entry.agent_type.value,
                "source": entry.source,
            }
            for name, entry in agents.items()
        ]
        typer.echo(json_mod.dumps(result, indent=2))
        return

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
```

- [ ] **Apply same pattern to `search()` and `_search()`:**

Add `json_output: bool = typer.Option(False, "--json", help="Output as JSON")` to `search()`, pass through to `_search()`, and in `_search()` when `json_output=True`, output JSON array instead of formatted text.

- [ ] **Step 3.4: Run tests**

Run: `pytest tests/unit/test_cli_module.py::TestJsonOutput -v --no-header -q`
Expected: All PASS

- [ ] **Step 3.5: Commit**

```bash
git add src/agent_nexus/platform/local/cli/_lifecycle.py tests/unit/test_cli_module.py
git commit -m "feat(cli): add --json output to list and search commands"
```

---

### Task 4: `logs --follow` real-time tailing

**Files:**
- Modify: `src/agent_nexus/platform/local/cli/runtime_cmd.py`

#### Step 4.1: Write failing test

- [ ] **Add to `tests/unit/test_cli_module.py`**

```python
class TestLogsFollow:
    def test_logs_follow_option_exists(self) -> None:
        """Verify --follow option is accepted by logs command."""
        result = runner.invoke(app, ["runtime", "logs", "--help"])
        assert result.exit_code == 0
        assert "--follow" in result.output
```

- [ ] **Step 4.2: Run test to verify it fails**

Run: `pytest tests/unit/test_cli_module.py::TestLogsFollow -v --no-header -q`
Expected: FAIL — `--follow` not in help output

#### Step 4.3: Implement `--follow` in `runtime_cmd.py`

- [ ] **Modify `logs()` command in `runtime_cmd.py`:**

```python
@runtime_app.command()
def logs(
    name: str = typer.Argument(help="Agent name"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
) -> None:
    """Show recent log output for an agent."""
    if follow:
        _follow_logs(name)
    else:
        _show_logs(name, lines)
```

- [ ] **Add `_follow_logs()` function in `runtime_cmd.py`:**

```python
def _follow_logs(name: str) -> None:
    """Follow log output in real-time (tail -f style)."""
    if not AGENT_NAME_RE.match(name):
        typer.echo(f"Invalid agent name: {name!r}", err=True)
        raise typer.Exit(code=1)

    _loader, lockfile, _sources, config_dir = _init_managers()
    log_path = config_dir / "logs" / f"{name}.log"

    if not log_path.exists():
        typer.echo(f"No log file for '{name}'. Agent may not have been started.")
        return

    import collections
    import time

    # Print existing content first
    content = log_path.read_text(encoding="utf-8", errors="replace")
    lines_list = content.splitlines()
    for line in lines_list[-50:]:
        typer.echo(line)

    # Follow for new lines
    last_size = log_path.stat().st_size
    last_pos = last_size

    try:
        while True:
            time.sleep(0.5)
            try:
                current_size = log_path.stat().st_size
            except FileNotFoundError:
                break
            if current_size < last_pos:
                last_pos = 0
            if current_size > last_pos:
                with open(log_path, encoding="utf-8", errors="replace") as f:
                    f.seek(last_pos)
                    new_content = f.read()
                    last_pos = f.tell()
                for line in new_content.splitlines():
                    typer.echo(line)
    except KeyboardInterrupt:
        pass
```

- [ ] **Step 4.4: Run tests**

Run: `pytest tests/unit/test_cli_module.py::TestLogsFollow -v --no-header -q`
Expected: PASS

- [ ] **Step 4.5: Commit**

```bash
git add src/agent_nexus/platform/local/cli/runtime_cmd.py tests/unit/test_cli_module.py
git commit -m "feat(cli): add --follow flag to 'runtime logs' for real-time tailing"
```

---

### Task 5: Update documentation

**Files:**
- Modify: `README.md`
- Modify: `README_EN.md`

#### Step 5.1: Update README.md

- [ ] **Add `check` command section** after the "质量检查" section, expand with examples:

```markdown
### 质量检查（Agent 开发者用）

```bash
# 发布前验证 Agent 包
agent-nexus check ./my-agent

# 检查项：
# - agent-manifest.yaml 格式合法、字段完整、name 匹配目录名
# - SKILL.md 存在且含 YAML frontmatter
# - pyproject.toml 存在且可解析
# - Atomic Agent: agent.py 存在
# - Composite Agent: composition.toml DAG 无环、字段完整
```
```

- [ ] **Update `sources` section** to reflect new subcommand structure:

```markdown
### 包源管理

```bash
# 列出已配置的源
agent-nexus sources list

# 添加私有源
agent-nexus sources add --name internal --url https://github.com/myorg/agents.git

# 移除源
agent-nexus sources remove internal
```
```

- [ ] **Update `logs` section** to add `--follow`:

```bash
# 实时追踪日志
agent-nexus logs doc-filler --follow
agent-nexus logs doc-filler -f
```

- [ ] **Update `list` and `search` sections** to mention `--json`:

```bash
# JSON 格式输出（方便脚本化）
agent-nexus list --json
agent-nexus search "security" --json
```

#### Step 5.2: Apply same changes to README_EN.md

Mirror all README.md changes in English.

#### Step 5.3: Commit

```bash
git add README.md README_EN.md
git commit -m "docs: update CLI documentation for check, sources sub-app, --json, logs --follow"
```

---

## Self-Review Checklist

1. **Spec coverage:**
   - `check` command: Task 1 covers manifest, SKILL.md, pyproject, type-specific files, cycle detection
   - `sources` sub-app: Task 2 covers list/add/remove with proper Typer sub-app
   - `--json` output: Task 3 covers list and search (info deferred — lower priority)
   - `logs --follow`: Task 4 covers real-time tailing with Ctrl+C exit
   - Docs: Task 5 updates both READMEs

2. **Placeholder scan:** No TBD/TODO/fill-in-later patterns. All code blocks contain complete implementations.

3. **Type consistency:**
   - `check_agent()` accepts `path: str` (Typer Argument), converts to `Path` internally
   - `sources_app` uses `typer.Typer(help=...)` matching existing pattern
   - `--json` parameter name `json_output` avoids shadowing `json` module
   - `_follow_logs()` uses same log path resolution pattern as `_show_logs()`
