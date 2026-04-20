"""Config management commands: show, get, edit, validate, providers, path."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any

import typer

from agent_nexus.platform.local.cli._shared import ConfigMigrator, _get_config_dir

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

    value, found = _resolve_dot_path(config.model_dump(), key)
    if not found:
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

    editor_name = os.environ.get("EDITOR", "vi")
    editor = shutil.which(editor_name)
    if not editor:
        typer.echo(f"Editor '{editor_name}' not found in PATH.", err=True)
        raise typer.Exit(code=1)
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
        has_key = bool(key_env and os.environ.get(key_env))
        status = "set" if has_key else "not set"
        base_url = provider.base_url or "(default)"
        typer.echo(f"{name:<20} {base_url:<35} {key_env:<20} {status}")


@config_app.command("path")
def config_path() -> None:
    """Print the config directory path."""
    typer.echo(str(_get_config_dir()))


def _resolve_dot_path(data: dict[str, Any], key: str) -> tuple[Any, bool]:
    """Resolve a dot-separated key path on a nested dict.

    Returns (value, found). ``found`` is False when the key path doesn't exist,
    allowing callers to distinguish a legitimate None value from a missing key.
    """
    parts = key.split(".")
    current: Any = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None, False
    return current, True
