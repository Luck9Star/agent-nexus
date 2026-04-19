"""CLI entry point for Agent Nexus.

Built with Typer.  Declared in ``pyproject.toml`` as::

    [project.scripts]
    agent-nexus = "agent_nexus.platform.local.cli:app"

This module MUST export ``app`` as a Typer instance at module level.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

import typer

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="agent-nexus",
    help="Agent Nexus -- MCP-native Agent Platform",
    no_args_is_help=True,
)

# Sub-command groups
install_app = typer.Typer(help="Agent installation and management")
run_app = typer.Typer(help="Run agents and workflows")
app.add_typer(install_app, name="install")
app.add_typer(run_app, name="run", invoke_without_command=True)


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


@app.command("list")
def list_agents() -> None:
    """List installed agents."""
    asyncio.run(_list_agents())


@app.command()
def search(query: str = typer.Argument(help="Search query")) -> None:
    """Search for available agents."""
    asyncio.run(_search(query))


@app.command()
def info(name: str = typer.Argument(help="Agent name")) -> None:
    """Show detailed information about an agent."""
    asyncio.run(_info(name))


# =====================================================================
# Source commands
# =====================================================================


@app.command()
def sources(
    action: str = typer.Argument(help="Action: list, add, remove"),
    name: Optional[str] = typer.Option(None, "--name", help="Source name"),
    url: Optional[str] = typer.Option(None, "--url", help="Source git URL"),
    source_type: Optional[str] = typer.Option(
        None, "--type", help="Source type: official, private"
    ),
) -> None:
    """Manage package sources."""
    asyncio.run(_sources(action, name, url, source_type))


# =====================================================================
# Run commands
# =====================================================================


@run_app.callback()
def run_agent(
    name: str = typer.Argument(help="Agent name to run"),
    mode: str = typer.Option(
        "mcp", "--mode", "-m", help="Run mode: mcp, router, cli"
    ),
    transport: str = typer.Option(
        "stdio", "--transport", "-t", help="Transport: stdio, sse"
    ),
) -> None:
    """Run an agent in the specified mode."""
    asyncio.run(_run(name, mode, transport))


# =====================================================================
# Internal async implementations
# =====================================================================


def _get_config_dir() -> Path:
    """Resolve the platform config directory."""
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


async def _install(
    name: str, version: str | None, source_url: str | None
) -> None:
    """Async install implementation."""
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
    """Async uninstall implementation."""
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
    """Async update implementation."""
    from agent_nexus.platform.local.installer import (
        AgentNotFoundError,
        GitInstaller,
    )

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
    """Async list implementation."""
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
    """Async search implementation.

    Searches across all configured sources' index.yaml files for
    matching agent names, descriptions, or tags.
    """
    _loader, lockfile, sources, _config_dir = _init_managers()

    # Search across all source indexes
    results: list[dict] = []
    for source in sources.list_sources():
        index = sources._load_source_index(source)
        if index is None:
            continue
        for entry in index:
            # Match against name, description, or tags
            searchable = " ".join(
                [entry.name, entry.description] + entry.tags
            ).lower()
            if query.lower() in searchable:
                results.append(
                    {
                        "name": entry.name,
                        "version": entry.version,
                        "type": entry.type.value,
                        "description": entry.description,
                        "source": source.name,
                    }
                )

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
    """Async info implementation."""
    import yaml

    _loader, lockfile, _sources, config_dir = _init_managers()
    entry = lockfile.get_entry(name)

    if entry is None:
        typer.echo(f"Agent '{name}' is not installed.", err=True)
        raise typer.Exit(code=1)

    # Display lockfile info
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

    # Try to read and display manifest
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

    # Try to read SKILL.md summary
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
    action: str,
    name: str | None,
    url: str | None,
    source_type: str | None,
) -> None:
    """Async sources management implementation."""
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
        entry = SourceEntry(
            name=name,
            type=source_type or "git",
            url=url,
        )
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
        typer.echo(
            f"Unknown action '{action}'. Use: list, add, remove.", err=True
        )
        raise typer.Exit(code=1)


async def _run(name: str, mode: str, transport: str) -> None:
    """Async run implementation.

    Starts an agent via the supervisor (or gateway for router mode).
    """
    from agent_nexus.platform.local.supervisor import AgentSupervisor
    from agent_nexus.platform.orchestration.process_manager import ProcessManager

    _loader, lockfile, _sources, config_dir = _init_managers()

    # Check agent is installed
    entry = lockfile.get_entry(name)
    if entry is None:
        typer.echo(
            f"Agent '{name}' is not installed. "
            f"Use 'agent-nexus install {name}' first.",
            err=True,
        )
        raise typer.Exit(code=1)

    if mode == "mcp":
        # Run agent directly as MCP server (standalone mode)
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
            # Keep running until interrupted
            await _wait_forever()
        except (KeyboardInterrupt, asyncio.CancelledError):
            typer.echo("\nStopping agent...")
        finally:
            await supervisor.stop_agent(name)

    elif mode == "router":
        # Run via Platform Router (gateway mode)
        try:
            from agent_nexus.platform.gateway.gateway import MCPGateway
            from agent_nexus.platform.router.router import PlatformRouter
        except ImportError as exc:
            typer.echo(
                f"Router mode requires additional modules: {exc}",
                err=True,
            )
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
        # Direct CLI invocation (no MCP server)
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
        typer.echo(
            f"Unknown mode '{mode}'. Use: mcp, router, cli.", err=True
        )
        raise typer.Exit(code=1)


async def _wait_forever() -> None:
    """Block indefinitely until cancelled."""
    while True:
        await asyncio.sleep(3600)


# =====================================================================
# Entry point
# =====================================================================

if __name__ == "__main__":
    app()
