"""IPC message models: Platform <-> Agent communication via stdin/stdout JSON-lines."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_nexus.models._common import FrozenModel


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


class PlatformToAgent(FrozenModel):
    """Message from Platform Router to Agent subprocess (stdin).

    Examples:
        Chat: {"type": "chat", "content": "...", "conversation_id": "..."}
        Task: {"type": "task", "content": "...", "task_id": "..."}
        Data: {"type": "data_reference", "ref_id": "var://...", "summary": "..."}
    """

    type: PlatformToAgentType
    content: str = Field(default="", max_length=65536)
    task_id: str | None = None
    conversation_id: str | None = None
    ref_id: str | None = None
    summary: str | None = None


class AgentToPlatform(FrozenModel):
    """Message from Agent subprocess to Platform Router (stdout).

    Examples:
        Result:   {"type": "result", "task_id": "...", "output": "...", "status": "completed"}
        Progress: {"type": "progress", "task_id": "...", "message": "..."}
        Error:    {"type": "error", "task_id": "...", "error": "..."}
    """

    type: AgentToPlatformType
    content: str = Field(default="", max_length=65536)
    task_id: str | None = None
    message: str | None = Field(default=None, max_length=65536)
    progress_pct: float | None = Field(default=None, ge=0.0, le=100.0)
    error: str | None = Field(default=None, max_length=65536)
    status: str | None = None
    output: Any | None = None

    @property
    def is_success(self) -> bool:
        """Check if this response indicates successful completion."""
        if self.type == AgentToPlatformType.ERROR:
            return False
        return self.status is None or self.status.lower() == "completed"

    @field_validator("output")
    @classmethod
    def _validate_output_size(cls, v):
        if v is not None:
            # Fast path: strings are the most common output type
            if isinstance(v, str):
                if len(v) > 65536:
                    raise ValueError("output exceeds maximum serialized size of 65536 bytes")
                return v
            # Complex types: measure serialized size directly
            try:
                serialized = json.dumps(v, default=str)
            except (TypeError, ValueError):
                serialized = str(v)
            if len(serialized) > 65536:
                raise ValueError("output exceeds maximum serialized size of 65536 bytes")
        return v


class IPCMessage(FrozenModel):
    """Envelope for any IPC message, with direction tagging.

    This is the union type used for deserialization of raw JSON-lines
    read from stdin/stdout pipes.

    Uses a ``model_validator`` to discriminate between PlatformToAgent
    and AgentToPlatform based on the ``direction`` field.  Without this,
    Pydantic would try PlatformToAgent first (left-to-right union) and
    might accept AgentToPlatform data with wrong default values.
    """

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
