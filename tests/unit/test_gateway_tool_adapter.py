"""Unit tests for McpToolAdapter: dynamic MCP tool bridge."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_nexus.models.ipc import AgentToPlatform, AgentToPlatformType
from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter


# ---------------------------------------------------------------------------
# Construction / naming
# ---------------------------------------------------------------------------

class TestMcpToolAdapterInit:
    def test_basic_construction(self) -> None:
        adapter = McpToolAdapter("my-server", {
            "name": "search",
            "description": "Search documents",
            "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
        })
        assert adapter.server_name == "my_server"
        assert adapter.tool_name == "search"
        assert adapter.full_name == "mcp__my_server__search"
        assert adapter.description == "Search documents"

    def test_name_sanitization(self) -> None:
        adapter = McpToolAdapter("my-server/v2", {
            "name": "do-thing!",
            "description": "",
        })
        assert adapter.server_name == "my_server_v2"
        assert adapter.tool_name == "do_thing_"
        assert adapter.full_name == "mcp__my_server_v2__do_thing_"

    def test_preserves_original_agent_name(self) -> None:
        adapter = McpToolAdapter("my-server/v2", {"name": "tool"})
        assert adapter.agent_name == "my-server/v2"

    def test_preserves_original_tool_name(self) -> None:
        adapter = McpToolAdapter("srv", {"name": "raw-tool!"})
        assert adapter._original_tool_name == "raw-tool!"

    def test_empty_input_schema_defaults(self) -> None:
        adapter = McpToolAdapter("srv", {"name": "t"})
        defn = adapter.get_tool_definition()
        assert defn["inputSchema"]["type"] == "object"
        assert defn["inputSchema"]["properties"] == {}

    def test_repr(self) -> None:
        adapter = McpToolAdapter("srv", {"name": "t"})
        assert repr(adapter) == "McpToolAdapter('mcp__srv__t')"


# ---------------------------------------------------------------------------
# get_tool_definition
# ---------------------------------------------------------------------------

class TestMcpToolAdapterDefinition:
    def test_definition_shape(self) -> None:
        schema = {"type": "object", "properties": {"x": {"type": "int"}}}
        adapter = McpToolAdapter("agent", {
            "name": "compute", "description": "Compute", "inputSchema": schema,
        })
        defn = adapter.get_tool_definition()
        assert defn["name"] == "mcp__agent__compute"
        assert defn["description"] == "Compute"
        assert defn["inputSchema"] == schema

    def test_definition_with_missing_description(self) -> None:
        adapter = McpToolAdapter("a", {"name": "b"})
        defn = adapter.get_tool_definition()
        assert defn["description"] == ""


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

def _make_handle(alive: bool = True) -> MagicMock:
    handle = MagicMock()
    handle.is_alive = alive
    handle.ipc = MagicMock()
    handle.ipc.send_chat = AsyncMock()
    handle.ipc.receive_until_result = AsyncMock()
    return handle


class TestMcpToolAdapterExecute:
    @pytest.mark.asyncio
    async def test_execute_dead_agent_returns_error(self) -> None:
        adapter = McpToolAdapter("srv", {"name": "t"})
        handle = _make_handle(alive=False)
        result = await adapter.execute(handle, {"q": "x"})
        assert result["success"] is False
        assert "not alive" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_success(self) -> None:
        adapter = McpToolAdapter("srv", {"name": "t"})
        handle = _make_handle(alive=True)
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="the answer",
            status="completed",
        )
        handle.ipc.receive_until_result.return_value = response

        result = await adapter.execute(handle, {"q": "x"})
        assert result["success"] is True
        assert result["output"] == "the answer"

    @pytest.mark.asyncio
    async def test_execute_error_response(self) -> None:
        adapter = McpToolAdapter("srv", {"name": "t"})
        handle = _make_handle(alive=True)
        response = AgentToPlatform(
            type=AgentToPlatformType.ERROR,
            error="something broke",
        )
        handle.ipc.receive_until_result.return_value = response

        result = await adapter.execute(handle, {})
        assert result["success"] is False
        assert "something broke" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_ipc_exception(self) -> None:
        adapter = McpToolAdapter("srv", {"name": "t"})
        handle = _make_handle(alive=True)
        handle.ipc.send_chat.side_effect = ConnectionError("pipe broken")

        result = await adapter.execute(handle, {})
        assert result["success"] is False
        assert "IPC error" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_null_status_treated_as_success(self) -> None:
        adapter = McpToolAdapter("srv", {"name": "t"})
        handle = _make_handle(alive=True)
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="ok",
            status=None,
        )
        handle.ipc.receive_until_result.return_value = response

        result = await adapter.execute(handle, {})
        assert result["success"] is True


# ---------------------------------------------------------------------------
# lock management
# ---------------------------------------------------------------------------

class TestMcpToolAdapterLocks:
    def test_remove_lock(self) -> None:
        McpToolAdapter._ipc_locks["test-agent"] = asyncio.Lock()
        McpToolAdapter.remove_lock("test-agent")
        assert "test-agent" not in McpToolAdapter._ipc_locks

    def test_remove_lock_missing_is_noop(self) -> None:
        McpToolAdapter.remove_lock("nonexistent")  # should not raise

    def test_remove_all_locks(self) -> None:
        McpToolAdapter._ipc_locks["a"] = asyncio.Lock()
        McpToolAdapter._ipc_locks["b"] = asyncio.Lock()
        McpToolAdapter.remove_all_locks()
        assert len(McpToolAdapter._ipc_locks) == 0
