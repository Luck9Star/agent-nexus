"""Tests for iteration 18 bug fixes — Gateway name lookup, core tool registration, identity check."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_nexus.models.agent import AgentManifest, AgentType, AgentDependencies
from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter, _sanitize


# ---------------------------------------------------------------------------
# Fix 1: McpToolAdapter stores original agent_name for lookups
# ---------------------------------------------------------------------------


class TestToolAdapterOriginalName:
    """McpToolAdapter must preserve original unsanitized agent name."""

    def test_hyphenated_name_preserved(self) -> None:
        schema = {"name": "analyze", "description": "Analyze"}
        adapter = McpToolAdapter("my-agent", schema)
        assert adapter.agent_name == "my-agent"
        assert adapter.server_name == "my_agent"
        assert "my_agent" in adapter.full_name

    def test_clean_name_no_change(self) -> None:
        schema = {"name": "chat", "description": "Chat"}
        adapter = McpToolAdapter("simple", schema)
        assert adapter.agent_name == "simple"
        assert adapter.server_name == "simple"

    def test_sanitize_helper(self) -> None:
        assert _sanitize("my-agent") == "my_agent"
        assert _sanitize("my.agent") == "my_agent"
        assert _sanitize("my agent") == "my_agent"
        assert _sanitize("myAgent") == "myAgent"


# ---------------------------------------------------------------------------
# Fix 2: Core agent tools are registered immediately on register_agent
# ---------------------------------------------------------------------------


class TestCoreAgentToolRegistration:
    """register_agent(deferred=False) must immediately call _register_agent_tools."""

    def test_core_agent_tools_registered(self) -> None:
        from agent_nexus.platform.gateway.gateway import MCPGateway
        from agent_nexus.platform.gateway.deferred_registry import DeferredAgentRegistry

        pm = MagicMock()
        router = MagicMock()

        gateway = MCPGateway(process_manager=pm, router=router)
        # Intercept _register_agent_tools
        registered_agents = []
        original_register = gateway._register_agent_tools

        def tracking_register(name: str) -> None:
            registered_agents.append(name)
            original_register(name)

        gateway._register_agent_tools = tracking_register

        manifest = _make_manifest("core-agent")
        gateway.register_agent(manifest, deferred=False)

        assert "core-agent" in registered_agents

    def test_deferred_agent_tools_not_registered(self) -> None:
        from agent_nexus.platform.gateway.gateway import MCPGateway

        pm = MagicMock()
        router = MagicMock()
        gateway = MCPGateway(process_manager=pm, router=router)

        registered_agents = []
        original_register = gateway._register_agent_tools

        def tracking_register(name: str) -> None:
            registered_agents.append(name)
            original_register(name)

        gateway._register_agent_tools = tracking_register

        manifest = _make_manifest("lazy-agent")
        gateway.register_agent(manifest, deferred=True)

        assert "lazy-agent" not in registered_agents


# ---------------------------------------------------------------------------
# Fix 3: list_agents uses name comparison, not object identity
# ---------------------------------------------------------------------------


class TestListAgentsNameComparison:
    """_list_agents must compare by name, not by object identity."""

    @pytest.mark.asyncio
    async def test_list_agents_core_tier_by_name(self) -> None:
        from agent_nexus.platform.gateway.gateway import MCPGateway

        pm = MagicMock()
        router = MagicMock()
        gateway = MCPGateway(process_manager=pm, router=router)

        # Register a core agent
        manifest = _make_manifest("test-core")
        gateway.register_agent(manifest, deferred=False)

        # _list_agents should classify it as "core" tier
        result = await gateway._list_agents()
        assert "test-core" in result
        assert "core" in result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest(name: str) -> AgentManifest:
    return AgentManifest(
        name=name,
        version="0.1.0",
        type=AgentType.ATOMIC,
        description=f"Test agent {name}",
        dependencies=AgentDependencies(),
    )
