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

# --- Lifecycle commands (migrated from original cli.py) ---
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
