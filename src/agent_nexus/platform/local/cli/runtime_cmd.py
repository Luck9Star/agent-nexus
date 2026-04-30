"""Runtime management commands: start, stop, restart, status, logs, ps."""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path

import typer

from agent_nexus.platform.local.cli._shared import _init_managers
from agent_nexus.platform.utils import AGENT_NAME_RE

runtime_app = typer.Typer(help="Runtime management")


# =====================================================================
# CLI-facing commands
# =====================================================================


@runtime_app.command()
def start(
    name: str | None = typer.Argument(None, help="Agent name to start"),
    all_agents: bool = typer.Option(False, "--all", help="Start all installed agents"),
) -> None:
    """Start an agent or all agents."""
    if not name and not all_agents:
        typer.echo("Specify an agent name or use --all.")
        raise typer.Exit(code=1)

    if all_agents:
        asyncio.run(_start_all())
    else:
        asyncio.run(_start_one(name))  # type: ignore[arg-type]


@runtime_app.command()
def stop(
    name: str | None = typer.Argument(None, help="Agent name to stop"),
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
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
) -> None:
    """Show recent log output for an agent."""
    if follow:
        _follow_logs(name, lines)
    else:
        _show_logs(name, lines)


@runtime_app.command()
def ps() -> None:
    """Alias for status -- show running agents."""
    asyncio.run(_status())


# =====================================================================
# Async implementations
# =====================================================================


async def _make_supervisor():
    """Create AgentSupervisor with the standard manager stack.

    Returns (supervisor, config_dir) to avoid repeating the
    init-managers + ProcessManager + AgentSupervisor boilerplate.
    """
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
    return supervisor, config_dir, pm


async def _start_one(name: str) -> None:
    if not AGENT_NAME_RE.match(name):
        typer.echo(f"Invalid agent name: {name!r}", err=True)
        raise typer.Exit(code=1)

    supervisor, config_dir, pm = await _make_supervisor()

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
    supervisor, _config_dir, _pm = await _make_supervisor()

    started = await supervisor.start_all()
    if started:
        typer.echo(f"Started {len(started)} agent(s): {', '.join(started)}")
    else:
        typer.echo("No agents to start.")


async def _stop_one(name: str) -> None:
    if not AGENT_NAME_RE.match(name):
        typer.echo(f"Invalid agent name: {name!r}", err=True)
        raise typer.Exit(code=1)

    supervisor, config_dir, _pm = await _make_supervisor()

    ok = await supervisor.stop_agent(name)
    if ok:
        pid_file = config_dir / "agents" / f"{name}.pid"
        with contextlib.suppress(FileNotFoundError):
            pid_file.unlink()
        typer.echo(f"Stopped {name}")
    else:
        typer.echo(f"Agent '{name}' is not running.", err=True)


async def _stop_all() -> None:
    supervisor, config_dir, _pm = await _make_supervisor()

    await supervisor.stop_all()
    pid_dir = config_dir / "agents"
    if pid_dir.exists():
        for pid_file in pid_dir.glob("*.pid"):
            with contextlib.suppress(FileNotFoundError):
                pid_file.unlink()
    typer.echo("Stopped all agents.")


async def _restart_agent(name: str) -> None:
    if not AGENT_NAME_RE.match(name):
        typer.echo(f"Invalid agent name: {name!r}", err=True)
        raise typer.Exit(code=1)

    supervisor, config_dir, pm = await _make_supervisor()

    ok = await supervisor.stop_agent(name)
    if ok:
        pid_file = config_dir / "agents" / f"{name}.pid"
        with contextlib.suppress(FileNotFoundError):
            pid_file.unlink()

    ok = await supervisor.start_agent(name)
    if not ok:
        typer.echo(f"Failed to restart agent '{name}'.", err=True)
        raise typer.Exit(code=1)

    handle = pm.get_agent(name)
    pid_str = str(handle.pid) if handle else "unknown"
    pid_dir = config_dir / "agents"
    pid_dir.mkdir(parents=True, exist_ok=True)
    (pid_dir / f"{name}.pid").write_text(pid_str, encoding="utf-8")

    typer.echo(f"Restarted {name} (pid: {pid_str})")


async def _status() -> None:
    _loader, lockfile, _sources, config_dir = _init_managers()
    lockfile_data = lockfile.load()
    installed_agents = list(lockfile_data.agents.keys())

    if not installed_agents:
        typer.echo("No agents installed.")
        return

    running: set[str] = set()
    pids: dict[str, str] = {}
    pid_dir = config_dir / "agents"
    for agent_name in installed_agents:
        pid_file = pid_dir / f"{agent_name}.pid"
        if pid_file.exists():
            try:
                pid_str = pid_file.read_text(encoding="utf-8").strip()
                pid_int = int(pid_str)
                try:
                    os.kill(pid_int, 0)
                    running.add(agent_name)
                    pids[agent_name] = pid_str
                except (ProcessLookupError, OSError):
                    with contextlib.suppress(FileNotFoundError):
                        pid_file.unlink()
            except (ValueError, OSError):
                pass

    typer.echo(f"{'Name':<25} {'Installed':<12} {'Running':<10} {'PID':<10}")
    typer.echo("-" * 60)
    for agent_name in installed_agents:
        is_running = "yes" if agent_name in running else "no"
        pid = pids.get(agent_name, "-")
        typer.echo(f"{agent_name:<25} {'yes':<12} {is_running:<10} {pid:<10}")


def _resolve_log_path(name: str) -> Path:
    """Validate agent name and return log file path.

    Raises ``typer.Exit`` if the name is invalid or the log file does not exist.
    """
    if not AGENT_NAME_RE.match(name):
        typer.echo(f"Invalid agent name: {name!r}", err=True)
        raise typer.Exit(code=1)

    _loader, _lockfile, _sources, config_dir = _init_managers()
    log_path = config_dir / "logs" / f"{name}.log"

    if not log_path.exists():
        typer.echo(f"No log file for '{name}'. Agent may not have been started.")
        raise typer.Exit(code=0)

    return log_path


def _show_logs(name: str, num_lines: int) -> None:
    log_path = _resolve_log_path(name)
    try:
        from collections import deque

        with open(log_path, encoding="utf-8") as f:
            tail = deque(f, maxlen=num_lines)
        typer.echo("".join(tail))
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"Error reading log: {exc}", err=True)


def _follow_logs(name: str, num_lines: int = 50) -> None:
    """Follow log output in real-time (tail -f style)."""
    log_path = _resolve_log_path(name)

    import time
    from collections import deque

    # Print existing content (memory-efficient: only keep last N lines)
    with open(log_path, encoding="utf-8", errors="replace") as f:
        tail = deque(f, maxlen=num_lines)
    for line in tail:
        typer.echo(line)

    # Follow for new lines
    last_pos = log_path.stat().st_size

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
