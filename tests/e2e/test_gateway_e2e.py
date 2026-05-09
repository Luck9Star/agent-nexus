"""E2E tests for MCP Gateway: tool registration, name collision, cleanup,
and deferred loading edge cases.

Tests gateway-level logic without requiring live MCP connections.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_nexus.models.agent import AgentManifest, AgentType


def _make_manifest(
    name: str = "test-agent",
    version: str = "1.0",
    description: str = "Test agent",
    agent_type: AgentType = AgentType.ATOMIC,
) -> AgentManifest:
    return AgentManifest(
        name=name,
        version=version,
        type=agent_type,
        description=description,
    )


class TestDeferredRegistryE2E:
    """E2E tests using real DeferredAgentRegistry with mock ProcessManager."""

    @pytest.fixture()
    def registry(self):
        """Create a DeferredAgentRegistry with a mock ProcessManager."""
        from agent_nexus.platform.gateway.deferred_registry import DeferredAgentRegistry

        pm = MagicMock()
        pm.start_agent = AsyncMock(return_value=None)
        pm.stop_agent = AsyncMock(return_value=None)
        reg = DeferredAgentRegistry(process_manager=pm)
        yield reg

    def test_register_core_and_deferred(self, registry) -> None:
        """Register agents in both tiers."""
        registry.register_agent(
            _make_manifest(name="core-agent"),
            deferred=False,
        )
        registry.register_agent(
            _make_manifest(name="deferred-agent"),
            deferred=True,
        )

        core = registry.list_core_agents()
        deferred = registry.list_deferred_agents()
        assert len(core) == 1
        assert len(deferred) == 1
        assert core[0].name == "core-agent"
        assert deferred[0].name == "deferred-agent"

    def test_register_reregister_changes_tier(self, registry) -> None:
        """Re-registering an agent moves it to the new tier."""
        manifest = _make_manifest(name="mover")
        registry.register_agent(manifest, deferred=False)
        assert len(registry.list_core_agents()) == 1

        registry.register_agent(manifest, deferred=True)
        assert len(registry.list_core_agents()) == 0
        assert len(registry.list_deferred_agents()) == 1

    def test_list_all_agents_combined(self, registry) -> None:
        """list_all_agents returns both tiers."""
        registry.register_agent(_make_manifest(name="a"), deferred=False)
        registry.register_agent(_make_manifest(name="b"), deferred=True)
        all_agents = registry.list_all_agents()
        assert len(all_agents) == 2

    def test_get_agent_info(self, registry) -> None:
        """get_agent_info returns registered agent data."""
        registry.register_agent(
            _make_manifest(name="info-agent"),
            deferred=False,
        )
        info = registry.get_agent_info("info-agent")
        assert info is not None
        assert info.name == "info-agent"

    def test_get_agent_info_missing_returns_none(self, registry) -> None:
        """get_agent_info returns None for unknown agents."""
        assert registry.get_agent_info("nonexistent") is None

    def test_search_agents_by_keyword(self, registry) -> None:
        """search_agents finds agents matching keywords."""
        registry.register_agent(
            _make_manifest(name="code-reviewer", description="Reviews Python code"),
            deferred=False,
        )
        registry.register_agent(
            _make_manifest(name="doc-filler", description="Generates documentation"),
            deferred=False,
        )

        results = registry.search_agents("review python")
        assert len(results) >= 1
        names = {r.name for r in results}
        assert "code-reviewer" in names

    def test_build_manifest_includes_all(self, registry) -> None:
        """build_manifest includes all registered agents."""
        for i in range(3):
            registry.register_agent(
                _make_manifest(name=f"agent-{i}"),
                deferred=False,
            )
        manifest = registry.build_manifest()
        assert len(manifest) >= 3


class TestDeferredRegistryToolLifecycle:
    """E2E tests for deferred agent tool visibility and tier transitions.

    Verifies that deferred agents don't expose tools until activated,
    and that deregistration properly cleans up state.
    """

    @pytest.fixture()
    def registry(self):
        """Create a DeferredAgentRegistry with a mock ProcessManager."""
        from agent_nexus.platform.gateway.deferred_registry import DeferredAgentRegistry

        pm = MagicMock()
        pm.start_agent = AsyncMock(return_value=None)
        pm.stop_agent = AsyncMock(return_value=None)
        reg = DeferredAgentRegistry(process_manager=pm)
        yield reg

    def test_deferred_agent_tools_not_in_get_tools_for_llm(self, registry) -> None:
        """Deferred agents are excluded from get_tools_for_llm until activated."""
        registry.register_agent(
            _make_manifest(name="lazy-agent"),
            deferred=True,
        )
        registry.register_agent(
            _make_manifest(name="eager-agent"),
            deferred=False,
        )

        # Only core agent tools should be available
        tools = registry.get_tools_for_llm()
        # Deferred agent should not contribute tools
        tool_names = [t.name for t in tools] if tools else []
        assert "lazy-agent" not in str(tool_names)

    def test_remove_agent_tools_cleans_up(self, registry) -> None:
        """remove_agent_tools clears tool registrations for an agent."""
        manifest = _make_manifest(name="temp-agent")
        registry.register_agent(manifest, deferred=False)

        assert registry.get_agent_info("temp-agent") is not None

        registry.remove_agent_tools("temp-agent")
        # Tool adapters should be gone, but agent info may persist
        assert len(registry._tool_adapters) == 0

    def test_register_many_agents_stability(self, registry) -> None:
        """Registering many agents doesn't corrupt internal state."""
        for i in range(20):
            tier = i % 2 == 0
            registry.register_agent(
                _make_manifest(name=f"batch-{i}"),
                deferred=not tier,
            )

        all_agents = registry.list_all_agents()
        assert len(all_agents) == 20

        core = registry.list_core_agents()
        deferred = registry.list_deferred_agents()
        assert len(core) + len(deferred) == 20

    def test_search_agents_returns_empty_for_no_match(self, registry) -> None:
        """search_agents returns empty list when no keywords match."""
        registry.register_agent(
            _make_manifest(name="code-reviewer", description="Reviews code"),
            deferred=False,
        )

        results = registry.search_agents("completely unrelated quantum physics")
        assert results == []


