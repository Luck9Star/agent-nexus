"""Agent package quality check command: agent-nexus check <path>."""

from __future__ import annotations

from pathlib import Path

import typer


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
            f"agent-manifest.yaml: name '{data['name']}' does not match directory name '{dir_name}'"
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
