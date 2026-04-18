"""Unit tests for IPC protocol — JSON-lines stream communication.

Tests IPCStream (send/receive/close) and IPCProtocol (high-level helpers)
using mocked asyncio streams.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.models.ipc import (
    AgentToPlatform,
    AgentToPlatformType,
    PlatformToAgent,
    PlatformToAgentType,
)
from agent_nexus.models.task import TaskItem, TaskState
from agent_nexus.platform.orchestration.ipc import (
    IPCConnectionError,
    IPCError,
    IPCProtocol,
    IPCStream,
    IPCTimeoutError,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_stdin() -> MagicMock:
    """Mock asyncio.StreamWriter for stdin."""
    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.is_closing = MagicMock(return_value=False)
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    return writer


@pytest.fixture
def mock_stdout() -> MagicMock:
    """Mock asyncio.StreamReader for stdout."""
    reader = MagicMock(spec=asyncio.StreamReader)
    reader.readline = AsyncMock()
    reader.read = AsyncMock(return_value=b"")
    return reader


@pytest.fixture
def stream(mock_stdin: MagicMock, mock_stdout: MagicMock) -> IPCStream:
    """IPCStream with mocked stdin/stdout."""
    return IPCStream(stdin=mock_stdin, stdout=mock_stdout)


@pytest.fixture
def protocol(stream: IPCStream) -> IPCProtocol:
    """IPCProtocol wrapping a mocked stream."""
    return IPCProtocol(stream)


def _agent_message(**overrides) -> dict:
    """Build a valid AgentToPlatform JSON dict."""
    msg = {"type": "result", "content": "done", "task_id": "t1"}
    msg.update(overrides)
    return msg


# ============================================================================
# IPCStream.send()
# ============================================================================


class TestIPCStreamSend:
    async def test_send_writes_json_lines(self, stream: IPCStream, mock_stdin: MagicMock) -> None:
        """send() writes JSON + newline to stdin."""
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hello")
        await stream.send(msg)

        mock_stdin.write.assert_called_once()
        written = mock_stdin.write.call_args[0][0]
        assert written.endswith(b"\n")
        parsed = json.loads(written.decode("utf-8").strip())
        assert parsed["type"] == "chat"
        assert parsed["content"] == "hello"

    async def test_send_calls_drain(self, stream: IPCStream, mock_stdin: MagicMock) -> None:
        """send() flushes by calling drain()."""
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hi")
        await stream.send(msg)
        mock_stdin.drain.assert_awaited_once()

    async def test_send_excludes_none_fields(self, stream: IPCStream, mock_stdin: MagicMock) -> None:
        """send() excludes None fields from JSON output."""
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hi")
        await stream.send(msg)

        written = mock_stdin.write.call_args[0][0]
        parsed = json.loads(written.decode("utf-8").strip())
        # task_id, conversation_id, ref_id, summary should be absent
        assert "task_id" not in parsed
        assert "conversation_id" not in parsed
        assert "ref_id" not in parsed
        assert "summary" not in parsed


# ============================================================================
# IPCStream.receive()
# ============================================================================


class TestIPCStreamReceive:
    async def test_receive_valid_json(self, stream: IPCStream, mock_stdout: MagicMock) -> None:
        """receive() deserializes a valid AgentToPlatform JSON line."""
        data = _agent_message()
        mock_stdout.readline.return_value = (json.dumps(data) + "\n").encode("utf-8")

        result = await stream.receive()

        assert isinstance(result, AgentToPlatform)
        assert result.type == AgentToPlatformType.RESULT
        assert result.content == "done"
        assert result.task_id == "t1"

    async def test_receive_timeout(self, stream: IPCStream, mock_stdout: MagicMock) -> None:
        """receive() raises IPCTimeoutError on timeout."""
        mock_stdout.readline.side_effect = asyncio.TimeoutError()

        with pytest.raises(IPCTimeoutError, match="Timed out"):
            await stream.receive(timeout=5.0)

    async def test_receive_eof(self, stream: IPCStream, mock_stdout: MagicMock) -> None:
        """receive() raises IPCConnectionError on EOF (empty bytes)."""
        mock_stdout.readline.return_value = b""

        with pytest.raises(IPCConnectionError, match="EOF"):
            await stream.receive()

    async def test_receive_empty_line(self, stream: IPCStream, mock_stdout: MagicMock) -> None:
        """receive() raises IPCConnectionError on empty line."""
        mock_stdout.readline.return_value = b"\n"

        with pytest.raises(IPCConnectionError, match="empty line"):
            await stream.receive()

    async def test_receive_invalid_json(self, stream: IPCStream, mock_stdout: MagicMock) -> None:
        """receive() raises IPCError on non-JSON data."""
        mock_stdout.readline.return_value = b"not json at all\n"

        with pytest.raises(IPCError, match="Invalid JSON"):
            await stream.receive()

    async def test_receive_invalid_schema(self, stream: IPCStream, mock_stdout: MagicMock) -> None:
        """receive() raises IPCError on JSON that doesn't match schema."""
        mock_stdout.readline.return_value = b'{"type": "bogus_type", "content": "x"}\n'

        with pytest.raises(IPCError, match="Invalid message schema"):
            await stream.receive()


# ============================================================================
# IPCStream.close()
# ============================================================================


class TestIPCStreamClose:
    async def test_close_closes_stdin(self, stream: IPCStream, mock_stdin: MagicMock) -> None:
        """close() calls stdin.close() and wait_closed()."""
        await stream.close()
        mock_stdin.close.assert_called_once()
        mock_stdin.wait_closed.assert_awaited_once()

    async def test_close_skips_if_already_closing(
        self, stream: IPCStream, mock_stdin: MagicMock
    ) -> None:
        """close() skips if stdin is already closing."""
        mock_stdin.is_closing.return_value = True
        await stream.close()
        mock_stdin.close.assert_not_called()


