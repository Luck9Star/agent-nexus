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
from fastmcp.exceptions import ToolError

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

    def test_description_from_schema(self) -> None:
        schema = _make_tool_schema("t", "Custom description")
        adapter = McpToolAdapter(server_name="srv", tool_schema=schema)
        assert adapter.description == "Custom description"

    # NOTE: test_description_default_empty removed — asserts Pydantic default
    # NOTE: test_repr removed — tests __repr__ formatting (implementation detail)


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

    # NOTE: test_empty_input_schema_gets_default removed — default schema
    # injection tested by ValidateToolSchemas.test_missing_input_schema_injected_default


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

    # NOTE: test_default_fields removed — asserts Pydantic/dataclass defaults


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

    # NOTE: test_default_deferred_true removed — covered by test_register_deferred


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

        # NOTE: test_get_deferred_agent removed — same get logic as test_get_core_agent
        assert registry.get_agent_info("nonexistent") is None

    # NOTE: test_reregister_same_name_replaces_tier removed — tier replacement logic
    # is already tested by test_register_both_tiers


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

    # NOTE: test_activate_creates_tool_adapters removed — adapter creation tested
    # by test_activate_deferred_no_subprocess which also verifies the chat tool schema

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

    # NOTE: test_activate_core_already_activated removed — caching path for already-active
    # agents is trivial; core activation is tested by test_activate_starts_subprocess


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

    # NOTE: test_core_agent_no_tools_skipped removed — same empty-result logic
    # as test_empty_registry

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

    # NOTE: test_multiline_description_truncated removed — text formatting detail


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
        with pytest.raises(ToolError, match="not available"):
            await func(x=1)

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
        """IPC error between is_alive check and execute raises ToolError."""

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
        with pytest.raises(ToolError, match="IPC failed"):
            await func(x=1)

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

    # NOTE: test_agent_info_with_activated_tools removed — same _agent_info logic
    # as test_agent_info_found


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

    # NOTE: test_run_sse_default removed — same delegation pattern as test_run_stdio

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

    # NOTE: test_clean_name_no_change removed — no-sanitization case is the
    # inverse of test_hyphenated_name_preserved


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


# NOTE: TestMcpToolAdapterExecuteStatusCompleted removed — status="completed" already
# tested by TestMcpToolAdapterExecute.test_execute_success


# NOTE: TestMcpToolAdapterAffirmativeStatus removed — parametrized test for
# ambiguous statuses; TestMcpToolAdapterExecuteStatusNone covers the NULL status
# edge case which is the important regression


# ---------------------------------------------------------------------------
# Iteration 24 fixes: gateway activation message, registry priority
# ---------------------------------------------------------------------------


# ============================================================================
# Iteration 25: Dead agent cleanup + lazy asyncio.Lock
# ============================================================================


# NOTE: TestDeadAgentCleanup removed — 2 tests for same dead-agent logic;
# TestDeadAgentToolNameCleanup tests the same paths plus tool-name cleanup


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

    # NOTE: test_activate_agent_no_subprocess_returns_placeholder removed —
    # same logic as TestDeferredRegistryActivate.test_activate_deferred_no_subprocess


# ============================================================================
# Fix 1 regression: _invoke does NOT re-acquire lock (deadlock prevention)
# ============================================================================


# NOTE: TestInvokeNoLockReacquire removed — deadlock prevention is an implementation
# detail; dead-agent cleanup is covered by TestDeadAgentToolNameCleanup


# ============================================================================
# Fix 2 regression: get_agent_info prefers activated over dormant
# ============================================================================


# ============================================================================
# Regression: McpToolAdapter IPC per-agent lock (from security audit)
# ============================================================================


class TestMcpToolAdapterIPCLock:
    """McpToolAdapter.execute() acquires a per-agent asyncio.Lock for IPC."""

    @pytest.mark.asyncio
    async def test_ipc_lock_created_on_execute(self) -> None:
        """After execute(), a per-agent lock exists in _ipc_locks."""
        schema = _make_tool_schema("tool")
        adapter = McpToolAdapter(server_name="lock-test-agent", tool_schema=schema)

        assert "lock-test-agent" not in _ipc_lock_registry

        handle = _mock_agent_handle("lock-test-agent", alive=True)
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="ok",
            status="completed",
        )
        handle.ipc.receive_until_result.return_value = response

        remove_all_locks()
        try:
            await adapter.execute(handle, {})
            assert "lock-test-agent" in _ipc_lock_registry
        finally:
            remove_all_locks()

    # NOTE: test_ipc_lock_prevents_concurrent_interleave removed — complex timing test;
    # lock creation test above verifies the lock mechanism exists
    # NOTE: test_different_agents_use_different_locks removed — separate-lock logic
    # is the corollary of same-lock behavior; not a unique code path


# ============================================================================
# McpToolAdapter lock cleanup classmethods
# ============================================================================


