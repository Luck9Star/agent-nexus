"""Team lifecycle manager for multi-agent coordination."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from agent_nexus.models.team import TeamEvent, TeamState, TeamStatus

if TYPE_CHECKING:
    from agent_nexus.platform.orchestration.process_manager import ProcessManager

logger = logging.getLogger(__name__)

EventHandler = Callable[[str, str, dict], None]  # (team_id, event_type, payload)

# Agent start config: command + optional cwd/env overrides.
AgentConfig = dict[str, dict]  # agent_id -> {"command": [...], "cwd": ..., "env": ...}


class TeamManager:
    """Manage team lifecycle: form, activate, suspend, dissolve.

    Wraps ProcessManager to provide team-level grouping and lifecycle
    state machine for coordinated multi-agent operations.
    """

    VALID_TRANSITIONS: dict[str, set[str]] = {
        TeamState.FORMING: {TeamState.ACTIVE, TeamState.ERROR},
        TeamState.ACTIVE: {TeamState.SUSPENDED, TeamState.DISSOLVING, TeamState.ERROR},
        TeamState.SUSPENDED: {TeamState.ACTIVE, TeamState.DISSOLVING, TeamState.ERROR},
        TeamState.DISSOLVING: {TeamState.DISSOLVED, TeamState.ERROR},
        TeamState.DISSOLVED: set(),  # terminal state
        TeamState.ERROR: {TeamState.DISSOLVING},
    }

    def __init__(
        self,
        process_manager: ProcessManager,
        agent_config: AgentConfig | None = None,
    ) -> None:
        self._pm = process_manager
        self._agent_config: AgentConfig = agent_config or {}
        self._teams: dict[str, TeamStatus] = {}
        self._event_handlers: list[EventHandler] = []

    # ------------------------------------------------------------------
    # Event system
    # ------------------------------------------------------------------

    def on_event(self, handler: EventHandler) -> None:
        """Register an event handler for team lifecycle events."""
        self._event_handlers.append(handler)

    def _emit(self, team_id: str, event_type: str, payload: dict | None = None) -> None:
        for handler in self._event_handlers:
            try:
                handler(team_id, event_type, payload or {})
            except Exception:
                logger.exception("Event handler error for %s/%s", team_id, event_type)

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _transition(self, team_id: str, new_state: str) -> TeamStatus:
        current = self._teams.get(team_id)
        if current is None:
            raise ValueError(f"Team {team_id} not found")
        valid = self.VALID_TRANSITIONS.get(current.state, set())
        if new_state not in valid:
            raise ValueError(
                f"Invalid transition for team {team_id}: {current.state} -> {new_state}"
            )
        now = datetime.now(UTC)
        updates: dict = {"state": new_state}
        if new_state == TeamState.ACTIVE:
            updates["activated_at"] = now
        elif new_state == TeamState.DISSOLVED:
            updates["dissolved_at"] = now

        updated = current.model_copy(update=updates)
        self._teams[team_id] = updated
        return updated

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_team(
        self,
        team_id: str,
        agent_ids: list[str],
    ) -> TeamStatus:
        """Create a new team in FORMING state."""
        if team_id in self._teams:
            raise ValueError(f"Team {team_id} already exists")
        status = TeamStatus(
            team_id=team_id,
            state=TeamState.FORMING,
            agents=list(agent_ids),
        )
        self._teams[team_id] = status
        self._emit(team_id, TeamEvent.TEAM_FORMED, {"agents": agent_ids})
        logger.info("Team %s formed with agents: %s", team_id, agent_ids)
        return status

    async def activate_team(self, team_id: str) -> TeamStatus:
        """Activate a formed/suspended team — starts all agent subprocesses."""
        status = self._get_team(team_id)
        for agent_id in status.agents:
            try:
                cfg = self._agent_config.get(agent_id, {})
                command = cfg.get("command", [agent_id])
                cwd = cfg.get("cwd")
                env = cfg.get("env")
                cwd_path = Path(cwd) if cwd else None
                await self._pm.start_agent(agent_id, command=command, cwd=cwd_path, env=env)
            except Exception as e:
                logger.error("Failed to start agent %s in team %s: %s", agent_id, team_id, e)
                self._transition(team_id, TeamState.ERROR)
                error_status = self._teams[team_id].model_copy(
                    update={"error_message": f"Agent {agent_id} failed to start: {e}"}
                )
                self._teams[team_id] = error_status
                self._emit(team_id, TeamEvent.TEAM_ERROR, {"error": str(e)})
                raise
        self._transition(team_id, TeamState.ACTIVE)
        self._emit(team_id, TeamEvent.TEAM_ACTIVATED)
        return self._teams[team_id]

    async def suspend_team(self, team_id: str) -> TeamStatus:
        """Suspend an active team — stops agents but keeps team in memory."""
        self._get_team(team_id)
        for agent_id in self._teams[team_id].agents:
            try:
                await self._pm.stop_agent(agent_id)
            except Exception as e:
                logger.warning("Error stopping agent %s during suspend: %s", agent_id, e)
        self._transition(team_id, TeamState.SUSPENDED)
        self._emit(team_id, TeamEvent.TEAM_SUSPENDED)
        return self._teams[team_id]

    async def dissolve_team(self, team_id: str) -> TeamStatus:
        """Gracefully dissolve a team — stop all agents and release resources."""
        status = self._get_team(team_id)
        self._transition(team_id, TeamState.DISSOLVING)
        self._emit(team_id, TeamEvent.TEAM_DISSOLVING)
        for agent_id in status.agents:
            try:
                await self._pm.stop_agent(agent_id)
            except Exception as e:
                logger.warning("Error stopping agent %s during dissolve: %s", agent_id, e)
        self._transition(team_id, TeamState.DISSOLVED)
        self._emit(team_id, TeamEvent.TEAM_DISSOLVED)
        return self._teams[team_id]

    async def add_agent(self, team_id: str, agent_id: str) -> TeamStatus:
        """Add an agent to an active team."""
        status = self._get_team(team_id)
        if agent_id in status.agents:
            raise ValueError(f"Agent {agent_id} already in team {team_id}")
        if status.state != TeamState.ACTIVE:
            raise ValueError(f"Cannot add agent to team in {status.state} state")
        new_agents = status.agents + [agent_id]
        updated = status.model_copy(update={"agents": new_agents})
        self._teams[team_id] = updated
        try:
            cfg = self._agent_config.get(agent_id, {})
            command = cfg.get("command", [agent_id])
            cwd = cfg.get("cwd")
            env = cfg.get("env")
            cwd_path = Path(cwd) if cwd else None
            await self._pm.start_agent(agent_id, command=command, cwd=cwd_path, env=env)
        except Exception as e:
            # Rollback
            self._teams[team_id] = status
            raise RuntimeError(f"Failed to start agent {agent_id}: {e}") from e
        self._emit(team_id, TeamEvent.AGENT_ADDED, {"agent_id": agent_id})
        return self._teams[team_id]

    async def remove_agent(self, team_id: str, agent_id: str) -> TeamStatus:
        """Remove an agent from an active team."""
        status = self._get_team(team_id)
        if agent_id not in status.agents:
            raise ValueError(f"Agent {agent_id} not in team {team_id}")
        if len(status.agents) == 1:
            raise ValueError("Cannot remove last agent from team -- dissolve instead")
        new_agents = [a for a in status.agents if a != agent_id]
        updated = status.model_copy(update={"agents": new_agents})
        self._teams[team_id] = updated
        try:
            await self._pm.stop_agent(agent_id)
        except Exception as e:
            logger.warning("Error stopping removed agent %s: %s", agent_id, e)
        self._emit(team_id, TeamEvent.AGENT_REMOVED, {"agent_id": agent_id})
        return self._teams[team_id]

    def get_team(self, team_id: str) -> TeamStatus:
        """Get current team status."""
        return self._get_team(team_id)

    def list_teams(self) -> dict[str, TeamStatus]:
        """List all teams and their statuses."""
        return dict(self._teams)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_team(self, team_id: str) -> TeamStatus:
        status = self._teams.get(team_id)
        if status is None:
            raise ValueError(f"Team {team_id} not found")
        return status
