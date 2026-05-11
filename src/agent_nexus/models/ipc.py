"""IPC message models: Platform <-> Agent communication via stdin/stdout JSON-lines."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, Literal

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
    # A2A delivery types — Platform relays these to target agents
    RECEIVE_MESSAGE = "receive_message"
    RECEIVE_REQUEST = "receive_request"
    RECEIVE_BROADCAST = "receive_broadcast"
    RECEIVE_REPLY = "receive_reply"


class AgentToPlatformType(StrEnum):
    """Types of messages sent from Agent subprocess to Platform Router."""

    RESULT = "result"
    PROGRESS = "progress"
    ERROR = "error"
    # A2A origination types — Agent asks Platform to relay
    SEND_MESSAGE = "send_message"
    SEND_REQUEST = "send_request"
    BROADCAST = "broadcast"
    REPLY = "reply"


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
                byte_len = len(v.encode("utf-8"))
                if byte_len > 65536:
                    raise ValueError(f"output exceeds maximum serialized size of 65536 bytes ({byte_len} bytes)")
                return v
            # Complex types: measure serialized size directly
            try:
                serialized = json.dumps(v, default=str)
            except (TypeError, ValueError):
                serialized = str(v)
            byte_len = len(serialized.encode("utf-8"))
            if byte_len > 65536:
                raise ValueError(f"output exceeds maximum serialized size of 65536 bytes ({byte_len} bytes)")
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


# ---------------------------------------------------------------------------
# A2A (Agent-to-Agent) messaging models
# ---------------------------------------------------------------------------
# Platform-as-Broker pattern: agents never connect directly.
# Platform Router relays messages between agents via existing IPC transport.
# See: docs/roadmap/ for A2A design document.


class AgentAddress(FrozenModel):
    """Network-layer address for an agent in A2A communication.

    Used to identify senders and recipients in agent-to-agent messages.
    ``role`` is optional metadata (e.g. "coordinator", "worker", "reviewer").
    """

    agent_id: str
    role: str | None = None
    composition: str | None = None


class A2AMessage(FrozenModel):
    """Agent-to-Agent message carried over the Platform-as-Broker relay.

    This is the high-level message model. It is serialized into a
    ``PlatformToAgent`` envelope for delivery to the target agent's IPC stream.

    Attributes:
        message_id: Unique identifier (UUID) for this message.
        from_agent: Sender agent identifier.
        to_agent: Recipient agent identifier. ``None`` for broadcasts.
        msg_type: Message pattern — "chat", "request", "broadcast", "reply".
        in_reply_to: For replies, the ``message_id`` of the original request.
        content: Message payload (text).
        metadata: Optional key-value metadata (e.g. priority, correlation tags).
        timestamp: Unix timestamp (seconds since epoch).
    """

    message_id: str
    from_agent: str
    to_agent: str | None = None
    msg_type: Literal["chat", "request", "broadcast", "reply"]
    in_reply_to: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: float
