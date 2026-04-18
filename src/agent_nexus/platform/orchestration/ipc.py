"""IPC Protocol — JSON-lines communication over stdin/stdout.

Message framing:
- Each message is a single JSON object on one line
- Messages are delimited by newline (\\n)
- Platform writes to Agent's stdin, reads from Agent's stdout
- No file-based mailbox — pure stream-based IPC

This replaces ClawTeam's file-based MailboxManager with a
lighter-weight stream approach: zero file I/O, no broadcast needed,
and the Platform Router is the sole coordinator.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from agent_nexus.models.ipc import (
    AgentToPlatform,
    AgentToPlatformType,
    PlatformToAgent,
    PlatformToAgentType,
)
from agent_nexus.models.task import TaskItem

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class IPCError(Exception):
    """Base IPC error."""


class IPCTimeoutError(IPCError):
    """Timeout waiting for message."""


class IPCConnectionError(IPCError):
    """Connection closed or broken."""


# ---------------------------------------------------------------------------
# Low-level stream
# ---------------------------------------------------------------------------


class IPCStream:
    """Bidirectional JSON-lines stream over asyncio stdin/stdout pipes.

    ``IPCStream`` owns the raw byte-level framing.  Callers should use
    :class:`IPCProtocol` for semantic message helpers.
    """

    def __init__(
        self,
        stdin: asyncio.StreamWriter,
        stdout: asyncio.StreamReader,
    ) -> None:
        self._stdin = stdin
        self._stdout = stdout

    # -- send ---------------------------------------------------------------

    async def send(self, message: PlatformToAgent) -> None:
        """Serialize and write message to agent's stdin.

        JSON-lines format: single JSON object + newline.
        Must flush after write to ensure agent receives immediately.
        """
        payload = message.model_dump_json(exclude_none=True)
        line = payload + "\n"
        self._stdin.write(line.encode("utf-8"))
        await self._stdin.drain()
        logger.debug("IPC send: %s", payload)

    # -- receive ------------------------------------------------------------

    async def receive(self, timeout: float = 30.0) -> AgentToPlatform:
        """Read and deserialize message from agent's stdout.

        Uses :meth:`readline` for line-based framing.

        Raises:
            IPCTimeoutError: on timeout.
            IPCConnectionError: if stdout is closed.
        """
        try:
            raw = await asyncio.wait_for(self._stdout.readline(), timeout=timeout)
        except asyncio.TimeoutError:
            raise IPCTimeoutError(
                f"Timed out after {timeout:.1f}s waiting for agent message"
            )

        if not raw:
            raise IPCConnectionError("Agent stdout closed (EOF)")

        line = raw.decode("utf-8").strip()
        if not line:
            raise IPCConnectionError("Agent sent empty line (possible EOF)")

        logger.debug("IPC recv: %s", line)

        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise IPCError(f"Invalid JSON from agent: {exc}") from exc

        try:
            return AgentToPlatform.model_validate(data)
        except Exception as exc:
            raise IPCError(f"Invalid message schema from agent: {exc}") from exc

    # -- close --------------------------------------------------------------

    async def close(self) -> None:
        """Close stdin (signals EOF to agent), drain stdout."""
        if not self._stdin.is_closing():
            self._stdin.close()
            try:
                await asyncio.wait_for(self._stdin.wait_closed(), timeout=2.0)
            except (asyncio.TimeoutError, Exception):
                pass
        # Drain any remaining stdout to avoid BrokenPipeError on the
        # agent side.
        try:
            while True:
                chunk = await asyncio.wait_for(self._stdout.read(4096), timeout=1.0)
                if not chunk:
                    break
        except (asyncio.TimeoutError, Exception):
            pass


# ---------------------------------------------------------------------------
# High-level protocol
# ---------------------------------------------------------------------------

_HEARTBEAT_TIMEOUT: float = 10.0


class IPCProtocol:
    """High-level protocol for platform-agent communication.

    Handles:
    - Sending task assignments to agents
    - Receiving results, progress updates, and errors
    - Data reference passing (cross-agent data without full transfer)
    - Heartbeat / health-check messages
    """

    def __init__(self, stream: IPCStream) -> None:
        self._stream = stream

    @property
    def stream(self) -> IPCStream:
        """Low-level stream access (for ProcessManager)."""
        return self._stream

    # -- outbound helpers ---------------------------------------------------

    async def send_chat(
        self,
        message: str,
        conversation_id: str | None = None,
    ) -> None:
        """Send a chat message to agent."""
        msg = PlatformToAgent(
            type=PlatformToAgentType.CHAT,
            content=message,
            conversation_id=conversation_id,
        )
        await self._stream.send(msg)

    async def send_task(self, task: TaskItem) -> None:
        """Send a task assignment to agent."""
        msg = PlatformToAgent(
            type=PlatformToAgentType.TASK,
            content=task.description,
            task_id=task.id,
        )
        await self._stream.send(msg)

    async def send_data_reference(
        self,
        ref_id: str,
        summary: str,
        agent_source: str,
        size_hint: str = "",
    ) -> None:
        """Send a data reference (cross-agent data pointer).

        Instead of transferring full data between agents, we pass a
        lightweight reference (~50 tokens) so the receiving agent can
        request it on demand or access it from shared storage.
        """
        msg = PlatformToAgent(
            type=PlatformToAgentType.DATA_REFERENCE,
            content=summary,
            ref_id=ref_id,
            summary=f"[{agent_source}] {summary} {size_hint}".strip(),
        )
        await self._stream.send(msg)

    # -- inbound helpers ----------------------------------------------------

    async def receive_result(self, timeout: float = 60.0) -> AgentToPlatform:
        """Wait for agent's result / progress / error message.

        This is the primary receive loop entry point.  The caller is
        expected to inspect ``msg.type`` to determine how to handle it.
        """
        return await self._stream.receive(timeout=timeout)

    async def receive_until_result(
        self,
        task_id: str | None = None,
        timeout: float = 300.0,
        progress_callback: Any | None = None,
    ) -> AgentToPlatform:
        """Receive messages until a final result or error arrives.

        Intermediate ``progress`` messages are optionally forwarded to
        *progress_callback*.  The loop exits on ``result`` or ``error``.

        Args:
            task_id: Optional filter — only accept messages for this task.
            timeout: Total timeout for the entire wait.
            progress_callback: ``async def callback(msg) -> None``
        """
        deadline = asyncio.get_running_loop().time() + timeout

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise IPCTimeoutError(
                    f"Timed out after {timeout:.1f}s waiting for final result"
                )

            msg = await self._stream.receive(timeout=remaining)

            # Optional task-id filter
            if task_id is not None and msg.task_id != task_id:
                logger.warning(
                    "Ignoring message for task %s (expected %s): type=%s",
                    msg.task_id,
                    task_id,
                    msg.type,
                )
                continue

            if msg.type == AgentToPlatformType.PROGRESS:
                if progress_callback is not None:
                    await progress_callback(msg)
                continue

            # result or error — terminal
            return msg

    # -- heartbeat ----------------------------------------------------------

    async def send_heartbeat(self) -> bool:
        """Send heartbeat ping, expect pong within timeout.

        The agent is expected to respond with a ``progress`` message
        where ``content == "pong"`` when it receives a chat message
        with content ``"__heartbeat__"``.

        Returns:
            True if pong received, False otherwise.
        """
        try:
            await self.send_chat("__heartbeat__", conversation_id="__hb__")
            resp = await self._stream.receive(timeout=_HEARTBEAT_TIMEOUT)
            return resp.type == AgentToPlatformType.PROGRESS
        except (IPCError, asyncio.TimeoutError):
            return False
