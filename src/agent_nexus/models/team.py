"""Team lifecycle models for multi-agent coordination."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from agent_nexus.models._common import FrozenModel, _utc_now


class TeamState:
    """Team lifecycle states."""

    FORMING = "forming"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DISSOLVING = "dissolving"
    DISSOLVED = "dissolved"
    ERROR = "error"


class TeamStatus(FrozenModel):
    """Snapshot of a team's current state."""

    team_id: str
    state: str = TeamState.FORMING
    agents: list[str] = []
    formed_at: datetime = Field(default_factory=_utc_now)
    activated_at: datetime | None = None
    dissolved_at: datetime | None = None
    error_message: str | None = None


class TeamEvent:
    """Events emitted during team lifecycle transitions."""

    TEAM_FORMED = "team_formed"
    TEAM_ACTIVATED = "team_activated"
    TEAM_SUSPENDED = "team_suspended"
    AGENT_ADDED = "agent_added"
    AGENT_REMOVED = "agent_removed"
    TEAM_DISSOLVING = "team_dissolving"
    TEAM_DISSOLVED = "team_dissolved"
    TEAM_ERROR = "team_error"
