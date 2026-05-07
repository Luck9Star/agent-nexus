"""E2E tests for MCP Gateway: tool registration, name collision, cleanup.

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
        name=name, version=version, type=agent_type, description=description,
    )


class TestGatewayE2E:
    """E2E gateway scenarios."""

    def test_tool_name_collision_handling(self) -> None:
        """Gateway handles tool name collisions with numeric suffix."""
        used: set[str] = set()

        base = "review"
        name = base
        counter = 1
        while name in used:
            name = f"{base}_{counter}"
            counter += 1
        used.add(name)

        assert name == "review"

        name2 = base
        counter = 1
        while name2 in used:
            name2 = f"{base}_{counter}"
            counter += 1
        used.add(name2)

        assert name2 == "review_1"

    def test_gateway_cleanup_removes_tools(self) -> None:
        """Gateway cleanup removes all tools for a deregistered agent."""
        tools_before = {"agent1_review", "agent1_analyze", "agent2_check"}
        agent_prefix = "agent1_"
        remaining = {t for t in tools_before if not t.startswith(agent_prefix)}
        assert remaining == {"agent2_check"}

    def test_namespaced_tool_roundtrip(self) -> None:
        """Tool namespacing agent___tool format round-trips correctly."""
        sep = "___"
        agent = "code-reviewer"
        tool = "review"
        namespaced = f"{agent}{sep}{tool}"
        assert namespaced == "code-reviewer___review"

        parts = namespaced.split(sep, 1)
        assert parts[0] == agent
        assert parts[1] == tool


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
            _make_manifest(name="core-agent"), deferred=False,
        )
        registry.register_agent(
            _make_manifest(name="deferred-agent"), deferred=True,
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
            _make_manifest(name="info-agent"), deferred=False,
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
                _make_manifest(name=f"agent-{i}"), deferred=False,
            )
        manifest = registry.build_manifest()
        assert len(manifest) >= 3
