"""Unit tests for AgentSupervisor: _find_dead_agents, auto_restart_dead, RestartTracker."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_nexus.models.distribution import AgentType, Lockfile, LockfileEntry
from agent_nexus.platform.local.supervisor import AgentSupervisor

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    version: str = "1.0.0",
    source: str = "official",
    commit_sha: str = "a" * 40,
) -> LockfileEntry:
    return LockfileEntry(
        version=version,
        source=source,
        commit_sha=commit_sha,
        agent_type=AgentType.ATOMIC,
        installed_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _make_lockfile(
    agent_names: list[str],
) -> Lockfile:
    return Lockfile(
        agents={name: _make_entry() for name in agent_names},
    )


def _make_supervisor(
    lockfile_agents: list[str] | None = None,
    max_restarts: int = 3,
) -> tuple[AgentSupervisor, MagicMock, MagicMock, MagicMock]:
    """Build an AgentSupervisor with mock dependencies.

    Returns (supervisor, pm_mock, lockfile_mock, config_mock).
    """
    pm = MagicMock()
    pm.get_agent = MagicMock(return_value=None)
    pm.list_running = MagicMock(return_value=[])
    pm.start_agent = AsyncMock()

    lockfile_mgr = MagicMock()
    if lockfile_agents is not None:
        lockfile_mgr.load.return_value = _make_lockfile(lockfile_agents)
    else:
        lockfile_mgr.load.return_value = Lockfile()

    config = MagicMock()
    config.config_dir = Path("/tmp/test-agent-nexus")

    supervisor = AgentSupervisor(
        process_manager=pm,
        lockfile_manager=lockfile_mgr,
        config_loader=config,
        max_restarts=max_restarts,
    )
    return supervisor, pm, lockfile_mgr, config


def _make_handle(alive: bool) -> MagicMock:
    handle = MagicMock()
    handle.is_alive = alive
    return handle


# ===========================================================================
# RestartTracker
# ===========================================================================


# ===========================================================================
# auto_restart_dead
# ===========================================================================


class TestAutoRestartDead:
    """Unit tests for AgentSupervisor.auto_restart_dead()."""

    @pytest.mark.asyncio
    async def test_restarts_dead_agents(self) -> None:
        """Successfully restarted agents are returned."""
        sup, _, _, _ = _make_supervisor(
            lockfile_agents=["agent-1", "agent-2"],
        )

        # Mock _find_dead_agents
        sup._find_dead_agents = MagicMock(return_value=["agent-1", "agent-2"])

        # agent-1 restarts ok, agent-2 fails
        sup.start_agent = AsyncMock(side_effect=[True, False])

        restarted = await sup.auto_restart_dead()
        assert restarted == ["agent-1"]

    @pytest.mark.asyncio
    async def test_empty_dead_list_returns_empty(self) -> None:
        """No dead agents means empty restart list."""
        sup, _, _, _ = _make_supervisor(lockfile_agents=[])
        sup._find_dead_agents = MagicMock(return_value=[])

        restarted = await sup.auto_restart_dead()
        assert restarted == []

    @pytest.mark.asyncio
    async def test_exception_during_restart_is_caught(self) -> None:
        """Exceptions from start_agent are caught; successful ones still returned."""
        sup, _, _, _ = _make_supervisor(
            lockfile_agents=["agent-1", "agent-2"],
        )
        sup._find_dead_agents = MagicMock(return_value=["agent-1", "agent-2"])

        sup.start_agent = AsyncMock(
            side_effect=[RuntimeError("process crashed"), True],
        )

        restarted = await sup.auto_restart_dead()
        assert restarted == ["agent-2"]

    @pytest.mark.asyncio
    async def test_all_restarts_fail_returns_empty(self) -> None:
        """When all start_agent calls fail, returns empty list."""
        sup, _, _, _ = _make_supervisor(
            lockfile_agents=["agent-1"],
        )
        sup._find_dead_agents = MagicMock(return_value=["agent-1"])
        sup.start_agent = AsyncMock(return_value=False)

        restarted = await sup.auto_restart_dead()
        assert restarted == []

    @pytest.mark.asyncio
    async def test_all_restarts_succeed(self) -> None:
        """When all start_agent calls succeed, returns all names."""
        sup, _, _, _ = _make_supervisor(
            lockfile_agents=["agent-1", "agent-2"],
        )
        sup._find_dead_agents = MagicMock(return_value=["agent-1", "agent-2"])
        sup.start_agent = AsyncMock(return_value=True)

        restarted = await sup.auto_restart_dead()
        assert restarted == ["agent-1", "agent-2"]
