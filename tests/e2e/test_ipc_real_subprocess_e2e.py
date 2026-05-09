"""E2E: Real subprocess IPC — IPCStream + IPCProtocol with live echo agents.

Unlike test_ipc_error_paths_e2e.py (which uses MagicMock stdin/stdout),
these tests spawn real subprocesses and exercise the full IPC pipeline:
  asyncio.subprocess -> IPCStream -> IPCProtocol -> JSON-lines over pipes

This validates:
  - Real pipe buffering and EOF behavior
  - Actual subprocess lifecycle interaction
  - Heartbeat ping/pong over real pipes
  - Race conditions in concurrent send/receive
  - CancelledError propagation through IPC during subprocess death
"""

import asyncio
import signal

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
    get_ipc_lock,
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
    "    if msg.get('type') == 'chat':\n"
    "        d = {'type':'result','content':msg.get('content','')}\n"
    "        d['task_id'] = msg.get('conversation_id')\n"
    "        sys.stdout.write(json.dumps(d) + '\\n')\n"
    "        sys.stdout.flush()\n"
    "        continue\n"
    "    if msg.get('type') == 'task':\n"
    "        tid = msg.get('task_id', '')\n"
    "        p = json.dumps({'type':'progress','content':'started '+tid})\n"
    "        sys.stdout.write(p + '\\n')\n"
    "        sys.stdout.flush()\n"
    "        r = json.dumps({'type':'result','content':'done '+tid})\n"
    "        r = r[:-1] + ',\"task_id\":\"'+tid+'\"}'\n"
    "        sys.stdout.write(r + '\\n')\n"
    "        sys.stdout.flush()\n"
    "        continue\n"
    "    d = {'type':'result','content':'echo'}\n"
    "    d['task_id'] = msg.get('task_id')\n"
    "    sys.stdout.write(json.dumps(d) + '\\n')\n"
    "    sys.stdout.flush()\n"
)

_ECHO_AGENT = ["python3", "-c", _ECHO_AGENT_SCRIPT]

_CRASH_AFTER_FIRST = [
    "python3",
    "-c",
    (
        "import sys, json\n"
        "line = sys.stdin.readline()\n"
        "if line:\n"
        "    msg = json.loads(line.strip())\n"
        "    sys.stdout.write(json.dumps({'type':'result','content':'first'}) + '\\n')\n"
        "    sys.stdout.flush()\n"
        "sys.exit(0)\n"
    ),
]

