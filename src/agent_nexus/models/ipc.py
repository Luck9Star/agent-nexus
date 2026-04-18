"""IPC message models: Platform <-> Agent communication via stdin/stdout JSON-lines."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MessageDirection(StrEnum):
    """Direction of IPC message flow."""

    PLATFORM_TO_AGENT = "platform_to_agent"
    AGENT_TO_PLATFORM = "agent_to_platform"


class PlatformToAgentType(StrEnum):
    """Types of messages sent from Platform Router to Agent subprocess."""

    CHAT = "chat"
    TASK = "task"
    DATA_REFERENCE = "data_reference"


class AgentToPlatformType(StrEnum):
    """Types of messages sent from Agent subprocess to Platform Router."""

    RESULT = "result"
    PROGRESS = "progress"
    ERROR = "error"


class PlatformToAgent(BaseModel):
    """Message from Platform Router to Agent subprocess (stdin).

    Examples:
        Chat: {"type": "chat", "content": "...", "conversation_id": "..."}
        Task: {"type": "task", "task_id": "...", "description": "..."}
        Data: {"type": "data_reference", "ref_id": "var://...", "summary": "..."}
    """

    model_config = ConfigDict(frozen=True)

    type: PlatformToAgentType
    content: str = ""
    task_id: str | None = None
    conversation_id: str | None = None
    ref_id: str | None = None
    summary: str | None = None


class AgentToPlatform(BaseModel):
    """Message from Agent subprocess to Platform Router (stdout).

    Examples:
        Result:   {"type": "result", "task_id": "...", "output": "...", "status": "completed"}
        Progress: {"type": "progress", "task_id": "...", "message": "..."}
        Error:    {"type": "error", "task_id": "...", "error": "..."}
    """

    model_config = ConfigDict(frozen=True)

    type: AgentToPlatformType
    content: str = ""
    task_id: str | None = None
    message: str | None = None
    progress_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    error: str | None = None
    status: str | None = None
    output: Any | None = None


class IPCMessage(BaseModel):
    """Envelope for any IPC message, with direction tagging.

    This is the union type used for deserialization of raw JSON-lines
    read from stdin/stdout pipes.

    Uses a ``model_validator`` to discriminate between PlatformToAgent
    and AgentToPlatform based on the ``direction`` field.  Without this,
    Pydantic would try PlatformToAgent first (left-to-right union) and
    might accept AgentToPlatform data with wrong default values.
    """

    model_config = ConfigDict(frozen=True)

    direction: MessageDirection
    payload: PlatformToAgent | AgentToPlatform

    @model_validator(mode="before")
    @classmethod
    def _resolve_payload(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        direction = values.get("direction")
        payload = values.get("payload")
        if isinstance(payload, dict) and isinstance(direction, str):
            if direction == MessageDirection.PLATFORM_TO_AGENT.value:
                values["payload"] = PlatformToAgent.model_validate(payload)
            elif direction == MessageDirection.AGENT_TO_PLATFORM.value:
                values["payload"] = AgentToPlatform.model_validate(payload)
        return values
