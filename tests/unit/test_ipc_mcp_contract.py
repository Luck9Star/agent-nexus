"""E2E: IPC MCP contract correctness — protocol boundary validation.

Validates that IPC messages at the Platform↔Agent boundary conform to the
JSON-lines protocol contract.  This is the Python-Rust language boundary:
Rust crates (ap-core, ap-runtime) must produce and consume messages in
exactly this format.

Contract invariants tested:
1. PlatformToAgent serialization matches JSON-lines spec (single-line JSON + \\n)
2. AgentToPlatform deserialization handles all field types
3. Field constraints: content max_length=65536, progress_pct in [0, 100]
4. is_success() logic matches Rust's is_success() implementation
5. IPCMessage envelope correctly discriminates direction
6. Data reference protocol round-trips with ref_id and summary fields
7. Output field size validation enforces 65536-byte serialized limit
"""

import json

import pytest
from pydantic_core import ValidationError as PydanticValidationError

from agent_nexus.models.ipc import (
    AgentToPlatform,
    AgentToPlatformType,
    IPCMessage,
    MessageDirection,
    PlatformToAgent,
    PlatformToAgentType,
)

# ---------------------------------------------------------------------------
# PlatformToAgent serialization contract
# ---------------------------------------------------------------------------


class TestPlatformToAgentSerialization:
    """Verify outgoing messages serialize to correct JSON-lines format."""

    def test_chat_message_json_format(self) -> None:
        """Chat message serializes with type='chat' and all fields."""
        msg = PlatformToAgent(
            type=PlatformToAgentType.CHAT,
            content="hello agent",
            conversation_id="conv-1",
        )
        raw = msg.model_dump_json(exclude_none=True)
        data = json.loads(raw)

        assert data["type"] == "chat"
        assert data["content"] == "hello agent"
        assert data["conversation_id"] == "conv-1"
        # No null fields leaked
        assert "task_id" not in data
        assert "ref_id" not in data

    def test_task_message_json_format(self) -> None:
        """Task message serializes with type='task' and task_id."""
        msg = PlatformToAgent(
            type=PlatformToAgentType.TASK,
            content="analyze this code",
            task_id="t-42",
        )
        raw = msg.model_dump_json(exclude_none=True)
        data = json.loads(raw)

        assert data["type"] == "task"
        assert data["content"] == "analyze this code"
        assert data["task_id"] == "t-42"

    def test_data_reference_json_format(self) -> None:
        """Data reference message serializes with ref_id and summary."""
        msg = PlatformToAgent(
            type=PlatformToAgentType.DATA_REFERENCE,
            content="upstream analysis",
            ref_id="var://agent-1/output",
            summary="[agent-1] upstream analysis ~2KB",
        )
        raw = msg.model_dump_json(exclude_none=True)
        data = json.loads(raw)

        assert data["type"] == "data_reference"
        assert data["ref_id"] == "var://agent-1/output"
        assert data["summary"] == "[agent-1] upstream analysis ~2KB"

    def test_serialization_is_single_line_json(self) -> None:
        """Serialized messages contain no newlines (JSON-lines requirement)."""
        msg = PlatformToAgent(
            type=PlatformToAgentType.CHAT,
            content="multi\nline\ncontent",
        )
        raw = msg.model_dump_json(exclude_none=True)
        # JSON-lines: no literal newlines in the serialized form
        assert "\n" not in raw

    def test_content_max_length_enforced(self) -> None:
        """Content field rejects strings exceeding 65536 characters."""
        with pytest.raises(PydanticValidationError):
            PlatformToAgent(
                type=PlatformToAgentType.CHAT,
                content="x" * 65537,
            )

    def test_content_at_max_length_accepted(self) -> None:
        """Content field accepts strings at exactly 65536 characters."""
        msg = PlatformToAgent(
            type=PlatformToAgentType.CHAT,
            content="x" * 65536,
        )
        assert len(msg.content) == 65536