# ---------------------------------------------------------------------------
# MCP tool adapter contract: naming, schema, and execution boundary
# ---------------------------------------------------------------------------


class TestMcpToolAdapterContract:
    """Verify McpToolAdapter produces correct MCP tool definitions.

    The adapter converts agent tool schemas into MCP-compatible format:
      full_name = mcp__{server_name}__{tool_name}
      get_tool_definition() returns {name, description, inputSchema}

    This contract must match what the Rust gateway (ap-gateway) produces.
    """

    def test_tool_name_sanitization(self) -> None:
        """Non-alphanumeric characters in server/tool names are replaced with _."""
        from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter

        adapter = McpToolAdapter(
            server_name="code-reviewer-v2",
            tool_schema={"name": "review PR", "description": "Review a PR"},
        )
        assert adapter.full_name == "mcp__code_reviewer_v2__review_PR"

    def test_tool_definition_schema(self) -> None:
        """get_tool_definition returns MCP-compatible schema dict."""
        from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter

        adapter = McpToolAdapter(
            server_name="doc-filler",
            tool_schema={
                "name": "fill_template",
                "description": "Fill a document template",
                "inputSchema": {
                    "type": "object",
                    "properties": {"template": {"type": "string"}},
                    "required": ["template"],
                },
            },
        )
        defn = adapter.get_tool_definition()
        assert defn["name"] == "mcp__doc_filler__fill_template"
        assert defn["description"] == "Fill a document template"
        assert defn["inputSchema"]["type"] == "object"
        assert "template" in defn["inputSchema"]["properties"]

    def test_tool_definition_default_schema(self) -> None:
        """Missing inputSchema defaults to empty object schema."""
        from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter

        adapter = McpToolAdapter(
            server_name="minimal",
            tool_schema={"name": "noop"},
        )
        defn = adapter.get_tool_definition()
        assert defn["inputSchema"] == {"type": "object", "properties": {}}

    def test_tool_schema_missing_name_raises(self) -> None:
        """Tool schema without 'name' raises ValueError."""
        from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter

        with pytest.raises(ValueError, match="name"):
            McpToolAdapter(server_name="test", tool_schema={"description": "no name"})

    def test_mcp_triple_underscore_separator(self) -> None:
        """MCP tool names use mcp__ prefix with double-underscore separator."""
        from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter

        adapter = McpToolAdapter(
            server_name="agent",
            tool_schema={"name": "tool"},
        )
        assert adapter.full_name == "mcp__agent__tool"
        assert adapter.full_name.count("__") == 2  # mcp__agent__tool

    @pytest.mark.asyncio
    async def test_execute_on_dead_process_returns_error(self) -> None:
        """Executing a tool on a dead agent handle returns error dict."""
        from unittest.mock import MagicMock

        from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter

        adapter = McpToolAdapter(
            server_name="dead-agent",
            tool_schema={"name": "tool", "description": "A tool"},
        )
        dead_handle = MagicMock()
        dead_handle.is_alive = False

        result = await adapter.execute(dead_handle, {"arg": "value"})

        assert result["success"] is False
        assert "not alive" in result["error"].lower()