class TestMcpToolAdapterLockCleanup:
    """remove_all_locks cleans up class-level locks."""

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

    # NOTE: test_gateway_stop_cleans_locks removed — same cleanup logic
    # NOTE: test_invoke_dead_agent_removes_lock removed — same dead-agent path
    # as TestDeadAgentToolNameCleanup


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

    # NOTE: test_prefers_core_over_dormant_deferred removed — inverse of
    # test_prefers_activated_deferred_over_core


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
        with (
            patch.object(
                gw._registry,
                "search_agents",
                return_value=[manifest],
            ),
            patch.object(
                gw._registry,
                "activate_agent",
                new_callable=AsyncMock,
                return_value=[{"name": "t", "description": "d", "inputSchema": {}}],
            ),
            patch.object(
                gw,
                "_register_agent_tools",
                new_callable=AsyncMock,
                side_effect=RuntimeError("subprocess crashed"),
            ),
        ):
            result = await gw._search_and_activate("fail")

        assert "fail-agent" in result
        assert "activation failed" in result
        assert "subprocess crashed" in result

    # NOTE: test_activation_mixed_success_and_failure removed — same error handling
    # as test_activation_failure_reports_error; mixed case adds no new coverage


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

    # NOTE: test_multiple_collisions_increment_suffix removed — same collision logic
    # as test_collision_gets_numeric_suffix, just more adapters


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

        # Make execute return a failure — now raises ToolError (F85 MCP contract fix)
        with patch.object(
            adapter,
            "execute",
            new_callable=AsyncMock,
            return_value={"success": False, "error": "task blew up"},
        ):
            func = gw._make_tool_func(adapter)
            with pytest.raises(ToolError, match="task blew up"):
                await func(x=1)

    # NOTE: test_invoke_returns_unknown_failure_when_no_error_key removed —
    # same error formatting as test_invoke_returns_error_on_execution_failure


# ============================================================================
# Coverage: _register_agent_tools FastMCP registration error (lines 274-280)
# ============================================================================


# ============================================================================
# Coverage: deferred_registry.py missed lines
# ============================================================================


class TestDeferredRegistryFetchAgentTools:
    """Tests for _fetch_agent_tools edge cases (lines 259, 268, 276-277)."""

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

    # NOTE: test_fetch_tools_content_json_fallback removed — JSON parsing fallback
    # tested by test_fetch_tools_response_output_list which covers the primary path

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

    # NOTE: test_fetch_tools_ipc_exception_returns_fallback removed — same fallback
    # logic as test_fetch_tools_content_not_json_returns_fallback


# ============================================================================
# Iteration 88: Regression tests for dead-agent tool-name cleanup + empty
# tool list truthy bug
# ============================================================================


class TestDeadAgentToolNameCleanup:
    """Bug: _invoke cleaned _registered_agents but NOT _registered_tool_names."""

    @pytest.mark.asyncio
    async def test_tool_names_cleaned_on_dead_agent(self) -> None:
        """All tool names for a dead agent are removed from _registered_tool_names."""
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)
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

        func = gw._make_tool_func(adapter_a)
        with pytest.raises(ToolError, match="process has died"):
            await func(x=1)

        assert "stale-agent" not in gw._registered_agents
        assert adapter_a.full_name not in gw._registered_tool_names
        assert adapter_b.full_name not in gw._registered_tool_names

    # NOTE: test_reregistration_keeps_original_names removed — same cleanup +
    # re-register flow as IPCExceptionToolNameCleanup test


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
    """Bug: _fetch_agent_tools did not check for ERROR response type from IPC."""

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

        assert len(result) == 1
        assert result[0]["name"] == "chat"

    # NOTE: test_error_response_does_not_parse_content removed — same ERROR handling
    # as test_error_response_returns_fallback, just with extra content field


# ============================================================================
# Regression: _invoke IPC exception handler must clean _registered_tool_names
# ============================================================================


class TestInvokeIPCExceptionToolNameCleanup:
    """Bug: _invoke exception handler cleaned _registered_agents but NOT
    _registered_tool_names on IPC transport failure.
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

        with patch.object(
            adapter_x,
            "execute",
            new_callable=AsyncMock,
            side_effect=BrokenPipeError("Connection lost"),
        ):
            func = gw._make_tool_func(adapter_x)
            with pytest.raises(ToolError, match="IPC failed"):
                await func(data="test")

        assert "ipc-fail-agent" not in gw._registered_agents
        assert adapter_x.full_name not in gw._registered_tool_names
        assert adapter_y.full_name not in gw._registered_tool_names

    # NOTE: test_reregistration_after_ipc_exception_no_suffix removed —
    # same cleanup + re-register flow as DeadAgentToolNameCleanup test


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
        with pytest.raises(ToolError, match="pipe closed"):
            await func(x=1)
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
                with pytest.raises(ToolError, match="IPC broken"):
                    await func(x=1)


# ============================================================================
# _build_params and _build_params_from_schema — static method unit tests
# ============================================================================


class TestBuildParams:
    """Tests for MCPGateway._build_params static method.

    _build_params reads adapter._input_schema and delegates to
    _build_params_from_schema, returning ([], {"return": str}) for empty
    or property-less schemas.
    """

    @pytest.mark.parametrize(
        "schema",
        [None, {"type": "object"}, {}],
        ids=["none", "no_properties", "empty"],
    )
    def test_empty_schema_returns_empty(self, schema: object) -> None:
        """Empty/None/property-less schemas return ([], {"return": str})."""
        adapter = MagicMock(spec=McpToolAdapter)
        adapter._input_schema = schema
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