# ---------------------------------------------------------------------------
# AgentToPlatform deserialization contract
# ---------------------------------------------------------------------------


class TestAgentToPlatformDeserialization:
    """Verify incoming messages parse correctly from agent JSON output."""

    def test_result_message_from_json(self) -> None:
        """Result message deserializes from agent JSON output."""
        raw = {
            "type": "result",
            "task_id": "t-1",
            "content": "analysis complete",
            "status": "completed",
        }
        msg = AgentToPlatform.model_validate(raw)

        assert msg.type == AgentToPlatformType.RESULT
        assert msg.task_id == "t-1"
        assert msg.content == "analysis complete"
        assert msg.status == "completed"
        assert msg.is_success is True

    def test_progress_message_from_json(self) -> None:
        """Progress message deserializes with progress_pct field."""
        raw = {
            "type": "progress",
            "task_id": "t-1",
            "content": "50% done",
            "progress_pct": 50.0,
        }
        msg = AgentToPlatform.model_validate(raw)

        assert msg.type == AgentToPlatformType.PROGRESS
        assert msg.progress_pct == 50.0
        assert msg.is_success is True  # Non-error types with no status

    def test_error_message_from_json(self) -> None:
        """Error message deserializes with error field."""
        raw = {
            "type": "error",
            "task_id": "t-1",
            "error": "OOM during analysis",
        }
        msg = AgentToPlatform.model_validate(raw)

        assert msg.type == AgentToPlatformType.ERROR
        assert msg.error == "OOM during analysis"
        assert msg.is_success is False

    def test_progress_pct_range_validation(self) -> None:
        """progress_pct rejects values outside [0, 100]."""
        with pytest.raises(PydanticValidationError):
            AgentToPlatform(
                type=AgentToPlatformType.PROGRESS,
                content="test",
                progress_pct=-1.0,
            )
        with pytest.raises(PydanticValidationError):
            AgentToPlatform(
                type=AgentToPlatformType.PROGRESS,
                content="test",
                progress_pct=101.0,
            )

    def test_progress_pct_boundary_values(self) -> None:
        """progress_pct accepts exact boundary values 0.0 and 100.0."""
        msg_0 = AgentToPlatform(
            type=AgentToPlatformType.PROGRESS,
            content="start",
            progress_pct=0.0,
        )
        assert msg_0.progress_pct == 0.0

        msg_100 = AgentToPlatform(
            type=AgentToPlatformType.PROGRESS,
            content="done",
            progress_pct=100.0,
        )
        assert msg_100.progress_pct == 100.0

    def test_output_field_complex_type(self) -> None:
        """output field accepts complex Python objects (dict, list)."""
        raw = {
            "type": "result",
            "task_id": "t-1",
            "output": {"scores": [0.9, 0.8], "label": "positive"},
        }
        msg = AgentToPlatform.model_validate(raw)
        assert msg.output["scores"] == [0.9, 0.8]

    def test_output_field_size_limit(self) -> None:
        """output field rejects objects whose JSON exceeds 65536 bytes."""
        large_output = {"data": "x" * 65537}
        with pytest.raises(ValueError):
            AgentToPlatform(
                type=AgentToPlatformType.RESULT,
                output=large_output,
            )


# ---------------------------------------------------------------------------
# is_success() contract — must match Rust ap-core/src/models/ipc.rs
# ---------------------------------------------------------------------------


