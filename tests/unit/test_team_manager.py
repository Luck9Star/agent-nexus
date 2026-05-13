"""Unit tests for TeamManager — team lifecycle state machine.

Exercises the full lifecycle: FORMING -> ACTIVE -> SUSPENDED -> ACTIVE -> DISSOLVING -> DISSOLVED,
plus agent add/remove, invalid transitions, event callbacks, and error handling.

ProcessManager is mocked via AsyncMock — no real subprocesses.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agent_nexus.models.team import TeamEvent, TeamState
from agent_nexus.platform.orchestration.team_manager import TeamManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pm() -> AsyncMock:
    """Mock ProcessManager with async start_agent / stop_agent."""
    mock = AsyncMock()
    mock.start_agent = AsyncMock()
    mock.stop_agent = AsyncMock()
    return mock


@pytest.fixture
def tm(pm: AsyncMock) -> TeamManager:
    """TeamManager with mock ProcessManager and agent configs."""
    agent_config = {
        "agent-a": {"command": ["echo", "a"]},
        "agent-b": {"command": ["echo", "b"]},
        "agent-c": {"command": ["echo", "c"]},
        "agent-d": {"command": ["echo", "d"]},
    }
    return TeamManager(process_manager=pm, agent_config=agent_config)


@pytest.fixture
def formed_team(tm: TeamManager) -> str:
    """Create a team in FORMING state and return its ID."""
    import asyncio

    team_id = "test-team"
    asyncio.get_event_loop().run_until_complete(tm.create_team(team_id, ["agent-a", "agent-b"]))
    return team_id


# ---------------------------------------------------------------------------
# create_team
# ---------------------------------------------------------------------------


class TestCreateTeam:
    @pytest.mark.asyncio
    async def test_creates_team_in_forming_state(self, tm: TeamManager) -> None:
        status = await tm.create_team("team-1", ["agent-a", "agent-b"])
        assert status.team_id == "team-1"
        assert status.state == TeamState.FORMING
        assert status.agents == ["agent-a", "agent-b"]
        assert status.formed_at is not None

    @pytest.mark.asyncio
    async def test_duplicate_team_raises(self, tm: TeamManager) -> None:
        await tm.create_team("team-1", ["agent-a"])
        with pytest.raises(ValueError, match="already exists"):
            await tm.create_team("team-1", ["agent-b"])

    @pytest.mark.asyncio
    async def test_emits_team_formed_event(self, tm: TeamManager) -> None:
        events: list[tuple[str, str, dict]] = []
        tm.on_event(lambda tid, evt, payload: events.append((tid, evt, payload)))
        await tm.create_team("team-1", ["agent-a"])
        assert len(events) == 1
        assert events[0][1] == TeamEvent.TEAM_FORMED
        assert events[0][2]["agents"] == ["agent-a"]


# ---------------------------------------------------------------------------
# activate_team
# ---------------------------------------------------------------------------


class TestActivateTeam:
    @pytest.mark.asyncio
    async def test_forming_to_active(self, tm: TeamManager) -> None:
        await tm.create_team("team-1", ["agent-a", "agent-b"])
        status = await tm.activate_team("team-1")
        assert status.state == TeamState.ACTIVE
        assert status.activated_at is not None

    @pytest.mark.asyncio
    async def test_emits_activated_event(self, tm: TeamManager) -> None:
        events: list[tuple[str, str, dict]] = []
        tm.on_event(lambda tid, evt, payload: events.append((tid, evt, payload)))
        await tm.create_team("team-1", ["agent-a"])
        await tm.activate_team("team-1")
        event_types = [e[1] for e in events]
        assert TeamEvent.TEAM_ACTIVATED in event_types

    @pytest.mark.asyncio
    async def test_agent_start_failure_transitions_to_error(
        self, tm: TeamManager, pm: AsyncMock
    ) -> None:
        pm.start_agent.side_effect = [None, RuntimeError("boom")]
        await tm.create_team("team-1", ["agent-a", "agent-b"])
        with pytest.raises(RuntimeError, match="boom"):
            await tm.activate_team("team-1")
        error_team = tm.get_team("team-1")
        assert error_team.state == TeamState.ERROR
        assert error_team.error_message is not None
        assert "boom" in error_team.error_message


# ---------------------------------------------------------------------------
# suspend_team
# ---------------------------------------------------------------------------


class TestSuspendTeam:
    @pytest.mark.asyncio
    async def test_active_to_suspended(self, tm: TeamManager) -> None:
        await tm.create_team("team-1", ["agent-a"])
        await tm.activate_team("team-1")
        status = await tm.suspend_team("team-1")
        assert status.state == TeamState.SUSPENDED

    @pytest.mark.asyncio
    async def test_suspended_to_active_resume(self, tm: TeamManager) -> None:
        await tm.create_team("team-1", ["agent-a"])
        await tm.activate_team("team-1")
        await tm.suspend_team("team-1")
        status = await tm.activate_team("team-1")
        assert status.state == TeamState.ACTIVE


# ---------------------------------------------------------------------------
# dissolve_team
# ---------------------------------------------------------------------------


class TestDissolveTeam:
    @pytest.mark.asyncio
    async def test_active_to_dissolved(self, tm: TeamManager) -> None:
        await tm.create_team("team-1", ["agent-a"])
        await tm.activate_team("team-1")
        status = await tm.dissolve_team("team-1")
        assert status.state == TeamState.DISSOLVED
        assert status.dissolved_at is not None

    @pytest.mark.asyncio
    async def test_emits_dissolving_then_dissolved(self, tm: TeamManager) -> None:
        events: list[tuple[str, str, dict]] = []
        tm.on_event(lambda tid, evt, payload: events.append((tid, evt, payload)))
        await tm.create_team("team-1", ["agent-a"])
        await tm.activate_team("team-1")
        await tm.dissolve_team("team-1")
        event_types = [e[1] for e in events]
        assert TeamEvent.TEAM_DISSOLVING in event_types
        assert TeamEvent.TEAM_DISSOLVED in event_types
        dissolve_idx = event_types.index(TeamEvent.TEAM_DISSOLVING)
        dissolved_idx = event_types.index(TeamEvent.TEAM_DISSOLVED)
        assert dissolve_idx < dissolved_idx

    @pytest.mark.asyncio
    async def test_suspended_to_dissolved(self, tm: TeamManager) -> None:
        await tm.create_team("team-1", ["agent-a"])
        await tm.activate_team("team-1")
        await tm.suspend_team("team-1")
        status = await tm.dissolve_team("team-1")
        assert status.state == TeamState.DISSOLVED


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------


class TestInvalidTransitions:
    @pytest.mark.asyncio
    async def test_dissolved_cannot_activate(self, tm: TeamManager) -> None:
        await tm.create_team("team-1", ["agent-a"])
        await tm.activate_team("team-1")
        await tm.dissolve_team("team-1")
        with pytest.raises(ValueError, match="Invalid transition"):
            await tm.activate_team("team-1")

    @pytest.mark.asyncio
    async def test_forming_cannot_suspend(self, tm: TeamManager) -> None:
        await tm.create_team("team-1", ["agent-a"])
        with pytest.raises(ValueError, match="Invalid transition"):
            await tm.suspend_team("team-1")

    @pytest.mark.asyncio
    async def test_forming_cannot_dissolve_directly(self, tm: TeamManager) -> None:
        await tm.create_team("team-1", ["agent-a"])
        with pytest.raises(ValueError, match="Invalid transition"):
            await tm.dissolve_team("team-1")


# ---------------------------------------------------------------------------
# add_agent / remove_agent
# ---------------------------------------------------------------------------


class TestAgentMembership:
    @pytest.mark.asyncio
    async def test_add_agent_to_active_team(self, tm: TeamManager) -> None:
        await tm.create_team("team-1", ["agent-a"])
        await tm.activate_team("team-1")
        status = await tm.add_agent("team-1", "agent-b")
        assert "agent-b" in status.agents

    @pytest.mark.asyncio
    async def test_add_duplicate_agent_raises(self, tm: TeamManager) -> None:
        await tm.create_team("team-1", ["agent-a"])
        await tm.activate_team("team-1")
        with pytest.raises(ValueError, match="already in team"):
            await tm.add_agent("team-1", "agent-a")

    @pytest.mark.asyncio
    async def test_add_agent_to_non_active_raises(self, tm: TeamManager) -> None:
        await tm.create_team("team-1", ["agent-a"])
        with pytest.raises(ValueError, match="Cannot add agent"):
            await tm.add_agent("team-1", "agent-b")

    @pytest.mark.asyncio
    async def test_add_agent_rollback_on_start_failure(
        self, tm: TeamManager, pm: AsyncMock
    ) -> None:
        await tm.create_team("team-1", ["agent-a"])
        await tm.activate_team("team-1")
        pm.start_agent.side_effect = RuntimeError("cannot start")
        with pytest.raises(RuntimeError, match="cannot start"):
            await tm.add_agent("team-1", "agent-b")
        # Agent list should be unchanged
        assert "agent-b" not in tm.get_team("team-1").agents

    @pytest.mark.asyncio
    async def test_remove_agent_from_active_team(self, tm: TeamManager) -> None:
        await tm.create_team("team-1", ["agent-a", "agent-b"])
        await tm.activate_team("team-1")
        status = await tm.remove_agent("team-1", "agent-b")
        assert "agent-b" not in status.agents
        assert "agent-a" in status.agents

    @pytest.mark.asyncio
    async def test_remove_nonexistent_agent_raises(self, tm: TeamManager) -> None:
        await tm.create_team("team-1", ["agent-a"])
        await tm.activate_team("team-1")
        with pytest.raises(ValueError, match="not in team"):
            await tm.remove_agent("team-1", "agent-z")

    @pytest.mark.asyncio
    async def test_remove_last_agent_raises(self, tm: TeamManager) -> None:
        await tm.create_team("team-1", ["agent-a"])
        await tm.activate_team("team-1")
        with pytest.raises(ValueError, match="Cannot remove last agent"):
            await tm.remove_agent("team-1", "agent-a")

    @pytest.mark.asyncio
    async def test_add_and_remove_emit_events(self, tm: TeamManager) -> None:
        events: list[tuple[str, str, dict]] = []
        tm.on_event(lambda tid, evt, payload: events.append((tid, evt, payload)))
        await tm.create_team("team-1", ["agent-a"])
        await tm.activate_team("team-1")
        await tm.add_agent("team-1", "agent-b")
        await tm.remove_agent("team-1", "agent-b")
        event_types = [e[1] for e in events]
        assert TeamEvent.AGENT_ADDED in event_types
        assert TeamEvent.AGENT_REMOVED in event_types


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


class TestQueryHelpers:
    @pytest.mark.asyncio
    async def test_get_team_returns_status(self, tm: TeamManager) -> None:
        await tm.create_team("team-1", ["agent-a"])
        status = tm.get_team("team-1")
        assert status.team_id == "team-1"

    @pytest.mark.asyncio
    async def test_get_nonexistent_team_raises(self, tm: TeamManager) -> None:
        with pytest.raises(ValueError, match="not found"):
            tm.get_team("no-such-team")

    @pytest.mark.asyncio
    async def test_list_teams(self, tm: TeamManager) -> None:
        await tm.create_team("team-1", ["agent-a"])
        await tm.create_team("team-2", ["agent-b"])
        teams = tm.list_teams()
        assert "team-1" in teams
        assert "team-2" in teams
        assert len(teams) == 2


# ---------------------------------------------------------------------------
# Event handler error isolation
# ---------------------------------------------------------------------------


class TestEventHandlerErrors:
    @pytest.mark.asyncio
    async def test_handler_exception_does_not_break_flow(self, tm: TeamManager) -> None:
        def bad_handler(tid: str, evt: str, payload: dict) -> None:
            raise RuntimeError("handler broke")

        events: list[str] = []
        tm.on_event(bad_handler)
        tm.on_event(lambda tid, evt, payload: events.append(evt))

        # Should not raise despite bad_handler
        await tm.create_team("team-1", ["agent-a"])
        assert TeamEvent.TEAM_FORMED in events
