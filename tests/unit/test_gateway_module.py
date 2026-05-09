"""Unit tests for gateway module — tool_adapter, deferred_registry, gateway.

Tests McpToolAdapter (name sanitization, tool definitions, execute),
DeferredAgentRegistry (registration tiers, activation, search, manifest),
and MCPGateway (core tools, agent registration, tool forwarding).
"""

from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.models.agent import AgentDependencies, AgentManifest, AgentRole, AgentType
from agent_nexus.models.ipc import AgentToPlatform, AgentToPlatformType
from agent_nexus.platform.gateway.deferred_registry import (
    AgentInfo,
    DeferredAgentRegistry,
)
from agent_nexus.platform.gateway.gateway import MCPGateway
from agent_nexus.platform.gateway.tool_adapter import (
    McpToolAdapter,
    _sanitize,
    remove_all_locks,
)
from agent_nexus.platform.orchestration.ipc import _ipc_lock_registry
from agent_nexus.platform.orchestration.process_manager import (
    AgentHandle,
    ProcessManager,
)

# ============================================================================
# Helpers / Fixtures
# ============================================================================


def _make_manifest(
    name: str = "test-agent",
    description: str = "A test agent",
    agent_type: AgentType = AgentType.ATOMIC,
    **overrides,
) -> AgentManifest:
    """Build an AgentManifest with sensible defaults."""
    return AgentManifest(
        name=name,
        version="0.1.0",
        type=agent_type,
        description=description,
        **overrides,  # type: ignore[arg-type]
    )


def _make_tool_schema(
    name: str = "do_thing",
    description: str = "Does a thing",
    input_schema: dict | None = None,
) -> dict:
    """Build a tool schema dict matching MCP format."""
    schema: dict[str, object] = {"name": name, "description": description}
    if input_schema is not None:
        schema["inputSchema"] = input_schema
    return schema


def _mock_agent_handle(name: str = "test-agent", alive: bool = True) -> MagicMock:
    """Create a mock AgentHandle for IPC tests."""
    handle = MagicMock(spec=AgentHandle)
    handle.name = name
    handle.is_alive = alive
    handle.ipc = MagicMock()
    handle.ipc.send_chat = AsyncMock()
    handle.ipc.receive_until_result = AsyncMock()
    return handle


@pytest.fixture
def process_manager() -> MagicMock:
    """Mock ProcessManager (no real subprocesses)."""
    pm = MagicMock(spec=ProcessManager)
    pm.start_agent = AsyncMock()
    pm.stop_all = AsyncMock()
    return pm


@pytest.fixture
def registry(process_manager: MagicMock) -> DeferredAgentRegistry:
    """DeferredAgentRegistry with mock ProcessManager."""
    return DeferredAgentRegistry(process_manager)


@pytest.fixture
def router(process_manager: MagicMock) -> MagicMock:  # pyright: ignore[reportUnusedParameter]
    """Mock PlatformRouter."""
    router = MagicMock()
    return router


@pytest.fixture
def gateway(process_manager: MagicMock, router: MagicMock) -> MCPGateway:
    """MCPGateway with mock dependencies."""
    return MCPGateway(process_manager=process_manager, router=router)


# ============================================================================
# McpToolAdapter — _sanitize
# ============================================================================


class TestSanitize:
    """Tests for the _sanitize static helper."""

    @pytest.mark.parametrize(
        "input_str, expected",
        [
            ("my-tool", "my_tool"),
            ("my.tool", "my_tool"),
            ("my tool", "my_tool"),
            ("a.b!c@d", "a_b_c_d"),
            ("", ""),
            ("MyTool", "MyTool"),
            (".hidden", "_hidden"),
        ],
    )
    def test_sanitize_cases(self, input_str: str, expected: str) -> None:
        assert _sanitize(input_str) == expected


# ============================================================================
# McpToolAdapter — __init__ & properties
# ============================================================================


class TestMcpToolAdapterInit:
    """Tests for McpToolAdapter construction and property access."""

    def test_basic_construction(self) -> None:
        schema = _make_tool_schema("do_work", "Does work")
        adapter = McpToolAdapter(server_name="my-agent", tool_schema=schema)
        # server_name is sanitized: hyphens -> underscores
        assert adapter.server_name == "my_agent"
        assert adapter.tool_name == "do_work"
        assert adapter.full_name == "mcp__my_agent__do_work"

    def test_description_from_schema(self) -> None:
        schema = _make_tool_schema("t", "Custom description")
        adapter = McpToolAdapter(server_name="srv", tool_schema=schema)
        assert adapter.description == "Custom description"

    def test_description_default_empty(self) -> None:
        schema = {"name": "t"}
        adapter = McpToolAdapter(server_name="srv", tool_schema=schema)
        assert adapter.description == ""

    def test_repr(self) -> None:
        schema = _make_tool_schema("tool")
        adapter = McpToolAdapter(server_name="srv", tool_schema=schema)
        assert repr(adapter) == "McpToolAdapter('mcp__srv__tool')"


# ============================================================================
# McpToolAdapter — get_tool_definition
# ============================================================================


class TestMcpToolAdapterGetToolDefinition:
    """Tests for McpToolAdapter.get_tool_definition()."""

    def test_basic_definition(self) -> None:
        schema = _make_tool_schema("do_work", "Does work")
        adapter = McpToolAdapter(server_name="agent", tool_schema=schema)
        defn = adapter.get_tool_definition()
        assert defn["name"] == "mcp__agent__do_work"
        assert defn["description"] == "Does work"

    def test_custom_input_schema(self) -> None:
        ischema = {
            "type": "object",
            "properties": {"msg": {"type": "string"}},
            "required": ["msg"],
        }
        schema = _make_tool_schema("t", input_schema=ischema)
        adapter = McpToolAdapter(server_name="srv", tool_schema=schema)
        defn = adapter.get_tool_definition()
        assert defn["inputSchema"] == ischema

    def test_empty_input_schema_gets_default(self) -> None:
        schema = {"name": "t", "description": "d"}
        adapter = McpToolAdapter(server_name="srv", tool_schema=schema)
        defn = adapter.get_tool_definition()
        assert defn["inputSchema"] == {"type": "object", "properties": {}}


# ============================================================================
# McpToolAdapter — execute
# ============================================================================


class TestMcpToolAdapterExecute:
    """Tests for McpToolAdapter.execute() with mocked IPC."""

    @pytest.mark.asyncio
    async def test_execute_dead_agent(self) -> None:
        schema = _make_tool_schema("tool")
        adapter = McpToolAdapter(server_name="dead-agent", tool_schema=schema)
        handle = _mock_agent_handle("dead-agent", alive=False)
        result = await adapter.execute(handle, {"x": 1})
        assert result["success"] is False
        assert "not alive" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_success(self) -> None:
        schema = _make_tool_schema("tool")
        adapter = McpToolAdapter(server_name="agent", tool_schema=schema)
        handle = _mock_agent_handle("agent", alive=True)
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="output data",
            status="completed",
        )
        handle.ipc.receive_until_result.return_value = response
        result = await adapter.execute(handle, {"arg": "val"})
        assert result["success"] is True
        assert result["output"] == "output data"

    @pytest.mark.asyncio
    async def test_execute_ipc_error(self) -> None:
        schema = _make_tool_schema("tool")
        adapter = McpToolAdapter(server_name="agent", tool_schema=schema)
        handle = _mock_agent_handle("agent", alive=True)
        handle.ipc.send_chat.side_effect = ConnectionError("pipe broken")
        result = await adapter.execute(handle, {})
        assert result["success"] is False
        assert "IPC error" in result["error"]

    @pytest.mark.asyncio
    async def test_execute_agent_error_response(self) -> None:
        schema = _make_tool_schema("tool")
        adapter = McpToolAdapter(server_name="agent", tool_schema=schema)
        handle = _mock_agent_handle("agent", alive=True)
        response = AgentToPlatform(
            type=AgentToPlatformType.ERROR,
            error="something went wrong",
        )
        handle.ipc.receive_until_result.return_value = response
        result = await adapter.execute(handle, {})
        assert result["success"] is False
        assert "something went wrong" in result["error"]


# ============================================================================
# AgentInfo — dataclass
# ============================================================================


class TestAgentInfo:
    """Tests for AgentInfo dataclass properties."""

    @pytest.mark.parametrize(
        "tool_schemas, handle_alive, expect_activated, expect_running",
        [
            (None, None, False, False),
            ([{"name": "tool1"}], None, True, False),
            (None, True, False, True),
            (None, False, False, False),
        ],
        ids=["dormant", "activated", "running", "dead_handle"],
    )
    def test_state_combinations(
        self,
        tool_schemas: list | None,
        handle_alive: bool | None,
        expect_activated: bool,
        expect_running: bool,
    ) -> None:
        handle = _mock_agent_handle(alive=handle_alive) if handle_alive is not None else None
        info = AgentInfo(
            name="test",
            manifest=_make_manifest("test"),
            tool_schemas=tool_schemas,
            handle=handle,
        )
        assert info.is_activated is expect_activated
        assert info.is_running is expect_running

    def test_default_fields(self) -> None:
        info = AgentInfo(
            name="test",
            manifest=_make_manifest("test"),
        )
        assert info.start_command == []
        assert info.start_cwd is None
        assert info.start_env == {}


# ============================================================================
# DeferredAgentRegistry — registration
# ============================================================================


class TestDeferredRegistryRegistration:
    """Tests for DeferredAgentRegistry.register_agent()."""

    def test_register_deferred(self, registry: DeferredAgentRegistry) -> None:
        manifest = _make_manifest("deferred-agent")
        registry.register_agent(manifest, deferred=True)
        agents = registry.list_deferred_agents()
        assert len(agents) == 1
        assert agents[0].name == "deferred-agent"

    def test_register_core(self, registry: DeferredAgentRegistry) -> None:
        manifest = _make_manifest("core-agent")
        registry.register_agent(manifest, deferred=False)
        agents = registry.list_core_agents()
        assert len(agents) == 1
        assert agents[0].name == "core-agent"

    def test_register_both_tiers(self, registry: DeferredAgentRegistry) -> None:
        registry.register_agent(_make_manifest("core"), deferred=False)
        registry.register_agent(_make_manifest("deferred"), deferred=True)
        assert len(registry.list_core_agents()) == 1
        assert len(registry.list_deferred_agents()) == 1

    def test_register_with_start_command(self, registry: DeferredAgentRegistry) -> None:
        manifest = _make_manifest("cmd-agent")
        registry.register_agent(
            manifest,
            deferred=True,
            start_command=["python", "-m", "agent"],
            start_cwd="/tmp",
            start_env={"KEY": "VAL"},
        )
        info = registry.get_agent_info("cmd-agent")
        assert info is not None
        assert info.start_command == ["python", "-m", "agent"]
        assert info.start_cwd == "/tmp"
        assert info.start_env == {"KEY": "VAL"}

    def test_default_deferred_true(self, registry: DeferredAgentRegistry) -> None:
        manifest = _make_manifest("agent")
        registry.register_agent(manifest)  # no deferred kwarg
        assert len(registry.list_deferred_agents()) == 1
        assert len(registry.list_core_agents()) == 0


