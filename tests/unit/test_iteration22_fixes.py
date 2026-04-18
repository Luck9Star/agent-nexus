"""Tests for iteration 22 bug fixes.

Fixes:
1. IPC send() drain() now has a 5s timeout (was unbounded)
2. get_tools() deduplicates by tool name (was silent overwrite)
3. _execute_single_agent wraps IPC errors in RuntimeError
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_nexus.models.ipc import AgentToPlatform, AgentToPlatformType, PlatformToAgent, PlatformToAgentType
from agent_nexus.platform.orchestration.ipc import IPCStream
from agent_nexus.platform.router.router import PlatformRouter


def _make_mock_streams() -> tuple[MagicMock, MagicMock]:
    """Create mock stdin/stdout with proper EOF behavior."""
    mock_stdin = MagicMock()
    mock_stdin.write = MagicMock()
    mock_stdin.drain = AsyncMock()
    mock_stdout = MagicMock()
    mock_stdout.read = AsyncMock(return_value=b"")
    mock_stdout.readline = AsyncMock(return_value=b"")
    return mock_stdin, mock_stdout


def _make_router_with_mock_pm() -> tuple[PlatformRouter, MagicMock]:
    """Create a PlatformRouter with a mocked ProcessManager."""
    mock_pm = MagicMock()
    router = PlatformRouter.__new__(PlatformRouter)
    router._pm = mock_pm
    return router, mock_pm


# ---------------------------------------------------------------------------
# Fix 1: IPC send() drain timeout
# ---------------------------------------------------------------------------


class TestIPCSendDrainTimeout:
    """send() must not block indefinitely on drain()."""

    @pytest.mark.asyncio
    async def test_send_drain_timeout_raises(self) -> None:
        """If drain() blocks beyond 5s, send() raises TimeoutError."""
        mock_stdin, mock_stdout = _make_mock_streams()
        mock_stdin.drain = AsyncMock(side_effect=asyncio.TimeoutError)

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hi")

        with pytest.raises(asyncio.TimeoutError):
            await stream.send(msg)

    @pytest.mark.asyncio
    async def test_send_drain_succeeds_within_timeout(self) -> None:
        """send() completes when drain() resolves within timeout."""
        mock_stdin, mock_stdout = _make_mock_streams()

        stream = IPCStream(stdin=mock_stdin, stdout=mock_stdout)
        msg = PlatformToAgent(type=PlatformToAgentType.CHAT, content="hi")
        await stream.send(msg)

        mock_stdin.write.assert_called_once()


# ---------------------------------------------------------------------------
# Fix 2: get_tools() deduplication
# ---------------------------------------------------------------------------


class TestGetToolsDeduplication:
    """get_tools() must not silently overwrite tools with the same name."""

    @pytest.mark.asyncio
    async def test_duplicate_tools_deduplicated(self) -> None:
        """Two agents with same tool name: second is skipped."""
        import json
        router, mock_pm = _make_router_with_mock_pm()

        tool_a = {"name": "search", "description": "Agent A search"}
        tool_b = {"name": "search", "description": "Agent B search"}

        agent_a_handle = MagicMock()
        agent_a_handle.is_alive = True
        agent_a_handle.ipc = AsyncMock()
        agent_a_handle.ipc.send_chat = AsyncMock()
        agent_a_handle.ipc.receive_until_result = AsyncMock(
            return_value=AgentToPlatform(
                type=AgentToPlatformType.RESULT,
                content=json.dumps([tool_a]),
            )
        )

        agent_b_handle = MagicMock()
        agent_b_handle.is_alive = True
        agent_b_handle.ipc = AsyncMock()
        agent_b_handle.ipc.send_chat = AsyncMock()
        agent_b_handle.ipc.receive_until_result = AsyncMock(
            return_value=AgentToPlatform(
                type=AgentToPlatformType.RESULT,
                content=json.dumps([tool_b]),
            )
        )

        mock_pm.list_running.return_value = ["agent-a", "agent-b"]
        mock_pm.get_agent.side_effect = lambda n: {
            "agent-a": agent_a_handle,
            "agent-b": agent_b_handle,
        }.get(n)

        tools = await router.get_tools()

        assert len(tools) == 1
        assert tools[0]["description"] == "Agent A search"

    @pytest.mark.asyncio
    async def test_unique_tools_all_returned(self) -> None:
        """Different tool names are all returned."""
        import json
        router, mock_pm = _make_router_with_mock_pm()

        tools_list = [
            {"name": "search", "description": "search tool"},
            {"name": "analyze", "description": "analyze tool"},
        ]

        handle = MagicMock()
        handle.is_alive = True
        handle.ipc = AsyncMock()
        handle.ipc.send_chat = AsyncMock()
        handle.ipc.receive_until_result = AsyncMock(
            return_value=AgentToPlatform(
                type=AgentToPlatformType.RESULT,
                content=json.dumps(tools_list),
            )
        )

        mock_pm.list_running.return_value = ["agent-x"]
        mock_pm.get_agent.return_value = handle

        result = await router.get_tools()

        assert len(result) == 2
        assert result[0]["name"] == "search"
        assert result[1]["name"] == "analyze"


# ---------------------------------------------------------------------------
# Fix 3: _execute_single_agent IPC error wrapping
# ---------------------------------------------------------------------------


class TestExecuteSingleAgentErrorWrapping:
    """_execute_single_agent must wrap IPC errors in RuntimeError."""

    @pytest.mark.asyncio
    async def test_ipc_timeout_wrapped_as_runtime_error(self) -> None:
        """IPC timeout in _execute_single_agent raises RuntimeError."""
        router, mock_pm = _make_router_with_mock_pm()

        handle = MagicMock()
        handle.is_alive = True
        handle.ipc = AsyncMock()
        handle.ipc.send_chat = AsyncMock()
        handle.ipc.receive_until_result = AsyncMock(
            side_effect=asyncio.TimeoutError("IPC timeout")
        )

        mock_pm.get_agent.return_value = handle

        with pytest.raises(RuntimeError, match="IPC error"):
            await router._execute_single_agent(
                "test-agent", "hello", conversation_id="c1"
            )

    @pytest.mark.asyncio
    async def test_ipc_connection_error_wrapped(self) -> None:
        """IPC connection error in _execute_single_agent raises RuntimeError."""
        router, mock_pm = _make_router_with_mock_pm()

        handle = MagicMock()
        handle.is_alive = True
        handle.ipc = AsyncMock()
        handle.ipc.send_chat = AsyncMock()
        handle.ipc.receive_until_result = AsyncMock(
            side_effect=ConnectionError("Broken pipe")
        )

        mock_pm.get_agent.return_value = handle

        with pytest.raises(RuntimeError, match="IPC error"):
            await router._execute_single_agent(
                "test-agent", "hello", conversation_id="c1"
            )
