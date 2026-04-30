"""Sources management sub-commands: list, add, remove."""

from __future__ import annotations

import typer

from agent_nexus.models.distribution import SourceEntry
from agent_nexus.platform.local.cli._shared import _init_managers
from agent_nexus.platform.local.installer import _validate_git_url

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
    source_type: str | None = typer.Option(None, "--type", help="Source type (default: git)"),
) -> None:
    """Add a new package source."""
    try:
        _validate_git_url(url)
    except ValueError as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=1) from None

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
