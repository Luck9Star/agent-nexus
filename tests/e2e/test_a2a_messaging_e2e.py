"""E2E: A2A messaging — MessageBroker full message flow.

TRUE E2E tests verifying the complete A2A message relay:
  register agents -> send/receive messages -> request/reply -> broadcast

All internal objects are real (MessageBroker, AgentAddress, A2AMessage).
Only the ProcessManager IPC layer is faked because we cannot run real agent
subprocesses in CI.  The fake captures PlatformToAgent envelopes so tests
can verify delivery correctness.

Test sections:
  1. send_message: fire-and-forget delivery
  2. send_request / deliver_reply: request-reply with asyncio.Future
  3. broadcast: one-to-many delivery
  4. request timeout: TimeoutError on missing reply
  5. nesting prohibition: deadlock prevention
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_nexus.models.ipc import (
    A2AMessage,
    PlatformToAgent,
    PlatformToAgentType,
)
from agent_nexus.platform.orchestration.agent_directory import AgentDirectory
from agent_nexus.platform.orchestration.message_broker import MessageBroker
from agent_nexus.platform.orchestration.process_manager import (
    AgentHandle,
    ProcessManager,
)

# ---------------------------------------------------------------------------
# Fake ProcessManager — captures PlatformToAgent envelopes in per-agent queues
# ---------------------------------------------------------------------------


class FakeIPCStream:
    """Minimal IPCStream that records sent messages."""

    def __init__(self) -> None:
        self.sent: list[PlatformToAgent] = []
        self._send = AsyncMock()

    async def send(self, msg: PlatformToAgent) -> None:
        self.sent.append(msg)

    def close_sync(self) -> None:
        pass

    async def close(self) -> None:
        pass


class FakeProcessManager(ProcessManager):
    """ProcessManager subclass that fakes agent handles with real IPCStream.

    Instead of launching subprocesses, stores FakeIPCStream instances so
    tests can inspect what MessageBroker delivered.
    """

    def __init__(self) -> None:
        super().__init__()
        self.streams: dict[str, FakeIPCStream] = {}

    def add_fake_agent(self, agent_id: str) -> None:
        """Register a fake agent with a capturing IPCStream."""
        stream = FakeIPCStream()
        self.streams[agent_id] = stream

        # Build a mock handle whose ipc.stream.send routes to our fake stream
        handle = MagicMock(spec=AgentHandle)
        handle.is_alive = True
        handle.ipc = MagicMock()
        handle.ipc.stream = stream
        handle.process = MagicMock()
        handle.process.returncode = None

        # Inject into the parent's internal dict (bypasses start_agent)
        self._agents[agent_id] = handle  # type: ignore[assignment]

    def get_sent_messages(self, agent_id: str) -> list[PlatformToAgent]:
        """Return all PlatformToAgent messages delivered to agent_id."""
        stream = self.streams.get(agent_id)
        if stream is None:
            return []
        return list(stream.sent)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_pm() -> Generator[FakeProcessManager, None, None]:
    pm = FakeProcessManager()
    yield pm


@pytest.fixture()
def broker(fake_pm: FakeProcessManager) -> Generator[MessageBroker, None, None]:
    yield MessageBroker(fake_pm)


@pytest.fixture()
def broker_with_directory(
    fake_pm: FakeProcessManager,
) -> Generator[tuple[MessageBroker, AgentDirectory, FakeProcessManager], None, None]:
    directory = AgentDirectory()
    mb = MessageBroker(fake_pm, agent_directory=directory)
    yield mb, directory, fake_pm


# ===========================================================================
# 1. send_message: fire-and-forget delivery
# ===========================================================================


class TestSendMessage:
    """send_message delivers a PlatformToAgent to the target's IPC stream."""

    async def test_send_and_deliver(
        self, broker: MessageBroker, fake_pm: FakeProcessManager
    ) -> None:
        """Send a message from agent A to agent B and verify delivery."""
        fake_pm.add_fake_agent("agent-a")
        fake_pm.add_fake_agent("agent-b")

        await broker.send_message("agent-a", "agent-b", "Hello B")

        sent = fake_pm.get_sent_messages("agent-b")
        assert len(sent) == 1

        msg = sent[0]
        assert msg.type == PlatformToAgentType.RECEIVE_MESSAGE
        # The content is the serialized A2AMessage JSON
        a2a = A2AMessage.model_validate_json(msg.content)
        assert a2a.from_agent == "agent-a"
        assert a2a.to_agent == "agent-b"
        assert a2a.msg_type == "chat"
        assert a2a.content == "Hello B"

    async def test_send_to_nonexistent_agent_raises(
        self, broker: MessageBroker, fake_pm: FakeProcessManager
    ) -> None:
        """Sending to an unregistered agent raises KeyError."""
        fake_pm.add_fake_agent("agent-a")

        with pytest.raises(KeyError, match="not found"):
            await broker.send_message("agent-a", "agent-ghost", "Hello?")

    async def test_composition_boundary_blocks_cross_group(
        self,
        broker_with_directory: tuple[MessageBroker, AgentDirectory, FakeProcessManager],
    ) -> None:
        """Agents in different composition groups cannot message each other."""
        broker, directory, fake_pm = broker_with_directory

        fake_pm.add_fake_agent("alpha-1")
        fake_pm.add_fake_agent("beta-1")
        directory.register("alpha-1", ["chat"], "worker", composition="alpha")
        directory.register("beta-1", ["chat"], "worker", composition="beta")

        with pytest.raises(PermissionError, match="Composition boundary"):
            await broker.send_message("alpha-1", "beta-1", "Cross-group message")

    async def test_composition_boundary_allows_same_group(
        self,
        broker_with_directory: tuple[MessageBroker, AgentDirectory, FakeProcessManager],
    ) -> None:
        """Agents in the same composition group can message each other."""
        broker, directory, fake_pm = broker_with_directory

        fake_pm.add_fake_agent("alpha-1")
        fake_pm.add_fake_agent("alpha-2")
        directory.register("alpha-1", ["chat"], "worker", composition="alpha")
        directory.register("alpha-2", ["chat"], "worker", composition="alpha")

        await broker.send_message("alpha-1", "alpha-2", "Same group")

        sent = fake_pm.get_sent_messages("alpha-2")
        assert len(sent) == 1


