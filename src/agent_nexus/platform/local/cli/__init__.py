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
    invoke_without_command=True,
)

# --- Callback for top-level --version ---


@app.callback()
def _main(
    version: bool = typer.Option(
        False, "--version", "-v", help="Show version and exit"
    ),
) -> None:
    """Agent Nexus -- MCP-native Agent Platform"""
    if version:
        import importlib.metadata

        try:
            ver = importlib.metadata.version("agent-nexus")
        except importlib.metadata.PackageNotFoundError:
            ver = "unknown (dev mode)"
        typer.echo(f"agent-nexus {ver}")
        raise typer.Exit()


# --- Lifecycle commands (migrated from original cli.py) ---
from agent_nexus.platform.local.cli._lifecycle import (
    install_agent,
    uninstall,
    update,
    run_agent,
    list_agents,
    search,
    info,
    sources,
)

# All lifecycle commands registered as top-level commands
app.command("install")(install_agent)
app.command("uninstall")(uninstall)
app.command("update")(update)
app.command("run", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})(run_agent)
app.command("list")(list_agents)
app.command()(search)
app.command()(info)
app.command()(sources)

# --- New command modules ---
from agent_nexus.platform.local.cli.init_cmd import init_app
from agent_nexus.platform.local.cli.config_cmd import config_app
from agent_nexus.platform.local.cli.runtime_cmd import runtime_app
from agent_nexus.platform.local.cli.evolution_cmd import evolution_app
from agent_nexus.platform.local.cli.create_cmd import create_app

app.add_typer(init_app)
app.add_typer(config_app, name="config")
app.add_typer(runtime_app, name="runtime")
app.add_typer(evolution_app, name="evolution")
app.add_typer(create_app, name="create")

if __name__ == "__main__":
    app()
