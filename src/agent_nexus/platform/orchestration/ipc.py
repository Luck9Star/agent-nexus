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
from collections import deque
from typing import Any

from agent_nexus.models.ipc import (
    AgentToPlatform,
    AgentToPlatformType,
    PlatformToAgent,
    PlatformToAgentType,
)
from agent_nexus.models.task import TaskItem

logger = logging.getLogger(__name__)

# IPC control message constants — shared across platform components.
# These are internal protocol strings, not user-facing values.
_HEARTBEAT_MSG = "__heartbeat__"
_HEARTBEAT_CID = "__hb__"
_LIST_TOOLS_MSG = "__list_tools__"
_INTERNAL_CID = "__internal__"

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


# Maximum single-line message size (4 MB).  Prevents unbounded memory
# growth if a misbehaving agent sends an extremely long line.
_MAX_MESSAGE_SIZE = 4 * 1024 * 1024


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
        try:
            data = payload.encode("utf-8") + b"\n"
            self._stdin.write(data)
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            raise IPCConnectionError(f"Agent stdin closed: {exc}") from exc
        try:
            await asyncio.wait_for(self._stdin.drain(), timeout=5.0)
        except asyncio.TimeoutError as exc:
            raise IPCTimeoutError("Timed out draining stdin to agent") from exc
        except (BrokenPipeError, ConnectionResetError, OSError, RuntimeError) as exc:
            raise IPCConnectionError(f"Agent stdin closed during drain: {exc}") from exc
        # Truncate to 200 chars to avoid leaking full payloads in logs
        logger.debug("IPC send: %.200s", payload)

    # -- receive ------------------------------------------------------------

    async def receive(self, timeout: float = 30.0) -> AgentToPlatform:
        """Read and deserialize message from agent's stdout.

        Uses :meth:`readline` for line-based framing.

        Raises:
            IPCTimeoutError: on timeout.
            IPCConnectionError: if stdout is closed.
        """
        timeout = max(timeout, 0.1)
        try:
            raw = await asyncio.wait_for(self._stdout.readline(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise IPCTimeoutError(
                f"Timed out after {timeout:.1f}s waiting for agent message"
            ) from exc

        if not raw:
            raise IPCConnectionError("Agent stdout closed (EOF)")

        if len(raw) > _MAX_MESSAGE_SIZE:
            raise IPCError(
                f"Agent message too large ({len(raw)} bytes, max {_MAX_MESSAGE_SIZE})"
            )

        # Whitespace-only lines (e.g. b"\n") indicate the agent sent no
        # payload — treat as connection issue rather than JSON error.
        if not raw.strip():
            raise IPCConnectionError("Agent sent empty line (possible EOF)")

        # Validate UTF-8 before json.loads — the stdlib swallows
        # UnicodeDecodeError inside json.loads, making it impossible
        # to distinguish encoding errors from malformed JSON.
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IPCError(
                f"Agent sent non-UTF-8 data ({len(raw)} bytes): {exc}"
            ) from exc

        # json.loads accepts bytes directly and handles leading/trailing
        # whitespace, so no intermediate .strip() or .decode() is needed.
        logger.debug("IPC recv: %.200s", raw[:200])

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IPCError(f"Invalid JSON from agent: {exc}") from exc

        try:
            return AgentToPlatform.model_validate(data)
        except Exception as exc:
            raise IPCError(f"Invalid message schema from agent: {exc}") from exc

    # -- close --------------------------------------------------------------

    def close_sync(self) -> None:
        """Synchronous best-effort close for dead-process cleanup.

        Closes both stdin (StreamWriter) and stdout (StreamReader
        transport) FDs.  Suitable for calling from sync contexts
        (e.g. ``_cleanup_dead``) when the remote process is already
        gone and only local FD release matters.
        """
        if not self._stdin.is_closing():
            self._stdin.close()
        # Release the stdout FD via its transport.  StreamReader has
        # no public close(), but the underlying transport owns the FD
        # and ``transport.close()`` releases it immediately.
        transport = getattr(self._stdout, "_transport", None)
        if transport is not None and not transport.is_closing():
            transport.close()

    async def close(self) -> None:
        """Close stdin (signals EOF to agent), drain stdout."""
        if not self._stdin.is_closing():
            self._stdin.close()
            try:
                await asyncio.wait_for(self._stdin.wait_closed(), timeout=2.0)
            except Exception:
                logger.debug("Failed to wait for stdin close", exc_info=True)
        # Drain any remaining stdout to avoid BrokenPipeError on the
        # agent side.  Upper bound prevents a misbehaving agent from
        # delaying close() indefinitely.
        _MAX_DRAIN_CHUNKS = 64
        try:
            for _ in range(_MAX_DRAIN_CHUNKS):
                chunk = await asyncio.wait_for(self._stdout.read(4096), timeout=1.0)
                if not chunk:
                    break
        except Exception:
            logger.debug("Failed to drain stdout during close", exc_info=True)


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

    # Maximum number of buffered messages.  Prevents unbounded memory growth
    # when an agent sends many messages that are not consumed promptly.
    # 10K messages with ~1KB each = ~10MB ceiling.
    _MAX_PEEK_BUFFER_SIZE: int = 10_000

    def __init__(self, stream: IPCStream) -> None:
        self._stream = stream
        self._peek_buffer: deque[AgentToPlatform] = deque()

    @property
    def stream(self) -> IPCStream:
        """Low-level stream access (for ProcessManager)."""
        return self._stream

    # -- buffer management --------------------------------------------------

    def _buffer_message(self, msg: AgentToPlatform) -> None:
        """Append *msg* to the peek buffer, enforcing the size limit.

        If the buffer has reached ``_MAX_PEEK_BUFFER_SIZE``, the oldest
        message is discarded to make room (FIFO eviction).
        """
        if len(self._peek_buffer) >= self._MAX_PEEK_BUFFER_SIZE:
            logger.warning(
                "IPC peek buffer reached max size (%d); discarding oldest message",
                self._MAX_PEEK_BUFFER_SIZE,
            )
            self._peek_buffer.popleft()
        self._peek_buffer.append(msg)

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
        if self._peek_buffer:
            return self._peek_buffer.popleft()
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
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        # Minimum per-receive timeout to avoid stream corruption from
        # prematurely interrupting a partial readline.
        _MIN_RECEIVE_TIMEOUT: float = 1.0

        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise IPCTimeoutError(
                    f"Timed out after {timeout:.1f}s waiting for final result"
                )

            msg = await self.receive_result(timeout=max(remaining, _MIN_RECEIVE_TIMEOUT))

            # Optional task-id filter
            if task_id is not None and msg.task_id != task_id:
                logger.warning(
                    "Buffering message for task %s (expected %s): type=%s",
                    msg.task_id,
                    task_id,
                    msg.type,
                )
                self._buffer_message(msg)
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
            await self.send_chat(_HEARTBEAT_MSG, conversation_id=_HEARTBEAT_CID)
            # Read messages until we find the pong or exhaust attempts.
            # Non-pong messages (e.g. progress) are buffered so they
            # are not lost — callers of receive() / receive_until_result()
            # will find them in _peek_buffer.
            loop = asyncio.get_running_loop()
            deadline = loop.time() + _HEARTBEAT_TIMEOUT
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return False
                try:
                    resp = await self._stream.receive(timeout=remaining)
                except (IPCError, asyncio.TimeoutError):
                    return False
                if resp.type == AgentToPlatformType.PROGRESS:
                    if resp.content and "pong" in resp.content.lower():
                        return True
                    # Not a pong — buffer it for later consumption.
                    self._buffer_message(resp)
                    continue
                # Non-progress message — buffer and keep looking for pong.
                self._buffer_message(resp)
        except (IPCError, asyncio.TimeoutError) as exc:
            logger.debug(
                "Heartbeat failed for agent: [%s] %s",
                type(exc).__name__, exc,
            )
            return False


# ---------------------------------------------------------------------------
# IPC Lock Registry
# ---------------------------------------------------------------------------
# Per-agent asyncio.Lock for serializing IPC calls (tool execution and
# orchestration).  Lives here because both the gateway (tool_adapter) and
# the router need shared access without a circular dependency.


_ipc_lock_registry: dict[str, asyncio.Lock] = {}
_ipc_lock_loop_id: int | None = None


def get_ipc_lock(agent_name: str) -> asyncio.Lock:
    """Get or create an asyncio.Lock for *agent_name*.

    If the current event loop differs from the one that created the
    locks (e.g. after ``asyncio.run()`` in tests), all locks are
    discarded and recreated to prevent ``attached to a different loop``
    errors.
    """
    global _ipc_lock_registry, _ipc_lock_loop_id

    # Check loop identity only when registry is empty (startup / test reset).
    # A running event loop's id() does not change during normal operation.
    if not _ipc_lock_registry:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        current_loop_id = id(loop) if loop is not None else None
        if current_loop_id != _ipc_lock_loop_id:
            _ipc_lock_registry.clear()
            _ipc_lock_loop_id = current_loop_id

    lock = _ipc_lock_registry.get(agent_name)
    if lock is None:
        lock = asyncio.Lock()
        _ipc_lock_registry[agent_name] = lock
    return lock