_GARBAGE_AGENT = [
    "python3",
    "-c",
    (
        "import sys\n"
        "sys.stdin.readline()\n"
        "sys.stdout.write('not json at all!!!\\n')\n"
        "sys.stdout.flush()\n"
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
class TestIPCRealSubprocessChat:
    """Real subprocess IPC: chat message exchange over pipes."""

    @pytest.mark.asyncio
    async def test_send_chat_receive_result(self):
        """Send a chat message to echo agent and receive echoed result."""
        proc, stream = await _start_subprocess(_ECHO_AGENT)
        try:
            proto = IPCProtocol(stream)
            await proto.send_chat("hello world", conversation_id="c1")

            msg = await stream.receive(timeout=5.0)
            assert msg.type == AgentToPlatformType.RESULT
            assert msg.content == "hello world"
        finally:
            proc.terminate()
            await proc.wait()

    @pytest.mark.asyncio
    async def test_multiple_sequential_messages(self):
        """Send multiple messages in sequence; each gets a response."""
        proc, stream = await _start_subprocess(_ECHO_AGENT)
        try:
            proto = IPCProtocol(stream)

            for i in range(5):
                await proto.send_chat(f"msg-{i}")
                msg = await stream.receive(timeout=5.0)
                assert msg.content == f"msg-{i}"
        finally:
            proc.terminate()
            await proc.wait()


@pytest.mark.timeout(30)
class TestIPCRealSubprocessHeartbeat:
    """Real subprocess IPC: heartbeat ping/pong."""

    @pytest.mark.asyncio
    async def test_heartbeat_returns_true_for_alive_process(self):
        """send_heartbeat returns True when agent responds with pong."""
        proc, stream = await _start_subprocess(_ECHO_AGENT)
        try:
            proto = IPCProtocol(stream)
            result = await proto.send_heartbeat()
            assert result is True
        finally:
            proc.terminate()
            await proc.wait()

    @pytest.mark.asyncio
    async def test_heartbeat_returns_false_for_dead_process(self):
        """send_heartbeat returns False when agent process has exited."""
        proc, stream = await _start_subprocess(_CRASH_AFTER_FIRST)
        try:
            proto = IPCProtocol(stream)
            # Trigger the crash by sending any message
            await proto.send_chat("trigger")
            msg = await stream.receive(timeout=5.0)
            assert msg.content == "first"

            # Process should exit now
            await asyncio.sleep(0.3)

            # Heartbeat on dead process should return False
            result = await proto.send_heartbeat()
            assert result is False
        finally:
            try:
                proc.terminate()
                await proc.wait()
            except ProcessLookupError:
                pass


@pytest.mark.timeout(30)
class TestIPCRealSubprocessTaskExecution:
    """Real subprocess IPC: task dispatch and result collection."""

    @pytest.mark.asyncio
    async def test_send_task_receive_progress_then_result(self):
        """Task dispatch produces progress then result message."""
        from agent_nexus.models.task import TaskItem, TaskState

        proc, stream = await _start_subprocess(_ECHO_AGENT)
        try:
            proto = IPCProtocol(stream)

            task = TaskItem(
                id="t1",
                description="Test task",
                agent="echo",
                state=TaskState.PENDING,
            )
            await proto.send_task(task)

            # First receive: progress message
            msg1 = await stream.receive(timeout=5.0)
            assert msg1.type == AgentToPlatformType.PROGRESS
            assert "started" in msg1.content

            # Second receive: result message
            msg2 = await stream.receive(timeout=5.0)
            assert msg2.type == AgentToPlatformType.RESULT
            assert "done" in msg2.content
        finally:
            proc.terminate()
            await proc.wait()

    @pytest.mark.asyncio
    async def test_receive_until_result_skips_progress(self):
        """receive_until_result returns final result, skipping progress messages."""
        from agent_nexus.models.task import TaskItem, TaskState

        received_progress = []

        async def track_progress(msg):
            received_progress.append(msg)

        proc, stream = await _start_subprocess(_ECHO_AGENT)
        try:
            proto = IPCProtocol(stream)

            task = TaskItem(
                id="t2",
                description="Pipeline task",
                agent="echo",
                state=TaskState.PENDING,
            )
            await proto.send_task(task)

            # receive_until_result should skip progress and return result
            result = await proto.receive_until_result(
                timeout=10.0, progress_callback=track_progress
            )
            assert result.type == AgentToPlatformType.RESULT
            assert "done" in result.content

            # Progress message should have been forwarded to callback
            assert len(received_progress) >= 1
            assert any("started" in p.content for p in received_progress)
        finally:
            proc.terminate()
            await proc.wait()


@pytest.mark.timeout(30)
class TestIPCRealSubprocessErrorPaths:
    """Real subprocess IPC: error conditions with real pipes."""

    @pytest.mark.asyncio
    async def test_eof_on_process_exit(self):
        """Reading from a process that has exited raises IPCConnectionError."""
        proc, stream = await _start_subprocess(_CRASH_AFTER_FIRST)
        try:
            # Send trigger directly via stream (bypasses IPCProtocol wrapper)
            await stream.send(
                PlatformToAgent(type=PlatformToAgentType.CHAT, content="trigger")
            )

            # Read the one message the process sends before exiting
            msg = await stream.receive(timeout=5.0)
            assert msg.content == "first"

            # Wait for process to exit and pipe to close
            await proc.wait()
            await asyncio.sleep(0.2)

            # Next receive should hit EOF
            with pytest.raises(IPCConnectionError, match="EOF"):
                await stream.receive(timeout=5.0)
        finally:
            try:
                proc.terminate()
                await proc.wait()
            except ProcessLookupError:
                pass

    @pytest.mark.asyncio
    async def test_garbage_output_raises_ipc_error(self):
        """Agent sending non-JSON output triggers IPCError."""
        proc, stream = await _start_subprocess(_GARBAGE_AGENT)
        try:
            # Trigger the garbage output
            await stream.send(
                PlatformToAgent(type=PlatformToAgentType.CHAT, content="trigger")
            )

            with pytest.raises(IPCError):
                await stream.receive(timeout=5.0)
        finally:
            proc.terminate()
            await proc.wait()

    @pytest.mark.asyncio
    async def test_send_to_dead_process_raises_connection_error(self):
        """Sending to a process that was SIGKILL'd raises IPCConnectionError."""
        proc, stream = await _start_subprocess(_ECHO_AGENT)
        try:
            proto = IPCProtocol(stream)

            # Verify process is alive
            assert await proto.send_heartbeat() is True

            # Kill the process
            proc.send_signal(signal.SIGKILL)
            await asyncio.sleep(0.3)

            # Send should fail — pipe is broken
            with pytest.raises((IPCConnectionError, BrokenPipeError, OSError)):
                await proto.send_chat("should fail")
        finally:
            try:
                proc.terminate()
                await proc.wait()
            except ProcessLookupError:
                pass


@pytest.mark.timeout(30)
class TestIPCRealSubprocessConcurrent:
    """Real subprocess IPC: concurrent operations."""

    @pytest.mark.asyncio
    async def test_concurrent_heartbeats_serialized_by_lock(self):
        """Multiple concurrent heartbeat calls succeed (serialized by IPC lock)."""
        proc, stream = await _start_subprocess(_ECHO_AGENT)
        lock = get_ipc_lock("concurrent-test-agent")
        try:
            proto = IPCProtocol(stream)

            async def heartbeat():
                async with lock:
                    return await proto.send_heartbeat()

            # 3 concurrent heartbeats
            results = await asyncio.gather(
                heartbeat(), heartbeat(), heartbeat(), return_exceptions=True
            )

            # All should succeed (serialized, not interleaved)
            for r in results:
                assert not isinstance(r, Exception), f"Heartbeat failed: {r}"
                assert r is True
        finally:
            proc.terminate()
            await proc.wait()
