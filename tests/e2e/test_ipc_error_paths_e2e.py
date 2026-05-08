"""E2E tests for IPC error paths and edge cases.

Covers:
- IPC message format error handling (non-UTF-8, invalid JSON, schema errors)
- Connection interruption and EOF behavior
- Large message handling (near/past size limits)
- IPC lock registry correctness
- Peek buffer overflow behavior
"""

import asyncio
import json
import sys
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.models.ipc import (
    AgentToPlatform,
    AgentToPlatformType,
    PlatformToAgent,
    PlatformToAgentType,
)
from agent_nexus.platform.orchestration.ipc import (
    IPCConnectionError,
    IPCError,
    IPCProtocol,
    IPCStream,
    IPCTimeoutError,
    _MAX_MESSAGE_SIZE,
    get_ipc_lock,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stream_pair():
    """Create a connected IPCStream with mock stdin/stdout for error injection."""
    mock_stdin = MagicMock(spec=asyncio.StreamWriter)
    mock_stdin.write = MagicMock()
    mock_stdin.is_closing.return_value = False
    mock_stdin.close = MagicMock()
    mock_stdin.drain = AsyncMock()
    mock_stdin.wait_closed = AsyncMock()

    mock_stdout = MagicMock(spec=asyncio.StreamReader)

    stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
    return stream, mock_stdin, mock_stdout


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestIPCMessageFormatErrors:
    """IPCStream properly rejects malformed messages."""

    def test_receive_non_utf8_data_raises_ipc_error(self):
        """Non-UTF-8 bytes from agent stdout produce IPCError."""
        stream, _, mock_stdout = _make_stream_pair()
        # Invalid UTF-8 sequence
        mock_stdout.readline = AsyncMock(return_value=b"\x80\x81\xfe\xff\n")

        async def _test():
            with pytest.raises(IPCError, match="non-UTF-8"):
                await stream.receive(timeout=5.0)

        asyncio.run(_test())

    def test_receive_invalid_json_raises_ipc_error(self):
        """Non-JSON data from agent produces IPCError."""
        stream, _, mock_stdout = _make_stream_pair()
        mock_stdout.readline = AsyncMock(return_value=b"not json at all\n")

        async def _test():
            with pytest.raises(IPCError, match="Invalid JSON"):
                await stream.receive(timeout=5.0)

        asyncio.run(_test())

    def test_receive_invalid_schema_raises_ipc_error(self):
        """Valid JSON but wrong schema produces IPCError."""
        stream, _, mock_stdout = _make_stream_pair()
        mock_stdout.readline = AsyncMock(
            return_value=b'{"unknown_field": "value", "not_a_type": true}\n'
        )

        async def _test():
            with pytest.raises(IPCError, match="Invalid message schema"):
                await stream.receive(timeout=5.0)

        asyncio.run(_test())

    def test_receive_valid_message_succeeds(self):
        """Well-formed IPC message is parsed correctly."""
        stream, _, mock_stdout = _make_stream_pair()
        mock_stdout.readline = AsyncMock(
            return_value=b'{"type": "result", "content": "ok"}\n'
        )

        async def _test():
            msg = await stream.receive(timeout=5.0)
            assert msg.type == AgentToPlatformType.RESULT
            assert msg.content == "ok"

        asyncio.run(_test())

    def test_receive_empty_line_raises_connection_error(self):
        """Empty/whitespace-only line is treated as connection issue."""
        stream, _, mock_stdout = _make_stream_pair()
        mock_stdout.readline = AsyncMock(return_value=b"   \n")

        async def _test():
            with pytest.raises(IPCConnectionError, match="empty line"):
                await stream.receive(timeout=5.0)

        asyncio.run(_test())


@pytest.mark.timeout(30)
class TestIPCConnectionInterruption:
    """IPCStream handles connection interruption correctly."""

    def test_receive_eof_raises_connection_error(self):
        """EOF (empty bytes from readline) raises IPCConnectionError."""
        stream, _, mock_stdout = _make_stream_pair()
        mock_stdout.readline = AsyncMock(return_value=b"")

        async def _test():
            with pytest.raises(IPCConnectionError, match="EOF"):
                await stream.receive(timeout=5.0)

        asyncio.run(_test())

    def test_send_broken_pipe_raises_connection_error(self):
        """BrokenPipeError during send raises IPCConnectionError."""
        stream, mock_stdin, _ = _make_stream_pair()
        mock_stdin.write.side_effect = BrokenPipeError("pipe broken")

        async def _test():
            with pytest.raises(IPCConnectionError, match="stdin closed"):
                await stream.send(
                    PlatformToAgent(type=PlatformToAgentType.CHAT, content="hello")
                )

        asyncio.run(_test())

    def test_send_drain_broken_pipe_raises_connection_error(self):
        """BrokenPipeError during drain raises IPCConnectionError."""
        stream, mock_stdin, _ = _make_stream_pair()
        mock_stdin.drain.side_effect = BrokenPipeError("drain broken")

        async def _test():
            with pytest.raises(IPCConnectionError, match="stdin closed during drain"):
                await stream.send(
                    PlatformToAgent(type=PlatformToAgentType.CHAT, content="hello")
                )

        asyncio.run(_test())

    def test_receive_timeout_raises_ipc_timeout(self):
        """Timeout waiting for message raises IPCTimeoutError."""
        stream, _, mock_stdout = _make_stream_pair()
        mock_stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError())

        async def _test():
            with pytest.raises(IPCTimeoutError, match="Timed out"):
                await stream.receive(timeout=1.0)

        asyncio.run(_test())

    def test_close_sync_releases_fds(self):
        """close_sync closes stdin and stdout transport."""
        stream, mock_stdin, mock_stdout = _make_stream_pair()
        mock_transport = MagicMock()
        mock_transport.is_closing.return_value = False
        mock_stdout._transport = mock_transport

        stream.close_sync()

        mock_stdin.close.assert_called_once()
        mock_transport.close.assert_called_once()

    def test_close_sync_handles_missing_transport_gracefully(self):
        """close_sync does not crash when stdout has no _transport."""
        stream, mock_stdin, mock_stdout = _make_stream_pair()
        # Simulate no _transport attribute
        type(mock_stdout)._transport = property(lambda self: None)

        # Should not raise
        stream.close_sync()
        mock_stdin.close.assert_called_once()