# ============================================================================
# DeferredAgentRegistry — get_agent_info
# ============================================================================


class TestDeferredRegistryGetAgent:
    """Tests for DeferredAgentRegistry.get_agent_info()."""

    def test_get_core_agent(self, registry: DeferredAgentRegistry) -> None:
        manifest = _make_manifest("core-agent")
        registry.register_agent(manifest, deferred=False)
        info = registry.get_agent_info("core-agent")
        assert info is not None
        assert info.name == "core-agent"

    def test_get_deferred_agent(self, registry: DeferredAgentRegistry) -> None:
        manifest = _make_manifest("deferred-agent")
        registry.register_agent(manifest, deferred=True)
        info = registry.get_agent_info("deferred-agent")
        assert info is not None
        assert info.name == "deferred-agent"

    def test_get_unknown_agent(self, registry: DeferredAgentRegistry) -> None:
        assert registry.get_agent_info("nonexistent") is None

    def test_reregister_same_name_replaces_tier(self, registry: DeferredAgentRegistry) -> None:
        """Re-registering the same name as a different tier replaces the old entry."""
        registry.register_agent(_make_manifest("x"), deferred=False)
        # x is in core
        assert registry.get_agent_info("x") in registry.list_core_agents()

        # Re-register same name as deferred — removes from core
        registry.register_agent(_make_manifest("x"), deferred=True)
        # x is now in deferred only (old core entry removed)
        assert len(registry.list_core_agents()) == 0
        assert len(registry.list_deferred_agents()) == 1
        info = registry.get_agent_info("x")
        assert info is not None
        assert info in registry.list_deferred_agents()


# ============================================================================
# DeferredAgentRegistry — activate_agent
# ============================================================================


class TestDeferredRegistryActivate:
    """Tests for DeferredAgentRegistry.activate_agent()."""

    @pytest.mark.asyncio
    async def test_activate_unknown_raises(self, registry: DeferredAgentRegistry) -> None:
        with pytest.raises(KeyError, match="not registered"):
            await registry.activate_agent("nonexistent")

    @pytest.mark.asyncio
    async def test_activate_deferred_no_subprocess(self, registry: DeferredAgentRegistry) -> None:
        """Activating a deferred agent with no start_command gets placeholder."""
        manifest = _make_manifest("no-cmd")
        registry.register_agent(manifest, deferred=True, start_command=[])
        schemas = await registry.activate_agent("no-cmd")
        assert len(schemas) == 1
        assert schemas[0]["name"] == "no-cmd__chat"
        assert "message" in schemas[0]["inputSchema"]["properties"]

    @pytest.mark.asyncio
    async def test_activate_creates_tool_adapters(self, registry: DeferredAgentRegistry) -> None:
        manifest = _make_manifest("adapter-agent")
        registry.register_agent(manifest, deferred=True, start_command=[])
        await registry.activate_agent("adapter-agent")
        adapters = registry._tool_adapters.get("adapter-agent", [])
        assert len(adapters) == 1
        assert adapters[0].server_name == "adapter_agent"

    @pytest.mark.asyncio
    async def test_activate_starts_subprocess(
        self, registry: DeferredAgentRegistry, process_manager: MagicMock
    ) -> None:
        manifest = _make_manifest("sub-agent")
        registry.register_agent(
            manifest,
            deferred=True,
            start_command=["python", "-m", "agent"],
        )
        mock_handle = _mock_agent_handle("sub-agent", alive=True)
        # Simulate IPC tool discovery
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="ok",
            status="completed",
        )
        mock_handle.ipc.receive_until_result.return_value = response
        process_manager.start_agent.return_value = mock_handle

        schemas = await registry.activate_agent("sub-agent")
        process_manager.start_agent.assert_awaited_once()
        assert len(schemas) >= 1  # at least the fallback chat tool

    @pytest.mark.asyncio
    async def test_activate_core_already_activated(self, registry: DeferredAgentRegistry) -> None:
        """Activating a core agent that already has schemas returns them."""
        manifest = _make_manifest("core-ok")
        registry.register_agent(manifest, deferred=False)
        info = registry.get_agent_info("core-ok")
        assert info is not None
        info.tool_schemas = [{"name": "tool1"}]
        schemas = await registry.activate_agent("core-ok")
        assert schemas == [{"name": "tool1"}]


# ============================================================================
# DeferredAgentRegistry — get_tools_for_llm
# ============================================================================


class TestDeferredRegistryGetToolsForLLM:
    """Tests for DeferredAgentRegistry.get_tools_for_llm()."""

    def test_empty_registry(self, registry: DeferredAgentRegistry) -> None:
        assert registry.get_tools_for_llm() == []

    def test_core_agent_tools_included(self, registry: DeferredAgentRegistry) -> None:
        manifest = _make_manifest("core-agent")
        registry.register_agent(manifest, deferred=False)
        info = registry.get_agent_info("core-agent")
        assert info is not None
        info.tool_schemas = [{"name": "tool1"}, {"name": "tool2"}]
        tools = registry.get_tools_for_llm()
        assert len(tools) == 2

    def test_core_agent_no_tools_skipped(self, registry: DeferredAgentRegistry) -> None:
        manifest = _make_manifest("empty-core")
        registry.register_agent(manifest, deferred=False)
        info = registry.get_agent_info("empty-core")
        assert info is not None
        # Not running, no tools
        assert info.tool_schemas is None
        tools = registry.get_tools_for_llm()
        assert tools == []

    @pytest.mark.asyncio
    async def test_activated_deferred_tools_included(self, registry: DeferredAgentRegistry) -> None:
        registry.register_agent(_make_manifest("deferred"), deferred=True, start_command=[])
        await registry.activate_agent("deferred")
        tools = registry.get_tools_for_llm()
        assert len(tools) >= 1


# ============================================================================
# DeferredAgentRegistry — get_tool_adapter
# ============================================================================


class TestDeferredRegistryGetToolAdapter:
    """Tests for DeferredAgentRegistry.get_tool_adapter()."""

    @pytest.mark.asyncio
    async def test_find_adapter_by_full_name(self, registry: DeferredAgentRegistry) -> None:
        registry.register_agent(_make_manifest("srv"), deferred=True, start_command=[])
        await registry.activate_agent("srv")
        adapters = registry._tool_adapters["srv"]
        full_name = adapters[0].full_name
        found = registry.get_tool_adapter(full_name)
        assert found is not None
        assert found.full_name == full_name

    def test_find_nonexistent_adapter(self, registry: DeferredAgentRegistry) -> None:
        assert registry.get_tool_adapter("mcp__nope__tool") is None


# ============================================================================
# DeferredAgentRegistry — build_manifest
# ============================================================================


class TestDeferredRegistryBuildManifest:
    """Tests for DeferredAgentRegistry.build_manifest()."""

    def test_empty(self, registry: DeferredAgentRegistry) -> None:
        assert registry.build_manifest() == ""

    def test_core_agent_manifest(self, registry: DeferredAgentRegistry) -> None:
        manifest = _make_manifest("core-agent", description="Does core stuff")
        registry.register_agent(manifest, deferred=False)
        info = registry.get_agent_info("core-agent")
        assert info is not None
        info.tool_schemas = [{"name": "t1"}, {"name": "t2"}]
        text = registry.build_manifest()
        assert "core-agent" in text
        assert "core" in text
        assert "2 tools" in text

    @pytest.mark.asyncio
    async def test_activated_deferred_manifest(self, registry: DeferredAgentRegistry) -> None:
        registry.register_agent(
            _make_manifest("active", description="Active agent"),
            deferred=True,
            start_command=[],
        )
        await registry.activate_agent("active")
        text = registry.build_manifest()
        assert "active" in text
        assert "activated" in text

    def test_multiline_description_truncated(self, registry: DeferredAgentRegistry) -> None:
        desc = "Line one\nLine two\nLine three"
        registry.register_agent(_make_manifest("multi", description=desc), deferred=True)
        text = registry.build_manifest()
        # Only first line should appear (up to 80 chars)
        assert "Line one" in text
        assert "Line two" not in text


# ============================================================================
# DeferredAgentRegistry — search_agents
# ============================================================================


class TestDeferredRegistrySearch:
    """Tests for DeferredAgentRegistry.search_agents()."""

    def test_empty_registry(self, registry: DeferredAgentRegistry) -> None:
        assert registry.search_agents("anything") == []

    def test_search_by_description(self, registry: DeferredAgentRegistry) -> None:
        registry.register_agent(
            _make_manifest("agent-a", description="Reviews code changes"),
            deferred=True,
        )
        registry.register_agent(
            _make_manifest("agent-b", description="Writes documentation"),
            deferred=True,
        )
        results = registry.search_agents("code")
        names = [m.name for m in results]
        assert "agent-a" in names

    def test_search_across_tiers(self, registry: DeferredAgentRegistry) -> None:
        registry.register_agent(
            _make_manifest("core-test", description="Testing core"),
            deferred=False,
        )
        registry.register_agent(
            _make_manifest("deferred-test", description="Testing deferred"),
            deferred=True,
        )
        results = registry.search_agents("Testing")
        assert len(results) == 2

    def test_search_max_results(self, registry: DeferredAgentRegistry) -> None:
        for i in range(10):
            registry.register_agent(
                _make_manifest(f"agent-{i}", description="Shared description"),
                deferred=True,
            )
        results = registry.search_agents("Shared", max_results=3)
        assert len(results) == 3

    def test_search_multi_word_query(self, registry: DeferredAgentRegistry) -> None:
        registry.register_agent(
            _make_manifest("code-reviewer", description="Reviews code for bugs"),
            deferred=True,
        )
        results = registry.search_agents("code reviewer")
        assert len(results) == 1


# ============================================================================
# MCPGateway — register_agent
# ============================================================================


class TestMCPGatewayRegisterAgent:
    """Tests for MCPGateway.register_agent()."""

    @pytest.mark.asyncio
    async def test_register_multiple(self, gateway: MCPGateway) -> None:
        for i in range(3):
            await gateway.register_agent(_make_manifest(f"agent-{i}"), deferred=True)
        assert len(gateway.registry.list_deferred_agents()) == 3


# ============================================================================
# MCPGateway — _register_agent_tools
# ============================================================================