# ===========================================================================
# 2. send_request / deliver_reply: request-reply with asyncio.Future
# ===========================================================================


class TestRequestReply:
    """send_request waits for deliver_reply to resolve the Future."""

    async def test_request_reply_cycle(
        self, broker: MessageBroker, fake_pm: FakeProcessManager
    ) -> None:
        """A sends request to B, B replies, A gets the reply content."""
        fake_pm.add_fake_agent("agent-a")
        fake_pm.add_fake_agent("agent-b")

        # We need to intercept the request message_id to simulate a reply
        # We run send_request in a task so we can concurrently deliver_reply
        request_task = asyncio.create_task(
            broker.send_request("agent-a", "agent-b", "What is 2+2?", timeout=5.0)
        )

        # Give the task a moment to send the request and create the Future
        await asyncio.sleep(0.05)

        # The request should have been delivered to agent-b's stream
        sent = fake_pm.get_sent_messages("agent-b")
        assert len(sent) == 1

        # Extract the A2AMessage to get the message_id for the reply
        a2a = A2AMessage.model_validate_json(sent[0].content)
        assert a2a.msg_type == "request"
        request_msg_id = a2a.message_id

        # Simulate agent-b replying through the broker
        await broker.deliver_reply(request_msg_id, "4")

        # The original request should now resolve
        reply = await request_task
        assert reply == "4"

    async def test_request_uses_correct_ipc_type(
        self, broker: MessageBroker, fake_pm: FakeProcessManager
    ) -> None:
        """send_request delivers RECEIVE_REQUEST type, not RECEIVE_MESSAGE."""
        fake_pm.add_fake_agent("agent-a")
        fake_pm.add_fake_agent("agent-b")

        request_task = asyncio.create_task(
            broker.send_request("agent-a", "agent-b", "request", timeout=1.0)
        )
        await asyncio.sleep(0.05)

        sent = fake_pm.get_sent_messages("agent-b")
        assert len(sent) == 1
        assert sent[0].type == PlatformToAgentType.RECEIVE_REQUEST

        # Clean up: deliver a reply so the task completes
        a2a = A2AMessage.model_validate_json(sent[0].content)
        await broker.deliver_reply(a2a.message_id, "ok")
        await request_task


