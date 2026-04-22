"""Lifecycle commands: install, uninstall, update, list, search, info, sources, run.

Migrated from the original cli.py monolith.  All async implementations
are here; sync Typer callbacks delegate to them via ``asyncio.run()``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Optional

import typer

from agent_nexus.models.distribution import LockfileEntry
from agent_nexus.platform.local.cli._shared import _get_config_dir, _init_managers
from agent_nexus.platform.utils import AGENT_NAME_RE

logger = logging.getLogger(__name__)


# =====================================================================
# Install commands (registered as top-level commands in __init__.py)
# =====================================================================


def install_agent(
    name: str = typer.Argument(help="Agent name to install"),
    version: Optional[str] = typer.Option(
        None, "--version", "-v", help="Specific version"
    ),
    source: Optional[str] = typer.Option(
        None, "--source", "-s", help="Git URL (direct install)"
    ),
    local: bool = typer.Option(
        False, "--local", "-l", help="Install from local project agents/ directory"
    ),
) -> None:
    """Install an agent from a package source or local directory."""
    asyncio.run(_install(name, version, source, local))


def uninstall(name: str = typer.Argument(help="Agent name to uninstall")) -> None:
    """Uninstall an agent."""
    asyncio.run(_uninstall(name))


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
# Discovery commands (top-level, registered in __init__.py)
# =====================================================================


def list_agents(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List installed agents."""
    asyncio.run(_list_agents(json_output))


