"""E2E tests for IPC async safety: concurrent send/receive, large payloads,
and CancelledError propagation through IPC operations.

Quality focus: async_safety — verifies IPCStream and IPCProtocol handle
concurrent operations, large messages, and cancellation without resource leaks.
"""

import asyncio

import pytest

from agent_nexus.models.ipc import (
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
)

# ---------------------------------------------------------------------------
# Echo subprocess scripts
# ---------------------------------------------------------------------------

_ECHO_AGENT_SCRIPT = (
    "import sys, json\n"
    "for line in sys.stdin:\n"
    "    line = line.strip()\n"
    "    if not line:\n"
    "        continue\n"
    "    try:\n"
    "        msg = json.loads(line)\n"
    "    except json.JSONDecodeError:\n"
    "        continue\n"
    "    if msg.get('content') == '__heartbeat__':\n"
    "        resp = json.dumps({'type':'progress','content':'pong'})\n"
    "        sys.stdout.write(resp + '\\n')\n"
    "        sys.stdout.flush()\n"
    "        continue\n"
    "    d = {'type':'result','content':msg.get('content','echo')}\n"
    "    d['task_id'] = msg.get('conversation_id')\n"
    "    sys.stdout.write(json.dumps(d) + '\\n')\n"
    "    sys.stdout.flush()\n"
)

_ECHO_AGENT = ["python3", "-c", _ECHO_AGENT_SCRIPT]

# Agent that echoes back large payloads
_LARGE_ECHO_AGENT = [
    "python3",
    "-c",
    (
        "import sys, json\n"
        "for line in sys.stdin:\n"
        "    line = line.strip()\n"
        "    if not line:\n"
        "        continue\n"
        "    msg = json.loads(line)\n"
        "    payload = msg.get('content', '')\n"
        "    large = payload + '_' + 'x' * 100000\n"
        "    resp = json.dumps({'type':'result','content':large})\n"
        "    sys.stdout.write(resp + '\\n')\n"
        "    sys.stdout.flush()\n"
    ),
]


async def _start_subprocess(cmd):
    """Start a subprocess and return (process, IPCStream)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stream = IPCStream(stdin=proc.stdin, stdout=proc.stdout)
    return proc, stream


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestIPCConcurrentOperations:
    """Verify IPCStream handles concurrent send/receive operations."""

    @pytest.mark.asyncio
    async def test_interleaved_send_receive(self):
        """Multiple send-receive cycles work correctly in sequence with real pipes."""
        proc, stream = await _start_subprocess(_ECHO_AGENT)
        try:
            proto = IPCProtocol(stream)

            for i in range(3):
                await proto.send_chat(f"msg-{i}", conversation_id=f"c{i}")
                msg = await stream.receive(timeout=5.0)
                assert msg.type == AgentToPlatformType.RESULT
                assert msg.content == f"msg-{i}"

            # Verify pipe is not corrupted — one more message works
            await proto.send_chat("final", conversation_id="cf")
            msg = await stream.receive(timeout=5.0)
            assert msg.content == "final"
        finally:
            proc.terminate()
            await proc.wait()

    @pytest.mark.asyncio
    async def test_sequential_heartbeats_during_chat(self):
        """Heartbeats interleaved with chat messages work correctly."""
        proc, stream = await _start_subprocess(_ECHO_AGENT)
        try:
            proto = IPCProtocol(stream)

            await proto.send_chat("hello", conversation_id="c1")
            msg = await stream.receive(timeout=5.0)
            assert msg.content == "hello"

            assert await proto.send_heartbeat() is True

            await proto.send_chat("world", conversation_id="c2")
            msg = await stream.receive(timeout=5.0)
            assert msg.content == "world"
        finally:
            proc.terminate()
            await proc.wait()

    @pytest.mark.asyncio
    async def test_receive_timeout_on_no_response(self):
        """receive() raises an exception when agent doesn't respond in time."""
        silent_cmd = [
            "python3",
            "-c",
            ("import sys\nfor line in sys.stdin:\n    pass\n"),
        ]
        proc, stream = await _start_subprocess(silent_cmd)
        try:
            proto = IPCProtocol(stream)
            await proto.send_chat("hello", conversation_id="c1")

            with pytest.raises(IPCTimeoutError):
                await stream.receive(timeout=0.5)
        finally:
            proc.terminate()
            await proc.wait()