# ===========================================================================
# 3. broadcast: one-to-many delivery
# ===========================================================================


class TestBroadcast:
    """broadcast delivers to all agents except the sender."""

    async def test_broadcast_three_agents(
        self, broker: MessageBroker, fake_pm: FakeProcessManager
    ) -> None:
        """Broadcast from A reaches B and C, but not A itself."""
        fake_pm.add_fake_agent("agent-a")
        fake_pm.add_fake_agent("agent-b")
        fake_pm.add_fake_agent("agent-c")

        delivered = await broker.broadcast("agent-a", "Announcement!")

        assert sorted(delivered) == ["agent-b", "agent-c"]

        # Verify actual messages
        assert len(fake_pm.get_sent_messages("agent-b")) == 1
        assert len(fake_pm.get_sent_messages("agent-c")) == 1
        assert len(fake_pm.get_sent_messages("agent-a")) == 0  # sender excluded

        # Check message content
        msg_b = fake_pm.get_sent_messages("agent-b")[0]
        assert msg_b.type == PlatformToAgentType.RECEIVE_BROADCAST
        a2a_b = A2AMessage.model_validate_json(msg_b.content)
        assert a2a_b.content == "Announcement!"
        assert a2a_b.from_agent == "agent-a"

    async def test_broadcast_with_role_filter(
        self,
        broker_with_directory: tuple[MessageBroker, AgentDirectory, FakeProcessManager],
    ) -> None:
        """Broadcast with group=role only targets agents matching that role."""
        broker, directory, fake_pm = broker_with_directory

        fake_pm.add_fake_agent("coordinator-1")
        fake_pm.add_fake_agent("worker-1")
        fake_pm.add_fake_agent("worker-2")

        directory.register("coordinator-1", ["chat"], "coordinator")
        directory.register("worker-1", ["chat"], "worker")
        directory.register("worker-2", ["chat"], "worker")

        delivered = await broker.broadcast("coordinator-1", "Work to do!", group="worker")

        assert sorted(delivered) == ["worker-1", "worker-2"]
        assert len(fake_pm.get_sent_messages("coordinator-1")) == 0

    async def test_broadcast_single_agent_no_peers(
        self, broker: MessageBroker, fake_pm: FakeProcessManager
    ) -> None:
        """Broadcast with no other agents returns empty list."""
        fake_pm.add_fake_agent("lonely-agent")

        delivered = await broker.broadcast("lonely-agent", "Hello?")
        assert delivered == []


# ===========================================================================
# 4. request timeout: TimeoutError on missing reply
# ===========================================================================


class TestRequestTimeout:
    """send_request raises TimeoutError when no reply arrives in time."""

    async def test_request_timeout_raises(
        self, broker: MessageBroker, fake_pm: FakeProcessManager
    ) -> None:
        """Request with very short timeout raises TimeoutError."""
        fake_pm.add_fake_agent("agent-a")
        fake_pm.add_fake_agent("agent-b")

        with pytest.raises(TimeoutError, match="timed out"):
            await broker.send_request("agent-a", "agent-b", "Anybody there?", timeout=0.1)

    async def test_request_timeout_cleans_up_pending_future(
        self, broker: MessageBroker, fake_pm: FakeProcessManager
    ) -> None:
        """After timeout, the pending future is removed from internal state."""
        fake_pm.add_fake_agent("agent-a")
        fake_pm.add_fake_agent("agent-b")

        with pytest.raises(TimeoutError):
            await broker.send_request("agent-a", "agent-b", "test", timeout=0.1)

        # Internal state should be clean
        assert len(broker._pending_replies) == 0
        assert len(broker._active_requests) == 0


# ===========================================================================
# 5. nesting prohibition: deadlock prevention
# ===========================================================================