class TestMCPGatewayRegisterAgentTools:
    """Tests for MCPGateway._register_agent_tools()."""

    @pytest.mark.asyncio
    async def test_register_tools_creates_adapters(self, gateway: MCPGateway) -> None:
        manifest = _make_manifest("tool-agent")
        await gateway.register_agent(manifest, deferred=True, start_command=[])
        await gateway.registry.activate_agent("tool-agent")
        await gateway._register_agent_tools("tool-agent")
        # Verify adapters were created
        adapters = gateway.registry._tool_adapters.get("tool-agent", [])
        assert len(adapters) >= 1

    @pytest.mark.asyncio
    async def test_register_tools_unknown_agent(self, gateway: MCPGateway) -> None:
        """Registering tools for nonexistent agent does nothing."""
        await gateway._register_agent_tools("nonexistent")
        assert "nonexistent" not in gateway.registry._tool_adapters

    @pytest.mark.asyncio
    async def test_register_tools_not_activated(self, gateway: MCPGateway) -> None:
        """Registering tools for dormant agent does nothing."""
        manifest = _make_manifest("dormant")
        await gateway.register_agent(manifest, deferred=True)
        await gateway._register_agent_tools("dormant")
        assert "dormant" not in gateway.registry._tool_adapters

    @pytest.mark.asyncio
    async def test_register_tools_skips_when_already_registered_and_alive(
        self, gateway: MCPGateway
    ) -> None:
        """Second _register_agent_tools call skips when agent still alive (lines 251-255)."""
        manifest = _make_manifest("skip-agent")
        await gateway.register_agent(manifest, deferred=True, start_command=[])

        # Directly set up the state: agent is registered, info has a live handle.

        mock_handle = MagicMock()
        mock_handle.is_alive = True
        info = gateway.registry.get_agent_info("skip-agent")
        info.tool_schemas = [{"name": "tool1", "inputSchema": {"type": "object"}}]
        info.handle = mock_handle
        gateway._registered_agents.add("skip-agent")

        # First call registers the tool
        await gateway._register_agent_tools("skip-agent")

        # Second call should hit the early-return at line 255
        await gateway._register_agent_tools("skip-agent")

        # Agent should still be in registered set
        assert "skip-agent" in gateway._registered_agents


# ============================================================================
# MCPGateway — _make_tool_func
# ============================================================================


class TestMCPGatewayMakeToolFunc:
    """Tests for MCPGateway._make_tool_func()."""

    @pytest.mark.asyncio
    async def test_func_returns_error_if_no_handle(self, gateway: MCPGateway) -> None:
        schema = _make_tool_schema("tool")
        adapter = McpToolAdapter(server_name="nope", tool_schema=schema)
        func = gateway._make_tool_func(adapter)
        result = await func(x=1)
        assert "Error" in result
        assert "not available" in result

    @pytest.mark.asyncio
    async def test_func_delegates_to_adapter(self, gateway: MCPGateway) -> None:
        # Use a name without hyphens so sanitized name == registry key
        manifest = _make_manifest("run_agent")
        await gateway.register_agent(manifest, deferred=False)
        info = gateway.registry.get_agent_info("run_agent")
        assert info is not None
        mock_handle = _mock_agent_handle("run_agent", alive=True)
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="tool output",
            status="completed",
        )
        mock_handle.ipc.receive_until_result.return_value = response
        info.handle = mock_handle

        schema = _make_tool_schema("tool")
        adapter = McpToolAdapter(server_name="run_agent", tool_schema=schema)
        # Manually inject adapter
        gateway.registry._tool_adapters["run_agent"] = [adapter]

        func = gateway._make_tool_func(adapter)
        result = await func(x=1)
        assert result == "tool output"

    @pytest.mark.asyncio
    async def test_func_handles_ipc_failure_gracefully(self, gateway: MCPGateway) -> None:
        """IPC error between is_alive check and execute returns error string."""
        manifest = _make_manifest("crash_agent")
        await gateway.register_agent(manifest, deferred=False)
        info = gateway.registry.get_agent_info("crash_agent")
        assert info is not None
        mock_handle = _mock_agent_handle("crash_agent", alive=True)
        info.handle = mock_handle

        schema = _make_tool_schema("tool")
        adapter = McpToolAdapter(server_name="crash_agent", tool_schema=schema)
        # Make execute raise directly so gateway's own except block is tested
        adapter.execute = AsyncMock(side_effect=ConnectionError("pipe closed"))  # type: ignore[method-assign]
        gateway.registry._tool_adapters["crash_agent"] = [adapter]

        func = gateway._make_tool_func(adapter)
        result = await func(x=1)
        assert "Error" in result
        assert "IPC failed" in result

    async def test_func_propagates_programming_errors(self, gateway: MCPGateway) -> None:
        """Programming errors (TypeError, AttributeError) propagate
        instead of being swallowed as IPC errors."""
        manifest = _make_manifest("prog_err_agent")
        await gateway.register_agent(manifest, deferred=False)
        info = gateway.registry.get_agent_info("prog_err_agent")
        assert info is not None
        mock_handle = _mock_agent_handle("prog_err_agent", alive=True)
        info.handle = mock_handle

        schema = _make_tool_schema("tool")
        adapter = McpToolAdapter(server_name="prog_err_agent", tool_schema=schema)
        adapter.execute = AsyncMock(side_effect=TypeError("bad arg type"))  # type: ignore[method-assign]
        gateway.registry._tool_adapters["prog_err_agent"] = [adapter]

        func = gateway._make_tool_func(adapter)
        with pytest.raises(TypeError, match="bad arg type"):
            await func(x=1)


# ============================================================================
# MCPGateway — core tools (_search_and_activate, _list_agents, _agent_info)
# ============================================================================


class TestMCPGatewayCoreTools:
    """Tests for MCPGateway core tool methods."""

    @pytest.mark.asyncio
    async def test_search_and_activate_no_match(self, gateway: MCPGateway) -> None:
        result = await gateway._search_and_activate("nonexistent")
        assert "No matching agents found" in result

    @pytest.mark.asyncio
    async def test_search_and_activate_match(self, gateway: MCPGateway) -> None:
        manifest = _make_manifest("test-agent", description="A test agent for searching")
        await gateway.register_agent(manifest, deferred=True, start_command=[])
        result = await gateway._search_and_activate("test")
        assert "test-agent" in result
        assert "activated" in result.lower() or "loaded" in result.lower()

    @pytest.mark.asyncio
    async def test_list_agents_with_agents(self, gateway: MCPGateway) -> None:
        await gateway.register_agent(
            _make_manifest("core-a", description="Core agent"), deferred=False
        )
        await gateway.register_agent(
            _make_manifest("deferred-a", description="Deferred agent"),
            deferred=True,
        )
        result = await gateway._list_agents()
        assert "core-a" in result
        assert "deferred-a" in result

    @pytest.mark.asyncio
    async def test_agent_info_found(self, gateway: MCPGateway) -> None:
        manifest = _make_manifest(
            "detail-agent",
            description="Detailed agent",
            role=None,
        )
        await gateway.register_agent(manifest, deferred=True)
        result = await gateway._agent_info("detail-agent")
        assert "detail-agent" in result
        assert "0.1.0" in result
        assert "atomic" in result
        assert "dormant" in result

    @pytest.mark.asyncio
    async def test_agent_info_with_activated_tools(self, gateway: MCPGateway) -> None:
        await gateway.register_agent(
            _make_manifest("act-agent", description="Active agent"),
            deferred=True,
            start_command=[],
        )
        await gateway.registry.activate_agent("act-agent")
        result = await gateway._agent_info("act-agent")
        assert "activated" in result


# ============================================================================
# MCPGateway — run methods
# ============================================================================


class TestMCPGatewayRun:
    """Tests for MCPGateway.run_stdio(), run_sse(), stop()."""

    @pytest.mark.asyncio
    async def test_run_stdio(self, gateway: MCPGateway) -> None:
        with patch.object(gateway._mcp, "run") as mock_run:
            await gateway.run_stdio()
            mock_run.assert_called_once_with(transport="stdio")

    @pytest.mark.asyncio
    async def test_run_sse_default(self, gateway: MCPGateway) -> None:
        with patch.object(gateway._mcp, "run") as mock_run:
            await gateway.run_sse()
            mock_run.assert_called_once_with(transport="sse", host="127.0.0.1", port=8080)

    @pytest.mark.asyncio
    async def test_stop(self, gateway: MCPGateway, process_manager: MagicMock) -> None:
        await gateway.stop()
        process_manager.stop_all.assert_awaited_once()


# ============================================================================
# Merged from iteration 16: DeferredRegistry deduplication
# ============================================================================


# ============================================================================
# Merged from iteration 18: ToolAdapter original name, core tool registration,
# identity check
# ============================================================================


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


# ============================================================================
# Merged from iteration 24: McpToolAdapter execute status edge cases
# ============================================================================


def _make_bare_adapter() -> McpToolAdapter:
    """Create a bare McpToolAdapter without calling __init__."""
    adapter = McpToolAdapter.__new__(McpToolAdapter)
    adapter.agent_name = "test-agent"
    adapter.server_name = "test_agent"
    adapter.tool_name = "my_tool"
    adapter._original_tool_name = "my_tool"
    adapter.full_name = "mcp__test_agent__my_tool"
    adapter.description = "test tool"
    adapter._input_schema = {}
    return adapter


def _make_mock_handle_for_status(is_alive: bool = True) -> MagicMock:
    """Create a mock AgentHandle for status tests."""
    handle = MagicMock(spec=AgentHandle)
    handle.is_alive = is_alive
    handle.ipc = MagicMock()
    handle.ipc.send_chat = AsyncMock()
    handle.ipc.receive_until_result = AsyncMock()
    return handle


class TestMcpToolAdapterExecuteStatusNone:
    """execute returns success=True when response.status is None.

    Minimal agent implementations may omit the status field. A RESULT
    type response with no status defaults to success — only ERROR type
    responses are treated as failure.
    """

    @pytest.mark.asyncio
    async def test_status_none_returns_success_true(self) -> None:
        adapter = _make_bare_adapter()
        handle = _make_mock_handle_for_status()

        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            status=None,
            content="test",
        )
        handle.ipc.receive_until_result = AsyncMock(return_value=response)

        result = await adapter.execute(handle, {})
        assert result["success"] is True


class TestMcpToolAdapterExecuteStatusCompleted:
    """execute returns success=True when response.status is 'completed'."""

    @pytest.mark.asyncio
    async def test_status_completed_returns_success_true(self) -> None:
        adapter = _make_bare_adapter()
        handle = _make_mock_handle_for_status()

        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            status="completed",
            content="done",
        )
        handle.ipc.receive_until_result = AsyncMock(return_value=response)

        result = await adapter.execute(handle, {})
        assert result["success"] is True


