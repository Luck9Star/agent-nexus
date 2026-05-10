"""MessageBroker — Platform-as-Broker A2A message relay.

Implements four message patterns over the existing IPC transport:
- **send_message**: fire-and-forget, delivers to target agent via IPC.
- **send_request**: waits for reply via ``asyncio.Future``, raises
  ``TimeoutError`` if no reply within timeout.
- **broadcast**: delivers to all agents in same composition group (or
  all running agents if group is None).
- **deliver_reply**: resolves the pending ``Future`` for a request.

Nesting prohibition: if agent A has a pending request to B, B cannot
make a request to A (raises ``RuntimeError``).  This prevents
deadlock from circular request-wait cycles.

Design: D1 — in-memory ``asyncio`` only, no persistence.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid

from agent_nexus.models.ipc import (
    A2AMessage,
    PlatformToAgent,
    PlatformToAgentType,
)
from agent_nexus.platform.orchestration.agent_directory import AgentDirectory
from agent_nexus.platform.orchestration.process_manager import ProcessManager

logger = logging.getLogger(__name__)


class MessageBroker:
    """Relay A2A messages between agents via the Platform Router.

    The broker wraps the existing ``ProcessManager`` / ``IPCProtocol``
    stack.  Agents never connect directly — the Platform is the sole
    coordinator.
    """

    def __init__(
        self,
        process_manager: ProcessManager,
        agent_directory: AgentDirectory | None = None,
    ) -> None:
        self._pm = process_manager
        self._directory = agent_directory
        self._pending_replies: dict[str, asyncio.Future[str]] = {}
        self._active_requests: set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send_message(self, from_id: str, to_id: str, content: str) -> None:
        """Fire-and-forget: deliver *content* from *from_id* to *to_id*.

        Raises:
            KeyError: target agent not found.
        """
        msg = self._build_a2a(
            from_agent=from_id,
            to_agent=to_id,
            msg_type="chat",
            content=content,
        )
        await self._deliver(msg)

    async def send_request(
        self,
        from_id: str,
        to_id: str,
        content: str,
        timeout: float = 30.0,
    ) -> str:
        """Send a request and wait for a reply.

        Creates an ``asyncio.Future`` and waits up to *timeout* seconds
        for ``deliver_reply`` to resolve it.

        Args:
            from_id: Sender agent identifier.
            to_id: Target agent identifier.
            content: Request payload.
            timeout: Seconds to wait for reply (default 30).

        Returns:
            The reply content string.

        Raises:
            TimeoutError: no reply received within *timeout*.
            RuntimeError: nesting prohibition violated.
            KeyError: target agent not found.
        """
        self._check_nesting(from_id, to_id)

        msg = self._build_a2a(
            from_agent=from_id,
            to_agent=to_id,
            msg_type="request",
            content=content,
        )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending_replies[msg.message_id] = future

        request_key = self._request_key(from_id, to_id)
        self._active_requests.add(request_key)

        try:
            await self._deliver(msg)
            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            raise TimeoutError(
                f"Request from '{from_id}' to '{to_id}' timed out after {timeout}s"
            ) from None
        finally:
            self._pending_replies.pop(msg.message_id, None)
            self._active_requests.discard(request_key)

    async def broadcast(
        self,
        from_id: str,
        content: str,
        group: str | None = None,
    ) -> list[str]:
        """Deliver *content* to all agents in *group* (or all if ``None``).

        Returns:
            List of agent IDs that received the broadcast.
        """
        targets = self._get_broadcast_targets(from_id, group)
        msg = self._build_a2a(
            from_agent=from_id,
            to_agent=None,
            msg_type="broadcast",
            content=content,
        )

        delivered: list[str] = []
        for target_id in targets:
            per_agent_msg = msg.model_copy(update={"to_agent": target_id})
            try:
                await self._deliver(per_agent_msg)
                delivered.append(target_id)
            except Exception:
                logger.warning(
                    "Broadcast delivery failed for agent '%s'",
                    target_id,
                    exc_info=True,
                )
        return delivered

    async def deliver_reply(self, request_id: str, content: str) -> None:
        """Resolve the pending Future for *request_id* with *content*.

        Called when the platform receives a reply IPC message from an
        agent responding to a prior ``send_request``.

        Raises:
            KeyError: no pending request for *request_id*.
        """
        future = self._pending_replies.get(request_id)
        if future is None:
            raise KeyError(f"No pending request for message_id '{request_id}'")
        if not future.done():
            future.set_result(content)

    async def route(self, from_id: str, message: A2AMessage) -> None:
        """Dispatch *message* based on ``msg_type``.

        This is the unified entry point for the Platform Router to
        handle incoming A2A messages from agents.
        """
        match message.msg_type:
            case "chat":
                if message.to_agent is None:
                    raise ValueError("chat message requires to_agent")
                await self.send_message(from_id, message.to_agent, message.content)
            case "request":
                if message.to_agent is None:
                    raise ValueError("request message requires to_agent")
                await self.send_request(from_id, message.to_agent, message.content)
            case "broadcast":
                await self.broadcast(from_id, message.content)
            case "reply":
                reply_to = message.in_reply_to
                if reply_to is not None:
                    await self.deliver_reply(reply_to, message.content)
                else:
                    logger.warning(
                        "Reply message from '%s' has no in_reply_to; dropping",
                        from_id,
                    )
            case _:
                logger.warning("Unknown A2A msg_type '%s'; dropping", message.msg_type)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_a2a(
        from_agent: str,
        to_agent: str | None,
        msg_type: str,
        content: str,
        in_reply_to: str | None = None,
    ) -> A2AMessage:
        return A2AMessage(
            message_id=str(uuid.uuid4()),
            from_agent=from_agent,
            to_agent=to_agent,
            msg_type=msg_type,  # type: ignore[arg-type]
            in_reply_to=in_reply_to,
            content=content,
            timestamp=time.time(),
        )

    async def _deliver(self, msg: A2AMessage) -> None:
        """Serialize *msg* into a ``PlatformToAgent`` and send via IPC."""
        if msg.to_agent is None:
            raise ValueError("Cannot deliver message with to_agent=None")

        # D4: composition boundary — only allow messaging within same composition
        self._check_composition_boundary(msg.from_agent, msg.to_agent)

        handle = self._pm.get_agent(msg.to_agent)
        if handle is None:
            raise KeyError(f"Agent '{msg.to_agent}' not found")
        if not handle.is_alive:
            raise KeyError(f"Agent '{msg.to_agent}' is not alive")

        ipc_type = self._a2a_type_to_ipc(msg.msg_type)
        payload = PlatformToAgent(
            type=ipc_type,
            content=msg.model_dump_json(),
            conversation_id=msg.message_id,
        )
        await handle.ipc.stream.send(payload)
        logger.debug(
            "A2A delivered %s from '%s' to '%s' (msg_id=%s)",
            msg.msg_type,
            msg.from_agent,
            msg.to_agent,
            msg.message_id,
        )

    @staticmethod
    def _a2a_type_to_ipc(msg_type: str) -> PlatformToAgentType:
        return {
            "chat": PlatformToAgentType.RECEIVE_MESSAGE,
            "request": PlatformToAgentType.RECEIVE_REQUEST,
            "broadcast": PlatformToAgentType.RECEIVE_BROADCAST,
            "reply": PlatformToAgentType.RECEIVE_REPLY,
        }[msg_type]

    def _get_broadcast_targets(self, from_id: str, group: str | None) -> list[str]:
        """Return agent IDs eligible for broadcast, excluding *from_id*.

        When *group* is provided and an ``AgentDirectory`` is wired in,
        filter targets by role (the *group* value is treated as a role name).
        Otherwise fall back to all running agents.
        """
        if group is not None and self._directory is not None:
            role_agents = {addr.agent_id for addr in self._directory.find_by_role(group)}
            running = set(self._pm.list_running())
            return [aid for aid in running if aid != from_id and aid in role_agents]
        running = self._pm.list_running()
        return [aid for aid in running if aid != from_id]

    def _check_composition_boundary(self, from_id: str, to_id: str) -> None:
        """D4: restrict messaging to agents in the same composition group.

        When an AgentDirectory is available and both agents declare a
        composition group, delivery is blocked if the groups don't match.
        If either agent has no composition (or no directory), delivery is
        allowed — this supports standalone agents that aren't part of any group.
        """
        if self._directory is None:
            return
        from_addr = self._directory.resolve(from_id)
        to_addr = self._directory.resolve(to_id)
        if from_addr is None or to_addr is None:
            return
        if from_addr.composition is None or to_addr.composition is None:
            return
        if from_addr.composition != to_addr.composition:
            raise PermissionError(
                f"Composition boundary: agent '{from_id}' (group={from_addr.composition}) "
                f"cannot message agent '{to_id}' (group={to_addr.composition})"
            )

    def _check_nesting(self, from_id: str, to_id: str) -> None:
        """Prevent A→B request when B→A is already pending.

        This avoids deadlock from circular request-wait cycles.
        """
        reverse_key = self._request_key(to_id, from_id)
        if reverse_key in self._active_requests:
            raise RuntimeError(
                f"Nesting prohibition: agent '{to_id}' already has a pending "
                f"request to '{from_id}'; cannot make reverse request"
            )

    @staticmethod
    def _request_key(from_id: str, to_id: str) -> str:
        return f"{from_id}->{to_id}"