class TestNestingProhibition:
    """Reverse requests are blocked while a forward request is pending."""

    async def test_nesting_prohibited(
        self, broker: MessageBroker, fake_pm: FakeProcessManager
    ) -> None:
        """While A has a pending request to B, B cannot request A."""
        fake_pm.add_fake_agent("agent-a")
        fake_pm.add_fake_agent("agent-b")

        # Start a request from A to B (will not be replied to)
        request_task = asyncio.create_task(
            broker.send_request("agent-a", "agent-b", "Please respond", timeout=5.0)
        )
        await asyncio.sleep(0.05)

        # Verify forward request is in progress
        assert "agent-a->agent-b" in broker._active_requests

        # Attempting reverse request should raise RuntimeError
        with pytest.raises(RuntimeError, match="Nesting prohibition"):
            await broker.send_request("agent-b", "agent-a", "Reverse request!", timeout=1.0)

        # Clean up: reply to the original request
        sent = fake_pm.get_sent_messages("agent-b")
        assert len(sent) == 1
        a2a = A2AMessage.model_validate_json(sent[0].content)
        await broker.deliver_reply(a2a.message_id, "reply")
        await request_task


# ===========================================================================
# 6. route dispatch: unified entry point
# ===========================================================================


class TestRouteDispatch:
    """route() dispatches based on msg_type."""

    async def test_route_chat_message(
        self, broker: MessageBroker, fake_pm: FakeProcessManager
    ) -> None:
        """route dispatches a chat message via send_message."""
        fake_pm.add_fake_agent("agent-a")
        fake_pm.add_fake_agent("agent-b")

        msg = A2AMessage(
            message_id="msg-1",
            from_agent="agent-a",
            to_agent="agent-b",
            msg_type="chat",
            content="via route",
            timestamp=1000.0,
        )
        await broker.route("agent-a", msg)

        sent = fake_pm.get_sent_messages("agent-b")
        assert len(sent) == 1

    async def test_route_reply_message(
        self, broker: MessageBroker, fake_pm: FakeProcessManager
    ) -> None:
        """route dispatches a reply message via deliver_reply."""
        fake_pm.add_fake_agent("agent-a")
        fake_pm.add_fake_agent("agent-b")

        # Start a pending request first
        request_task = asyncio.create_task(
            broker.send_request("agent-a", "agent-b", "orig request", timeout=5.0)
        )
        await asyncio.sleep(0.05)

        # Get the request message_id
        sent = fake_pm.get_sent_messages("agent-b")
        a2a = A2AMessage.model_validate_json(sent[0].content)

        # Route a reply
        reply_msg = A2AMessage(
            message_id="msg-reply",
            from_agent="agent-b",
            to_agent="agent-a",
            msg_type="reply",
            in_reply_to=a2a.message_id,
            content="reply via route",
            timestamp=1001.0,
        )
        await broker.route("agent-b", reply_msg)

        result = await request_task
        assert result == "reply via route"

    async def test_route_broadcast_message(
        self, broker: MessageBroker, fake_pm: FakeProcessManager
    ) -> None:
        """route dispatches a broadcast message."""
        fake_pm.add_fake_agent("agent-a")
        fake_pm.add_fake_agent("agent-b")
        fake_pm.add_fake_agent("agent-c")

        msg = A2AMessage(
            message_id="msg-bcast",
            from_agent="agent-a",
            to_agent=None,
            msg_type="broadcast",
            content="broadcast via route",
            timestamp=1000.0,
        )
        await broker.route("agent-a", msg)

        assert len(fake_pm.get_sent_messages("agent-b")) == 1
        assert len(fake_pm.get_sent_messages("agent-c")) == 1

    async def test_route_chat_without_to_agent_raises(
        self, broker: MessageBroker
    ) -> None:
        """route raises ValueError for chat message without to_agent."""
        msg = A2AMessage(
            message_id="msg-bad",
            from_agent="agent-a",
            to_agent=None,
            msg_type="chat",
            content="no target",
            timestamp=1000.0,
        )
        with pytest.raises(ValueError, match="requires to_agent"):
            await broker.route("agent-a", msg)

    async def test_route_unknown_type_dropped(
        self, broker: MessageBroker
    ) -> None:
        """route silently drops unknown msg_type (no exception)."""
        msg = A2AMessage(
            message_id="msg-unknown",
            from_agent="agent-a",
            to_agent=None,
            msg_type="chat",  # type checker happy; we override below
            content="mystery",
            timestamp=1000.0,
        )
        # Manually set an unrecognized type to test the default branch
        msg = msg.model_copy(update={"msg_type": "unknown_type"})  # type: ignore[arg-type]

        # Should not raise — just logs a warning
        await broker.route("agent-a", msg)
