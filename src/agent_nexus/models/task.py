"""Task graph models: TaskItem, TaskState, and related data structures."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

_utc_now = lambda: datetime.now(timezone.utc)


class TaskState(StrEnum):
    """Task lifecycle states.

    State machine: pending -> in_progress -> completed | failed
                           ^                |
                           +--- blocked <---+  (if dependencies not met)
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class TaskItem(BaseModel):
    """A single task in the TaskGraph.

    Tasks form a dependency graph via `blocked_by`. The TaskGraph engine
    manages state transitions; this model is pure data.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    description: str
    agent: str
    blocked_by: list[str] = Field(default_factory=list)
    vars: dict[str, Any] = Field(default_factory=dict)
    state: TaskState = TaskState.PENDING
    result: Any | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)


class TaskGraphSnapshot(BaseModel):
    """Frozen snapshot of a TaskGraph for serialization / inspection."""

    model_config = ConfigDict(frozen=True)

    tasks: list[TaskItem] = Field(default_factory=list)
    parallel_groups: list[list[str]] = Field(default_factory=list)