def search(
    query: str = typer.Argument(help="Search query"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Search for available agents."""
    asyncio.run(_search(query, json_output))


def info(name: str = typer.Argument(help="Agent name")) -> None:
    """Show detailed information about an agent."""
    asyncio.run(_info(name))


def run_agent(
    ctx: typer.Context,
    name: str = typer.Argument(help="Agent name to run"),
    mode: str = typer.Option(
        "mcp", "--mode", "-m", help="Run mode: mcp, router, cli"
    ),
    transport: str = typer.Option(
        "stdio", "--transport", "-t", help="Transport: stdio, sse"
    ),
) -> None:
    """Run an agent in the specified mode.

    Extra arguments after the agent name are forwarded to the agent
    (only effective in CLI mode).  Example:
        agent-nexus run doc-filler --mode cli analyze template.docx
    """
    extra_args = ctx.args if ctx.args else []
    asyncio.run(_run(name, mode, transport, extra_args))


# =====================================================================
# Internal async implementations
# =====================================================================


async def _install(
    name: str, version: str | None, source_url: str | None, local: bool
) -> None:
    """Async install implementation."""
    from agent_nexus.platform.local.installer import (
        AgentNotFoundError,
        GitInstaller,
    )

    _loader, lockfile, sources, config_dir = _init_managers()
    installer = GitInstaller(sources, lockfile, config_dir)

    try:
        if local:
            local_path = _resolve_local_agent(name)
            entry = await installer.install_local(name, local_path)
            typer.echo(f"Installed {name}@{entry.version} (local)")
        else:
            entry = await installer.install(name, version=version, source_url=source_url)
            typer.echo(f"Installed {name}@{entry.version}")
    except AgentNotFoundError as exc:
        typer.echo(f"Error: {exc}", err=True)
        typer.echo("Hint: Use --local to install from the local project directory.")
        raise typer.Exit(code=1)
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
            raise typer.Exit(code=1)
    except typer.Exit:
        raise
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

    async def _update_one(a_name: str):
        return await installer.update(a_name)

    semaphore = asyncio.Semaphore(4)  # limit concurrent git operations

    async def _bounded_update(a_name: str):
        async with semaphore:
            return await _update_one(a_name)

    results = await asyncio.gather(
        *[_bounded_update(n) for n in agents_to_update],
        return_exceptions=True,
    )

    updated_count = 0
    for agent_name, result in zip(agents_to_update, results):
        if isinstance(result, BaseException):
            if isinstance(result, AgentNotFoundError):
                typer.echo(f"Agent '{agent_name}' is not installed.", err=True)
            else:
                typer.echo(f"Error updating {agent_name}: {result}", err=True)
        elif result:
            entry: LockfileEntry = result  # type: ignore[assignment]
            typer.echo(f"Updated {agent_name}@{entry.version}")
            updated_count += 1
        else:
            typer.echo(f"{agent_name} is already up to date.")

    typer.echo(f"Updated {updated_count}/{len(agents_to_update)} agent(s).")
    if updated_count == 0 and any(isinstance(r, BaseException) for r in results):
        raise typer.Exit(code=1)


async def _list_agents(json_output: bool = False) -> None:
    """Async list implementation."""
    import json

    _loader, lockfile, _sources, _config_dir = _init_managers()
    lockfile_data = lockfile.load()

    agents = lockfile_data.agents
    if json_output:
        result = [
            {
                "name": agent_name,
                "version": entry.version,
                "type": entry.agent_type.value,
                "source": entry.source,
            }
            for agent_name, entry in agents.items()
        ]
        typer.echo(json.dumps(result, indent=2))
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


async def _search(query: str, json_output: bool = False) -> None:
    """Async search implementation."""
    import json

    from agent_nexus.platform.local.sources import SourceManager

    config_dir = _get_config_dir()
    sources = SourceManager(config_dir / "sources.yaml")

    results: list[dict] = []
    for source, entry in sources.search_agents(query):
        results.append(
            {
                "name": entry.name,
                "version": entry.version,
                "type": entry.type.value,
                "description": entry.description,
                "source": source.name,
            }
        )

    if json_output:
        typer.echo(json.dumps(results, indent=2))
        return

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
    if not AGENT_NAME_RE.match(name):
        typer.echo(f"Invalid agent name: {name!r}", err=True)
        raise typer.Exit(code=1)
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


async def _run(name: str, mode: str, transport: str, extra_args: list[str] | None = None) -> None:
    """Async run implementation."""
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
        # MCP stdio standalone: exec directly into the agent process so
        # the FastMCP server owns stdin/stdout for JSON-RPC framing.
        # Using ProcessManager with piped I/O would prevent the MCP
        # client from communicating with the agent.
        supervisor = AgentSupervisor(
            process_manager=ProcessManager(),
            lockfile_manager=lockfile,
            config_loader=_loader,
            config_dir=config_dir,
        )

        command = supervisor._build_command(name, entry)
        if not command:
            typer.echo(
                f"Could not resolve command for agent '{name}'.",
                err=True,
            )
            raise typer.Exit(code=1)

        env = supervisor._build_env(name, entry)
        env["AGENT_MODE"] = "mcp"

        spawn_env = os.environ.copy()
        spawn_env.update(env)

        try:
            os.execvpe(command[0], command, spawn_env)
        except FileNotFoundError:
            typer.echo(f"Command not found: {command[0]}", err=True)
            raise typer.Exit(code=1)
        except OSError as exc:
            typer.echo(f"Failed to exec agent: {exc}", err=True)
            raise typer.Exit(code=1)

    elif mode == "router":
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

        typer.echo(
            f"Starting agent '{name}' in router mode ({transport})...",
            err=True,
        )

        try:
            if transport == "sse":
                await gateway.run_sse()
            else:
                await gateway.run_stdio()
        except (KeyboardInterrupt, asyncio.CancelledError):
            typer.echo("\nShutting down...", err=True)
        finally:
            await gateway.stop()

    elif mode == "cli":
        # CLI mode: exec directly into the agent process for interactive use.
        # os.execvpe replaces the current process, giving the agent direct
        # terminal I/O without a pipe intermediary.  This avoids the deadlock
        # where piped stdin/stdout are never read/written by either side.
        supervisor = AgentSupervisor(
            process_manager=ProcessManager(),
            lockfile_manager=lockfile,
            config_loader=_loader,
            config_dir=config_dir,
        )

        command = supervisor._build_command(name, entry)
        if not command:
            typer.echo(
                f"Could not resolve command for agent '{name}'.",
                err=True,
            )
            raise typer.Exit(code=1)

        env = supervisor._build_env(name, entry)
        env["AGENT_MODE"] = "cli"

        # Merge with OS environment for interactive CLI mode
        spawn_env = os.environ.copy()
        spawn_env.update(env)

        try:
            exec_argv = command + (extra_args or [])
            os.execvpe(exec_argv[0], exec_argv, spawn_env)
        except FileNotFoundError:
            typer.echo(f"Command not found: {command[0]}", err=True)
            raise typer.Exit(code=1)
        except OSError as exc:
            typer.echo(f"Failed to exec agent: {exc}", err=True)
            raise typer.Exit(code=1)

    else:
        typer.echo(
            f"Unknown mode '{mode}'. Use: mcp, router, cli.", err=True
        )
        raise typer.Exit(code=1)


async def _wait_forever() -> None:
    """Block indefinitely until cancelled or signalled.

    Registers SIGINT/SIGTERM handlers that set a shutdown event,
    ensuring OS signals cascade graceful shutdown to child processes.
    """
    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()

    def _signal_handler() -> None:
        shutdown.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    try:
        await shutdown.wait()
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)


def _resolve_local_agent(name: str) -> Path:
    """Resolve a local agent path from the current project root.

    Walks up from CWD to find a project root (``pyproject.toml`` or ``.git``),
    then checks ``agents/atomic/{name}`` and ``agents/composite/{name}``.

    Raises ``typer.Exit`` if the agent cannot be found locally.
    """
    if not AGENT_NAME_RE.match(name):
        typer.echo(f"Invalid agent name: {name!r}", err=True)
        raise typer.Exit(code=1)
    cwd = Path.cwd()
    project_root: Path | None = None

    for parent in [cwd, *cwd.parents]:
        if (parent / "pyproject.toml").exists() or (parent / ".git").exists():
            project_root = parent
            break

    if project_root is None:
        typer.echo(
            "Error: Cannot find project root (no pyproject.toml or .git found).",
            err=True,
        )
        raise typer.Exit(code=1)

    # Search both atomic and composite agent directories
    for subdir in ("atomic", "composite"):
        candidate = project_root / "agents" / subdir / name
        if candidate.is_dir():
            return candidate.resolve()

    typer.echo(
        f"Error: Agent '{name}' not found locally. Searched:\n"
        f"  {project_root / 'agents' / 'atomic' / name}\n"
        f"  {project_root / 'agents' / 'composite' / name}",
        err=True,
    )
    raise typer.Exit(code=1)