class TestMcpToolAdapterAffirmativeStatus:
    """execute uses affirmative status check — only 'completed' is success.

    Ambiguous statuses like 'running', 'pending', 'timeout' must NOT be
    treated as success (was a defect: old code used
    `status not in (None, 'failed')`).
    """

    @pytest.mark.parametrize(
        "status",
        ["running", "pending", "timeout"],
    )
    @pytest.mark.asyncio
    async def test_ambiguous_status_not_success(self, status: str) -> None:
        adapter = _make_bare_adapter()
        handle = _make_mock_handle_for_status()
        response = AgentToPlatform(type=AgentToPlatformType.RESULT, status=status, content="...")
        handle.ipc.receive_until_result = AsyncMock(return_value=response)
        result = await adapter.execute(handle, {})
        assert result["success"] is False


# ---------------------------------------------------------------------------
# Iteration 24 fixes: gateway activation message, registry priority
# ---------------------------------------------------------------------------


# ============================================================================
# Iteration 25: Dead agent cleanup + lazy asyncio.Lock
# ============================================================================


class TestDeadAgentCleanup:
    """Tool invocation on dead agent must return error and clean up registration."""

    @pytest.mark.asyncio
    async def test_dead_agent_returns_error_message(self) -> None:
        """Invoking a tool on a dead agent returns 'process has died' error."""
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        manifest = _make_manifest("dead-agent")
        await gw.register_agent(manifest, deferred=False)

        # Set up registry state: agent has tool schemas + dead handle
        info = gw.registry.get_agent_info("dead-agent")
        assert info is not None
        dead_handle = _mock_agent_handle("dead-agent", alive=False)
        info.handle = dead_handle
        info.tool_schemas = [{"name": "do_work", "description": "Work"}]

        # Inject adapter
        schema = _make_tool_schema("do_work", "Work")
        adapter = McpToolAdapter(server_name="dead-agent", tool_schema=schema)
        gw.registry._tool_adapters["dead-agent"] = [adapter]

        # Register tools (this adds to _registered_agents)
        await gw._register_agent_tools("dead-agent")

        # Create the tool func and invoke it
        func = gw._make_tool_func(adapter)
        result = await func(x=1)

        assert "Error" in result
        assert "process has died" in result

    @pytest.mark.asyncio
    async def test_dead_agent_removed_from_registered_agents(self) -> None:
        """Dead agent is removed from _registered_agents, allowing re-registration."""
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        manifest = _make_manifest("dead-agent")
        await gw.register_agent(manifest, deferred=False)

        info = gw.registry.get_agent_info("dead-agent")
        assert info is not None
        dead_handle = _mock_agent_handle("dead-agent", alive=False)
        info.handle = dead_handle
        info.tool_schemas = [{"name": "do_work", "description": "Work"}]

        schema = _make_tool_schema("do_work", "Work")
        adapter = McpToolAdapter(server_name="dead-agent", tool_schema=schema)
        gw.registry._tool_adapters["dead-agent"] = [adapter]

        await gw._register_agent_tools("dead-agent")
        assert "dead-agent" in gw._registered_agents

        # Invoke tool — should detect dead handle and clean up
        func = gw._make_tool_func(adapter)
        await func(x=1)

        assert "dead-agent" not in gw._registered_agents


# ============================================================================
# Regression: DeferredRegistry lazy lock + activation guard (from iter 42 audit)
# ============================================================================


class TestDeferredRegistryActivationGuard:
    """Registry propagates ProcessManager exceptions during activation.

    The registry does not swallow subprocess start errors. It lets them
    propagate to the caller (Gateway), which handles the failure.
    """

    @pytest.mark.asyncio
    async def test_activate_agent_propagates_start_error(self) -> None:
        """When ProcessManager.start_agent raises, activation propagates
        the exception so the Gateway layer can handle it."""
        pm = MagicMock(spec=ProcessManager)
        pm.start_agent = AsyncMock(side_effect=OSError("subprocess failed"))
        registry = DeferredAgentRegistry(pm)

        manifest = AgentManifest(
            name="failing-agent",
            version="0.1.0",
            type=AgentType.ATOMIC,
            description="A test agent",
        )
        registry.register_agent(
            manifest,
            deferred=True,
            start_command=["python3", "-m", "failing"],
        )

        # Registry MUST propagate the error, not swallow it
        with pytest.raises(OSError, match="subprocess failed"):
            await registry.activate_agent("failing-agent")

    @pytest.mark.asyncio
    async def test_activate_agent_no_subprocess_returns_placeholder(
        self,
    ) -> None:
        """When agent has no start_command, activation returns placeholder
        schemas from manifest metadata (no subprocess needed)."""
        pm = MagicMock(spec=ProcessManager)
        registry = DeferredAgentRegistry(pm)

        manifest = AgentManifest(
            name="static-agent",
            version="0.1.0",
            type=AgentType.ATOMIC,
            description="A static agent",
        )
        registry.register_agent(
            manifest,
            deferred=True,
            # No start_command: agent has no subprocess
        )

        schemas = await registry.activate_agent("static-agent")
        assert isinstance(schemas, list)
        assert len(schemas) >= 1
        # Placeholder schema should have the agent name in the tool name
        assert "static-agent" in schemas[0].get("name", "")


# ============================================================================
# Fix 1 regression: _invoke does NOT re-acquire lock (deadlock prevention)
# ============================================================================


class TestInvokeNoLockReacquire:
    """_invoke must not acquire the registration lock.

    Regression: _invoke used ``async with self._get_lock()`` to call
    ``set.discard``, which deadlocked if the lock was already held by
    ``_register_agent_tools`` (asyncio.Lock is non-reentrant).  The
    discard on a set is atomic and does not need the lock.
    """

    @pytest.mark.asyncio
    async def test_invoke_dead_agent_no_deadlock(self) -> None:
        """Invoking a tool on a dead agent does not deadlock even when
        the registration lock is held by another coroutine."""
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        manifest = _make_manifest("deadlock-agent")
        await gw.register_agent(manifest, deferred=False)

        info = gw.registry.get_agent_info("deadlock-agent")
        assert info is not None
        dead_handle = _mock_agent_handle("deadlock-agent", alive=False)
        info.handle = dead_handle
        info.tool_schemas = [{"name": "work", "description": "Work"}]

        schema = _make_tool_schema("work", "Work")
        adapter = McpToolAdapter(server_name="deadlock-agent", tool_schema=schema)
        gw.registry._tool_adapters["deadlock-agent"] = [adapter]

        await gw._register_agent_tools("deadlock-agent")
        assert "deadlock-agent" in gw._registered_agents

        func = gw._make_tool_func(adapter)

        # Hold the lock externally to simulate the scenario where
        # _register_agent_tools still has it.  _invoke must NOT block.
        async with gw._reg_lock:
            result = await asyncio.wait_for(func(x=1), timeout=1.0)

        assert "Error" in result
        assert "process has died" in result


# ============================================================================
# Fix 2 regression: get_agent_info prefers activated over dormant
# ============================================================================


# ============================================================================
# Regression: McpToolAdapter IPC per-agent lock (from security audit)
# ============================================================================


class TestMcpToolAdapterIPCLock:
    """McpToolAdapter.execute() acquires a per-agent asyncio.Lock for IPC.

    The lock prevents interleaving of send_chat/receive_until_result calls
    when the same agent is invoked concurrently (e.g. two tool calls to
    the same agent from parallel coroutines).
    """

    @pytest.mark.asyncio
    async def test_ipc_lock_created_on_execute(self) -> None:
        """After execute(), a per-agent lock exists in _ipc_locks."""
        schema = _make_tool_schema("tool")
        adapter = McpToolAdapter(server_name="lock-test-agent", tool_schema=schema)

        # Before any execute, no lock for this agent's original name
        assert "lock-test-agent" not in _ipc_lock_registry

        handle = _mock_agent_handle("lock-test-agent", alive=True)
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="ok",
            status="completed",
        )
        handle.ipc.receive_until_result.return_value = response

        # Clean _ipc_locks to isolate this test
        remove_all_locks()
        try:
            await adapter.execute(handle, {})
            # Lock is keyed by original (unsanitized) agent_name, not server_name
            assert "lock-test-agent" in _ipc_lock_registry
        finally:
            remove_all_locks()

    @pytest.mark.asyncio
    async def test_ipc_lock_prevents_concurrent_interleave(self) -> None:
        """Two concurrent execute calls to the same agent do not interleave.

        The second call should only start IPC after the first completes
        receive_until_result.
        """
        schema = _make_tool_schema("tool")
        adapter = McpToolAdapter(server_name="conc-agent", tool_schema=schema)

        call_order: list[str] = []

        async def slow_receive(timeout: float = 300.0):  # pyright: ignore[reportUnusedParameter]
            call_order.append("receive_start")
            await asyncio.sleep(0.05)
            call_order.append("receive_end")
            return AgentToPlatform(
                type=AgentToPlatformType.RESULT,
                content="done",
                status="completed",
            )

        async def fast_receive(timeout: float = 300.0):  # pyright: ignore[reportUnusedParameter]
            call_order.append("receive_start_2")
            call_order.append("receive_end_2")
            return AgentToPlatform(
                type=AgentToPlatformType.RESULT,
                content="done2",
                status="completed",
            )

        handle1 = _mock_agent_handle("conc-agent", alive=True)
        handle1.ipc.send_chat = AsyncMock()
        handle1.ipc.receive_until_result = slow_receive

        handle2 = _mock_agent_handle("conc-agent", alive=True)
        handle2.ipc.send_chat = AsyncMock()
        handle2.ipc.receive_until_result = fast_receive

        remove_all_locks()
        try:
            # Launch both concurrently
            results = await asyncio.gather(
                adapter.execute(handle1, {}),
                adapter.execute(handle2, {}),
            )

            # Both should succeed
            assert results[0]["success"] is True
            assert results[1]["success"] is True

            # The first receive must complete before the second starts.
            # Without the lock, the second send_chat could happen before
            # the first receive_until_result completes.
            assert call_order.index("receive_end") < call_order.index("receive_start_2"), (
                f"Concurrent calls interleaved: {call_order}"
            )
        finally:
            remove_all_locks()

    @pytest.mark.asyncio
    async def test_different_agents_use_different_locks(self) -> None:
        """Two different agents can execute concurrently (separate locks)."""
        schema_a = _make_tool_schema("tool_a")
        schema_b = _make_tool_schema("tool_b")
        adapter_a = McpToolAdapter(server_name="agent-a", tool_schema=schema_a)
        adapter_b = McpToolAdapter(server_name="agent-b", tool_schema=schema_b)

        handle_a = _mock_agent_handle("agent-a", alive=True)
        handle_a.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="a",
            status="completed",
        )

        handle_b = _mock_agent_handle("agent-b", alive=True)
        handle_b.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="b",
            status="completed",
        )

        remove_all_locks()
        try:
            results = await asyncio.gather(
                adapter_a.execute(handle_a, {}),
                adapter_b.execute(handle_b, {}),
            )
            assert results[0]["output"] == "a"
            assert results[1]["output"] == "b"
            assert "agent-a" in _ipc_lock_registry
            assert "agent-b" in _ipc_lock_registry
        finally:
            remove_all_locks()