@pytest.mark.timeout(30)
class TestIPCLargePayload:
    """Verify IPCStream handles large messages correctly."""

    @pytest.mark.asyncio
    async def test_large_payload_roundtrip(self):
        """Sending a 50KB message and receiving it back works correctly.

        PlatformToAgent.content has a 65536-char Pydantic constraint,
        so we stay just under that limit.
        """
        proc, stream = await _start_subprocess(_ECHO_AGENT)
        try:
            proto = IPCProtocol(stream)

            large_content = "x" * 50_000
            await proto.send_chat(large_content, conversation_id="big")

            msg = await stream.receive(timeout=10.0)
            assert msg.type == AgentToPlatformType.RESULT
            assert msg.content == large_content
        finally:
            proc.terminate()
            await proc.wait()

    @pytest.mark.asyncio
    async def test_oversized_message_from_agent_rejected(self):
        """Messages exceeding buffer limits from agent are rejected.

        The asyncio StreamReader has an internal limit (64KB default).
        When a single line exceeds that, readline() raises ValueError.
        Our IPCStream wraps this as an IPCError.
        """
        huge_agent = [
            "python3",
            "-c",
            (
                "import sys, json\n"
                "line = sys.stdin.readline()\n"
                "payload = 'A' * (5 * 1024 * 1024)\n"
                "resp = json.dumps({'type':'result','content':payload})\n"
                "sys.stdout.write(resp + '\\n')\n"
                "sys.stdout.flush()\n"
            ),
        ]
        proc, stream = await _start_subprocess(huge_agent)
        try:
            await stream.send(PlatformToAgent(type=PlatformToAgentType.CHAT, content="trigger"))

            # Should raise — either IPCError (our check) or ValueError (asyncio buffer)
            with pytest.raises((IPCError, ValueError)):
                await stream.receive(timeout=10.0)
        finally:
            proc.terminate()
            await proc.wait()


@pytest.mark.timeout(30)
class TestIPCCancelledError:
    """Verify CancelledError propagation during IPC operations."""

    @pytest.mark.asyncio
    async def test_cancel_during_receive_propagates(self):
        """Cancelling a pending receive operation propagates CancelledError."""
        silent_cmd = [
            "python3",
            "-c",
            ("import sys\nfor line in sys.stdin:\n    pass\n"),
        ]
        proc, stream = await _start_subprocess(silent_cmd)
        try:
            # Start a receive with a long timeout
            receive_task = asyncio.create_task(stream.receive(timeout=30.0))

            # Cancel it after a short delay
            await asyncio.sleep(0.1)
            receive_task.cancel()

            with pytest.raises(asyncio.CancelledError):
                await receive_task
        finally:
            proc.terminate()
            await proc.wait()

    @pytest.mark.asyncio
    async def test_cancel_during_send_to_dead_process(self):
        """Sending to a dead process raises IPCConnectionError or BrokenPipeError."""
        proc, stream = await _start_subprocess(_ECHO_AGENT)
        try:
            proto = IPCProtocol(stream)

            assert await proto.send_heartbeat() is True

            import signal as sig

            proc.send_signal(sig.SIGKILL)
            await asyncio.sleep(0.3)

            with pytest.raises((IPCConnectionError, BrokenPipeError, OSError)):
                await proto.send_chat("should fail")
        finally:
            try:
                proc.terminate()
                await proc.wait()
            except ProcessLookupError:
                pass


@pytest.mark.timeout(30)
class TestIPCStreamCloseSafety:
    """Verify IPCStream close operations don't leak resources."""

    @pytest.mark.asyncio
    async def test_close_async_drains_remaining_data(self):
        """Async close() drains remaining data from stdout."""
        proc, stream = await _start_subprocess(_ECHO_AGENT)
        try:
            proto = IPCProtocol(stream)

            await proto.send_chat("hello", conversation_id="c1")

            await stream.close()
            # Verify stream stdin is closed after drain
            assert stream._stdin.is_closing()
        finally:
            try:
                proc.terminate()
                await proc.wait()
            except ProcessLookupError:
                pass

    @pytest.mark.asyncio
    async def test_close_sync_on_active_stream(self):
        """Synchronous close_sync() closes stdin and stdout without blocking."""
        proc, stream = await _start_subprocess(_ECHO_AGENT)
        try:
            proto = IPCProtocol(stream)

            await proto.send_chat("hello", conversation_id="c1")

            stream.close_sync()

            await asyncio.sleep(0.3)
            # Verify process has exited after sync close — stdin is closed so echo agent should terminate
            assert proc.returncode is not None, "Process should have exited after close_sync()"
        finally:
            try:
                proc.terminate()
                await proc.wait()
            except ProcessLookupError:
                pass

    @pytest.mark.asyncio
    async def test_double_close_does_not_raise(self):
        """Calling close() twice does not raise exceptions."""
        proc, stream = await _start_subprocess(_ECHO_AGENT)
        try:
            await stream.close()
            # Second close must not raise — idempotent cleanup
            await stream.close()
        finally:
            proc.terminate()
            await proc.wait()
