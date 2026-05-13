"""Unit tests for A2A messaging: data models + MessageBroker.

Covers:
- AgentAddress and A2AMessage model creation/validation
- MessageBroker.send_message (fire-and-forget)
- MessageBroker.send_request (reply-wait with timeout)
- MessageBroker.send_request timeout raises TimeoutError
- MessageBroker.broadcast (delivers to all targets)
- MessageBroker nesting prohibition
- MessageBroker.deliver_reply resolves Future
- MessageBroker.route dispatches by msg_type
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_nexus.models.ipc import (
    A2AMessage,
    AgentAddress,
    PlatformToAgentType,
)
from agent_nexus.platform.orchestration.agent_directory import AgentDirectory
from agent_nexus.platform.orchestration.message_broker import MessageBroker
from agent_nexus.platform.orchestration.process_manager import AgentHandle

# ============================================================================
# Fixtures
# ============================================================================


def _make_mock_handle(name: str, alive: bool = True) -> MagicMock:
    """Create a mock AgentHandle with IPC stream."""
    handle = MagicMock(spec=AgentHandle)
    handle.name = name
    handle.process = MagicMock()
    handle.process.returncode = None if alive else 1
    handle.ipc = MagicMock()
    handle.ipc.stream = MagicMock()
    handle.ipc.stream.send = AsyncMock()
    handle.ipc.send_chat = AsyncMock()
    handle.is_alive = alive
    return handle


def _make_mock_process_manager(
    agents: dict[str, MagicMock] | None = None,
) -> MagicMock:
    """Create a mock ProcessManager with registered agents."""
    pm = MagicMock()
    agents = agents or {}

    def get_agent(name: str):
        return agents.get(name)

    pm.get_agent = MagicMock(side_effect=get_agent)
    pm.list_running = MagicMock(return_value=list(agents.keys()))
    return pm


# ============================================================================
# Data Models
# ============================================================================


class TestAgentAddress:
    def test_basic_creation(self) -> None:
        addr = AgentAddress(agent_id="agent-a")
        assert addr.agent_id == "agent-a"
        assert addr.role is None

    def test_with_role(self) -> None:
        addr = AgentAddress(agent_id="agent-b", role="coordinator")
        assert addr.role == "coordinator"


class TestA2AMessage:
    def test_basic_creation(self) -> None:
        msg = A2AMessage(
            message_id="uuid-1",
            from_agent="agent-a",
            to_agent="agent-b",
            msg_type="chat",
            content="hello",
            timestamp=time.time(),
        )
        assert msg.message_id == "uuid-1"
        assert msg.from_agent == "agent-a"
        assert msg.to_agent == "agent-b"
        assert msg.msg_type == "chat"
        assert msg.content == "hello"
        assert msg.in_reply_to is None
        assert msg.metadata == {}

    def test_broadcast_to_agent_is_none(self) -> None:
        msg = A2AMessage(
            message_id="uuid-2",
            from_agent="agent-a",
            to_agent=None,
            msg_type="broadcast",
            content="announcement",
            timestamp=time.time(),
        )
        assert msg.to_agent is None

    def test_reply_with_in_reply_to(self) -> None:
        msg = A2AMessage(
            message_id="uuid-3",
            from_agent="agent-b",
            to_agent="agent-a",
            msg_type="reply",
            content="response",
            in_reply_to="uuid-1",
            timestamp=time.time(),
        )
        assert msg.in_reply_to == "uuid-1"

    def test_invalid_msg_type_raises(self) -> None:
        with pytest.raises(Exception):
            A2AMessage(
                message_id="uuid-4",
                from_agent="agent-a",
                to_agent="agent-b",
                msg_type="invalid_type",
                content="test",
                timestamp=time.time(),
            )

    def test_metadata_defaults_to_empty_dict(self) -> None:
        msg = A2AMessage(
            message_id="uuid-5",
            from_agent="agent-a",
            to_agent="agent-b",
            msg_type="chat",
            content="test",
            timestamp=1234.5,
        )
        assert msg.metadata == {}

    def test_with_metadata(self) -> None:
        msg = A2AMessage(
            message_id="uuid-6",
            from_agent="agent-a",
            to_agent="agent-b",
            msg_type="request",
            content="do thing",
            metadata={"priority": "high", "correlation": "corr-1"},
            timestamp=time.time(),
        )
        assert msg.metadata["priority"] == "high"

    def test_serialization_roundtrip(self) -> None:
        msg = A2AMessage(
            message_id="uuid-7",
            from_agent="agent-a",
            to_agent="agent-b",
            msg_type="chat",
            content="hello world",
            metadata={"key": "value"},
            timestamp=1234.5678,
        )
        serialized = msg.model_dump_json()
        restored = A2AMessage.model_validate_json(serialized)
        assert restored.message_id == msg.message_id
        assert restored.from_agent == msg.from_agent
        assert restored.to_agent == msg.to_agent
        assert restored.msg_type == msg.msg_type
        assert restored.content == msg.content
        assert restored.metadata == msg.metadata
        assert restored.timestamp == msg.timestamp


# ============================================================================
# Enum Extensions
# ============================================================================


# ============================================================================
# MessageBroker — send_message
# ============================================================================


class TestMessageBrokerSendMessage:
    async def test_send_message_delivers(self) -> None:
        handle_b = _make_mock_handle("agent-b")
        pm = _make_mock_process_manager({"agent-b": handle_b})
        broker = MessageBroker(pm)

        await broker.send_message("agent-a", "agent-b", "hello")

        # Should have called stream.send with PlatformToAgent
        handle_b.ipc.stream.send.assert_awaited_once()
        sent_msg = handle_b.ipc.stream.send.call_args[0][0]
        assert sent_msg.type == PlatformToAgentType.RECEIVE_MESSAGE

        # Verify the A2A payload inside
        a2a_data = json.loads(sent_msg.content)
        assert a2a_data["from_agent"] == "agent-a"
        assert a2a_data["to_agent"] == "agent-b"
        assert a2a_data["content"] == "hello"
        assert a2a_data["msg_type"] == "chat"

    async def test_send_message_target_not_found(self) -> None:
        pm = _make_mock_process_manager({})
        broker = MessageBroker(pm)

        with pytest.raises(KeyError, match="not found"):
            await broker.send_message("agent-a", "agent-b", "hello")

    async def test_send_message_target_dead(self) -> None:
        handle_b = _make_mock_handle("agent-b", alive=False)
        pm = _make_mock_process_manager({"agent-b": handle_b})
        broker = MessageBroker(pm)

        with pytest.raises(KeyError, match="not alive"):
            await broker.send_message("agent-a", "agent-b", "hello")


# ============================================================================
# MessageBroker — send_request / deliver_reply
# ============================================================================


class TestMessageBrokerSendRequest:
    async def test_send_request_gets_reply(self) -> None:
        handle_b = _make_mock_handle("agent-b")
        pm = _make_mock_process_manager({"agent-b": handle_b})
        broker = MessageBroker(pm)

        # Intercept the message_id from the delivered request
        handle_b.ipc.stream.send = AsyncMock()

        # Start request in background, then deliver reply
        async def request_and_reply():
            # Start the request — it will await the future
            request_task = asyncio.create_task(
                broker.send_request("agent-a", "agent-b", "what is 2+2?", timeout=5.0)
            )
            # Give the request a chance to deliver and create the future
            await asyncio.sleep(0.05)

            # Extract the message_id from the sent IPC message
            sent_msg = handle_b.ipc.stream.send.call_args[0][0]
            a2a_data = json.loads(sent_msg.content)
            request_id = a2a_data["message_id"]

            # Deliver the reply
            await broker.deliver_reply(request_id, "4")

            result = await request_task
            return result

        result = await request_and_reply()
        assert result == "4"

    async def test_send_request_timeout(self) -> None:
        handle_b = _make_mock_handle("agent-b")
        pm = _make_mock_process_manager({"agent-b": handle_b})
        broker = MessageBroker(pm)

        with pytest.raises(TimeoutError, match="timed out"):
            await broker.send_request("agent-a", "agent-b", "hello", timeout=0.1)

    async def test_deliver_reply_unknown_request(self) -> None:
        pm = _make_mock_process_manager({})
        broker = MessageBroker(pm)

        with pytest.raises(KeyError, match="No pending request"):
            await broker.deliver_reply("unknown-msg-id", "reply content")


# ============================================================================
# MessageBroker — broadcast
# ============================================================================


class TestMessageBrokerBroadcast:
    async def test_broadcast_delivers_to_all(self) -> None:
        handle_b = _make_mock_handle("agent-b")
        handle_c = _make_mock_handle("agent-c")
        pm = _make_mock_process_manager(
            {
                "agent-a": _make_mock_handle("agent-a"),
                "agent-b": handle_b,
                "agent-c": handle_c,
            }
        )
        broker = MessageBroker(pm)

        delivered = await broker.broadcast("agent-a", "announcement")

        assert "agent-b" in delivered
        assert "agent-c" in delivered
        assert "agent-a" not in delivered  # sender excluded

        # Both targets should have received
        assert handle_b.ipc.stream.send.await_count == 1
        assert handle_c.ipc.stream.send.await_count == 1

    async def test_broadcast_excludes_sender(self) -> None:
        pm = _make_mock_process_manager(
            {
                "agent-a": _make_mock_handle("agent-a"),
            }
        )
        broker = MessageBroker(pm)

        delivered = await broker.broadcast("agent-a", "msg")
        assert delivered == []

    async def test_broadcast_continues_on_failure(self) -> None:
        handle_b = _make_mock_handle("agent-b")
        handle_c = _make_mock_handle("agent-c")
        # Make agent-b's send fail
        handle_b.ipc.stream.send = AsyncMock(side_effect=OSError("pipe broken"))

        pm = _make_mock_process_manager(
            {
                "agent-a": _make_mock_handle("agent-a"),
                "agent-b": handle_b,
                "agent-c": handle_c,
            }
        )
        broker = MessageBroker(pm)

        delivered = await broker.broadcast("agent-a", "msg")

        # agent-c should still get it even though agent-b failed
        assert "agent-c" in delivered
        assert "agent-b" not in delivered
        assert handle_c.ipc.stream.send.await_count == 1


# ============================================================================
# MessageBroker — nesting prohibition
# ============================================================================


class TestMessageBrokerNestingProhibition:
    async def test_nesting_prohibition(self) -> None:
        """If A→B request is pending, B→A request must raise RuntimeError."""
        handle_a = _make_mock_handle("agent-a")
        handle_b = _make_mock_handle("agent-b")
        pm = _make_mock_process_manager({"agent-a": handle_a, "agent-b": handle_b})
        broker = MessageBroker(pm)

        # Start A→B request in background (will not complete)
        request_task = asyncio.create_task(
            broker.send_request("agent-a", "agent-b", "request from A", timeout=5.0)
        )
        await asyncio.sleep(0.05)

        # B→A should be prohibited while A→B is pending
        with pytest.raises(RuntimeError, match="Nesting prohibition"):
            await broker.send_request("agent-b", "agent-a", "request from B")

        request_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await request_task

    async def test_reverse_allowed_after_timeout(self) -> None:
        """After A→B times out, B→A should be allowed again."""
        handle_a = _make_mock_handle("agent-a")
        handle_b = _make_mock_handle("agent-b")
        pm = _make_mock_process_manager({"agent-a": handle_a, "agent-b": handle_b})
        broker = MessageBroker(pm)

        # A→B request times out
        with pytest.raises(TimeoutError):
            await broker.send_request("agent-a", "agent-b", "hello", timeout=0.1)

        # B→A should now succeed (starts but will also timeout)
        # Just verify it doesn't raise RuntimeError
        request_task = asyncio.create_task(
            broker.send_request("agent-b", "agent-a", "reverse", timeout=0.1)
        )
        with pytest.raises(TimeoutError):
            await request_task


# ============================================================================
# MessageBroker — deliver_reply
# ============================================================================


# ============================================================================
# MessageBroker — route
# ============================================================================


class TestMessageBrokerRoute:
    async def test_route_chat(self) -> None:
        handle_b = _make_mock_handle("agent-b")
        pm = _make_mock_process_manager({"agent-b": handle_b})
        broker = MessageBroker(pm)

        msg = A2AMessage(
            message_id="uuid-r1",
            from_agent="agent-a",
            to_agent="agent-b",
            msg_type="chat",
            content="routed chat",
            timestamp=time.time(),
        )
        await broker.route("agent-a", msg)

        handle_b.ipc.stream.send.assert_awaited_once()

    async def test_route_request(self) -> None:
        handle_b = _make_mock_handle("agent-b")
        pm = _make_mock_process_manager({"agent-b": handle_b})
        broker = MessageBroker(pm)

        msg = A2AMessage(
            message_id="uuid-r2",
            from_agent="agent-a",
            to_agent="agent-b",
            msg_type="request",
            content="routed request",
            timestamp=time.time(),
        )

        # It will start waiting for reply — run with timeout
        request_task = asyncio.create_task(broker.route("agent-a", msg))
        await asyncio.sleep(0.05)
        request_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await request_task

        handle_b.ipc.stream.send.assert_awaited_once()

    async def test_route_broadcast(self) -> None:
        handle_b = _make_mock_handle("agent-b")
        pm = _make_mock_process_manager(
            {
                "agent-a": _make_mock_handle("agent-a"),
                "agent-b": handle_b,
            }
        )
        broker = MessageBroker(pm)

        msg = A2AMessage(
            message_id="uuid-r3",
            from_agent="agent-a",
            to_agent=None,
            msg_type="broadcast",
            content="routed broadcast",
            timestamp=time.time(),
        )
        await broker.route("agent-a", msg)

        handle_b.ipc.stream.send.assert_awaited_once()

    async def test_route_reply(self) -> None:
        handle_a = _make_mock_handle("agent-a")
        pm = _make_mock_process_manager({"agent-a": handle_a})
        broker = MessageBroker(pm)

        # Set up a pending request
        handle_a.ipc.stream.send = AsyncMock()
        request_task = asyncio.create_task(
            broker.send_request("platform", "agent-a", "ping", timeout=5.0)
        )
        await asyncio.sleep(0.05)

        # Get the pending request's message_id
        pending_id = next(iter(broker._pending_replies.keys()))

        msg = A2AMessage(
            message_id="uuid-r4",
            from_agent="agent-a",
            to_agent="platform",
            msg_type="reply",
            content="pong",
            in_reply_to=pending_id,
            timestamp=time.time(),
        )
        await broker.route("agent-a", msg)

        result = await asyncio.wait_for(request_task, timeout=1.0)
        assert result == "pong"

    async def test_route_reply_without_in_reply_to_drops(self) -> None:
        """Reply with no in_reply_to is logged and dropped."""
        pm = _make_mock_process_manager({})
        broker = MessageBroker(pm)

        msg = A2AMessage(
            message_id="uuid-r5",
            from_agent="agent-a",
            to_agent="agent-b",
            msg_type="reply",
            content="orphan reply",
            in_reply_to=None,
            timestamp=time.time(),
        )
        # Should not raise
        await broker.route("agent-a", msg)


# ============================================================================
# MessageBroker — build helpers
# ============================================================================


class TestMessageBrokerBuildHelpers:
    def test_build_a2a_creates_uuid(self) -> None:
        msg = MessageBroker._build_a2a(from_agent="a", to_agent="b", msg_type="chat", content="hi")
        assert msg.message_id  # non-empty UUID string
        assert msg.from_agent == "a"
        assert msg.to_agent == "b"
        assert msg.msg_type == "chat"

    def test_a2a_type_to_ipc_mapping(self) -> None:
        assert MessageBroker._a2a_type_to_ipc("chat") == PlatformToAgentType.RECEIVE_MESSAGE
        assert MessageBroker._a2a_type_to_ipc("request") == PlatformToAgentType.RECEIVE_REQUEST
        assert MessageBroker._a2a_type_to_ipc("broadcast") == PlatformToAgentType.RECEIVE_BROADCAST
        assert MessageBroker._a2a_type_to_ipc("reply") == PlatformToAgentType.RECEIVE_REPLY

    def test_request_key_format(self) -> None:
        assert MessageBroker._request_key("a", "b") == "a->b"


# ============================================================================
# MessageBroker — composition boundary (D4)
# ============================================================================


class TestCompositionBoundary:
    def test_same_composition_allowed(self) -> None:
        """Agents in the same composition group can message each other."""
        broker = MessageBroker(process_manager=MagicMock())
        directory = AgentDirectory()
        directory.register("a", [], "worker", composition="pipeline-1")
        directory.register("b", [], "worker", composition="pipeline-1")
        broker._directory = directory
        # Should not raise
        broker._check_composition_boundary("a", "b")

    def test_different_composition_blocked(self) -> None:
        """Agents in different composition groups cannot message each other."""
        broker = MessageBroker(process_manager=MagicMock())
        directory = AgentDirectory()
        directory.register("a", [], "worker", composition="pipeline-1")
        directory.register("b", [], "worker", composition="pipeline-2")
        broker._directory = directory
        with pytest.raises(PermissionError, match="Composition boundary"):
            broker._check_composition_boundary("a", "b")

    def test_no_directory_allows_all(self) -> None:
        """Without a directory, all messages are allowed."""
        broker = MessageBroker(process_manager=MagicMock())
        # No directory set — should not raise
        broker._check_composition_boundary("a", "b")

    def test_no_composition_group_allows_all(self) -> None:
        """Agents without composition group can message freely."""
        broker = MessageBroker(process_manager=MagicMock())
        directory = AgentDirectory()
        directory.register("a", [], "worker")  # no composition
        directory.register("b", [], "worker")  # no composition
        broker._directory = directory
        broker._check_composition_boundary("a", "b")  # no error

    def test_unknown_agent_allows(self) -> None:
        """Unknown agents in directory are allowed (may not be registered yet)."""
        broker = MessageBroker(process_manager=MagicMock())
        directory = AgentDirectory()
        directory.register("a", [], "worker", composition="pipeline-1")
        broker._directory = directory
        broker._check_composition_boundary("a", "unknown")  # no error
