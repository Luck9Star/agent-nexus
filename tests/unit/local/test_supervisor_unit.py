"""Unit tests for AgentSupervisor: _find_dead_agents, auto_restart_dead, RestartTracker."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_nexus.models.distribution import AgentType, Lockfile, LockfileEntry
from agent_nexus.platform.local.supervisor import AgentSupervisor, RestartTracker


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


class TestRestartTracker:
    """Unit tests for the RestartTracker dataclass."""

    def test_should_retry_returns_true_when_below_max(self) -> None:
        tracker = RestartTracker(count=0, max_restarts=3)
        assert tracker.should_retry() is True

    def test_should_retry_returns_true_at_count_two(self) -> None:
        tracker = RestartTracker(count=2, max_restarts=3)
        assert tracker.should_retry() is True

    def test_should_retry_returns_false_at_max(self) -> None:
        tracker = RestartTracker(count=3, max_restarts=3)
        assert tracker.should_retry() is False

    def test_should_retry_returns_false_above_max(self) -> None:
        tracker = RestartTracker(count=5, max_restarts=3)
        assert tracker.should_retry() is False

    def test_record_increments_count(self) -> None:
        tracker = RestartTracker(count=0, max_restarts=3)
        tracker.record()
        assert tracker.count == 1
        tracker.record()
        assert tracker.count == 2

    def test_reset_sets_count_to_zero(self) -> None:
        tracker = RestartTracker(count=2, max_restarts=3)
        tracker.reset()
        assert tracker.count == 0

    def test_record_then_reset_cycle(self) -> None:
        tracker = RestartTracker(count=0, max_restarts=2)
        tracker.record()
        tracker.record()
        assert tracker.should_retry() is False
        tracker.reset()
        assert tracker.should_retry() is True
        assert tracker.count == 0


# ===========================================================================
# _find_dead_agents
# ===========================================================================


class TestFindDeadAgents:
    """Unit tests for AgentSupervisor._find_dead_agents()."""

    def test_returns_dead_agent_in_started_set(self) -> None:
        """Only agents in _started_agents with dead handles are returned."""
        sup, pm, _, _ = _make_supervisor(
            lockfile_agents=["agent-1", "agent-2", "agent-3"],
        )
        sup._started_agents = {"agent-1", "agent-2"}

        # agent-1: dead, agent-2: alive
        pm.get_agent.side_effect = lambda name: (
            _make_handle(alive=False) if name == "agent-1"
            else _make_handle(alive=True)
        )

        dead = sup._find_dead_agents()
        assert dead == ["agent-1"]

    def test_skips_agents_not_in_started_set(self) -> None:
        """Agents in lockfile but not in _started_agents are skipped."""
        sup, pm, _, _ = _make_supervisor(
            lockfile_agents=["agent-1", "agent-2"],
        )
        # Only agent-1 is in _started_agents; agent-2 has dead handle
        sup._started_agents = {"agent-1"}
        pm.get_agent.return_value = _make_handle(alive=False)

        dead = sup._find_dead_agents()
        assert dead == ["agent-1"]

    def test_skips_alive_agents(self) -> None:
        """Alive agents in _started_agents are not returned."""
        sup, pm, _, _ = _make_supervisor(
            lockfile_agents=["agent-1"],
        )
        sup._started_agents = {"agent-1"}
        pm.get_agent.return_value = _make_handle(alive=True)

        dead = sup._find_dead_agents()
        assert dead == []

    def test_skips_agent_exceeding_max_restarts(self) -> None:
        """Agent at max_restarts limit is skipped."""
        sup, pm, _, _ = _make_supervisor(
            lockfile_agents=["agent-1"],
            max_restarts=2,
        )
        sup._started_agents = {"agent-1"}
        pm.get_agent.return_value = _make_handle(alive=False)

        # Pre-set tracker to max
        sup._restart_trackers["agent-1"] = RestartTracker(count=2, max_restarts=2)

        dead = sup._find_dead_agents()
        assert dead == []

    def test_returns_multiple_dead_agents(self) -> None:
        """Multiple dead agents in _started_agents are all returned."""
        sup, pm, _, _ = _make_supervisor(
            lockfile_agents=["agent-1", "agent-2", "agent-3"],
        )
        sup._started_agents = {"agent-1", "agent-2", "agent-3"}
        pm.get_agent.return_value = _make_handle(alive=False)

        dead = sup._find_dead_agents()
        assert set(dead) == {"agent-1", "agent-2", "agent-3"}

    def test_skips_agent_with_no_handle(self) -> None:
        """Agent with None handle (never started by PM) is treated as dead."""
        sup, pm, _, _ = _make_supervisor(
            lockfile_agents=["agent-1"],
        )
        sup._started_agents = {"agent-1"}
        pm.get_agent.return_value = None

        dead = sup._find_dead_agents()
        assert dead == ["agent-1"]

    def test_records_restart_attempt(self) -> None:
        """_find_dead_agents records a restart attempt for each dead agent."""
        sup, pm, _, _ = _make_supervisor(
            lockfile_agents=["agent-1"],
        )
        sup._started_agents = {"agent-1"}
        pm.get_agent.return_value = _make_handle(alive=False)

        sup._find_dead_agents()
        tracker = sup._restart_trackers["agent-1"]
        assert tracker.count == 1

    def test_empty_lockfile_returns_empty(self) -> None:
        """Empty lockfile yields no dead agents."""
        sup, _, _, _ = _make_supervisor(lockfile_agents=[])
        sup._started_agents = {"agent-1"}

        dead = sup._find_dead_agents()
        assert dead == []


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