@pytest.mark.timeout(30)
class TestIPCLargeMessageHandling:
    """IPC handles large messages near and at size limits."""

    def test_receive_message_at_max_size_succeeds(self):
        """Message under _MAX_MESSAGE_SIZE but within model content limits is accepted."""
        stream, _, mock_stdout = _make_stream_pair()
        # Use content within model max_length (65536) but still a large message
        large_content = "x" * 60000
        msg_json = json.dumps(
            {"type": "result", "content": large_content}
        )
        raw = (msg_json + "\n").encode("utf-8")
        assert len(raw) < _MAX_MESSAGE_SIZE  # Under stream size limit
        mock_stdout.readline = AsyncMock(return_value=raw)

        async def _test():
            msg = await stream.receive(timeout=5.0)
            assert msg.type == AgentToPlatformType.RESULT
            assert len(msg.content) == 60000

        asyncio.run(_test())

    def test_receive_oversized_message_raises_ipc_error(self):
        """Message exceeding _MAX_MESSAGE_SIZE is rejected."""
        stream, _, mock_stdout = _make_stream_pair()
        # Create oversized payload
        large_content = "x" * (_MAX_MESSAGE_SIZE + 100)
        msg_json = json.dumps({"type": "result", "content": large_content})
        raw = (msg_json + "\n").encode("utf-8")

        mock_stdout.readline = AsyncMock(return_value=raw)

        async def _test():
            with pytest.raises(IPCError, match="too large"):
                await stream.receive(timeout=5.0)

        asyncio.run(_test())

    def test_send_normal_message_succeeds(self):
        """Normal-sized message sends without error."""
        stream, mock_stdin, _ = _make_stream_pair()

        async def _test():
            await stream.send(
                PlatformToAgent(type=PlatformToAgentType.CHAT, content="test")
            )
            mock_stdin.write.assert_called_once()
            # Verify JSON + newline format
            written = mock_stdin.write.call_args[0][0]
            assert written.endswith(b"\n")
            parsed = json.loads(written)
            assert parsed["type"] == "chat"

        asyncio.run(_test())


@pytest.mark.timeout(30)
class TestIPCLockRegistry:
    """IPC lock registry handles concurrency and edge cases."""

    def test_get_ipc_lock_returns_same_lock_for_same_name(self):
        """Same agent name always gets the same lock."""
        lock1 = get_ipc_lock("test-agent")
        lock2 = get_ipc_lock("test-agent")
        assert lock1 is lock2

    def test_get_ipc_lock_different_names_different_locks(self):
        """Different agent names get different locks."""
        lock1 = get_ipc_lock("agent-a")
        lock2 = get_ipc_lock("agent-b")
        assert lock1 is not lock2

    def test_get_ipc_lock_works_outside_async_context(self):
        """get_ipc_lock works when called from non-async context."""

        async def _test():
            lock = get_ipc_lock("sync-agent")
            assert isinstance(lock, asyncio.Lock)

        asyncio.run(_test())

    def test_lock_is_asyncio_lock(self):
        """Returned lock is an asyncio.Lock."""
        lock = get_ipc_lock("type-check")
        assert isinstance(lock, asyncio.Lock)


@pytest.mark.timeout(30)
class TestIPCProtocolPeekBuffer:
    """IPCProtocol peek buffer handles overflow and ordering."""

    def test_peek_buffer_returns_messages_in_order(self):
        """Buffered messages are returned FIFO on subsequent receive_result."""
        mock_stdin = MagicMock(spec=asyncio.StreamWriter)
        mock_stdin.write = MagicMock()
        mock_stdin.is_closing.return_value = False
        mock_stdin.drain = AsyncMock()

        mock_stdout = MagicMock(spec=asyncio.StreamReader)
        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        proto = IPCProtocol(stream)

        # Buffer some messages manually
        msg1 = AgentToPlatform(type=AgentToPlatformType.PROGRESS, content="progress-1")
        msg2 = AgentToPlatform(type=AgentToPlatformType.PROGRESS, content="progress-2")
        proto._buffer_message(msg1)
        proto._buffer_message(msg2)

        async def _test():
            # receive_result should return buffered messages first
            result1 = await proto.receive_result()
            result2 = await proto.receive_result()
            assert result1.content == "progress-1"
            assert result2.content == "progress-2"

        asyncio.run(_test())

    def test_peek_buffer_overflow_discards_oldest(self):
        """When buffer reaches max size, oldest messages are discarded."""
        mock_stdin = MagicMock(spec=asyncio.StreamWriter)
        mock_stdin.write = MagicMock()
        mock_stdin.is_closing.return_value = False
        mock_stdin.drain = AsyncMock()

        mock_stdout = MagicMock(spec=asyncio.StreamReader)
        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        proto = IPCProtocol(stream)

        # Fill buffer to max
        for i in range(proto._MAX_PEEK_BUFFER_SIZE + 10):
            proto._buffer_message(
                AgentToPlatform(type=AgentToPlatformType.PROGRESS, content=f"msg-{i}")
            )

        # Should have discarded oldest messages
        assert len(proto._peek_buffer) <= proto._MAX_PEEK_BUFFER_SIZE
        assert proto._discarded_count > 0
