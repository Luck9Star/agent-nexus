"""Unit tests for DeferredAgentRegistry — deferred agent loading and tool management.

The registry manages three tiers of agents (core, activated deferred, dormant
deferred) and interacts with ProcessManager for subprocess lifecycle and IPC
for tool discovery.  All external dependencies are mocked.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.models.agent import AgentManifest, AgentType
from agent_nexus.platform.gateway.deferred_registry import (
    AgentInfo,
    DeferredAgentRegistry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest(
    name: str = "test-agent",
    description: str = "A test agent",
    capabilities: list[str] | None = None,
) -> AgentManifest:
    return AgentManifest(
        name=name,
        version="1.0.0",
        type=AgentType.ATOMIC,
        description=description,
        capabilities=capabilities or [],
    )


def _make_mock_pm() -> MagicMock:
    """Create a mock ProcessManager."""
    pm = MagicMock()
    pm.start_agent = AsyncMock()
    return pm


def _make_handle(*, alive: bool = True) -> MagicMock:
    """Create a mock AgentHandle with IPC."""
    handle = MagicMock()
    handle.is_alive = alive
    handle.ipc = AsyncMock()
    return handle


# ---------------------------------------------------------------------------
# AgentInfo
# ---------------------------------------------------------------------------


class TestAgentInfo:
    def test_post_init_builds_search_text(self) -> None:
        info = AgentInfo(manifest=_make_manifest(description="code reviewer"), name="reviewer")
        assert "reviewer" in info._search_text
        assert "code reviewer" in info._search_text


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


class TestRegisterAgent:
    def test_register_deferred(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("d1"))
        assert "d1" in [a.name for a in reg.list_deferred_agents()]
        assert "d1" not in [a.name for a in reg.list_core_agents()]

    def test_register_core(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("c1"), deferred=False)
        assert "c1" in [a.name for a in reg.list_core_agents()]

    def test_reregister_deferred_to_core(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("x"), deferred=True)
        reg.register_agent(_make_manifest("x"), deferred=False)
        assert "x" in [a.name for a in reg.list_core_agents()]
        assert "x" not in [a.name for a in reg.list_deferred_agents()]

    def test_reregister_core_to_deferred(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("x"), deferred=False)
        reg.register_agent(_make_manifest("x"), deferred=True)
        assert "x" in [a.name for a in reg.list_deferred_agents()]
        assert "x" not in [a.name for a in reg.list_core_agents()]


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------


class TestActivateAgent:
    @pytest.mark.asyncio()
    async def test_activate_unknown_raises(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        with pytest.raises(KeyError, match="not registered"):
            await reg.activate_agent("ghost")

    @pytest.mark.asyncio()
    async def test_activate_core_already_activated(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        manifest = _make_manifest("core1")
        reg.register_agent(manifest, deferred=False)
        # Manually set tool_schemas to simulate already activated
        reg._core_agents["core1"].tool_schemas = [{"name": "tool1"}]
        result = await reg.activate_agent("core1")
        assert result == [{"name": "tool1"}]

    @pytest.mark.asyncio()
    async def test_activate_deferred_starts_subprocess(self) -> None:
        pm = _make_mock_pm()
        handle = _make_handle()
        pm.start_agent.return_value = handle

        reg = DeferredAgentRegistry(pm)
        reg.register_agent(
            _make_manifest("d1"),
            start_command=["python", "run.py"],
            start_cwd="/tmp",
        )

        # Mock IPC tool discovery
        mock_response = MagicMock()
        mock_response.type = "result"
        mock_response.output = [{"name": "tool-a", "inputSchema": {"type": "object"}}]
        mock_response.content = None
        mock_response.error = None
        handle.ipc.send_chat = AsyncMock()
        handle.ipc.receive_until_result = AsyncMock(return_value=mock_response)

        with patch("agent_nexus.platform.gateway.deferred_registry.get_ipc_lock") as mock_lock:
            mock_lock.return_value = asyncio.Lock()
            result = await reg.activate_agent("d1")

        assert len(result) == 1
        assert result[0]["name"] == "tool-a"
        pm.start_agent.assert_called_once()

    @pytest.mark.asyncio()
    async def test_activate_uses_fallback_when_no_handle(self) -> None:
        pm = _make_mock_pm()
        reg = DeferredAgentRegistry(pm)
        reg.register_agent(_make_manifest("d2"))
        # No start_command -> no subprocess -> fallback chat tool

        result = await reg.activate_agent("d2")
        assert len(result) == 1
        assert "chat" in result[0]["name"]

    @pytest.mark.asyncio()
    async def test_activate_subprocess_start_failure(self) -> None:
        pm = _make_mock_pm()
        pm.start_agent.side_effect = RuntimeError("spawn failed")

        reg = DeferredAgentRegistry(pm)
        reg.register_agent(
            _make_manifest("d3"),
            start_command=["fail"],
        )

        with pytest.raises(RuntimeError, match="spawn failed"):
            await reg.activate_agent("d3")

    @pytest.mark.asyncio()
    async def test_activate_idempotent(self) -> None:
        pm = _make_mock_pm()
        reg = DeferredAgentRegistry(pm)
        reg.register_agent(_make_manifest("d4"))
        reg._deferred_agents["d4"].tool_schemas = [{"name": "existing"}]

        result = await reg.activate_agent("d4")
        assert result == [{"name": "existing"}]


# ---------------------------------------------------------------------------
# _validate_tool_schemas
# ---------------------------------------------------------------------------


class TestValidateToolSchemas:
    def test_valid_schemas_pass(self) -> None:
        schemas = [
            {"name": "tool1", "inputSchema": {"type": "object"}},
            {"name": "tool2", "inputSchema": {"type": "object"}},
        ]
        result = DeferredAgentRegistry._validate_tool_schemas(schemas)
        assert len(result) == 2

    def test_skips_non_dict(self) -> None:
        schemas = [{"name": "good"}, "bad", None, 42]  # type: ignore[list-item]
        result = DeferredAgentRegistry._validate_tool_schemas(schemas)
        assert len(result) == 1

    def test_skips_missing_name(self) -> None:
        schemas = [{"inputSchema": {}}, {"name": "", "inputSchema": {}}]
        result = DeferredAgentRegistry._validate_tool_schemas(schemas)
        assert len(result) == 0

    def test_injects_default_input_schema(self) -> None:
        schemas = [{"name": "tool-no-schema"}]
        result = DeferredAgentRegistry._validate_tool_schemas(schemas)
        assert len(result) == 1
        assert result[0]["inputSchema"] == {"type": "object", "properties": {}}

    def test_empty_list(self) -> None:
        assert DeferredAgentRegistry._validate_tool_schemas([]) == []


# ---------------------------------------------------------------------------
# _fallback_chat_tool
# ---------------------------------------------------------------------------


class TestFallbackChatTool:
    def test_structure(self) -> None:
        info = AgentInfo(manifest=_make_manifest(description="hello"), name="x")
        tool = DeferredAgentRegistry._fallback_chat_tool(info)
        assert tool["name"] == "chat"
        assert tool["description"] == "hello"
        assert "message" in tool["inputSchema"]["properties"]


# ---------------------------------------------------------------------------
# remove_agent_tools
# ---------------------------------------------------------------------------


class TestRemoveAgentTools:
    def test_removes_from_all_dicts(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("a1"))
        reg.register_agent(_make_manifest("a2"))
        reg.remove_agent_tools("a1")
        assert reg.get_agent_info("a1") is None
        assert reg.get_agent_info("a2") is not None

    def test_removes_tool_adapters(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("a1"))
        # Simulate adapters being present
        from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter

        adapter = McpToolAdapter(
            server_name="a1",
            tool_schema={"name": "t1", "inputSchema": {"type": "object"}},
        )
        reg._tool_adapters["a1"] = [adapter]
        reg._tool_by_name[adapter.full_name] = adapter
        reg.remove_agent_tools("a1")
        assert adapter.full_name not in reg._tool_by_name
        assert "a1" not in reg._tool_adapters

    def test_noop_for_unknown(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.remove_agent_tools("ghost")
        assert len(reg._tool_adapters) == 0
        assert len(reg._tool_by_name) == 0


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------


class TestGetToolsForLlm:
    def test_empty_registry(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        assert reg.get_tools_for_llm() == []

    def test_core_tools_included(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("c1"), deferred=False)
        reg._core_agents["c1"].tool_schemas = [{"name": "tool1"}]
        tools = reg.get_tools_for_llm()
        assert len(tools) == 1
        assert tools[0]["name"] == "tool1"

    def test_deduplicates_by_adapter_full_name(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        # Core agent with tool "tool1"
        reg.register_agent(_make_manifest("c1"), deferred=False)
        reg._core_agents["c1"].tool_schemas = [{"name": "tool1"}]
        # Adapter with a different tool "tool2" (different full_name)
        from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter

        adapter = McpToolAdapter(
            server_name="c1",
            tool_schema={"name": "tool2", "inputSchema": {"type": "object"}},
        )
        reg._tool_adapters["c1"] = [adapter]
        tools = reg.get_tools_for_llm()
        # Core tools use bare name, adapters use mcp__ prefixed name
        # They don't overlap unless names match exactly
        assert len(tools) == 2


class TestGetAgentInfo:
    def test_prefers_activated_deferred_over_core(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("a"), deferred=False)
        reg.register_agent(_make_manifest("a"), deferred=True)
        # Activate the deferred version
        reg._deferred_agents["a"].tool_schemas = [{"name": "t"}]
        info = reg.get_agent_info("a")
        assert info is not None
        assert info.is_activated is True

    def test_returns_core_when_not_activated_deferred(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("a"), deferred=False)
        info = reg.get_agent_info("a")
        assert info is not None
        assert info in reg._core_agents.values()

    def test_returns_none_for_unknown(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        assert reg.get_agent_info("ghost") is None

    def test_returns_dormant_deferred_last(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("d1"))
        info = reg.get_agent_info("d1")
        assert info is not None
        assert not info.is_activated


class TestListAgents:
    def test_list_all(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("c1"), deferred=False)
        reg.register_agent(_make_manifest("d1"))
        assert len(reg.list_all_agents()) == 2


# ---------------------------------------------------------------------------
# build_manifest / search_agents
# ---------------------------------------------------------------------------


class TestBuildManifest:
    def test_core_agent_format(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("c1", description="Core agent"), deferred=False)
        reg._core_agents["c1"].tool_schemas = [{"name": "t1"}, {"name": "t2"}]
        manifest = reg.build_manifest()
        assert "c1" in manifest
        assert "core" in manifest
        assert "2 tools" in manifest

    def test_deferred_dormant_format(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("d1", description="Deferred agent"))
        manifest = reg.build_manifest()
        assert "d1" in manifest
        assert "available" in manifest

    def test_deferred_activated_format(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("d1"))
        reg._deferred_agents["d1"].tool_schemas = [{"name": "t1"}]
        manifest = reg.build_manifest()
        assert "activated" in manifest


class TestSearchAgents:
    def test_search_by_name(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("code-reviewer", description="Reviews code"))
        reg.register_agent(_make_manifest("doc-writer", description="Writes docs"))
        results = reg.search_agents("code")
        assert len(results) == 1
        assert results[0].name == "code-reviewer"

    def test_search_by_description(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("a1", description="security vulnerability scanner"))
        reg.register_agent(_make_manifest("a2", description="document formatter"))
        results = reg.search_agents("security")
        assert len(results) == 1

    def test_search_no_results(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("a1"))
        assert reg.search_agents("nonexistent") == []

    def test_search_max_results(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        for i in range(10):
            reg.register_agent(_make_manifest(f"agent-{i}", description=f"code tool number {i}"))
        results = reg.search_agents("code", max_results=3)
        assert len(results) == 3

    def test_search_across_core_and_deferred(self) -> None:
        reg = DeferredAgentRegistry(_make_mock_pm())
        reg.register_agent(_make_manifest("c-review", description="review code"), deferred=False)
        reg.register_agent(_make_manifest("d-review", description="review docs"))
        results = reg.search_agents("review")
        assert len(results) == 2