# ============================================================================
# McpToolAdapter lock cleanup classmethods
# ============================================================================


class TestMcpToolAdapterLockCleanup:
    """remove_lock and remove_all_locks clean up class-level locks."""

    def setup_method(self) -> None:
        remove_all_locks()

    def teardown_method(self) -> None:
        remove_all_locks()

    def test_remove_all_locks_clears_everything(self) -> None:
        """remove_all_locks clears all entries."""
        _ipc_lock_registry["a"] = asyncio.Lock()
        _ipc_lock_registry["b"] = asyncio.Lock()
        remove_all_locks()
        assert len(_ipc_lock_registry) == 0

    @pytest.mark.asyncio
    async def test_gateway_stop_cleans_locks(self) -> None:
        """MCPGateway.stop() calls remove_all_locks."""
        pm = MagicMock()
        pm.stop_all = AsyncMock()
        router = MagicMock()
        _ipc_lock_registry["stale-agent"] = asyncio.Lock()

        gw = MCPGateway(pm, router)
        await gw.stop()
        assert len(_ipc_lock_registry) == 0

    @pytest.mark.asyncio
    async def test_invoke_dead_agent_removes_lock(self) -> None:
        """_invoke detects dead agent and cleans up gateway registration.

        Note: remove_lock no longer pops from _ipc_locks (lock stays for
        serialization safety).  It is cleaned up by remove_all_locks on
        gateway shutdown.
        """
        pm = MagicMock()
        router = MagicMock()
        gw = MCPGateway(pm, router)

        schema = _make_tool_schema("test-tool", "desc")
        adapter = McpToolAdapter("dead-agent", schema)
        _ipc_lock_registry["dead-agent"] = asyncio.Lock()

        # Register the agent info with a dead handle
        dead_handle = MagicMock()
        dead_handle.is_alive = False
        info = AgentInfo(
            name="dead-agent",
            manifest=MagicMock(),
            tool_schemas=[schema],
            handle=dead_handle,
        )
        gw._registry._core_agents["dead-agent"] = info
        gw._registered_agents.add("dead-agent")

        func = gw._make_tool_func(adapter)
        result = await func()
        assert "process has died" in result
        # Gateway registration is cleaned up
        assert "dead-agent" not in gw._registered_agents
        # IPC lock stays in _ipc_locks for serialization safety
        assert "dead-agent" in _ipc_lock_registry


# ============================================================================
# Fix 2 regression: get_agent_info prefers activated over dormant
# ============================================================================


class TestGetAgentInfoPriority:
    """get_agent_info returns the activated entry when agent is in both tiers.

    When an agent name appears in both core and deferred dicts, the
    dormant deferred entry (no tool_schemas, no handle) must NOT be
    returned over the functional core entry.
    """

    def test_prefers_activated_deferred_over_core(self) -> None:
        """When both dicts have the same name, activated deferred wins."""
        pm = MagicMock()
        registry = DeferredAgentRegistry(pm)

        # Directly insert into both dicts to simulate the dual-entry scenario.
        core_manifest = _make_manifest("shared-agent", description="Core version")
        registry._core_agents["shared-agent"] = AgentInfo(
            name="shared-agent",
            manifest=core_manifest,
        )

        deferred_manifest = _make_manifest("shared-agent", description="Deferred version")
        registry._deferred_agents["shared-agent"] = AgentInfo(
            name="shared-agent",
            manifest=deferred_manifest,
            tool_schemas=[{"name": "tool1"}],  # activated
        )

        result = registry.get_agent_info("shared-agent")
        assert result is not None
        # The activated deferred entry should be returned
        assert result.is_activated is True

    def test_prefers_core_over_dormant_deferred(self) -> None:
        pm = MagicMock()
        registry = DeferredAgentRegistry(pm)

        core_manifest = _make_manifest("dual-agent", description="Core version")
        registry.register_agent(core_manifest, deferred=False)

        # Force both entries to exist: core is functional, deferred is dormant
        registry._deferred_agents["dual-agent"] = AgentInfo(
            name="dual-agent",
            manifest=_make_manifest("dual-agent", description="Deferred version"),
            # No tool_schemas — dormant
        )

        result = registry.get_agent_info("dual-agent")
        assert result is not None
        # Core entry should be returned because deferred is not activated
        assert result.manifest.description == "Core version"


# ============================================================================
# Coverage: _search_and_activate error path (lines 100-106)
# ============================================================================


class TestSearchAndActivateErrorPath:
    """_search_and_activate logs and reports activation failures gracefully.

    When _register_agent_tools raises during activation, the exception is
    caught, logged, and a failure message is appended instead of propagating.
    """

    @pytest.mark.asyncio
    async def test_activation_failure_reports_error(self) -> None:
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        manifest = _make_manifest("fail-agent", description="Agent that fails")
        gw._registry.register_agent(manifest, deferred=True)

        # activate_agent succeeds but _register_agent_tools raises
        with patch.object(
            gw._registry,
            "search_agents",
            return_value=[manifest],
        ), patch.object(
            gw._registry,
            "activate_agent",
            new_callable=AsyncMock,
            return_value=[{"name": "t", "description": "d", "inputSchema": {}}],
        ), patch.object(
            gw,
            "_register_agent_tools",
            new_callable=AsyncMock,
            side_effect=RuntimeError("subprocess crashed"),
        ):
            result = await gw._search_and_activate("fail")

        assert "fail-agent" in result
        assert "activation failed" in result
        assert "subprocess crashed" in result

    @pytest.mark.asyncio
    async def test_activation_mixed_success_and_failure(self) -> None:
        """When one agent succeeds and another fails, both are reported."""
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        manifest_ok = _make_manifest("ok-agent", description="Works fine")
        manifest_bad = _make_manifest("bad-agent", description="Always breaks")
        gw._registry.register_agent(manifest_ok, deferred=True)
        gw._registry.register_agent(manifest_bad, deferred=True)

        call_count = 0

        async def activate_side_effect(name: str):
            nonlocal call_count
            call_count += 1
            return [{"name": f"tool_{name}", "description": "d", "inputSchema": {}}]

        async def register_side_effect(name: str):
            if name == "bad-agent":
                raise ConnectionError("IPC broken")

        with patch.object(
            gw._registry,
            "search_agents",
            return_value=[manifest_ok, manifest_bad],
        ), patch.object(
            gw._registry,
            "activate_agent",
            new_callable=AsyncMock,
            side_effect=activate_side_effect,
        ), patch.object(
            gw,
            "_register_agent_tools",
            new_callable=AsyncMock,
            side_effect=register_side_effect,
        ):
            result = await gw._search_and_activate("agent")

        assert "ok-agent" in result
        assert "tools loaded" in result
        assert "bad-agent" in result
        assert "activation failed" in result
        assert "IPC broken" in result


# ============================================================================
# Coverage: _list_agents "activated" tier branch (line 131)
# ============================================================================


# ============================================================================
# Coverage: _agent_info role, dependencies, running process (lines 168, 170, 174)
# ============================================================================


class TestAgentInfoRoleAndDependencies:
    """_agent_info displays role and dependencies when set in manifest,
    and shows 'Process: running' when the agent handle is alive.
    """

    @pytest.mark.asyncio
    async def test_agent_info_with_role(self) -> None:
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        manifest = _make_manifest(
            "role-agent",
            description="Has a role",
            role=AgentRole.WORKER,
        )
        await gw.register_agent(manifest, deferred=True)
        result = await gw._agent_info("role-agent")
        assert "worker" in result

    @pytest.mark.asyncio
    async def test_agent_info_with_dependencies(self) -> None:
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        deps = AgentDependencies(atomic_agents=["atom-a", "atom-b"])
        manifest = _make_manifest(
            "comp-agent",
            description="Composite agent",
            agent_type=AgentType.COMPOSITE,
            dependencies=deps,
        )
        await gw.register_agent(manifest, deferred=True)
        result = await gw._agent_info("comp-agent")
        assert "atom-a" in result
        assert "atom-b" in result
        assert "Dependencies" in result

    @pytest.mark.asyncio
    async def test_agent_info_running_process(self) -> None:
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        manifest = _make_manifest("running-agent", description="Running")
        await gw.register_agent(manifest, deferred=False)

        info = gw.registry.get_agent_info("running-agent")
        assert info is not None
        alive_handle = _mock_agent_handle("running-agent", alive=True)
        info.handle = alive_handle

        result = await gw._agent_info("running-agent")
        assert "running" in result


# ============================================================================
# Coverage: _register_agent_tools name collision disambiguation (lines 254-266, 270-271)
# ============================================================================


class TestRegisterAgentToolsCollision:
    """_register_agent_tools disambiguates tool name collisions.

    When two adapters within the same agent produce the same sanitized
    tool full_name, the second one gets a numeric suffix appended (_2, _3, ...).

    FastMCP rejects functions with **kwargs, so we mock _mcp.tool to
    succeed, ensuring the name enters _registered_tool_names.
    """

    @pytest.mark.asyncio
    async def test_collision_gets_numeric_suffix(self) -> None:
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        # Mock FastMCP.tool so it accepts the _invoke function
        mcp_tool_mock = MagicMock()
        gw._mcp.tool = mcp_tool_mock

        schema = _make_tool_schema("shared_tool", "A tool")
        adapter1 = McpToolAdapter(server_name="agent-x", tool_schema=schema)
        adapter2 = McpToolAdapter(server_name="agent-x", tool_schema=schema)

        manifest = _make_manifest("agent-x")
        gw._registry.register_agent(manifest, deferred=False)
        info = gw._registry.get_agent_info("agent-x")
        assert info is not None
        info.tool_schemas = [schema, schema]
        gw._registry._tool_adapters["agent-x"] = [adapter1, adapter2]

        await gw._register_agent_tools("agent-x")

        # Adapter objects are NOT mutated — collision is tracked via
        # _registered_tool_names and the registered_name parameter.
        assert adapter1.full_name == adapter2.full_name  # both unchanged
        # But both names are in the registered set (one with suffix)
        assert "mcp__agent_x__shared_tool" in gw._registered_tool_names
        assert "mcp__agent_x__shared_tool_2" in gw._registered_tool_names

    @pytest.mark.asyncio
    async def test_multiple_collisions_increment_suffix(self) -> None:
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        mcp_tool_mock = MagicMock()
        gw._mcp.tool = mcp_tool_mock

        schema = _make_tool_schema("col_tool", "Collision tool")
        adapter1 = McpToolAdapter(server_name="cagent", tool_schema=schema)
        adapter2 = McpToolAdapter(server_name="cagent", tool_schema=schema)
        adapter3 = McpToolAdapter(server_name="cagent", tool_schema=schema)

        manifest = _make_manifest("cagent")
        gw._registry.register_agent(manifest, deferred=False)
        info = gw._registry.get_agent_info("cagent")
        assert info is not None
        info.tool_schemas = [schema, schema, schema]
        gw._registry._tool_adapters["cagent"] = [adapter1, adapter2, adapter3]

        await gw._register_agent_tools("cagent")

        # adapter objects not mutated; collision tracked in _registered_tool_names
        base = "mcp__cagent__col_tool"
        assert base in gw._registered_tool_names
        assert f"{base}_2" in gw._registered_tool_names
        assert f"{base}_3" in gw._registered_tool_names


