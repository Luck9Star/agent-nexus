"""IPC message models: Platform <-> Agent communication via stdin/stdout JSON-lines."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


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
    progress_pct: float | None = None
    error: str | None = None
    status: str | None = None
    output: Any | None = None


class IPCMessage(BaseModel):
    """Envelope for any IPC message, with direction tagging.

    This is the union type used for deserialization of raw JSON-lines
    read from stdin/stdout pipes.
    """

    model_config = ConfigDict(frozen=True)

    direction: MessageDirection
    payload: PlatformToAgent | AgentToPlatform