# ============================================================================
# IPCProtocol — outbound helpers
# ============================================================================


class TestIPCProtocolSendChat:
    async def test_send_chat(self, protocol: IPCProtocol, mock_stdin: MagicMock) -> None:
        """send_chat() creates PlatformToAgent with type=CHAT."""
        await protocol.send_chat("hello agent", conversation_id="conv-1")

        written = mock_stdin.write.call_args[0][0]
        parsed = json.loads(written.decode("utf-8").strip())
        assert parsed["type"] == "chat"
        assert parsed["content"] == "hello agent"
        assert parsed["conversation_id"] == "conv-1"


class TestIPCProtocolSendTask:
    async def test_send_task(self, protocol: IPCProtocol, mock_stdin: MagicMock) -> None:
        """send_task() creates PlatformToAgent with type=TASK and task_id set."""
        from datetime import datetime, timezone

        task = TaskItem(
            id="task-42",
            description="Write tests",
            agent="tester",
            state=TaskState.PENDING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        await protocol.send_task(task)

        written = mock_stdin.write.call_args[0][0]
        parsed = json.loads(written.decode("utf-8").strip())
        assert parsed["type"] == "task"
        assert parsed["task_id"] == "task-42"
        assert parsed["content"] == "Write tests"


class TestIPCProtocolSendDataReference:
    async def test_send_data_reference(
        self, protocol: IPCProtocol, mock_stdin: MagicMock
    ) -> None:
        """send_data_reference() creates DATA_REFERENCE with ref_id and summary."""
        await protocol.send_data_reference(
            ref_id="var://output/123",
            summary="Generated report",
            agent_source="doc-writer",
            size_hint="~10KB",
        )

        written = mock_stdin.write.call_args[0][0]
        parsed = json.loads(written.decode("utf-8").strip())
        assert parsed["type"] == "data_reference"
        assert parsed["ref_id"] == "var://output/123"
        assert "doc-writer" in parsed["summary"]
        assert "Generated report" in parsed["summary"]


# ============================================================================
# IPCProtocol — inbound helpers
# ============================================================================


class TestIPCProtocolReceiveResult:
    async def test_receive_result_delegates(
        self, protocol: IPCProtocol, mock_stdout: MagicMock
    ) -> None:
        """receive_result() delegates to stream.receive with timeout."""
        data = _agent_message(type="result", content="final output")
        mock_stdout.readline.return_value = (json.dumps(data) + "\n").encode("utf-8")

        result = await protocol.receive_result(timeout=45.0)
        assert result.type == AgentToPlatformType.RESULT
        assert result.content == "final output"


# ============================================================================
# IPCProtocol — heartbeat
# ============================================================================


class TestIPCProtocolHeartbeat:
    async def test_send_heartbeat_success(
        self, protocol: IPCProtocol, mock_stdout: MagicMock
    ) -> None:
        """send_heartbeat() returns True when pong received."""
        pong = {"type": "progress", "content": "pong"}
        mock_stdout.readline.return_value = (json.dumps(pong) + "\n").encode("utf-8")

        result = await protocol.send_heartbeat()
        assert result is True

    async def test_send_heartbeat_failure_wrong_type(
        self, protocol: IPCProtocol, mock_stdout: MagicMock
    ) -> None:
        """send_heartbeat() returns False when response is not progress."""
        resp = {"type": "result", "content": "something"}
        mock_stdout.readline.return_value = (json.dumps(resp) + "\n").encode("utf-8")

        result = await protocol.send_heartbeat()
        assert result is False

    async def test_send_heartbeat_timeout(
        self, protocol: IPCProtocol, mock_stdout: MagicMock
    ) -> None:
        """send_heartbeat() returns False on IPCError (timeout)."""
        mock_stdout.readline.side_effect = asyncio.TimeoutError()

        result = await protocol.send_heartbeat()
        assert result is False


# ============================================================================
# IPCProtocol — peek buffer
# ============================================================================


class TestIPCProtocolPeekBuffer:
    async def test_receive_returns_peeked_message_first(
        self, protocol: IPCProtocol, mock_stdout: MagicMock
    ) -> None:
        """receive_result() returns buffered message without reading stream."""
        # Manually put a message into the peek buffer
        buffered_msg = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="buffered output",
            task_id="t-buf",
        )
        protocol._peek_buffer.append(buffered_msg)

        result = await protocol.receive_result()
        assert result.content == "buffered output"
        assert result.task_id == "t-buf"
        # Stream should NOT have been read
        mock_stdout.readline.assert_not_called()

    async def test_send_heartbeat_preserves_non_pong_messages(
        self, protocol: IPCProtocol, mock_stdout: MagicMock
    ) -> None:
        """send_heartbeat() pushes non-progress messages to _peek_buffer."""
        # First read returns a result message (not progress), second returns pong.
        result_data = {"type": "result", "content": "some output", "task_id": "t1"}
        pong_data = {"type": "progress", "content": "pong"}
        mock_stdout.readline.side_effect = [
            (json.dumps(result_data) + "\n").encode("utf-8"),
            (json.dumps(pong_data) + "\n").encode("utf-8"),
        ]

        result = await protocol.send_heartbeat()
        assert result is True
        # The non-pong result message should be in the peek buffer
        assert len(protocol._peek_buffer) == 1
        assert protocol._peek_buffer[0].content == "some output"
        assert protocol._peek_buffer[0].type == AgentToPlatformType.RESULT
        assert protocol._peek_buffer[0].task_id == "t1"