# ============================================================================
# Coverage: _invoke error path for failed execution (line 309)
# ============================================================================


class TestInvokeExecutionError:
    """_invoke returns formatted error when adapter.execute returns failure."""

    @pytest.mark.asyncio
    async def test_invoke_returns_error_on_execution_failure(self) -> None:
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        manifest = _make_manifest("err-agent")
        await gw.register_agent(manifest, deferred=False)

        info = gw.registry.get_agent_info("err-agent")
        assert info is not None
        alive_handle = _mock_agent_handle("err-agent", alive=True)
        info.handle = alive_handle
        info.tool_schemas = [{"name": "fail_tool", "description": "Fails"}]

        schema = _make_tool_schema("fail_tool", "Fails")
        adapter = McpToolAdapter(server_name="err-agent", tool_schema=schema)
        gw.registry._tool_adapters["err-agent"] = [adapter]

        # Make execute return a failure
        with patch.object(
            adapter,
            "execute",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "task blew up"},
        ):
            func = gw._make_tool_func(adapter)
            result = await func(x=1)

        assert "Error" in result
        assert "task blew up" in result

    @pytest.mark.asyncio
    async def test_invoke_returns_unknown_failure_when_no_error_key(self) -> None:
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        manifest = _make_manifest("nokey-agent")
        await gw.register_agent(manifest, deferred=False)

        info = gw.registry.get_agent_info("nokey-agent")
        assert info is not None
        alive_handle = _mock_agent_handle("nokey-agent", alive=True)
        info.handle = alive_handle
        info.tool_schemas = [{"name": "nk_tool", "description": "No key"}]

        schema = _make_tool_schema("nk_tool", "No key")
        adapter = McpToolAdapter(server_name="nokey-agent", tool_schema=schema)
        gw.registry._tool_adapters["nokey-agent"] = [adapter]

        with patch.object(
            adapter,
            "execute",
            new_callable=AsyncMock,
            return_value={"success": False},
        ):
            func = gw._make_tool_func(adapter)
            result = await func()

        assert "Error" in result
        assert "unknown failure" in result


# ============================================================================
# Coverage: _register_agent_tools FastMCP registration error (lines 274-280)
# ============================================================================


# ============================================================================
# Coverage: deferred_registry.py missed lines
# ============================================================================


class TestDeferredRegistryFetchAgentTools:
    """Tests for _fetch_agent_tools edge cases (lines 259, 268, 276-277)."""

    @pytest.mark.asyncio
    async def test_fetch_tools_none_handle_returns_empty(self) -> None:
        """_fetch_agent_tools returns [] when handle is None (line 259)."""
        pm = MagicMock(spec=ProcessManager)
        registry = DeferredAgentRegistry(pm)

        manifest = _make_manifest("no-handle")
        info = AgentInfo(name="no-handle", manifest=manifest)
        # handle is None by default

        result = await registry._fetch_agent_tools(info)
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_tools_response_output_list(self) -> None:
        """_fetch_agent_tools returns output list from IPC response (line 268)."""
        pm = MagicMock(spec=ProcessManager)
        registry = DeferredAgentRegistry(pm)

        manifest = _make_manifest("list-agent")
        handle = _mock_agent_handle("list-agent", alive=True)
        tool_list = [
            {"name": "tool_a", "description": "Tool A", "inputSchema": {}},
            {"name": "tool_b", "description": "Tool B", "inputSchema": {}},
        ]
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="",
            status="completed",
            output=tool_list,
        )
        handle.ipc.receive_until_result.return_value = response

        info = AgentInfo(name="list-agent", manifest=manifest, handle=handle)
        result = await registry._fetch_agent_tools(info)
        assert result == tool_list

    @pytest.mark.asyncio
    async def test_fetch_tools_content_json_fallback(self) -> None:
        """_fetch_agent_tools parses response.content as JSON list when output is None (lines 276-277)."""
        pm = MagicMock(spec=ProcessManager)
        registry = DeferredAgentRegistry(pm)

        manifest = _make_manifest("json-agent")
        handle = _mock_agent_handle("json-agent", alive=True)
        import json

        tool_list = [{"name": "parsed_tool"}]
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content=json.dumps(tool_list),
            status="completed",
            output=None,  # output is None -> falls back to content parsing
        )
        handle.ipc.receive_until_result.return_value = response

        info = AgentInfo(name="json-agent", manifest=manifest, handle=handle)
        result = await registry._fetch_agent_tools(info)
        assert len(result) == 1
        assert result[0]["name"] == "parsed_tool"

    @pytest.mark.asyncio
    async def test_fetch_tools_content_not_json_returns_fallback(self) -> None:
        """When content is not valid JSON, falls back to single chat tool."""
        pm = MagicMock(spec=ProcessManager)
        registry = DeferredAgentRegistry(pm)

        manifest = _make_manifest("bad-json-agent")
        handle = _mock_agent_handle("bad-json-agent", alive=True)
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="not json at all",
            status="completed",
            output=None,
        )
        handle.ipc.receive_until_result.return_value = response

        info = AgentInfo(name="bad-json-agent", manifest=manifest, handle=handle)
        result = await registry._fetch_agent_tools(info)
        assert len(result) == 1
        assert result[0]["name"] == "chat"

    @pytest.mark.asyncio
    async def test_fetch_tools_ipc_exception_returns_fallback(self) -> None:
        """When IPC raises an exception, returns fallback chat tool."""
        pm = MagicMock(spec=ProcessManager)
        registry = DeferredAgentRegistry(pm)

        manifest = _make_manifest("ipc-fail-agent")
        handle = _mock_agent_handle("ipc-fail-agent", alive=True)
        handle.ipc.send_chat.side_effect = RuntimeError("IPC timeout")

        info = AgentInfo(name="ipc-fail-agent", manifest=manifest, handle=handle)
        result = await registry._fetch_agent_tools(info)
        assert len(result) == 1
        assert result[0]["name"] == "chat"


# ============================================================================
# Iteration 88: Regression tests for dead-agent tool-name cleanup + empty
# tool list truthy bug
# ============================================================================


class TestDeadAgentToolNameCleanup:
    """Bug: _invoke cleaned _registered_agents but NOT _registered_tool_names.

    When an agent dies and is later re-registered, leftover tool names in
    _registered_tool_names cause the collision-detection logic to append
    numeric suffixes (e.g. ``mcp__agent__tool_2``) instead of keeping the
    original name.
    """

    @pytest.mark.asyncio
    async def test_tool_names_cleaned_on_dead_agent(self) -> None:
        """All tool names for a dead agent are removed from _registered_tool_names."""
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)
        # FastMCP rejects **kwargs functions, so mock it to accept any call.
        gw._mcp.tool = MagicMock()

        manifest = _make_manifest("stale-agent")
        await gw.register_agent(manifest, deferred=False)

        info = gw.registry.get_agent_info("stale-agent")
        assert info is not None
        dead_handle = _mock_agent_handle("stale-agent", alive=False)
        info.handle = dead_handle
        info.tool_schemas = [
            {"name": "do_work", "description": "Work"},
            {"name": "do_more", "description": "More"},
        ]

        adapter_a = McpToolAdapter("stale-agent", _make_tool_schema("do_work", "Work"))
        adapter_b = McpToolAdapter("stale-agent", _make_tool_schema("do_more", "More"))
        gw.registry._tool_adapters["stale-agent"] = [adapter_a, adapter_b]

        await gw._register_agent_tools("stale-agent")
        assert "stale-agent" in gw._registered_agents
        assert adapter_a.full_name in gw._registered_tool_names
        assert adapter_b.full_name in gw._registered_tool_names

        # Invoke via dead handle — triggers cleanup
        func = gw._make_tool_func(adapter_a)
        await func(x=1)

        assert "stale-agent" not in gw._registered_agents
        assert adapter_a.full_name not in gw._registered_tool_names
        assert adapter_b.full_name not in gw._registered_tool_names

    @pytest.mark.asyncio
    async def test_reregistration_keeps_original_names(self) -> None:
        """After dead-agent cleanup, re-registration uses original tool names (no suffix)."""
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)
        gw._mcp.tool = MagicMock()

        manifest = _make_manifest("restart-agent")
        await gw.register_agent(manifest, deferred=False)

        info = gw.registry.get_agent_info("restart-agent")
        assert info is not None

        # Phase 1: register tools with a dead handle
        dead_handle = _mock_agent_handle("restart-agent", alive=False)
        info.handle = dead_handle
        info.tool_schemas = [{"name": "compute", "description": "Compute"}]

        adapter = McpToolAdapter("restart-agent", _make_tool_schema("compute", "Compute"))
        gw.registry._tool_adapters["restart-agent"] = [adapter]
        await gw._register_agent_tools("restart-agent")

        # Trigger cleanup via invoke
        func = gw._make_tool_func(adapter)
        await func(x=1)
        assert "restart-agent" not in gw._registered_agents

        # Phase 2: agent comes back alive, re-register with new adapter
        alive_handle = _mock_agent_handle("restart-agent", alive=True)
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="42",
            status="completed",
        )
        alive_handle.ipc.receive_until_result.return_value = response
        info.handle = alive_handle
        info.tool_schemas = [{"name": "compute", "description": "Compute"}]

        adapter2 = McpToolAdapter("restart-agent", _make_tool_schema("compute", "Compute"))
        gw.registry._tool_adapters["restart-agent"] = [adapter2]
        await gw._register_agent_tools("restart-agent")

        # The new adapter should retain its original full_name — no _2 suffix
        assert adapter2.full_name == "mcp__restart_agent__compute"
        assert adapter2.full_name in gw._registered_tool_names


