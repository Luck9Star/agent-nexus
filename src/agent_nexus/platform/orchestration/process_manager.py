"""ProcessManager — asyncio.subprocess agent lifecycle management.

Each agent runs as an independent subprocess. The platform communicates
with agents via stdin/stdout JSON-lines through the IPC protocol.

AgentHandle tracks the process, its streams, and health metadata.

Reference: ClawTeam ``clawteam/spawn/subprocess_backend.py`` — MIT License.
Simplified: no tmux/wsh backends, no file-based mailbox, pure stream IPC.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_nexus.platform.orchestration.ipc import (
    IPCError,
    IPCProtocol,
    IPCStream,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AgentHandle — per-agent process tracker
# ---------------------------------------------------------------------------


@dataclass
class AgentHandle:
    """Track a running agent subprocess.

    Stores both the live process + IPC protocol AND the original start
    parameters so that ``ProcessManager.restart_agent`` can re-launch
    with identical settings.
    """

    name: str
    process: asyncio.subprocess.Process
    ipc: IPCProtocol

    # Original start parameters (for restart)
    start_command: list[str] = field(default_factory=list)
    start_cwd: Path | None = None
    start_env: dict[str, str] = field(default_factory=dict)

    # Health metadata
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_alive(self) -> bool:
        """Check if process is still running."""
        return self.process.returncode is None

    @property
    def pid(self) -> int | None:
        return self.process.pid


# ---------------------------------------------------------------------------
# ProcessManager — lifecycle coordinator
# ---------------------------------------------------------------------------


class ProcessManager:
    """Manage agent subprocess lifecycles.

    Responsibilities:
    - Start agents as ``asyncio.subprocess``
    - Stop agents gracefully (IPC EOF -> SIGTERM -> SIGKILL)
    - Health check via IPC heartbeat
    - Restart crashed agents (reuses original start params)
    - List / clean up running agents

    Not persistent across platform restarts — ``AgentSupervisor`` handles
    that higher-level concern.
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentHandle] = {}

    # ------------------------------------------------------------------
    # Start
    # ------------------------------------------------------------------

    async def start_agent(
        self,
        name: str,
        command: list[str],
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> AgentHandle:
        """Start an agent as a subprocess.

        Args:
            name: Unique agent identifier.
            command: Command to launch the agent (e.g. ``["uvx", "doc-filler"]``).
            cwd: Working directory for the agent.
            env: Additional environment variables (merged into ``os.environ``).

        Returns:
            :class:`AgentHandle` with :class:`IPCProtocol` attached.

        Raises:
            ValueError: An agent with *name* is already registered.
            RuntimeError: The subprocess failed to start.
        """
        if name in self._agents and self._agents[name].is_alive:
            raise ValueError(f"Agent '{name}' is already running")

        # Clean up stale handle (dead process) so we can reuse the name.
        if name in self._agents:
            self._agents.pop(name, None)

        spawn_env = os.environ.copy()
        if env:
            spawn_env.update(env)

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd) if cwd else None,
                env=spawn_env,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Failed to start agent '{name}' with command {command}: {exc}"
            ) from exc

        assert process.stdin is not None
        assert process.stdout is not None

        stream = IPCStream(
            stdin=process.stdin,
            stdout=process.stdout,
        )
        ipc = IPCProtocol(stream)

        handle = AgentHandle(
            name=name,
            process=process,
            ipc=ipc,
            start_command=list(command),
            start_cwd=cwd,
            start_env=dict(env) if env else {},
        )

        self._agents[name] = handle
        logger.info(
            "Agent '%s' started (pid=%s, command=%s)",
            name,
            process.pid,
            command,
        )
        return handle

    # ------------------------------------------------------------------
    # Stop
    # ------------------------------------------------------------------

    async def stop_agent(self, name: str, timeout: float = 10.0) -> None:
        """Stop a running agent gracefully.

        Shutdown sequence (defence-in-depth):

        1. Close IPC stdin — signals EOF so the agent can shut down cleanly.
        2. Wait up to *timeout* seconds for the process to exit.
        3. Send ``SIGTERM`` and wait another *timeout* seconds.
        4. Send ``SIGKILL`` if still alive.

        Args:
            name: Agent identifier.
            timeout: Seconds to wait at each graceful stage.

        Raises:
            KeyError: Agent *name* not found.
        """
        handle = self._agents.get(name)
        if handle is None:
            raise KeyError(f"Agent '{name}' not found")

        process = handle.process

        if not handle.is_alive:
            # Already dead — just clean up.
            self._agents.pop(name, None)
            logger.info("Agent '%s' already exited (rc=%s)", name, process.returncode)
            return

        # Stage 1: Close IPC stdin (signal EOF).
        try:
            await handle.ipc.stream.close()
        except Exception:
            pass

        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
            self._agents.pop(name, None)
            logger.info("Agent '%s' exited cleanly after IPC close", name)
            return
        except asyncio.TimeoutError:
            pass

        # Stage 2: SIGTERM.
        logger.warning("Agent '%s' did not exit, sending SIGTERM", name)
        try:
            process.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            self._agents.pop(name, None)
            return

        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
            self._agents.pop(name, None)
            logger.info("Agent '%s' terminated after SIGTERM", name)
            return
        except asyncio.TimeoutError:
            pass

        # Stage 3: SIGKILL.
        logger.error("Agent '%s' did not exit after SIGTERM, sending SIGKILL", name)
        try:
            process.kill()
        except ProcessLookupError:
            pass

        try:
            await process.wait()
        except Exception:
            pass

        self._agents.pop(name, None)
        logger.info("Agent '%s' killed", name)

    # ------------------------------------------------------------------
    # Restart
    # ------------------------------------------------------------------

    async def restart_agent(self, name: str, **kwargs: Any) -> AgentHandle:
        """Stop and restart an agent.

        Reuses the original ``command``, ``cwd``, and ``env`` that were
        passed to :meth:`start_agent`.  Any keyword arguments override
        those stored values.
        """
        handle = self._agents.get(name)
        if handle is None:
            raise KeyError(f"Agent '{name}' not found")

        # Merge stored params with overrides.
        command = kwargs.get("command", handle.start_command)
        cwd = kwargs.get("cwd", handle.start_cwd)
        env = kwargs.get("env", handle.start_env)

        await self.stop_agent(name)
        return await self.start_agent(name, command=command, cwd=cwd, env=env)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self, name: str) -> bool:
        """Check if agent is alive and responsive.

        Sends a heartbeat ping via IPC and waits for a pong response.
        Updates ``last_heartbeat`` on success.

        Returns:
            ``True`` if the agent responded, ``False`` otherwise.

        Raises:
            KeyError: Agent *name* not found.
        """
        handle = self._agents.get(name)
        if handle is None:
            raise KeyError(f"Agent '{name}' not found")

        if not handle.is_alive:
            return False

        try:
            ok = await handle.ipc.send_heartbeat()
        except (IPCError, OSError):
            return False

        if ok:
            handle.last_heartbeat = datetime.now(timezone.utc)
        return ok

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_agent(self, name: str) -> AgentHandle | None:
        """Get agent handle by name, or ``None`` if not registered."""
        return self._agents.get(name)

    def list_running(self) -> list[str]:
        """Return names of all agents whose process is still alive."""
        return [
            name
            for name, handle in self._agents.items()
            if handle.is_alive
        ]

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    async def stop_all(self, timeout: float = 10.0) -> None:
        """Stop all running agents gracefully."""
        names = list(self._agents.keys())
        if not names:
            return

        # Stop in parallel — each stop_agent has its own timeout.
        await asyncio.gather(
            *(self.stop_agent(name, timeout=timeout) for name in names),
            return_exceptions=True,
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _cleanup_dead(self) -> list[str]:
        """Remove handles for dead processes.

        Returns:
            List of agent names that were cleaned up.
        """
        dead: list[str] = []
        for name, handle in list(self._agents.items()):
            if not handle.is_alive:
                dead.append(name)
                self._agents.pop(name, None)
                logger.debug(
                    "Cleaned up dead agent handle '%s' (rc=%s)",
                    name,
                    handle.process.returncode,
                )
        return dead