class TestIsSuccessContract:
    """Verify is_success() logic matches the Rust implementation.

    Rust contract (from crates/ap-core/src/models/ipc.rs):
        fn is_success(&self) -> bool {
            if self.msg_type == "error" { return false; }
            self.status.is_none() || self.status.to_lowercase() == "completed"
        }
    """

    def test_error_type_always_fails(self) -> None:
        """ERROR type messages always return is_success=False."""
        msg = AgentToPlatform(
            type=AgentToPlatformType.ERROR,
            error="something broke",
            status="completed",
        )
        assert msg.is_success is False

    def test_result_no_status_succeeds(self) -> None:
        """RESULT with no status field returns is_success=True."""
        msg = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="done",
        )
        assert msg.is_success is True

    def test_result_completed_status_succeeds(self) -> None:
        """RESULT with status='completed' returns is_success=True."""
        msg = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="done",
            status="completed",
        )
        assert msg.is_success is True

    def test_result_failed_status_fails(self) -> None:
        """RESULT with status='failed' returns is_success=False."""
        msg = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="error output",
            status="failed",
        )
        assert msg.is_success is False

    def test_result_pending_status_fails(self) -> None:
        """RESULT with status='pending' returns is_success=False."""
        msg = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            status="pending",
        )
        assert msg.is_success is False

    def test_progress_always_succeeds_by_default(self) -> None:
        """PROGRESS messages with no status return is_success=True."""
        msg = AgentToPlatform(
            type=AgentToPlatformType.PROGRESS,
            content="working",
        )
        assert msg.is_success is True


# ---------------------------------------------------------------------------
# IPCMessage envelope contract
# ---------------------------------------------------------------------------


class TestIPCMessageEnvelope:
    """Verify IPCMessage envelope correctly discriminates message direction."""

    def test_platform_to_agent_direction(self) -> None:
        """PLATFORM_TO_AGENT direction resolves to PlatformToAgent payload."""
        envelope = IPCMessage(
            direction=MessageDirection.PLATFORM_TO_AGENT,
            payload={"type": "chat", "content": "hello"},
        )
        assert isinstance(envelope.payload, PlatformToAgent)
        assert envelope.payload.type == PlatformToAgentType.CHAT

    def test_agent_to_platform_direction(self) -> None:
        """AGENT_TO_PLATFORM direction resolves to AgentToPlatform payload."""
        envelope = IPCMessage(
            direction=MessageDirection.AGENT_TO_PLATFORM,
            payload={"type": "result", "content": "done", "status": "completed"},
        )
        assert isinstance(envelope.payload, AgentToPlatform)
        assert envelope.payload.type == AgentToPlatformType.RESULT

    def test_wrong_direction_rejects_invalid_type(self) -> None:
        """Agent result data under PLATFORM_TO_AGENT direction is rejected
        because 'result' is not a valid PlatformToAgent type."""
        with pytest.raises(Exception, match="chat|task|data_reference"):
            IPCMessage(
                direction=MessageDirection.PLATFORM_TO_AGENT,
                payload={"type": "result", "content": "agent response"},
            )

    def test_agent_result_under_correct_direction(self) -> None:
        """Agent result data under AGENT_TO_PLATFORM direction parses correctly."""
        envelope = IPCMessage(
            direction=MessageDirection.AGENT_TO_PLATFORM,
            payload={"type": "result", "content": "agent response", "status": "completed"},
        )
        assert isinstance(envelope.payload, AgentToPlatform)
        assert envelope.payload.type == AgentToPlatformType.RESULT


# ---------------------------------------------------------------------------
# Data reference protocol contract
# ---------------------------------------------------------------------------


class TestDataReferenceProtocol:
    """Verify DATA_REFERENCE messages carry ref_id and summary for cross-agent data."""

    def test_data_reference_has_required_fields(self) -> None:
        """DATA_REFERENCE must include ref_id and summary fields."""
        msg = PlatformToAgent(
            type=PlatformToAgentType.DATA_REFERENCE,
            content="upstream output",
            ref_id="var://code-reviewer/findings",
            summary="[code-reviewer] Found 3 issues ~1.2KB",
        )
        raw = msg.model_dump_json(exclude_none=True)
        data = json.loads(raw)

        assert data["type"] == "data_reference"
        assert data["ref_id"] == "var://code-reviewer/findings"
        assert "code-reviewer" in data["summary"]