class TestEmptyToolListNotFalsy:
    """Bug: _fetch_agent_tools treated empty list [] as falsy, skipping the
    isinstance branch and falling through to the JSON-content fallback which
    would create a spurious chat tool.

    After fix: isinstance(response.output, list) correctly returns True for [].
    """

    @pytest.mark.asyncio
    async def test_empty_tool_list_returns_empty(self) -> None:
        """Agent returning zero tools should get an empty list, not a chat fallback."""
        pm = MagicMock(spec=ProcessManager)
        registry = DeferredAgentRegistry(pm)

        manifest = _make_manifest("no-tools-agent")
        handle = _mock_agent_handle("no-tools-agent", alive=True)
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="",
            status="completed",
            output=[],  # empty list — was falsy, now correctly handled
        )
        handle.ipc.receive_until_result.return_value = response

        info = AgentInfo(name="no-tools-agent", manifest=manifest, handle=handle)
        result = await registry._fetch_agent_tools(info)
        assert result == []


# ============================================================================
# Regression: _fetch_agent_tools ERROR response check (iter110b)
# ============================================================================


class TestFetchAgentToolsErrorResponse:
    """Bug: _fetch_agent_tools did not check for ERROR response type from IPC.

    When an agent returned an ERROR response during tool discovery, the code
    skipped the ERROR check and tried to parse response.content or
    response.output as JSON tool definitions. This caused confusing log
    messages like "not valid JSON" instead of surfacing the actual agent error.

    After fix: ERROR responses are detected early and return the fallback
    chat tool with a clear warning message.
    """

    @pytest.mark.asyncio
    async def test_error_response_returns_fallback(self) -> None:
        """ERROR response from agent returns fallback chat tool, not JSON parse error."""
        pm = MagicMock(spec=ProcessManager)
        registry = DeferredAgentRegistry(pm)

        manifest = _make_manifest("error-agent")
        handle = _mock_agent_handle("error-agent", alive=True)
        handle.ipc.receive_until_result = AsyncMock(
            return_value=AgentToPlatform(
                type=AgentToPlatformType.ERROR,
                error="agent internal failure",
            )
        )

        info = AgentInfo(name="error-agent", manifest=manifest, handle=handle)
        result = await registry._fetch_agent_tools(info)

        # Should return fallback chat tool, not attempt JSON parsing
        assert len(result) == 1
        assert result[0]["name"] == "chat"

    @pytest.mark.asyncio
    async def test_error_response_does_not_parse_content(self) -> None:
        """ERROR response with content field should NOT be parsed as tool JSON."""
        pm = MagicMock(spec=ProcessManager)
        registry = DeferredAgentRegistry(pm)

        manifest = _make_manifest("error-with-content")
        handle = _mock_agent_handle("error-with-content", alive=True)
        handle.ipc.receive_until_result = AsyncMock(
            return_value=AgentToPlatform(
                type=AgentToPlatformType.ERROR,
                content="this is not JSON tool data",
                error="something went wrong",
            )
        )

        info = AgentInfo(name="error-with-content", manifest=manifest, handle=handle)
        result = await registry._fetch_agent_tools(info)

        assert len(result) == 1
        assert result[0]["name"] == "chat"


# ============================================================================
# Regression: _invoke IPC exception handler must clean _registered_tool_names
# ============================================================================


class TestInvokeIPCExceptionToolNameCleanup:
    """Bug: _invoke exception handler cleaned _registered_agents but NOT
    _registered_tool_names on IPC transport failure.

    When adapter.execute() raises (BrokenPipeError, etc), the exception
    handler discarded the agent from _registered_agents but left stale
    tool names in _registered_tool_names, causing false collision suffixes
    on re-registration.
    """

    @pytest.mark.asyncio
    async def test_tool_names_cleaned_on_ipc_exception(self) -> None:
        """IPC exception during execute cleans both agents and tool names."""
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)
        gw._mcp.tool = MagicMock()

        manifest = _make_manifest("ipc-fail-agent")
        await gw.register_agent(manifest, deferred=False)

        info = gw.registry.get_agent_info("ipc-fail-agent")
        assert info is not None
        alive_handle = _mock_agent_handle("ipc-fail-agent", alive=True)
        info.handle = alive_handle
        info.tool_schemas = [
            {"name": "tool_x", "description": "X"},
            {"name": "tool_y", "description": "Y"},
        ]

        adapter_x = McpToolAdapter("ipc-fail-agent", _make_tool_schema("tool_x", "X"))
        adapter_y = McpToolAdapter("ipc-fail-agent", _make_tool_schema("tool_y", "Y"))
        gw.registry._tool_adapters["ipc-fail-agent"] = [adapter_x, adapter_y]

        await gw._register_agent_tools("ipc-fail-agent")
        assert "ipc-fail-agent" in gw._registered_agents
        assert adapter_x.full_name in gw._registered_tool_names
        assert adapter_y.full_name in gw._registered_tool_names

        # Make adapter.execute raise (simulates transport-layer failure)
        with patch.object(
            adapter_x,
            "execute",
            new_callable=AsyncMock,
            side_effect=BrokenPipeError("Connection lost"),
        ):
            func = gw._make_tool_func(adapter_x)
            result = await func(data="test")

        assert "IPC failed" in result
        assert "ipc-fail-agent" not in gw._registered_agents
        # Both tool names must be cleaned, not just the one that failed
        assert adapter_x.full_name not in gw._registered_tool_names
        assert adapter_y.full_name not in gw._registered_tool_names

    @pytest.mark.asyncio
    async def test_reregistration_after_ipc_exception_no_suffix(self) -> None:
        """After IPC exception cleanup, re-registration uses original names."""
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)
        gw._mcp.tool = MagicMock()

        manifest = _make_manifest("ipc-retry-agent")
        await gw.register_agent(manifest, deferred=False)

        info = gw.registry.get_agent_info("ipc-retry-agent")
        assert info is not None

        # Phase 1: register with alive handle
        alive_handle = _mock_agent_handle("ipc-retry-agent", alive=True)
        info.handle = alive_handle
        info.tool_schemas = [{"name": "compute", "description": "Compute"}]
        adapter = McpToolAdapter("ipc-retry-agent", _make_tool_schema("compute", "Compute"))
        gw.registry._tool_adapters["ipc-retry-agent"] = [adapter]
        await gw._register_agent_tools("ipc-retry-agent")

        # Trigger IPC exception
        with patch.object(
            adapter,
            "execute",
            new_callable=AsyncMock,
            side_effect=ConnectionResetError("Reset"),
        ):
            func = gw._make_tool_func(adapter)
            await func(x=1)

        assert "ipc-retry-agent" not in gw._registered_agents

        # Phase 2: agent reconnects, re-register
        adapter2 = McpToolAdapter("ipc-retry-agent", _make_tool_schema("compute", "Compute"))
        alive_handle2 = _mock_agent_handle("ipc-retry-agent", alive=True)
        alive_handle2.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="ok",
            status="completed",
        )
        info.handle = alive_handle2
        info.tool_schemas = [{"name": "compute", "description": "Compute"}]
        gw.registry._tool_adapters["ipc-retry-agent"] = [adapter2]
        await gw._register_agent_tools("ipc-retry-agent")

        # Original name preserved — no _2 suffix
        assert adapter2.full_name == "mcp__ipc_retry_agent__compute"
        assert adapter2.full_name in gw._registered_tool_names


# iter105 regression: _validate_tool_schemas edge cases


class TestDeferredRegistryValidateToolSchemas:
    """_validate_tool_schemas filters non-dict and missing-name schemas."""

    def test_non_dict_schema_skipped(self) -> None:
        # _validate_tool_schemas is a staticmethod — call directly
        schemas = [
            "not-a-dict",  # string — should be skipped
            42,  # int — should be skipped
            {"name": "valid_tool", "description": "OK", "inputSchema": {"type": "object"}},
        ]
        valid = DeferredAgentRegistry._validate_tool_schemas(schemas)
        assert len(valid) == 1
        assert valid[0]["name"] == "valid_tool"

    def test_missing_name_schema_skipped(self) -> None:
        schemas = [
            {"description": "Missing name", "inputSchema": {"type": "object"}},
            {"name": "", "description": "Empty name", "inputSchema": {"type": "object"}},
            {"name": 123, "description": "Non-string name", "inputSchema": {"type": "object"}},
        ]
        valid = DeferredAgentRegistry._validate_tool_schemas(schemas)
        assert valid == []

    def test_missing_input_schema_injected_default(self) -> None:
        schemas = [{"name": "no_schema", "description": "OK"}]
        valid = DeferredAgentRegistry._validate_tool_schemas(schemas)
        assert len(valid) == 1
        assert valid[0]["inputSchema"] == {"type": "object", "properties": {}}


# ============================================================================
# Coverage gap tests: deferred_registry.py lines 320-324 (non-dict schema)
# ============================================================================


class TestDeferredRegistryNonDictSchema:
    """_validate_tool_schemas skips non-dict entries (lines 320-324)."""

    def test_skips_string_schema_entry(self) -> None:
        """String entries in tool schema list are skipped."""
        result = DeferredAgentRegistry._validate_tool_schemas(
            [
                "not a dict",
                {"name": "valid", "inputSchema": {"type": "object"}},
                None,
            ]
        )
        assert len(result) == 1
        assert result[0]["name"] == "valid"

    def test_skips_list_schema_entry(self) -> None:
        """List entries in tool schema list are skipped."""
        result = DeferredAgentRegistry._validate_tool_schemas(
            [
                ["nested", "list"],
                {"name": "ok", "inputSchema": {"type": "object"}},
            ]
        )
        assert len(result) == 1


# iter125 regression: error_type consistency in tool_adapter error paths
class TestToolAdapterErrorTypeConsistency:
    """Every error return dict from McpToolAdapter must include error_type."""

    @pytest.mark.parametrize(
        "scenario, expected_error_type",
        [
            ("dead_agent", "ProcessNotAliveError"),
            ("ipc_error", "ConnectionResetError"),
            ("agent_error_response", "AgentError"),
        ],
    )
    @pytest.mark.asyncio
    async def test_error_type_present(self, scenario: str, expected_error_type: str) -> None:
        schema = _make_tool_schema("tool")
        adapter = McpToolAdapter(server_name="test-agent", tool_schema=schema)
        handle = _mock_agent_handle("test-agent", alive=(scenario != "dead_agent"))

        if scenario == "ipc_error":
            handle.ipc.send_chat.side_effect = ConnectionResetError("broken")
        elif scenario == "agent_error_response":
            handle.ipc.receive_until_result.return_value = AgentToPlatform(
                type=AgentToPlatformType.ERROR,
                error="something went wrong",
            )

        result = await adapter.execute(handle, {"x": 1})
        assert result["success"] is False
        assert result["error_type"] == expected_error_type


