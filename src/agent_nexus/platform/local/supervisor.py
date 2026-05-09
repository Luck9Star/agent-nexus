"""AgentSupervisor: manage agent lifecycle based on config and lockfile.

Wraps ProcessManager with:
- Config-based startup (read lockfile, start all installed agents)
- Health monitoring (periodic heartbeat checks via IPC)
- Auto-restart on failure (with max restart count to prevent loops)
- Graceful shutdown

Not persistent across platform restarts -- reads lockfile on start.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import toml

from agent_nexus.models.distribution import Lockfile, LockfileEntry
from agent_nexus.platform.config.loader import ConfigLoader
from agent_nexus.platform.orchestration.process_manager import (
    ProcessManager,
)
from agent_nexus.platform.utils import AGENT_NAME_RE

from .lockfile import LockfileManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# RestartTracker — per-agent restart counter
# ---------------------------------------------------------------------------


@dataclass
class RestartTracker:
    """Track restart attempts for a single agent to prevent restart loops."""

    count: int = 0
    max_restarts: int = 3

    def should_retry(self) -> bool:
        """Return ``True`` if the agent has not exceeded ``max_restarts``."""
        return self.count < self.max_restarts

    def record(self) -> None:
        """Increment the restart counter."""
        self.count += 1

    def reset(self) -> None:
        """Reset the counter after a successful start."""
        self.count = 0


# ---------------------------------------------------------------------------
# AgentSupervisor
# ---------------------------------------------------------------------------


class AgentSupervisor:
    """Manage agent lifecycle based on config and lockfile.

    Parameters
    ----------
    process_manager:
        Low-level subprocess manager.
    lockfile_manager:
        Reads and writes ``lockfile.json``.
    config_loader:
        Loads platform config (model providers, runtime settings).
    config_dir:
        Platform config directory (typically ``~/.agent-nexus/``).
        Used to resolve agent install paths and venv paths.
    max_restarts:
        Maximum auto-restart attempts per agent per session.
    """

    def __init__(
        self,
        process_manager: ProcessManager,
        lockfile_manager: LockfileManager,
        config_loader: ConfigLoader,
        config_dir: Path | None = None,
        max_restarts: int = 3,
    ) -> None:
        self._pm = process_manager
        self._lockfile = lockfile_manager
        self._config = config_loader
        self._config_dir = config_dir or config_loader.config_dir
        self._max_restarts = max_restarts
        self._restart_trackers: dict[str, RestartTracker] = {}
        self._started_agents: set[str] = set()
        self._resolved_packages: dict[str, str] = {}  # agent_name -> package_name
        # Config cache with mtime-based invalidation
        self._config_cache: Any = None
        self._config_cache_mtime: float = 0.0

    # ------------------------------------------------------------------
    # Bulk start / stop
    # ------------------------------------------------------------------

    async def start_all(self) -> list[str]:
        """Start all agents listed in the lockfile.

        Reads the lockfile, resolves each agent entry to a command, and
        starts it via :class:`ProcessManager`.  Starts agents in parallel
        using ``asyncio.gather``.  Skips agents that fail to start (logs
        the error and continues).

        Returns
        -------
        list[str]
            Names of agents that were successfully started.
        """
        lockfile = self._lockfile.load()
        if not lockfile.agents:
            return []

        tasks = [self.start_agent(agent_name, lockfile=lockfile) for agent_name in lockfile.agents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        started: list[str] = []
        for agent_name, result in zip(lockfile.agents, results, strict=False):
            if isinstance(result, BaseException):
                logger.error(
                    "Failed to start agent '%s' [%s]: %s",
                    agent_name,
                    type(result).__name__,
                    result,
                )
            elif result:
                started.append(agent_name)

        logger.info(
            "start_all: %d/%d agents started",
            len(started),
            len(lockfile.agents),
        )
        return started

    async def stop_all(self) -> None:
        """Stop all running agents gracefully.

        Delegates to :meth:`ProcessManager.stop_all`.
        """
        await self._pm.stop_all()
        logger.info("All agents stopped")

    # ------------------------------------------------------------------
    # Single agent start / stop
    # ------------------------------------------------------------------

    async def start_agent(
        self,
        agent_name: str,
        lockfile: Lockfile | None = None,
    ) -> bool:
        """Start a single agent by name.

        1. Read the lockfile entry for *agent_name*.
        2. Resolve the agent path and venv path.
        3. Build the launch command.
        4. Start via :meth:`ProcessManager.start_agent`.

        Parameters
        ----------
        agent_name:
            Agent to start.
        lockfile:
            Already-loaded lockfile (avoids redundant disk read when
            called from :meth:`start_all`).  When ``None``, loads from
            disk.

        Returns
        -------
        bool
            ``True`` if started successfully, ``False`` if the agent is
            not installed or could not be launched.
        """
        if lockfile is not None:
            entry = self._lockfile.get_entry_from(lockfile, agent_name)
        else:
            entry = self._lockfile.get_entry(agent_name)
        if entry is None:
            logger.warning("Agent '%s' not found in lockfile", agent_name)
            return False

        command = self._build_command(agent_name, entry)
        if not command:
            logger.error("Could not build command for agent '%s'", agent_name)
            return False

        cwd = self._resolve_agent_dir(agent_name)
        env = self._build_env(agent_name, entry, agent_dir=cwd)

        try:
            handle = await self._pm.start_agent(
                name=agent_name,
                command=command,
                cwd=cwd,
                env=env,
            )
            # Reset restart tracker on successful start
            tracker = self._restart_trackers.get(agent_name)
            if tracker:
                tracker.reset()
            self._started_agents.add(agent_name)
            logger.info("Agent '%s' started (pid=%s)", agent_name, handle.pid)
            return True
        except Exception as exc:
            logger.error("Failed to start agent '%s': %s", agent_name, exc)
            return False

    async def stop_agent(self, agent_name: str) -> bool:
        """Stop a running agent.

        Returns
        -------
        bool
            ``True`` if the agent was running and is now stopped,
            ``False`` if the agent was not running.
        """
        handle = self._pm.get_agent(agent_name)
        if handle is None or not handle.is_alive:
            logger.debug("Agent '%s' is not running", agent_name)
            return False

        try:
            await self._pm.stop_agent(agent_name)
            # Remove from started set so auto_restart_dead won't
            # pull it back up against the user's intent.
            self._started_agents.discard(agent_name)
            logger.info("Agent '%s' stopped", agent_name)
            return True
        except KeyError:
            logger.warning("Agent '%s' not found in process manager", agent_name)
            return False

    # ------------------------------------------------------------------
    # Health monitoring
    # ------------------------------------------------------------------

    async def health_check_all(self) -> dict[str, bool]:
        """Check health of all running agents.

        Returns
        -------
        dict[str, bool]
            Mapping of ``{agent_name: alive}``.  Only includes agents
            that are registered in the process manager.
        """
        names = self._pm.list_running()
        if not names:
            return {}

        tasks = [self._pm.health_check(name) for name in names]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        return {
            name: (result if isinstance(result, bool) else False)
            for name, result in zip(names, results, strict=False)
        }

    # ------------------------------------------------------------------
    # Auto-restart
    # ------------------------------------------------------------------

    def _find_dead_agents(self) -> list[str]:
        """Identify dead agents eligible for restart (within budget)."""
        lockfile = self._lockfile.load()
        dead_agents: list[str] = []
        for agent_name in lockfile.agents:
            if agent_name not in self._started_agents:
                continue

            handle = self._pm.get_agent(agent_name)
            if handle is not None and handle.is_alive:
                continue

            tracker = self._restart_trackers.setdefault(
                agent_name,
                RestartTracker(max_restarts=self._max_restarts),
            )
            if not tracker.should_retry():
                logger.warning(
                    "Agent '%s' exceeded max restarts (%d), skipping",
                    agent_name,
                    tracker.max_restarts,
                )
                continue

            tracker.record()
            logger.info(
                "Restarting dead agent '%s' (attempt %d/%d)",
                agent_name,
                tracker.count,
                tracker.max_restarts,
            )
            dead_agents.append(agent_name)
        return dead_agents

    async def auto_restart_dead(self) -> list[str]:
        """Check for dead agents and restart them.

        Iterates over all agents known to the process manager.  Agents
        whose process has died are restarted in parallel via
        ``asyncio.gather``, subject to the per-agent ``max_restarts``
        limit to prevent infinite restart loops.

        Returns
        -------
        list[str]
            Names of agents that were successfully restarted.
        """
        dead_agents = self._find_dead_agents()
        if not dead_agents:
            return []

        results = await asyncio.gather(
            *(self.start_agent(name) for name in dead_agents),
            return_exceptions=True,
        )

        restarted: list[str] = []
        for agent_name, result in zip(dead_agents, results, strict=False):
            if isinstance(result, BaseException):
                logger.error(
                    "Failed to restart agent '%s' [%s]: %s",
                    agent_name,
                    type(result).__name__,
                    result,
                )
            elif result:
                restarted.append(agent_name)

        return restarted

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def list_running(self) -> list[str]:
        """List names of currently running agents."""
        return self._pm.list_running()

    def list_installed(self) -> list[str]:
        """List names of installed (but not necessarily running) agents."""
        lockfile = self._lockfile.load()
        return list(lockfile.agents.keys())

    # ------------------------------------------------------------------
    # Internal: command building
    # ------------------------------------------------------------------

    def _build_command(self, agent_name: str, entry: LockfileEntry) -> list[str] | None:
        """Build the subprocess command for an agent.

        Strategy:
        1. If the agent has a venv, use ``<venv>/bin/python <main.py>``.
        2. Otherwise, try ``python3 <agent_dir>/<pkg>/main.py``.
        3. Fallback: ``uvx <agent_name>``.
        """
        if not AGENT_NAME_RE.match(agent_name):
            logger.warning(
                "Agent name '%s' contains unsafe characters, skipping command build",
                agent_name,
            )
            return None

        agent_dir = self._resolve_agent_dir(agent_name)
        pkg_name = self._resolve_package_name(agent_name, agent_dir)

        agent_main = agent_dir / "main.py"
        pkg_main = (agent_dir / pkg_name / "main.py") if pkg_name else None

        if entry.venv_path:
            cmd = self._try_venv_command(agent_name, entry, agent_main, pkg_main)
            if cmd is not None:
                return cmd
            # venv tried but failed — if venv_python exists, it's a
            # security block or missing main files → stop (don't fall through)
            venv_python = Path(entry.venv_path).resolve() / "bin" / "python"
            if venv_python.exists():
                return None
            # venv_python not found → fall through to system commands

        if (agent_dir / "pyproject.toml").exists():
            logger.warning(
                "Agent '%s' has pyproject.toml but no venv — dependencies "
                "may not be installed. Re-install the agent to create a venv.",
                agent_name,
            )

        return self._try_system_command(agent_name, agent_main, pkg_main)

    def _try_venv_command(
        self,
        agent_name: str,
        entry: LockfileEntry,
        agent_main: Path,
        pkg_main: Path | None,
    ) -> list[str] | None:
        """Try building command using venv python (Strategy 1)."""
        if not entry.venv_path:
            return None
        venv_python = Path(entry.venv_path).resolve() / "bin" / "python"
        if not venv_python.exists():
            logger.warning(
                "Configured venv for '%s' not found at %s, falling back to system python/uvx",
                agent_name,
                venv_python,
            )
            return None
        allowed = self._config_dir.resolve()
        if not venv_python.is_relative_to(allowed):
            logger.warning(
                "venv_path outside config_dir, skipping: %s",
                venv_python,
            )
            return None  # treated as "not found" for the caller
        if agent_main.exists():
            return [str(venv_python), str(agent_main)]
        if pkg_main and pkg_main.exists():
            return [str(venv_python), str(pkg_main)]
        return None

    @staticmethod
    def _try_system_command(
        agent_name: str,
        agent_main: Path,
        pkg_main: Path | None,
    ) -> list[str]:
        """Try system python or fall back to uvx (Strategy 2+3)."""
        if pkg_main and pkg_main.exists():
            return ["python3", str(pkg_main)]
        if agent_main.exists() and not agent_main.is_symlink():
            return ["python3", str(agent_main)]
        return ["uvx", agent_name]

    def _resolve_package_name(self, agent_name: str, agent_dir: Path) -> str | None:
        """Discover the Python package name inside an installed agent directory.

        Tries three heuristics in order: subdir with ``__init__.py`` + ``main.py``,
        any subdir with ``__init__.py``, then ``pyproject.toml`` hatch config.
        Results are cached per ``agent_name``.
        """
        cached = self._resolved_packages.get(agent_name)
        if cached is not None:
            return cached

        if not agent_dir.is_dir():
            return None

        result = (
            self._find_package_with_main(agent_dir)
            or self._find_package_with_init(agent_dir)
            or self._read_hatch_packages(agent_dir)
        )

        if result is not None:
            self._resolved_packages[agent_name] = result

        return result

    @staticmethod
    def _find_package_with_main(agent_dir: Path) -> str | None:
        for child in sorted(agent_dir.iterdir()):
            if (
                child.is_dir()
                and not child.name.startswith((".", "_"))
                and (child / "__init__.py").exists()
                and (child / "main.py").exists()
            ):
                return child.name
        return None

    @staticmethod
    def _find_package_with_init(agent_dir: Path) -> str | None:
        for child in sorted(agent_dir.iterdir()):
            if (
                child.is_dir()
                and not child.name.startswith((".", "_"))
                and (child / "__init__.py").exists()
            ):
                return child.name
        return None

    @staticmethod
    def _read_hatch_packages(agent_dir: Path) -> str | None:
        pyproject = agent_dir / "pyproject.toml"
        if not pyproject.exists():
            return None
        try:
            raw = toml.loads(pyproject.read_text(encoding="utf-8"))
            packages = (
                raw.get("tool", {})
                .get("hatch", {})
                .get("build", {})
                .get("targets", {})
                .get("wheel", {})
                .get("packages", [])
            )
            return packages[0] if packages else None
        except Exception:
            logger.debug("Failed to read pyproject.toml for package name", exc_info=True)
            return None

    def _resolve_agent_dir(self, agent_name: str) -> Path:
        """Resolve the installed agent directory.

        Raises:
            ValueError: If agent_name contains unsafe characters.
        """
        if not AGENT_NAME_RE.match(agent_name):
            raise ValueError(f"Agent name '{agent_name}' contains unsafe characters")
        return self._config_dir / "agents" / agent_name

    def _build_env(
        self,
        agent_name: str,
        entry: LockfileEntry,
        *,
        agent_dir: Path | None = None,
    ) -> dict[str, str]:
        """Build extra environment variables for the agent subprocess.

        Platform-injected env vars (agent identity + runtime context):

        - ``AGENT_NAME`` — canonical agent name (e.g. ``doc-filler``).
        - ``AGENT_DIR`` — absolute path to the agent's install root
          (e.g. ``~/.agent-nexus/agents/doc-filler``).  Agents use this
          to locate composition.toml, configs, and other bundled files
          without relying on ``__file__`` traversal.
        - ``AGENT_MODEL`` — default model from platform config.

        Also forwards API keys from configured providers so agent
        subprocesses can make LLM calls.

        Args:
            agent_dir: Pre-resolved agent install directory.  When supplied
                the caller has already resolved the path via
                ``_resolve_agent_dir`` and it is reused here to avoid a
                redundant filesystem look-up.  Falls back to calling
                ``_resolve_agent_dir`` internally when *None*.
        """
        env: dict[str, str] = {}

        # Inject agent identity — agents must NOT guess their install path
        env["AGENT_NAME"] = agent_name
        if agent_dir is None:
            agent_dir = self._resolve_agent_dir(agent_name)
        env["AGENT_DIR"] = str(agent_dir)

        # Load platform config for model defaults (cached)
        try:
            config = self._load_config_cached()
            if config.models.default:
                env["AGENT_MODEL"] = config.models.default

            # Forward API keys for each configured provider
            for _name, provider in config.models.providers.items():
                if provider.api_key_env:
                    key = os.environ.get(provider.api_key_env, "")
                    if key:
                        env[provider.api_key_env] = key
        except (OSError, toml.TomlDecodeError):
            logger.error(
                "Failed to load config for agent '%s' env building "
                "-- agent may lack model config and API keys",
                agent_name,
                exc_info=True,
            )

        return env

    def _load_config_cached(self):
        """Load config with file-mtime-based cache invalidation."""
        config_path = self._config_dir / "config.toml"
        try:
            mtime = config_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if self._config_cache is not None and mtime == self._config_cache_mtime:
            return self._config_cache
        config = self._config.load_config()
        self._config_cache = config
        self._config_cache_mtime = mtime
        return config
