"""Unit tests for agent_nexus.models.ipc module."""

import json

import pytest
from pydantic import ValidationError

from agent_nexus.models.ipc import (
    AgentToPlatform,
    AgentToPlatformType,
    IPCMessage,
    MessageDirection,
    PlatformToAgent,
    PlatformToAgentType,
)


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------

class TestEnums:
    def test_message_direction(self):
        assert set(MessageDirection) == {
            MessageDirection.PLATFORM_TO_AGENT,
            MessageDirection.AGENT_TO_PLATFORM,
        }

    def test_platform_to_agent_type(self):
        assert set(PlatformToAgentType) == {
            PlatformToAgentType.CHAT,
            PlatformToAgentType.TASK,
            PlatformToAgentType.DATA_REFERENCE,
        }

    def test_agent_to_platform_type(self):
        assert set(AgentToPlatformType) == {
            AgentToPlatformType.RESULT,
            AgentToPlatformType.PROGRESS,
            AgentToPlatformType.ERROR,
        }

    def test_invalid_enum_raises(self):
        with pytest.raises(ValueError):
            PlatformToAgentType("unknown")
        with pytest.raises(ValueError):
            AgentToPlatformType("unknown")


# ---------------------------------------------------------------------------
# PlatformToAgent
# ---------------------------------------------------------------------------

class TestPlatformToAgent:
    def test_chat_message(self):
        msg = PlatformToAgent(
            type=PlatformToAgentType.CHAT,
            content="Hello agent",
            conversation_id="conv-1",
        )
        assert msg.type is PlatformToAgentType.CHAT
        assert msg.content == "Hello agent"
        assert msg.conversation_id == "conv-1"
        assert msg.task_id is None
        assert msg.ref_id is None

    def test_task_message(self):
        msg = PlatformToAgent(
            type=PlatformToAgentType.TASK,
            task_id="task-1",
            content="Process document",
        )
        assert msg.type is PlatformToAgentType.TASK
        assert msg.task_id == "task-1"

    def test_data_reference_message(self):
        msg = PlatformToAgent(
            type=PlatformToAgentType.DATA_REFERENCE,
            ref_id="var://input/docx",
            summary="Input document reference",
        )
        assert msg.type is PlatformToAgentType.DATA_REFERENCE
        assert msg.ref_id == "var://input/docx"

    def test_defaults(self):
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT)
        assert msg.content == ""
        assert msg.task_id is None
        assert msg.conversation_id is None
        assert msg.ref_id is None
        assert msg.summary is None

    def test_frozen(self):
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hi")
        with pytest.raises(ValidationError):
            msg.content = "changed"


# ---------------------------------------------------------------------------
# AgentToPlatform
# ---------------------------------------------------------------------------

class TestAgentToPlatform:
    def test_result_message(self):
        msg = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            task_id="task-1",
            output={"status": "done"},
            status="completed",
        )
        assert msg.type is AgentToPlatformType.RESULT
        assert msg.output == {"status": "done"}
        assert msg.status == "completed"

    def test_progress_message(self):
        msg = AgentToPlatform(
            type=AgentToPlatformType.PROGRESS,
            task_id="task-1",
            content="50% complete",
            progress_pct=50.0,
        )
        assert msg.type is AgentToPlatformType.PROGRESS
        assert msg.progress_pct == 50.0

    def test_error_message(self):
        msg = AgentToPlatform(
            type=AgentToPlatformType.ERROR,
            task_id="task-1",
            error="Division by zero",
        )
        assert msg.type is AgentToPlatformType.ERROR
        assert msg.error == "Division by zero"

    def test_defaults(self):
        msg = AgentToPlatform(type=AgentToPlatformType.RESULT)
        assert msg.content == ""
        assert msg.task_id is None
        assert msg.progress_pct is None
        assert msg.error is None
        assert msg.status is None
        assert msg.output is None

    def test_frozen(self):
        msg = AgentToPlatform(type=AgentToPlatformType.RESULT)
        with pytest.raises(ValidationError):
            msg.output = "changed"


# ---------------------------------------------------------------------------
# IPCMessage
# ---------------------------------------------------------------------------

class TestIPCMessage:
    def test_platform_to_agent_envelope(self):
        payload = PlatformToAgent(
            type=PlatformToAgentType.CHAT,
            content="Hello",
        )
        msg = IPCMessage(
            direction=MessageDirection.PLATFORM_TO_AGENT,
            payload=payload,
        )
        assert msg.direction is MessageDirection.PLATFORM_TO_AGENT
        assert isinstance(msg.payload, PlatformToAgent)

    def test_agent_to_platform_envelope(self):
        payload = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            output="done",
        )
        msg = IPCMessage(
            direction=MessageDirection.AGENT_TO_PLATFORM,
            payload=payload,
        )
        assert msg.direction is MessageDirection.AGENT_TO_PLATFORM
        assert isinstance(msg.payload, AgentToPlatform)

    def test_frozen(self):
        payload = PlatformToAgent(type=PlatformToAgentType.CHAT)
        msg = IPCMessage(
            direction=MessageDirection.PLATFORM_TO_AGENT,
            payload=payload,
        )
        with pytest.raises(ValidationError):
            msg.direction = MessageDirection.AGENT_TO_PLATFORM


# ---------------------------------------------------------------------------
# Round-trip serialization
# ---------------------------------------------------------------------------

class TestSerializationRoundTrip:
    def test_platform_to_agent_round_trip(self):
        msg = PlatformToAgent(
            type=PlatformToAgentType.TASK,
            content="Process it",
            task_id="task-1",
        )
        json_str = msg.model_dump_json()
        msg2 = PlatformToAgent.model_validate_json(json_str)
        assert msg2 == msg

    def test_agent_to_platform_round_trip(self):
        msg = AgentToPlatform(
            type=AgentToPlatformType.PROGRESS,
            task_id="task-1",
            progress_pct=75.5,
        )
        json_str = msg.model_dump_json()
        msg2 = AgentToPlatform.model_validate_json(json_str)
        assert msg2 == msg

    def test_ipc_message_round_trip(self):
        payload = PlatformToAgent(
            type=PlatformToAgentType.CHAT,
            content="Hello",
        )
        msg = IPCMessage(
            direction=MessageDirection.PLATFORM_TO_AGENT,
            payload=payload,
        )
        data = msg.model_dump()
        msg2 = IPCMessage(**data)
        assert msg2 == msg

    def test_ipc_message_json_round_trip(self):
        payload = AgentToPlatform(
            type=AgentToPlatformType.ERROR,
            error="fail",
        )
        msg = IPCMessage(
            direction=MessageDirection.AGENT_TO_PLATFORM,
            payload=payload,
        )
        json_str = msg.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["direction"] == "agent_to_platform"
        msg2 = IPCMessage.model_validate_json(json_str)
        assert msg2 == msg