# iter132 regression: dead-agent cleanup on IPC error in result dict
class TestGatewayIPCCleanup:
    """gateway._invoke must clean up dead-agent registrations when
    tool_adapter.execute() returns error_type indicating IPC failure."""

    @pytest.mark.asyncio
    async def test_ipc_connection_error_triggers_cleanup(self, gateway: MCPGateway) -> None:
        """BrokenPipeError in result dict triggers registration cleanup."""
        manifest = _make_manifest("clean_agent")
        await gateway.register_agent(manifest, deferred=False)
        info = gateway.registry.get_agent_info("clean_agent")
        assert info is not None

        mock_handle = _mock_agent_handle("clean_agent", alive=True)
        # execute() catches BrokenPipeError and returns error dict
        mock_handle.ipc.send_chat.side_effect = BrokenPipeError("pipe closed")
        info.handle = mock_handle

        schema = _make_tool_schema("tool1")
        adapter = McpToolAdapter(server_name="clean_agent", tool_schema=schema)
        gateway.registry._tool_adapters["clean_agent"] = [adapter]

        # Pre-register so cleanup can remove them
        gateway._registered_agents.add("clean_agent")
        gateway._registered_tool_names.add(adapter.full_name)

        func = gateway._make_tool_func(adapter)
        result = await func(x=1)
        assert "Error" in result
        # Agent and tool names should be cleaned up
        assert "clean_agent" not in gateway._registered_agents
        assert adapter.full_name not in gateway._registered_tool_names


# ============================================================================
# iter132 regression: _invoke cleanup exception safety (gateway.py)
# ============================================================================


class TestInvokeCleanupExceptionSafety:
    """Cleanup in _invoke error_type branch must not prevent error return."""

    @pytest.mark.asyncio
    async def test_cleanup_failure_does_not_prevent_error_return(self) -> None:
        """If cleanup raises after IPC error detection, error message still returned."""
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        manifest = _make_manifest("cleanup-agent")
        await gw.register_agent(manifest, deferred=False)

        info = gw.registry.get_agent_info("cleanup-agent")
        assert info is not None
        alive_handle = _mock_agent_handle("cleanup-agent", alive=True)
        info.handle = alive_handle
        info.tool_schemas = [{"name": "fail_tool", "description": "Fails"}]

        schema = _make_tool_schema("fail_tool", "Fails")
        adapter = McpToolAdapter(server_name="cleanup-agent", tool_schema=schema)
        gw.registry._tool_adapters["cleanup-agent"] = [adapter]

        # Execute returns IPC error — triggers cleanup branch
        with patch.object(
            adapter,
            "execute",
            new_callable=AsyncMock,
            return_value={
                "success": False,
                "error": "IPC broken",
                "error_type": "IPCConnectionError",
            },
        ):
            # Make get_tool_adapters raise to simulate registry inconsistency
            with patch.object(
                gw.registry,
                "get_tool_adapters",
                side_effect=RuntimeError("registry corrupt"),
            ):
                func = gw._make_tool_func(adapter)
                result = await func(x=1)

        # Error message must still be returned despite cleanup failure
        assert "Error" in result


# ============================================================================
# _build_params and _build_params_from_schema — static method unit tests
# ============================================================================


class TestBuildParams:
    """Tests for MCPGateway._build_params static method.

    _build_params reads adapter._input_schema and delegates to
    _build_params_from_schema, returning ([], {"return": str}) for empty
    or property-less schemas.
    """

    def test_adapter_no_schema_returns_empty(self) -> None:
        """When adapter._input_schema is None, returns ([], {"return": str})."""
        adapter = MagicMock(spec=McpToolAdapter)
        adapter._input_schema = None
        params, annotations = MCPGateway._build_params(adapter)
        assert params == []
        assert annotations == {"return": str}

    def test_adapter_schema_without_properties_returns_empty(self) -> None:
        """When schema has no 'properties' key, returns ([], {"return": str})."""
        adapter = MagicMock(spec=McpToolAdapter)
        adapter._input_schema = {"type": "object"}
        params, annotations = MCPGateway._build_params(adapter)
        assert params == []
        assert annotations == {"return": str}

    def test_adapter_empty_schema_returns_empty(self) -> None:
        """When schema is an empty dict, returns ([], {"return": str})."""
        adapter = MagicMock(spec=McpToolAdapter)
        adapter._input_schema = {}
        params, annotations = MCPGateway._build_params(adapter)
        assert params == []
        assert annotations == {"return": str}

    def test_adapter_valid_schema_delegates_to_from_schema(self) -> None:
        """When schema has properties, delegates to _build_params_from_schema."""
        adapter = MagicMock(spec=McpToolAdapter)
        adapter._input_schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        }
        params, annotations = MCPGateway._build_params(adapter)
        assert len(params) == 1
        assert params[0].name == "name"
        assert params[0].annotation is str
        assert "name" in annotations


class TestBuildParamsFromSchema:
    """Tests for MCPGateway._build_params_from_schema static method.

    Converts a JSON-schema dict into (list[inspect.Parameter], dict[str, Any])
    for overriding __signature__ and __annotations__ on the invoke function.
    """

    def test_empty_schema_returns_empty(self) -> None:
        """Empty dict returns ([], {"return": str})."""
        params, annotations = MCPGateway._build_params_from_schema({})
        assert params == []
        assert annotations == {"return": str}

    def test_none_falsy_returns_empty(self) -> None:
        """None input returns ([], {"return": str})."""
        params, annotations = MCPGateway._build_params_from_schema(None)  # type: ignore[arg-type]
        assert params == []
        assert annotations == {"return": str}

    def test_schema_without_properties_returns_empty(self) -> None:
        """Schema missing 'properties' key returns ([], {"return": str})."""
        params, annotations = MCPGateway._build_params_from_schema({"type": "object"})
        assert params == []
        assert annotations == {"return": str}

    def test_single_required_string_property(self) -> None:
        """A single required string property produces one positional-or-keyword
        Parameter with annotation=str and no default."""
        schema = {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        }
        params, annotations = MCPGateway._build_params_from_schema(schema)
        assert len(params) == 1
        p = params[0]
        assert p.name == "message"
        assert p.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert p.annotation is str
        assert p.default is inspect.Parameter.empty
        assert annotations["message"] is str
        # annotations dict does NOT contain "return" when properties exist;
        # that key is only set by the caller (_make_tool_func).

    def test_single_optional_property_no_default(self) -> None:
        """An optional property without 'default' gets default=None and
        annotation becomes str | None."""
        schema = {
            "type": "object",
            "properties": {"nickname": {"type": "string"}},
        }
        params, annotations = MCPGateway._build_params_from_schema(schema)
        assert len(params) == 1
        p = params[0]
        assert p.name == "nickname"
        assert p.default is None
        # annotation should be str | None
        assert annotations["nickname"] == str | None

    def test_single_optional_property_with_default(self) -> None:
        """An optional property with 'default' uses the provided default value
        and keeps the original type annotation."""
        schema = {
            "type": "object",
            "properties": {"count": {"type": "integer", "default": 42}},
        }
        params, annotations = MCPGateway._build_params_from_schema(schema)
        assert len(params) == 1
        p = params[0]
        assert p.name == "count"
        assert p.default == 42
        assert p.annotation is int
        assert annotations["count"] is int

    def test_mixed_required_and_optional(self) -> None:
        """Mix of required and optional properties produces correct params
        with proper defaults and annotations."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
                "active": {"type": "boolean", "default": True},
            },
            "required": ["name", "age"],
        }
        params, annotations = MCPGateway._build_params_from_schema(schema)
        assert len(params) == 3

        # 'name' — required
        assert params[0].name == "name"
        assert params[0].default is inspect.Parameter.empty
        assert params[0].annotation is str

        # 'age' — required
        assert params[1].name == "age"
        assert params[1].default is inspect.Parameter.empty
        assert params[1].annotation is int

        # 'active' — optional with default
        assert params[2].name == "active"
        assert params[2].default is True
        assert params[2].annotation is bool

    def test_non_dict_prop_def_skipped(self) -> None:
        """Non-dict property definitions (e.g. strings, ints) are skipped."""
        schema = {
            "type": "object",
            "properties": {
                "valid": {"type": "string"},
                "bad_string": "not a dict",
                "bad_int": 42,
            },
            "required": ["valid"],
        }
        params, annotations = MCPGateway._build_params_from_schema(schema)
        # Only 'valid' should produce a Parameter
        assert len(params) == 1
        assert params[0].name == "valid"
        assert "bad_string" not in annotations
        assert "bad_int" not in annotations

    def test_boolean_type_resolves_to_bool(self) -> None:
        """Boolean schema property resolves to Python bool type."""
        schema = {
            "type": "object",
            "properties": {"flag": {"type": "boolean"}},
            "required": ["flag"],
        }
        params, annotations = MCPGateway._build_params_from_schema(schema)
        assert len(params) == 1
        assert params[0].annotation is bool
        assert annotations["flag"] is bool

    def test_number_type_resolves_to_float(self) -> None:
        """Number schema property resolves to Python float type."""
        schema = {
            "type": "object",
            "properties": {"ratio": {"type": "number"}},
            "required": ["ratio"],
        }
        params, annotations = MCPGateway._build_params_from_schema(schema)
        assert len(params) == 1
        assert params[0].annotation is float
        assert annotations["ratio"] is float

    def test_array_type_resolves_to_list(self) -> None:
        """Array schema property resolves to list[item_type]."""
        schema = {
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["items"],
        }
        params, annotations = MCPGateway._build_params_from_schema(schema)
        assert len(params) == 1
        p = params[0]
        assert p.name == "items"
        # Should be list[str]
        assert annotations["items"] == list[str]

    def test_object_type_resolves_to_pydantic_model(self) -> None:
        """Object schema property resolves to a dynamically created
        Pydantic BaseModel subclass."""
        from pydantic import BaseModel

        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {"key": {"type": "string"}},
                    "required": ["key"],
                },
            },
            "required": ["config"],
        }
        params, annotations = MCPGateway._build_params_from_schema(schema)
        assert len(params) == 1
        config_type = annotations["config"]
        assert isinstance(config_type, type)
        assert issubclass(config_type, BaseModel)

    def test_optional_with_none_default_annotation(self) -> None:
        """Optional property without 'default' key: annotation is type|None,
        default value is None."""
        schema = {
            "type": "object",
            "properties": {
                "maybe_int": {"type": "integer"},
            },
            # not in required — optional, no default key
        }
        params, annotations = MCPGateway._build_params_from_schema(schema)
        assert len(params) == 1
        p = params[0]
        assert p.name == "maybe_int"
        assert p.default is None
        # annotation should be int | None
        assert annotations["maybe_int"] == int | None

    def test_empty_properties_returns_empty_params(self) -> None:
        """Schema with empty properties dict returns no params and empty annotations."""
        schema = {
            "type": "object",
            "properties": {},
        }
        params, annotations = MCPGateway._build_params_from_schema(schema)
        assert params == []
        # The method enters the properties loop but finds nothing, so
        # annotations is an empty dict (no "return" key — that is added
        # by the caller, not by _build_params_from_schema itself).
        assert annotations == {}
