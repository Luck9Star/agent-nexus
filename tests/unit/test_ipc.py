"""Unit tests for IPC protocol — JSON-lines stream communication.

Tests IPCStream (send/receive/close) and IPCProtocol (high-level helpers)
using mocked asyncio streams.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

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
        """send() writes JSON payload then newline to stdin."""
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hello")
        await stream.send(msg)

        # Single write call: payload bytes + newline byte combined
        assert mock_stdin.write.call_count == 1
        written = mock_stdin.write.call_args[0][0]
        assert written == b'{"type":"chat","content":"hello"}\n'

    async def test_send_calls_drain(self, stream: IPCStream, mock_stdin: MagicMock) -> None:
        """send() flushes by calling drain()."""
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hi")
        await stream.send(msg)
        mock_stdin.drain.assert_awaited_once()

    async def test_send_excludes_none_fields(
        self, stream: IPCStream, mock_stdin: MagicMock
    ) -> None:
        """send() excludes None fields from JSON output."""
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hi")
        await stream.send(msg)

        written = mock_stdin.write.call_args_list[0][0][0]
        parsed = json.loads(written.decode("utf-8"))
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

        written = mock_stdin.write.call_args_list[0][0][0]
        parsed = json.loads(written.decode("utf-8"))
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

        written = mock_stdin.write.call_args_list[0][0][0]
        parsed = json.loads(written.decode("utf-8"))
        assert parsed["type"] == "task"
        assert parsed["task_id"] == "task-42"
        assert parsed["content"] == "Write tests"


class TestIPCProtocolSendDataReference:
    async def test_send_data_reference(self, protocol: IPCProtocol, mock_stdin: MagicMock) -> None:
        """send_data_reference() creates DATA_REFERENCE with ref_id and summary."""
        await protocol.send_data_reference(
            ref_id="var://output/123",
            summary="Generated report",
            agent_source="doc-writer",
            size_hint="~10KB",
        )

        written = mock_stdin.write.call_args_list[0][0][0]
        parsed = json.loads(written.decode("utf-8"))
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
        mock_stdout.readline.side_effect = [
            (json.dumps(resp) + "\n").encode("utf-8"),
            b"",  # EOF on second read -> IPCConnectionError -> loop exits
        ]

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


# ============================================================================
# Iteration 13 merge: TestIPCPeekBufferPreservation
# ============================================================================


class TestIPCPeekBufferPreservation:
    """Verify receive_until_result buffers mismatched task_id messages."""

    async def test_mismatched_task_id_buffered(self) -> None:
        """Messages with wrong task_id are buffered, not discarded."""
        mock_stdin = MagicMock()
        mock_stdout = MagicMock(spec=asyncio.StreamReader)

        stream = IPCStream(mock_stdin, mock_stdout)
        protocol = IPCProtocol(stream)

        msg_a = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="result for A",
            task_id="task-A",
        )

        msg_b = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="result for B",
            task_id="task-B",
        )

        call_count = 0

        async def fake_receive(timeout=30.0):  # pyright: ignore[reportUnusedParameter]
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return msg_a
            return msg_b

        with patch.object(protocol, "receive_result", side_effect=fake_receive):
            result = await protocol.receive_until_result(task_id="task-B", timeout=5.0)

        assert result.task_id == "task-B"
        assert result.content == "result for B"

        assert len(protocol._peek_buffer) == 1
        assert protocol._peek_buffer[0].task_id == "task-A"
        assert protocol._peek_buffer[0].content == "result for A"

    async def test_mismatched_progress_still_continues(self) -> None:
        """Progress messages for wrong task_id are buffered too."""
        mock_stdin = MagicMock()
        mock_stdout = MagicMock(spec=asyncio.StreamReader)

        stream = IPCStream(mock_stdin, mock_stdout)
        protocol = IPCProtocol(stream)

        progress_wrong = AgentToPlatform(
            type=AgentToPlatformType.PROGRESS,
            content="progress for A",
            task_id="task-A",
        )
        result_right = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="result for B",
            task_id="task-B",
        )

        call_count = 0

        async def fake_receive(timeout=30.0):  # pyright: ignore[reportUnusedParameter]
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return progress_wrong
            return result_right

        with patch.object(protocol, "receive_result", side_effect=fake_receive):
            result = await protocol.receive_until_result(task_id="task-B", timeout=5.0)

        assert result.task_id == "task-B"
        assert len(protocol._peek_buffer) == 1
        assert protocol._peek_buffer[0].task_id == "task-A"

    async def test_no_task_filter_still_works(self) -> None:
        """When task_id is None, all messages are accepted (no buffering)."""
        mock_stdin = MagicMock()
        mock_stdout = MagicMock(spec=asyncio.StreamReader)

        stream = IPCStream(mock_stdin, mock_stdout)
        protocol = IPCProtocol(stream)

        msg = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="result",
            task_id="any-task",
        )

        with patch.object(protocol, "receive_result", return_value=msg):
            result = await protocol.receive_until_result(task_id=None, timeout=5.0)

        assert result.task_id == "any-task"
        assert len(protocol._peek_buffer) == 0


# ============================================================================
# Iteration 15 merge: TestIPCHearbeatPongCheck
# ============================================================================


class TestIPCHearbeatPongCheck:
    """send_heartbeat should only accept PROGRESS messages with pong content."""

    @pytest.mark.asyncio
    async def test_heartbeat_rejects_progress_without_pong(self) -> None:
        """A PROGRESS message without 'pong' content should NOT be accepted."""
        mock_stdin = AsyncMock()
        mock_stdout = MagicMock()
        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        protocol = IPCProtocol(stream)

        with patch.object(protocol, "send_chat", new_callable=AsyncMock):
            with patch.object(
                protocol._stream,
                "receive",
                new_callable=AsyncMock,
                side_effect=[
                    MagicMock(
                        type=AgentToPlatformType.PROGRESS,
                        content="working on task...",
                        task_id="t1",
                    ),
                    asyncio.TimeoutError(),
                ],
            ):
                result = await protocol.send_heartbeat()
        assert result is False
        assert len(protocol._peek_buffer) >= 1

    @pytest.mark.asyncio
    async def test_heartbeat_accepts_progress_with_pong(self) -> None:
        """A PROGRESS message with 'pong' content should be accepted."""
        mock_stdin = AsyncMock()
        mock_stdout = MagicMock()
        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        protocol = IPCProtocol(stream)

        with patch.object(protocol, "send_chat", new_callable=AsyncMock):
            with patch.object(
                protocol._stream,
                "receive",
                new_callable=AsyncMock,
                return_value=MagicMock(
                    type=AgentToPlatformType.PROGRESS,
                    content="pong",
                ),
            ):
                result = await protocol.send_heartbeat()
        assert result is True


# ============================================================================
# Iteration 22 merge: TestIPCSendDrainTimeout
# ============================================================================


class TestIPCSendDrainTimeout:
    """send() must not block indefinitely on drain()."""

    @pytest.mark.asyncio
    async def test_send_drain_timeout_raises(self) -> None:
        """If drain() blocks beyond 5s, send() raises IPCTimeoutError."""
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_stdout = MagicMock()
        mock_stdout.read = AsyncMock(return_value=b"")
        mock_stdout.readline = AsyncMock(return_value=b"")

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hi")

        with pytest.raises(IPCTimeoutError):
            await stream.send(msg)

    @pytest.mark.asyncio
    async def test_send_drain_succeeds_within_timeout(self) -> None:
        """send() completes when drain() resolves within timeout."""
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()
        mock_stdout = MagicMock()
        mock_stdout.read = AsyncMock(return_value=b"")
        mock_stdout.readline = AsyncMock(return_value=b"")

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hi")
        await stream.send(msg)

        assert mock_stdin.write.call_count == 1


# ============================================================================
# Iteration 23 merge: TestIPCSendBrokenPipe
# ============================================================================


class TestIPCSendBrokenPipe:
    @pytest.mark.asyncio
    async def test_write_broken_pipe(self) -> None:
        """write() BrokenPipeError is wrapped as IPCConnectionError."""
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock(side_effect=BrokenPipeError("pipe closed"))
        mock_stdout = MagicMock()

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hi")

        with pytest.raises(IPCConnectionError, match="stdin closed"):
            await stream.send(msg)

    @pytest.mark.asyncio
    async def test_drain_broken_pipe(self) -> None:
        """drain() BrokenPipeError is wrapped as IPCConnectionError."""
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock(side_effect=BrokenPipeError("gone"))
        mock_stdout = MagicMock()

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hi")

        with pytest.raises(IPCConnectionError, match="stdin closed during drain"):
            await stream.send(msg)

    @pytest.mark.asyncio
    async def test_drain_connection_reset(self) -> None:
        """drain() ConnectionResetError is wrapped as IPCConnectionError."""
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock(side_effect=ConnectionResetError("reset"))
        mock_stdout = MagicMock()

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hi")

        with pytest.raises(IPCConnectionError, match="stdin closed during drain"):
            await stream.send(msg)


# ============================================================================
# Iteration 23 merge: TestIPCCloseDrainBound
# ============================================================================


class TestIPCCloseDrainBound:
    @pytest.mark.asyncio
    async def test_close_drain_stops_after_max_chunks(self) -> None:
        """close() drain loop stops after 64 chunks even with more data."""
        mock_stdin = MagicMock()
        mock_stdin.is_closing.return_value = True
        mock_stdin.wait_closed = AsyncMock()
        mock_stdout = MagicMock()
        # Simulate unlimited output
        call_count = 0

        async def infinite_read(n):
            nonlocal call_count
            call_count += 1
            return b"x" * n  # never returns b"" → would loop forever

        mock_stdout.read = infinite_read

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        await stream.close()

        # Should stop at 64 chunks, not loop forever
        assert call_count == 64


# ============================================================================
# Regression: TestIPCReceiveNonUTF8
# ============================================================================


class TestIPCReceiveNonUTF8:
    """receive() must raise IPCError (not UnicodeDecodeError) on non-UTF-8 data."""

    @pytest.mark.asyncio
    async def test_receive_non_utf8_raises_ipc_error(self) -> None:
        """Non-UTF-8 bytes from agent stdout are wrapped as IPCError."""
        mock_stdin = MagicMock()
        mock_stdout = MagicMock(spec=asyncio.StreamReader)
        # Invalid UTF-8 sequence: 0xFF is never valid in UTF-8
        mock_stdout.readline = AsyncMock(return_value=b"\xff\xfe bad data\n")
        mock_stdout.read = AsyncMock(return_value=b"")

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)

        with pytest.raises(IPCError, match="non-UTF-8"):
            await stream.receive()

    @pytest.mark.asyncio
    async def test_receive_valid_utf8_still_works(self) -> None:
        """Valid UTF-8 with multibyte characters works correctly."""
        mock_stdin = MagicMock()
        mock_stdout = MagicMock(spec=asyncio.StreamReader)
        msg = {"type": "result", "content": "Chinese: 你好世界", "task_id": "t1"}
        encoded = (json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8")
        mock_stdout.readline = AsyncMock(return_value=encoded)
        mock_stdout.read = AsyncMock(return_value=b"")

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        result = await stream.receive()

        assert result.content == "Chinese: 你好世界"
        assert result.task_id == "t1"


# ============================================================================
# Regression: IPC model roundtrip (from iter 42 audit)
# ============================================================================


class TestIPCModelRoundtrip:
    """IPC message models serialize/deserialize correctly across the
    platform-agent boundary.  This validates the contract at the seam."""

    def test_platform_to_agent_roundtrip(self) -> None:
        """PlatformToAgent serializes and deserializes without data loss."""
        from agent_nexus.models.ipc import (
            PlatformToAgent,
            PlatformToAgentType,
        )

        p2a = PlatformToAgent(
            type=PlatformToAgentType.CHAT,
            conversation_id="conv-1",
            content="hello agent",
        )
        p2a_json = p2a.model_dump_json()
        p2a_round = PlatformToAgent.model_validate_json(p2a_json)
        assert p2a_round.content == "hello agent"
        assert p2a_round.conversation_id == "conv-1"

    def test_agent_to_platform_roundtrip(self) -> None:
        """AgentToPlatform with output dict roundtrips correctly."""
        from agent_nexus.models.ipc import (
            AgentToPlatform,
            AgentToPlatformType,
        )

        a2p = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="task done",
            output={"result": 42},
        )
        a2p_json = a2p.model_dump_json()
        a2p_round = AgentToPlatform.model_validate_json(a2p_json)
        assert a2p_round.output == {"result": 42}


# ============================================================================
# Issue 3: IPC content fields max_length validation (64KB limit)
# ============================================================================


class TestIPCContentMaxLength:
    """IPC text fields are capped at 65536 characters to prevent memory issues."""

    def test_platform_to_agent_content_within_limit(self):
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="x" * 65536)
        assert len(msg.content) == 65536

    def test_platform_to_agent_content_exceeds_limit(self):
        with pytest.raises(ValidationError, match="at most 65536 characters"):
            PlatformToAgent(type=PlatformToAgentType.CHAT, content="x" * 65537)

    def test_agent_to_platform_content_within_limit(self):
        msg = AgentToPlatform(type=AgentToPlatformType.RESULT, content="x" * 65536)
        assert len(msg.content) == 65536

    def test_agent_to_platform_content_exceeds_limit(self):
        with pytest.raises(ValidationError, match="at most 65536 characters"):
            AgentToPlatform(type=AgentToPlatformType.RESULT, content="x" * 65537)

    def test_agent_to_platform_message_within_limit(self):
        msg = AgentToPlatform(type=AgentToPlatformType.PROGRESS, message="x" * 65536)
        assert msg.message is not None
        assert len(msg.message) == 65536

    def test_agent_to_platform_message_exceeds_limit(self):
        with pytest.raises(ValidationError, match="at most 65536 characters"):
            AgentToPlatform(type=AgentToPlatformType.PROGRESS, message="x" * 65537)

    def test_agent_to_platform_error_within_limit(self):
        msg = AgentToPlatform(type=AgentToPlatformType.ERROR, error="x" * 65536)
        assert msg.error is not None
        assert len(msg.error) == 65536

    def test_agent_to_platform_error_exceeds_limit(self):
        with pytest.raises(ValidationError, match="at most 65536 characters"):
            AgentToPlatform(type=AgentToPlatformType.ERROR, error="x" * 65537)

    def test_empty_content_still_valid(self):
        """Empty string is within the limit and remains valid."""
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="")
        assert msg.content == ""

    def test_output_oversized_rejected(self):
        """AgentToPlatform output field rejects serialized size > 65536."""
        large_dict = {"data": "x" * 65530}
        with pytest.raises(ValidationError, match="output exceeds maximum serialized size"):
            AgentToPlatform(type=AgentToPlatformType.RESULT, output=large_dict)

    def test_output_none_accepted(self):
        msg = AgentToPlatform(type=AgentToPlatformType.RESULT, output=None)
        assert msg.output is None

    def test_output_small_dict_accepted(self):
        msg = AgentToPlatform(type=AgentToPlatformType.RESULT, output={"key": "value"})
        assert msg.output == {"key": "value"}


# ============================================================================
# Coverage gap: IPCStream.close() wait_closed timeout
# ============================================================================


class TestIPCCloseWaitClosedTimeout:
    """close() handles timeout/exception from wait_closed() (lines 141-142)."""

    @pytest.mark.asyncio
    async def test_close_handles_wait_closed_timeout(self) -> None:
        """close() does not raise when wait_closed times out."""
        mock_stdin = MagicMock()
        mock_stdin.is_closing.return_value = False
        mock_stdin.close = MagicMock()
        mock_stdin.wait_closed = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_stdout = MagicMock()
        mock_stdout.read = AsyncMock(return_value=b"")

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        await stream.close()

        mock_stdin.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_handles_wait_closed_exception(self) -> None:
        """close() does not raise when wait_closed raises a generic exception."""
        mock_stdin = MagicMock()
        mock_stdin.is_closing.return_value = False
        mock_stdin.close = MagicMock()
        mock_stdin.wait_closed = AsyncMock(side_effect=OSError("pipe broken"))
        mock_stdout = MagicMock()
        mock_stdout.read = AsyncMock(return_value=b"")

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        await stream.close()

        mock_stdin.close.assert_called_once()


# ============================================================================
# Coverage gap: IPCProtocol._buffer_message() eviction at max capacity
# ============================================================================


class TestIPCBufferEviction:
    """_buffer_message discards oldest when buffer reaches max size (lines 195-199)."""

    @pytest.mark.asyncio
    async def test_buffer_evicts_oldest_at_max_capacity(self) -> None:
        """When peek buffer reaches max size, the oldest message is discarded."""
        mock_stdin = MagicMock()
        mock_stdout = MagicMock()
        stream = IPCStream(mock_stdin, mock_stdout)
        protocol = IPCProtocol(stream)

        max_size = protocol._MAX_PEEK_BUFFER_SIZE

        old_msg = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="old message",
            task_id="t-old",
        )
        new_msg = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="new message",
            task_id="t-new",
        )

        # Fill buffer to max capacity
        for i in range(max_size):
            protocol._peek_buffer.append(
                AgentToPlatform(
                    type=AgentToPlatformType.RESULT,
                    content=f"msg-{i}",
                    task_id=f"t-{i}",
                )
            )

        assert len(protocol._peek_buffer) == max_size

        # Buffer a new message — should evict the oldest
        protocol._buffer_message(new_msg)

        assert len(protocol._peek_buffer) == max_size
        # Oldest ("msg-0") should be gone
        assert protocol._peek_buffer[0].content == "msg-1"
        # Newest should be "new message"
        assert protocol._peek_buffer[-1] is new_msg


# ============================================================================
# Coverage gap: IPCProtocol.receive_until_result() total timeout
# ============================================================================


class TestIPCReceiveUntilResultTimeout:
    """receive_until_result raises IPCTimeoutError when total time expires (line 283)."""

    @pytest.mark.asyncio
    async def test_receive_until_result_total_timeout(self) -> None:
        """receive_until_result raises IPCTimeoutError when deadline passes."""
        mock_stdin = MagicMock()
        mock_stdout = MagicMock()
        stream = IPCStream(mock_stdin, mock_stdout)
        protocol = IPCProtocol(stream)

        # Mock get_running_loop().time() to simulate time passing
        with patch(
            "agent_nexus.platform.orchestration.ipc.asyncio.get_running_loop"
        ) as mock_get_loop:
            mock_loop = MagicMock()
            mock_get_loop.return_value = mock_loop
            # time() is called multiple times:
            # First call (deadline calc): returns 1000
            # Second call (remaining check): returns 2000 → remaining = -1000 <= 0 → timeout
            mock_loop.time.side_effect = [1000.0, 2000.0]

            with pytest.raises(IPCTimeoutError, match="Timed out after"):
                await protocol.receive_until_result(timeout=5.0)


# ============================================================================
# Coverage gap: IPCProtocol.receive_until_result() progress with callback
# ============================================================================


class TestIPCReceiveUntilResultProgressCallback:
    """receive_until_result invokes progress_callback on PROGRESS messages (lines 301-303)."""

    @pytest.mark.asyncio
    async def test_progress_callback_invoked(self) -> None:
        """Progress messages are forwarded to the callback before continuing the loop."""
        mock_stdin = MagicMock()
        mock_stdout = MagicMock()
        stream = IPCStream(mock_stdin, mock_stdout)
        protocol = IPCProtocol(stream)

        progress_msg = AgentToPlatform(
            type=AgentToPlatformType.PROGRESS,
            content="50% done",
            task_id="t1",
        )
        result_msg = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="final",
            task_id="t1",
        )

        call_count = 0

        async def fake_receive(timeout=30.0):  # pyright: ignore[reportUnusedParameter]
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return progress_msg
            return result_msg

        callback_messages = []

        async def progress_callback(msg):
            callback_messages.append(msg)

        with patch.object(protocol, "receive_result", side_effect=fake_receive):
            result = await protocol.receive_until_result(
                timeout=10.0,
                progress_callback=progress_callback,
            )

        assert result.type == AgentToPlatformType.RESULT
        assert result.content == "final"
        assert len(callback_messages) == 1
        assert callback_messages[0].content == "50% done"


# ============================================================================
# Coverage gap: IPCProtocol.send_heartbeat() outer exception catch
# ============================================================================


class TestIPCSendHeartbeatOuterException:
    """send_heartbeat() returns False when outer IPCError/TimeoutError occurs (lines 343-344)."""

    @pytest.mark.asyncio
    async def test_send_heartbeat_outer_ipc_error(self) -> None:
        """send_heartbeat returns False when send_chat raises IPCError."""
        mock_stdin = MagicMock()
        mock_stdout = MagicMock()
        stream = IPCStream(mock_stdin, mock_stdout)
        protocol = IPCProtocol(stream)

        with patch.object(
            protocol, "send_chat", new_callable=AsyncMock, side_effect=IPCError("broken")
        ):
            result = await protocol.send_heartbeat()

        assert result is False

    @pytest.mark.asyncio
    async def test_send_heartbeat_outer_timeout_error(self) -> None:
        """send_heartbeat returns False when send_chat raises asyncio.TimeoutError."""
        mock_stdin = MagicMock()
        mock_stdout = MagicMock()
        stream = IPCStream(mock_stdin, mock_stdout)
        protocol = IPCProtocol(stream)

        with patch.object(
            protocol,
            "send_chat",
            new_callable=AsyncMock,
            side_effect=asyncio.TimeoutError(),
        ):
            result = await protocol.send_heartbeat()

        assert result is False


# ============================================================================
# Coverage gap: IPCStream.close() stdout drain exception handler
# ============================================================================


class TestIPCCloseDrainException:
    """close() handles exception during stdout drain loop (lines 152-153)."""

    @pytest.mark.asyncio
    async def test_close_handles_stdout_read_timeout(self) -> None:
        """close() does not raise when stdout.read times out during drain."""
        mock_stdin = MagicMock()
        mock_stdin.is_closing.return_value = False
        mock_stdin.close = MagicMock()
        mock_stdin.wait_closed = AsyncMock()
        mock_stdout = MagicMock()
        mock_stdout.read = AsyncMock(side_effect=asyncio.TimeoutError)

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        await stream.close()

        mock_stdin.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_handles_stdout_read_exception(self) -> None:
        """close() does not raise when stdout.read raises generic exception during drain."""
        mock_stdin = MagicMock()
        mock_stdin.is_closing.return_value = False
        mock_stdin.close = MagicMock()
        mock_stdin.wait_closed = AsyncMock()
        mock_stdout = MagicMock()
        mock_stdout.read = AsyncMock(side_effect=OSError("pipe error"))

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        await stream.close()

        mock_stdin.close.assert_called_once()


# ============================================================================
# Coverage gap: IPCProtocol.stream property
# ============================================================================


class TestIPCProtocolStreamProperty:
    """IPCProtocol.stream property returns the underlying stream (line 184)."""

    @pytest.mark.asyncio
    async def test_stream_property_returns_underlying_stream(self) -> None:
        """stream property exposes the IPCStream instance."""
        mock_stdin = MagicMock()
        mock_stdout = MagicMock()
        stream = IPCStream(mock_stdin, mock_stdout)
        protocol = IPCProtocol(stream)

        assert protocol.stream is stream


# ============================================================================
# Iteration 85: IPC RuntimeError catch during drain
# ============================================================================


class TestIPCSendDrainRuntimeError:
    """send() catches RuntimeError during drain and raises IPCConnectionError."""

    @pytest.mark.asyncio
    async def test_drain_runtime_error_raises_ipc_connection_error(self) -> None:
        """RuntimeError('Transport is closing') during drain is wrapped as IPCConnectionError."""
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock(side_effect=RuntimeError("Transport is closing"))
        mock_stdout = MagicMock()

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hi")

        with pytest.raises(IPCConnectionError, match="stdin closed during drain"):
            await stream.send(msg)


class TestIPCMessageNonDictResolve:
    """IPCMessage._resolve_payload returns values as-is when not a dict."""

    def test_non_dict_values_passed_through(self) -> None:
        from agent_nexus.models.ipc import IPCMessage

        # The "before" model_validator early-returns non-dict values at line 105.
        result = IPCMessage._resolve_payload("not-a-dict")
        assert result == "not-a-dict"

        result2 = IPCMessage._resolve_payload(42)
        assert result2 == 42


# ---------------------------------------------------------------------------
# iter122 regression: receive timeout=0 clamped to 0.1
# ---------------------------------------------------------------------------


class TestIPCReceiveTimeoutZeroClamped:
    """IPCStream.receive with timeout=0 is clamped to 0.1."""

    async def test_receive_timeout_zero_clamped(self) -> None:
        """timeout=0 does not immediately raise IPCTimeoutError."""
        from unittest.mock import AsyncMock, MagicMock

        mock_stdout = MagicMock()
        # Make readline return a valid message after a brief delay
        msg = AgentToPlatform(type=AgentToPlatformType.RESULT, content="ok")
        raw_line = msg.model_dump_json() + "\n"
        mock_stdout.readline = AsyncMock(return_value=raw_line.encode("utf-8"))

        mock_stdin = MagicMock()
        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        result = await stream.receive(timeout=0)
        assert result.type == AgentToPlatformType.RESULT

    async def test_receive_timeout_negative_clamped(self) -> None:
        """timeout=-1 does not immediately raise IPCTimeoutError."""
        from unittest.mock import AsyncMock, MagicMock

        mock_stdout = MagicMock()
        msg = AgentToPlatform(type=AgentToPlatformType.RESULT, content="ok")
        raw_line = msg.model_dump_json() + "\n"
        mock_stdout.readline = AsyncMock(return_value=raw_line.encode("utf-8"))

        mock_stdin = MagicMock()
        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        result = await stream.receive(timeout=-1)
        assert result.type == AgentToPlatformType.RESULT


class TestIPCTimeoutErrorChain:
    """IPCTimeoutError preserves exception chain from asyncio.TimeoutError."""

    @pytest.mark.asyncio
    async def test_timeout_error_has_cause(self) -> None:
        hanging_readline = AsyncMock(side_effect=asyncio.TimeoutError())
        stream = IPCStream(
            stdin=MagicMock(),
            stdout=MagicMock(readline=hanging_readline),
        )
        with pytest.raises(IPCTimeoutError) as exc_info:
            await stream.receive(timeout=1.0)
        assert exc_info.value.__cause__ is not None
        assert isinstance(exc_info.value.__cause__, asyncio.TimeoutError)


# ---------------------------------------------------------------------------
# iter128 regression: close_sync() releases stdout FD transport
# ---------------------------------------------------------------------------


class TestCloseSyncStdoutFD:
    """close_sync() must close both stdin StreamWriter and stdout transport."""

    def test_close_sync_closes_stdin(self) -> None:
        mock_stdin = MagicMock()
        mock_stdin.is_closing.return_value = False
        mock_stdout = MagicMock()
        mock_stdout._transport = MagicMock()
        mock_stdout._transport.is_closing.return_value = False

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        stream.close_sync()

        mock_stdin.close.assert_called_once()

    def test_close_sync_closes_stdout_transport(self) -> None:
        """close_sync() must close the stdout transport, not just stdin."""
        mock_stdin = MagicMock()
        mock_stdin.is_closing.return_value = False
        mock_transport = MagicMock()
        mock_transport.is_closing.return_value = False
        mock_stdout = MagicMock()
        mock_stdout._transport = mock_transport

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        stream.close_sync()

        mock_transport.close.assert_called_once()

    def test_close_sync_no_transport_does_not_crash(self) -> None:
        """If stdout has no _transport attribute, close_sync still succeeds."""
        mock_stdin = MagicMock()
        mock_stdin.is_closing.return_value = False
        mock_stdout = MagicMock(spec=["readline", "read"])
        # No _transport attribute

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        stream.close_sync()  # Should not raise

        mock_stdin.close.assert_called_once()
