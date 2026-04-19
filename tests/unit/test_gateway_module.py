"""Unit tests for gateway module — tool_adapter, deferred_registry, gateway.

Tests McpToolAdapter (name sanitization, tool definitions, execute),
DeferredAgentRegistry (registration tiers, activation, search, manifest),
and MCPGateway (core tools, agent registration, tool forwarding).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.models.agent import AgentDependencies, AgentManifest, AgentRole, AgentType
from agent_nexus.models.ipc import AgentToPlatform, AgentToPlatformType
from agent_nexus.platform.gateway.deferred_registry import (
    AgentInfo,
    DeferredAgentRegistry,
)
from agent_nexus.platform.gateway.gateway import MCPGateway
from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter, _sanitize
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
    data = dict(
        name=name,
        version="0.1.0",
        type=agent_type,
        description=description,
    )
    data.update(overrides)
    return AgentManifest(**data)


def _make_tool_schema(
    name: str = "do_thing",
    description: str = "Does a thing",
    input_schema: dict | None = None,
) -> dict:
    """Build a tool schema dict matching MCP format."""
    schema = {"name": name, "description": description}
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
def router(process_manager: MagicMock) -> MagicMock:
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

    def test_alphanumeric_unchanged(self) -> None:
        assert _sanitize("hello") == "hello"

    def test_with_hyphens(self) -> None:
        # Hyphens are NOT in the allowed set [a-zA-Z0-9_], so they get replaced
        assert _sanitize("my-tool") == "my_tool"

    def test_with_underscores(self) -> None:
        assert _sanitize("my_tool") == "my_tool"

    def test_dots_replaced(self) -> None:
        assert _sanitize("my.tool") == "my_tool"

    def test_spaces_replaced(self) -> None:
        assert _sanitize("my tool") == "my_tool"

    def test_multiple_special_chars(self) -> None:
        assert _sanitize("a.b!c@d") == "a_b_c_d"

    def test_empty_string(self) -> None:
        assert _sanitize("") == ""

    def test_numbers_preserved(self) -> None:
        assert _sanitize("tool123") == "tool123"

    def test_mixed_case(self) -> None:
        assert _sanitize("MyTool") == "MyTool"

    def test_leading_special(self) -> None:
        assert _sanitize(".hidden") == "_hidden"


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

    def test_server_name_sanitized(self) -> None:
        schema = _make_tool_schema("tool")
        adapter = McpToolAdapter(server_name="my.agent", tool_schema=schema)
        assert adapter.server_name == "my_agent"
        assert adapter.full_name == "mcp__my_agent__tool"

    def test_tool_name_sanitized(self) -> None:
        schema = _make_tool_schema("do.thing")
        adapter = McpToolAdapter(server_name="srv", tool_schema=schema)
        assert adapter.tool_name == "do_thing"
        assert adapter.full_name == "mcp__srv__do_thing"

    def test_input_schema_stored(self) -> None:
        ischema = {"type": "object", "properties": {"x": {"type": "int"}}}
        schema = _make_tool_schema("t", input_schema=ischema)
        adapter = McpToolAdapter(server_name="srv", tool_schema=schema)
        assert adapter._input_schema == ischema

    def test_input_schema_default_empty(self) -> None:
        schema = {"name": "t", "description": "d"}
        adapter = McpToolAdapter(server_name="srv", tool_schema=schema)
        assert adapter._input_schema == {}

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

    def test_definition_keys(self) -> None:
        schema = _make_tool_schema("t")
        adapter = McpToolAdapter(server_name="srv", tool_schema=schema)
        defn = adapter.get_tool_definition()
        assert set(defn.keys()) == {"name", "description", "inputSchema"}


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
        handle.ipc.send_chat.side_effect = RuntimeError("pipe broken")
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

    @pytest.mark.asyncio
    async def test_execute_sends_correct_payload(self) -> None:
        schema = _make_tool_schema("my_tool")
        adapter = McpToolAdapter(server_name="srv", tool_schema=schema)
        handle = _mock_agent_handle("srv", alive=True)
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="ok",
            status="completed",
        )
        handle.ipc.receive_until_result.return_value = response
        await adapter.execute(handle, {"key": "val"})
        # Verify send_chat was called with JSON payload
        handle.ipc.send_chat.assert_awaited_once()
        call_args = handle.ipc.send_chat.call_args
        import json

        payload = json.loads(call_args[0][0])
        assert payload["tool"] == "my_tool"
        assert payload["arguments"] == {"key": "val"}
        conv_id = call_args[1]["conversation_id"]
        assert conv_id.startswith("__tool_") and conv_id.endswith("__")

    @pytest.mark.asyncio
    async def test_execute_failed_status(self) -> None:
        schema = _make_tool_schema("tool")
        adapter = McpToolAdapter(server_name="agent", tool_schema=schema)
        handle = _mock_agent_handle("agent", alive=True)
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="partial",
            status="failed",
        )
        handle.ipc.receive_until_result.return_value = response
        result = await adapter.execute(handle, {})
        assert result["success"] is False
        assert result["output"] == "partial"


# ============================================================================
# AgentInfo — dataclass
# ============================================================================


class TestAgentInfo:
    """Tests for AgentInfo dataclass properties."""

    def test_default_not_activated(self) -> None:
        info = AgentInfo(
            name="test",
            manifest=_make_manifest("test"),
        )
        assert info.is_activated is False

    def test_activated_when_schemas_set(self) -> None:
        info = AgentInfo(
            name="test",
            manifest=_make_manifest("test"),
            tool_schemas=[{"name": "tool1"}],
        )
        assert info.is_activated is True

    def test_not_running_without_handle(self) -> None:
        info = AgentInfo(
            name="test",
            manifest=_make_manifest("test"),
        )
        assert info.is_running is False

    def test_running_with_alive_handle(self) -> None:
        handle = _mock_agent_handle(alive=True)
        info = AgentInfo(
            name="test",
            manifest=_make_manifest("test"),
            handle=handle,
        )
        assert info.is_running is True

    def test_not_running_with_dead_handle(self) -> None:
        handle = _mock_agent_handle(alive=False)
        info = AgentInfo(
            name="test",
            manifest=_make_manifest("test"),
            handle=handle,
        )
        assert info.is_running is False

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

    def test_register_with_start_command(
        self, registry: DeferredAgentRegistry
    ) -> None:
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

    def test_reregister_same_name_replaces_tier(
        self, registry: DeferredAgentRegistry
    ) -> None:
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
# DeferredAgentRegistry — list helpers
# ============================================================================


class TestDeferredRegistryList:
    """Tests for DeferredAgentRegistry list_* methods."""

    def test_list_all_empty(self, registry: DeferredAgentRegistry) -> None:
        assert registry.list_all_agents() == []

    def test_list_all_combined(self, registry: DeferredAgentRegistry) -> None:
        registry.register_agent(_make_manifest("a"), deferred=False)
        registry.register_agent(_make_manifest("b"), deferred=True)
        all_agents = registry.list_all_agents()
        names = {a.name for a in all_agents}
        assert names == {"a", "b"}

    def test_list_core_empty(self, registry: DeferredAgentRegistry) -> None:
        registry.register_agent(_make_manifest("x"), deferred=True)
        assert registry.list_core_agents() == []

    def test_list_deferred_empty(self, registry: DeferredAgentRegistry) -> None:
        registry.register_agent(_make_manifest("x"), deferred=False)
        assert registry.list_deferred_agents() == []


# ============================================================================
# DeferredAgentRegistry — activate_agent
# ============================================================================


class TestDeferredRegistryActivate:
    """Tests for DeferredAgentRegistry.activate_agent()."""

    @pytest.mark.asyncio
    async def test_activate_unknown_raises(
        self, registry: DeferredAgentRegistry
    ) -> None:
        with pytest.raises(KeyError, match="not registered"):
            await registry.activate_agent("nonexistent")

    @pytest.mark.asyncio
    async def test_activate_deferred_no_subprocess(
        self, registry: DeferredAgentRegistry
    ) -> None:
        """Activating a deferred agent with no start_command gets placeholder."""
        manifest = _make_manifest("no-cmd")
        registry.register_agent(manifest, deferred=True, start_command=[])
        schemas = await registry.activate_agent("no-cmd")
        assert len(schemas) == 1
        assert schemas[0]["name"] == "no-cmd__chat"
        assert "message" in schemas[0]["inputSchema"]["properties"]

    @pytest.mark.asyncio
    async def test_activate_creates_tool_adapters(
        self, registry: DeferredAgentRegistry
    ) -> None:
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
    async def test_activate_idempotent(
        self, registry: DeferredAgentRegistry
    ) -> None:
        """Activating an already-activated agent returns cached schemas."""
        manifest = _make_manifest("idem-agent")
        registry.register_agent(manifest, deferred=True, start_command=[])
        schemas1 = await registry.activate_agent("idem-agent")
        schemas2 = await registry.activate_agent("idem-agent")
        assert schemas1 is schemas2

    @pytest.mark.asyncio
    async def test_activate_core_already_activated(
        self, registry: DeferredAgentRegistry
    ) -> None:
        """Activating a core agent that already has schemas returns them."""
        manifest = _make_manifest("core-ok")
        registry.register_agent(manifest, deferred=False)
        info = registry.get_agent_info("core-ok")
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

    def test_core_agent_tools_included(
        self, registry: DeferredAgentRegistry
    ) -> None:
        manifest = _make_manifest("core-agent")
        registry.register_agent(manifest, deferred=False)
        info = registry.get_agent_info("core-agent")
        info.tool_schemas = [{"name": "tool1"}, {"name": "tool2"}]
        tools = registry.get_tools_for_llm()
        assert len(tools) == 2

    def test_core_agent_no_tools_skipped(
        self, registry: DeferredAgentRegistry
    ) -> None:
        manifest = _make_manifest("empty-core")
        registry.register_agent(manifest, deferred=False)
        info = registry.get_agent_info("empty-core")
        # Not running, no tools
        assert info.tool_schemas is None
        tools = registry.get_tools_for_llm()
        assert tools == []

    @pytest.mark.asyncio
    async def test_activated_deferred_tools_included(
        self, registry: DeferredAgentRegistry
    ) -> None:
        registry.register_agent(
            _make_manifest("deferred"), deferred=True, start_command=[]
        )
        await registry.activate_agent("deferred")
        tools = registry.get_tools_for_llm()
        assert len(tools) >= 1

    def test_dormant_deferred_not_included(
        self, registry: DeferredAgentRegistry
    ) -> None:
        registry.register_agent(_make_manifest("dormant"), deferred=True)
        tools = registry.get_tools_for_llm()
        assert tools == []


# ============================================================================
# DeferredAgentRegistry — get_tool_adapter
# ============================================================================


class TestDeferredRegistryGetToolAdapter:
    """Tests for DeferredAgentRegistry.get_tool_adapter()."""

    @pytest.mark.asyncio
    async def test_find_adapter_by_full_name(
        self, registry: DeferredAgentRegistry
    ) -> None:
        registry.register_agent(
            _make_manifest("srv"), deferred=True, start_command=[]
        )
        await registry.activate_agent("srv")
        adapters = registry._tool_adapters["srv"]
        full_name = adapters[0].full_name
        found = registry.get_tool_adapter(full_name)
        assert found is not None
        assert found.full_name == full_name

    def test_find_nonexistent_adapter(
        self, registry: DeferredAgentRegistry
    ) -> None:
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
        info.tool_schemas = [{"name": "t1"}, {"name": "t2"}]
        text = registry.build_manifest()
        assert "core-agent" in text
        assert "core" in text
        assert "2 tools" in text

    def test_dormant_deferred_manifest(
        self, registry: DeferredAgentRegistry
    ) -> None:
        manifest = _make_manifest("dormant", description="Sleeping agent")
        registry.register_agent(manifest, deferred=True)
        text = registry.build_manifest()
        assert "dormant" in text
        assert "available" in text

    @pytest.mark.asyncio
    async def test_activated_deferred_manifest(
        self, registry: DeferredAgentRegistry
    ) -> None:
        registry.register_agent(
            _make_manifest("active", description="Active agent"),
            deferred=True,
            start_command=[],
        )
        await registry.activate_agent("active")
        text = registry.build_manifest()
        assert "active" in text
        assert "activated" in text

    def test_multiline_description_truncated(
        self, registry: DeferredAgentRegistry
    ) -> None:
        desc = "Line one\nLine two\nLine three"
        registry.register_agent(
            _make_manifest("multi", description=desc), deferred=True
        )
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

    def test_search_by_name(self, registry: DeferredAgentRegistry) -> None:
        registry.register_agent(
            _make_manifest("code-reviewer", description="Reviews code"),
            deferred=True,
        )
        registry.register_agent(
            _make_manifest("doc-filler", description="Fills docs"),
            deferred=True,
        )
        results = registry.search_agents("code")
        assert len(results) == 1
        assert results[0].name == "code-reviewer"

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

    def test_search_no_match(self, registry: DeferredAgentRegistry) -> None:
        registry.register_agent(
            _make_manifest("x", description="something"), deferred=True
        )
        assert registry.search_agents("zzzzz") == []

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

    def test_search_case_insensitive(
        self, registry: DeferredAgentRegistry
    ) -> None:
        registry.register_agent(
            _make_manifest("CodeReviewer", description="Reviews code"),
            deferred=True,
        )
        results = registry.search_agents("codereviewer")
        assert len(results) == 1

    def test_search_scoring_order(self, registry: DeferredAgentRegistry) -> None:
        """Agent matching both name and description ranks higher."""
        registry.register_agent(
            _make_manifest("code-reviewer", description="Reviews code"),
            deferred=True,
        )
        registry.register_agent(
            _make_manifest("helper", description="Helps with code review"),
            deferred=True,
        )
        results = registry.search_agents("code reviewer")
        # Both match, but "code-reviewer" has more keyword hits
        assert results[0].name == "code-reviewer"


# ============================================================================
# MCPGateway — initialization
# ============================================================================


class TestMCPGatewayInit:
    """Tests for MCPGateway initialization."""

    def test_creates_registry(self, gateway: MCPGateway) -> None:
        assert isinstance(gateway.registry, DeferredAgentRegistry)

    def test_creates_mcp_server(self, gateway: MCPGateway) -> None:
        assert gateway.mcp is not None

    def test_mcp_server_name(self, gateway: MCPGateway) -> None:
        assert gateway.mcp.name == "agent-nexus-gateway"


# ============================================================================
# MCPGateway — register_agent
# ============================================================================


class TestMCPGatewayRegisterAgent:
    """Tests for MCPGateway.register_agent()."""

    @pytest.mark.asyncio
    async def test_register_core(self, gateway: MCPGateway) -> None:
        manifest = _make_manifest("core-agent")
        await gateway.register_agent(manifest, deferred=False)
        assert len(gateway.registry.list_core_agents()) == 1

    @pytest.mark.asyncio
    async def test_register_deferred(self, gateway: MCPGateway) -> None:
        manifest = _make_manifest("deferred-agent")
        await gateway.register_agent(manifest, deferred=True)
        assert len(gateway.registry.list_deferred_agents()) == 1

    @pytest.mark.asyncio
    async def test_register_multiple(self, gateway: MCPGateway) -> None:
        for i in range(3):
            await gateway.register_agent(
                _make_manifest(f"agent-{i}"), deferred=True
            )
        assert len(gateway.registry.list_deferred_agents()) == 3

    @pytest.mark.asyncio
    async def test_register_with_start_command(self, gateway: MCPGateway) -> None:
        manifest = _make_manifest("cmd-agent")
        await gateway.register_agent(
            manifest,
            deferred=True,
            start_command=["uvx", "cmd-agent"],
        )
        info = gateway.registry.get_agent_info("cmd-agent")
        assert info.start_command == ["uvx", "cmd-agent"]


# ============================================================================
# MCPGateway — _register_agent_tools
# ============================================================================


class TestMCPGatewayRegisterAgentTools:
    """Tests for MCPGateway._register_agent_tools()."""

    @pytest.mark.asyncio
    async def test_register_tools_creates_adapters(
        self, gateway: MCPGateway
    ) -> None:
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

    @pytest.mark.asyncio
    async def test_register_tools_not_activated(self, gateway: MCPGateway) -> None:
        """Registering tools for dormant agent does nothing."""
        manifest = _make_manifest("dormant")
        await gateway.register_agent(manifest, deferred=True)
        await gateway._register_agent_tools("dormant")


# ============================================================================
# MCPGateway — _make_tool_func
# ============================================================================


class TestMCPGatewayMakeToolFunc:
    """Tests for MCPGateway._make_tool_func()."""

    def test_func_name_matches_adapter(self, gateway: MCPGateway) -> None:
        schema = _make_tool_schema("my_tool", "Does something")
        adapter = McpToolAdapter(server_name="agent", tool_schema=schema)
        func = gateway._make_tool_func(adapter)
        assert func.__name__ == "mcp__agent__my_tool"

    def test_func_docstring_from_description(self, gateway: MCPGateway) -> None:
        schema = _make_tool_schema("tool", "Custom description")
        adapter = McpToolAdapter(server_name="agent", tool_schema=schema)
        func = gateway._make_tool_func(adapter)
        assert func.__doc__ == "Custom description"

    def test_func_docstring_default(self, gateway: MCPGateway) -> None:
        schema = {"name": "tool"}
        adapter = McpToolAdapter(server_name="agent", tool_schema=schema)
        func = gateway._make_tool_func(adapter)
        assert "mcp__agent__tool" in func.__doc__

    @pytest.mark.asyncio
    async def test_func_returns_error_if_no_handle(
        self, gateway: MCPGateway
    ) -> None:
        schema = _make_tool_schema("tool")
        adapter = McpToolAdapter(server_name="nope", tool_schema=schema)
        func = gateway._make_tool_func(adapter)
        result = await func(x=1)
        assert "Error" in result
        assert "not available" in result

    @pytest.mark.asyncio
    async def test_func_delegates_to_adapter(
        self, gateway: MCPGateway
    ) -> None:
        # Use a name without hyphens so sanitized name == registry key
        manifest = _make_manifest("run_agent")
        await gateway.register_agent(manifest, deferred=False)
        info = gateway.registry.get_agent_info("run_agent")
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


# ============================================================================
# MCPGateway — core tools (_search_and_activate, _list_agents, _agent_info)
# ============================================================================


class TestMCPGatewayCoreTools:
    """Tests for MCPGateway core tool methods."""

    @pytest.mark.asyncio
    async def test_search_and_activate_no_match(
        self, gateway: MCPGateway
    ) -> None:
        result = await gateway._search_and_activate("nonexistent")
        assert "No matching agents found" in result

    @pytest.mark.asyncio
    async def test_search_and_activate_match(
        self, gateway: MCPGateway
    ) -> None:
        manifest = _make_manifest(
            "test-agent", description="A test agent for searching"
        )
        await gateway.register_agent(manifest, deferred=True, start_command=[])
        result = await gateway._search_and_activate("test")
        assert "test-agent" in result
        assert "activated" in result.lower() or "loaded" in result.lower()

    @pytest.mark.asyncio
    async def test_list_agents_empty(self, gateway: MCPGateway) -> None:
        result = await gateway._list_agents()
        assert "Registered Agents" in result

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
    async def test_agent_info_not_found(self, gateway: MCPGateway) -> None:
        result = await gateway._agent_info("nonexistent")
        assert "not found" in result

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
    async def test_agent_info_with_activated_tools(
        self, gateway: MCPGateway
    ) -> None:
        await gateway.register_agent(
            _make_manifest("act-agent", description="Active agent"),
            deferred=True,
            start_command=[],
        )
        await gateway.registry.activate_agent("act-agent")
        result = await gateway._agent_info("act-agent")
        assert "activated" in result

    @pytest.mark.asyncio
    async def test_list_agents_shows_tier(self, gateway: MCPGateway) -> None:
        await gateway.register_agent(
            _make_manifest("core-x"), deferred=False
        )
        result = await gateway._list_agents()
        assert "core" in result

    @pytest.mark.asyncio
    async def test_list_agents_shows_available(
        self, gateway: MCPGateway
    ) -> None:
        await gateway.register_agent(
            _make_manifest("def-x"), deferred=True
        )
        result = await gateway._list_agents()
        assert "available" in result


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
            mock_run.assert_called_once_with(
                transport="sse", host="127.0.0.1", port=8080
            )

    @pytest.mark.asyncio
    async def test_run_sse_custom(self, gateway: MCPGateway) -> None:
        with patch.object(gateway._mcp, "run") as mock_run:
            await gateway.run_sse(host="127.0.0.1", port=9090)
            mock_run.assert_called_once_with(
                transport="sse", host="127.0.0.1", port=9090
            )

    @pytest.mark.asyncio
    async def test_stop(
        self, gateway: MCPGateway, process_manager: MagicMock
    ) -> None:
        await gateway.stop()
        process_manager.stop_all.assert_awaited_once()


# ============================================================================
# Merged from iteration 16: DeferredRegistry deduplication
# ============================================================================


class TestDeferredRegistryDeduplication:
    """get_tools_for_llm must not return duplicate tool names."""

    def test_no_duplication_when_core_and_adapters_overlap(self) -> None:
        """Core agent schemas that are also in _tool_adapters should not duplicate."""
        pm = MagicMock()
        registry = DeferredAgentRegistry(pm)

        manifest = MagicMock()
        manifest.name = "test-agent"
        manifest.description = "Test"
        manifest.type = MagicMock(value="atomic")
        manifest.role = None
        manifest.dependencies = MagicMock(atomic_agents=[])

        registry.register_agent(manifest, deferred=False)

        # Simulate tool schemas in core agents
        info = registry.get_agent_info("test-agent")
        assert info is not None
        info.tool_schemas = [
            {"name": "tool_a", "description": "Tool A"},
            {"name": "tool_b", "description": "Tool B"},
        ]

        # Simulate same tools also in _tool_adapters (what happens when
        # gateway's _register_agent_tools is called for a core agent)
        adapter_a = MagicMock(spec=McpToolAdapter)
        adapter_a.full_name = "tool_a"
        adapter_a.get_tool_definition.return_value = {"name": "tool_a", "description": "Tool A"}

        adapter_b = MagicMock(spec=McpToolAdapter)
        adapter_b.full_name = "tool_b"
        adapter_b.get_tool_definition.return_value = {"name": "tool_b", "description": "Tool B"}

        registry._tool_adapters["test-agent"] = [adapter_a, adapter_b]

        tools = registry.get_tools_for_llm()
        names = [t["name"] for t in tools]
        assert len(names) == len(set(names)), f"Duplicates found: {names}"
        assert "tool_a" in names
        assert "tool_b" in names


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

    def test_sanitize_helper(self) -> None:
        assert _sanitize("my-agent") == "my_agent"
        assert _sanitize("my.agent") == "my_agent"
        assert _sanitize("my agent") == "my_agent"
        assert _sanitize("myAgent") == "myAgent"


class TestCoreAgentToolRegistration:
    """register_agent(deferred=False) must immediately call _register_agent_tools."""

    @pytest.mark.asyncio
    async def test_core_agent_tools_registered(self) -> None:
        pm = MagicMock()
        router = MagicMock()
        gateway = MCPGateway(process_manager=pm, router=router)

        registered_agents = []
        original_register = gateway._register_agent_tools

        async def tracking_register(name: str) -> None:
            registered_agents.append(name)
            await original_register(name)

        gateway._register_agent_tools = tracking_register

        manifest = _make_manifest("core-agent")
        await gateway.register_agent(manifest, deferred=False)

        assert "core-agent" in registered_agents

    @pytest.mark.asyncio
    async def test_deferred_agent_tools_not_registered(self) -> None:
        pm = MagicMock()
        router = MagicMock()
        gateway = MCPGateway(process_manager=pm, router=router)

        registered_agents = []
        original_register = gateway._register_agent_tools

        async def tracking_register(name: str) -> None:
            registered_agents.append(name)
            await original_register(name)

        gateway._register_agent_tools = tracking_register

        manifest = _make_manifest("lazy-agent")
        await gateway.register_agent(manifest, deferred=True)

        assert "lazy-agent" not in registered_agents


class TestListAgentsNameComparison:
    """_list_agents must compare by name, not by object identity."""

    @pytest.mark.asyncio
    async def test_list_agents_core_tier_by_name(self) -> None:
        pm = MagicMock()
        router = MagicMock()
        gateway = MCPGateway(process_manager=pm, router=router)

        manifest = _make_manifest("test-core")
        await gateway.register_agent(manifest, deferred=False)

        result = await gateway._list_agents()
        assert "test-core" in result
        assert "core" in result


# ============================================================================
# Merged from iteration 21: Gateway _list_agents optimization
# ============================================================================


class TestGatewayListAgentsOptimization:
    """_list_agents hoists core_names set outside loop (perf fix)."""

    @pytest.mark.asyncio
    async def test_core_names_computed_once(self) -> None:
        gw = MCPGateway.__new__(MCPGateway)
        gw._registry = MagicMock()
        gw._registered_agents = set()

        call_count = 0

        class FakeCoreInfo:
            name = "core-agent"

        class FakeInfo:
            def __init__(self, name):
                self.name = name
                self.manifest = MagicMock()
                self.tool_schemas = []
                self.is_activated = False
                self.is_running = False

        def counting_list_core():
            nonlocal call_count
            call_count += 1
            return [FakeCoreInfo()]

        gw._registry.list_core_agents = counting_list_core
        gw._registry.list_all_agents = lambda: [
            FakeInfo("core-agent"),
            FakeInfo("agent-2"),
            FakeInfo("agent-3"),
        ]

        await gw._list_agents()

        # Should call list_core_agents exactly once, not once per agent
        assert call_count == 1


# ============================================================================
# Merged from iteration 24: McpToolAdapter execute status edge cases
# ============================================================================


def _make_bare_adapter() -> McpToolAdapter:
    """Create a bare McpToolAdapter without calling __init__."""
    adapter = McpToolAdapter.__new__(McpToolAdapter)
    adapter.agent_name = "test-agent"
    adapter.server_name = "test_agent"
    adapter.tool_name = "my_tool"
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

    None is a valid status per schema — agents that return a RESULT
    without explicitly setting status have succeeded.
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

    @pytest.mark.asyncio
    async def test_status_running_not_success(self) -> None:
        adapter = _make_bare_adapter()
        handle = _make_mock_handle_for_status()
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT, status="running", content="..."
        )
        handle.ipc.receive_until_result = AsyncMock(return_value=response)
        result = await adapter.execute(handle, {})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_status_pending_not_success(self) -> None:
        adapter = _make_bare_adapter()
        handle = _make_mock_handle_for_status()
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT, status="pending", content="..."
        )
        handle.ipc.receive_until_result = AsyncMock(return_value=response)
        result = await adapter.execute(handle, {})
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_status_timeout_not_success(self) -> None:
        adapter = _make_bare_adapter()
        handle = _make_mock_handle_for_status()
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT, status="timeout", content=""
        )
        handle.ipc.receive_until_result = AsyncMock(return_value=response)
        result = await adapter.execute(handle, {})
        assert result["success"] is False


# ---------------------------------------------------------------------------
# Iteration 24 fixes: gateway activation message, registry priority
# ---------------------------------------------------------------------------


class TestSearchAndActivateMessage:
    """_search_and_activate returns 'tools now available' (not 'in next call')."""

    @pytest.mark.asyncio
    async def test_activation_message_says_now_available(self) -> None:
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()

        gw = MCPGateway(pm, router)

        manifest = _make_manifest("test-agent")
        # Register directly with the registry (non-async) to avoid
        # needing await in the test setup
        gw._registry.register_agent(manifest, deferred=True)

        # Mock activate to return tool schemas
        with patch.object(
            gw._registry,
            "activate_agent",
            new_callable=AsyncMock,
            return_value=[{"name": "tool1", "description": "d", "inputSchema": {}}],
        ):
            with patch.object(
                gw._registry,
                "search_agents",
                return_value=[manifest],
            ):
                with patch.object(
                    gw,
                    "_register_agent_tools",
                    new_callable=AsyncMock,
                ):
                    result = await gw._search_and_activate("test")

        assert "tools now available" in result
        assert "in next call" not in result


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

    @pytest.mark.asyncio
    async def test_alive_agent_not_cleaned_up(self) -> None:
        """Alive agent should NOT be removed from _registered_agents."""
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        manifest = _make_manifest("alive-agent")
        await gw.register_agent(manifest, deferred=False)

        info = gw.registry.get_agent_info("alive-agent")
        assert info is not None
        alive_handle = _mock_agent_handle("alive-agent", alive=True)
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="done",
            status="completed",
        )
        alive_handle.ipc.receive_until_result.return_value = response
        info.handle = alive_handle
        info.tool_schemas = [{"name": "do_work", "description": "Work"}]

        schema = _make_tool_schema("do_work", "Work")
        adapter = McpToolAdapter(server_name="alive-agent", tool_schema=schema)
        gw.registry._tool_adapters["alive-agent"] = [adapter]

        await gw._register_agent_tools("alive-agent")
        assert "alive-agent" in gw._registered_agents

        func = gw._make_tool_func(adapter)
        result = await func(x=1)

        # Should succeed, agent should still be registered
        assert result == "done"
        assert "alive-agent" in gw._registered_agents


class TestLazyAsyncioLock:
    """Gateway can be instantiated without a running event loop."""

    def test_sync_instantiation_no_event_loop(self) -> None:
        """Creating MCPGateway outside async context does not raise."""
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()

        import asyncio

        try:
            loop = asyncio.get_running_loop()
            # If we're inside a test with a running loop, just verify
            # the lock is eagerly created
            gw = MCPGateway(pm, router)
            assert isinstance(gw._reg_lock, asyncio.Lock)
        except RuntimeError:
            # No running loop — this is the real test
            gw = MCPGateway(pm, router)
            assert isinstance(gw._reg_lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_lock_created_eagerly(self) -> None:
        """Lock is created in __init__, not lazily."""
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        assert isinstance(gw._reg_lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_lock_is_same_instance(self) -> None:
        """The lock attribute is the same instance on repeated access."""
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        lock1 = gw._reg_lock
        lock2 = gw._reg_lock
        assert lock1 is lock2


# ============================================================================
# Regression: DeferredRegistry lazy lock + activation guard (from iter 42 audit)
# ============================================================================


class TestDeferredRegistryLazyLock:
    """DeferredRegistry lock is created eagerly in __init__.

    In Python 3.10+, asyncio.Lock() does not require a running event
    loop, so the lock is created eagerly to avoid lazy-init race
    conditions.
    """

    def test_sync_instantiation_no_event_loop(self) -> None:
        """Creating DeferredRegistry outside async context does not raise."""
        pm = MagicMock(spec=ProcessManager)
        registry = DeferredAgentRegistry(pm)
        assert isinstance(registry._lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_lock_created_eagerly(self) -> None:
        """Lock is created in __init__, not lazily."""
        pm = MagicMock(spec=ProcessManager)
        registry = DeferredAgentRegistry(pm)
        assert isinstance(registry._lock, asyncio.Lock)

    @pytest.mark.asyncio
    async def test_activate_uses_lock(self) -> None:
        """activate_agent() works with the eager lock."""
        pm = MagicMock(spec=ProcessManager)
        pm.start_agent = AsyncMock()
        registry = DeferredAgentRegistry(pm)

        manifest = AgentManifest(
            name="lazy-agent",
            version="0.1.0",
            type=AgentType.ATOMIC,
            description="A test agent",
        )
        registry.register_agent(
            manifest,
            deferred=True,
            start_command=["python3", "-m", "lazy_agent"],
        )

        # activate_agent should use the lock and succeed
        schemas = await registry.activate_agent("lazy-agent")
        assert isinstance(schemas, list)


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
        assert "lock-test-agent" not in McpToolAdapter._ipc_locks

        handle = _mock_agent_handle("lock-test-agent", alive=True)
        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="ok",
            status="completed",
        )
        handle.ipc.receive_until_result.return_value = response

        # Clean _ipc_locks to isolate this test
        McpToolAdapter.remove_all_locks()
        try:
            await adapter.execute(handle, {})
            # Lock is keyed by original (unsanitized) agent_name, not server_name
            assert "lock-test-agent" in McpToolAdapter._ipc_locks
        finally:
            McpToolAdapter.remove_all_locks()

    @pytest.mark.asyncio
    async def test_ipc_lock_prevents_concurrent_interleave(self) -> None:
        """Two concurrent execute calls to the same agent do not interleave.

        The second call should only start IPC after the first completes
        receive_until_result.
        """
        schema = _make_tool_schema("tool")
        adapter = McpToolAdapter(server_name="conc-agent", tool_schema=schema)

        call_order: list[str] = []

        async def slow_receive(timeout: float = 300.0):
            call_order.append("receive_start")
            await asyncio.sleep(0.05)
            call_order.append("receive_end")
            return AgentToPlatform(
                type=AgentToPlatformType.RESULT,
                content="done",
                status="completed",
            )

        async def fast_receive(timeout: float = 300.0):
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

        McpToolAdapter.remove_all_locks()
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
            assert call_order.index("receive_end") < call_order.index(
                "receive_start_2"
            ), f"Concurrent calls interleaved: {call_order}"
        finally:
            McpToolAdapter.remove_all_locks()

    @pytest.mark.asyncio
    async def test_different_agents_use_different_locks(self) -> None:
        """Two different agents can execute concurrently (separate locks)."""
        schema_a = _make_tool_schema("tool_a")
        schema_b = _make_tool_schema("tool_b")
        adapter_a = McpToolAdapter(server_name="agent-a", tool_schema=schema_a)
        adapter_b = McpToolAdapter(server_name="agent-b", tool_schema=schema_b)

        handle_a = _mock_agent_handle("agent-a", alive=True)
        handle_a.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT, content="a", status="completed",
        )

        handle_b = _mock_agent_handle("agent-b", alive=True)
        handle_b.ipc.receive_until_result.return_value = AgentToPlatform(
            type=AgentToPlatformType.RESULT, content="b", status="completed",
        )

        McpToolAdapter.remove_all_locks()
        try:
            results = await asyncio.gather(
                adapter_a.execute(handle_a, {}),
                adapter_b.execute(handle_b, {}),
            )
            assert results[0]["output"] == "a"
            assert results[1]["output"] == "b"
            assert "agent-a" in McpToolAdapter._ipc_locks
            assert "agent-b" in McpToolAdapter._ipc_locks
        finally:
            McpToolAdapter.remove_all_locks()


# ============================================================================
# McpToolAdapter lock cleanup classmethods
# ============================================================================


class TestMcpToolAdapterLockCleanup:
    """remove_lock and remove_all_locks clean up class-level locks."""

    def setup_method(self) -> None:
        McpToolAdapter.remove_all_locks()

    def teardown_method(self) -> None:
        McpToolAdapter.remove_all_locks()

    def test_remove_lock_clears_single_agent(self) -> None:
        """remove_lock removes only the targeted agent's lock."""
        McpToolAdapter._ipc_locks["agent-x"] = asyncio.Lock()
        McpToolAdapter._ipc_locks["agent-y"] = asyncio.Lock()
        McpToolAdapter.remove_lock("agent-x")
        assert "agent-x" not in McpToolAdapter._ipc_locks
        assert "agent-y" in McpToolAdapter._ipc_locks

    def test_remove_lock_noop_for_unknown_agent(self) -> None:
        """remove_lock on a non-existent agent does not raise."""
        McpToolAdapter.remove_lock("nonexistent")

    def test_remove_all_locks_clears_everything(self) -> None:
        """remove_all_locks clears all entries."""
        McpToolAdapter._ipc_locks["a"] = asyncio.Lock()
        McpToolAdapter._ipc_locks["b"] = asyncio.Lock()
        McpToolAdapter.remove_all_locks()
        assert len(McpToolAdapter._ipc_locks) == 0

    @pytest.mark.asyncio
    async def test_gateway_stop_cleans_locks(self) -> None:
        """MCPGateway.stop() calls remove_all_locks."""
        pm = MagicMock()
        pm.stop_all = AsyncMock()
        router = MagicMock()
        McpToolAdapter._ipc_locks["stale-agent"] = asyncio.Lock()

        gw = MCPGateway(pm, router)
        await gw.stop()
        assert len(McpToolAdapter._ipc_locks) == 0

    @pytest.mark.asyncio
    async def test_invoke_dead_agent_removes_lock(self) -> None:
        """_invoke removes IPC lock when it detects a dead agent."""
        pm = MagicMock()
        router = MagicMock()
        gw = MCPGateway(pm, router)

        schema = _make_tool_schema("test-tool", "desc")
        adapter = McpToolAdapter("dead-agent", schema)
        McpToolAdapter._ipc_locks["dead-agent"] = asyncio.Lock()

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
        assert "dead-agent" not in McpToolAdapter._ipc_locks


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
        ):
            with patch.object(
                gw._registry,
                "activate_agent",
                new_callable=AsyncMock,
                return_value=[{"name": "t", "description": "d", "inputSchema": {}}],
            ):
                with patch.object(
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
        ):
            with patch.object(
                gw._registry,
                "activate_agent",
                new_callable=AsyncMock,
                side_effect=activate_side_effect,
            ):
                with patch.object(
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


class TestListAgentsActivatedTier:
    """_list_agents shows 'activated' tier for activated deferred agents
    that are not in the core set.
    """

    @pytest.mark.asyncio
    async def test_activated_deferred_shows_activated_tier(self) -> None:
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        # Register as deferred, then activate (gives it tool_schemas)
        await gw.register_agent(
            _make_manifest("lazy", description="Lazy agent"),
            deferred=True,
            start_command=[],
        )
        await gw.registry.activate_agent("lazy")

        result = await gw._list_agents()
        assert "lazy" in result
        assert "activated" in result


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
# Coverage: _register_agent_tools already-registered skip (lines 240-241)
# ============================================================================


class TestRegisterAgentToolsAlreadyRegistered:
    """_register_agent_tools returns early when agent tools already registered.
    Logs debug message and skips re-registration.
    """

    @pytest.mark.asyncio
    async def test_already_registered_skips(self) -> None:
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        manifest = _make_manifest("cached-agent")
        await gw.register_agent(manifest, deferred=True, start_command=[])
        await gw.registry.activate_agent("cached-agent")

        # First registration
        await gw._register_agent_tools("cached-agent")
        assert "cached-agent" in gw._registered_agents

        # Capture adapters before second call
        adapters_before = list(gw.registry._tool_adapters.get("cached-agent", []))

        # Second call should skip (line 240-241)
        await gw._register_agent_tools("cached-agent")

        # Adapters should be unchanged — no re-registration occurred
        adapters_after = list(gw.registry._tool_adapters.get("cached-agent", []))
        assert len(adapters_before) == len(adapters_after)


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

        # The second adapter should have been renamed to include a suffix
        assert adapter2.full_name != adapter1.full_name
        assert adapter2.full_name.endswith("_2")

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

        # adapter1 keeps original name, adapter2 gets _2, adapter3 gets _3
        assert adapter2.full_name.endswith("_2")
        assert adapter3.full_name.endswith("_3")

    @pytest.mark.asyncio
    async def test_collision_with_cross_agent_names(self) -> None:
        """Two different agents whose server names sanitize to the same value."""
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        mcp_tool_mock = MagicMock()
        gw._mcp.tool = mcp_tool_mock

        schema = _make_tool_schema("do_work", "Work tool")

        # Register first agent and its tools
        manifest1 = _make_manifest("my-agent")
        gw._registry.register_agent(manifest1, deferred=False)
        info1 = gw._registry.get_agent_info("my-agent")
        assert info1 is not None
        info1.tool_schemas = [schema]
        adapter1 = McpToolAdapter(server_name="my-agent", tool_schema=schema)
        gw._registry._tool_adapters["my-agent"] = [adapter1]
        await gw._register_agent_tools("my-agent")

        # Register second agent whose sanitized name collides: my_agent -> my_agent
        schema2 = _make_tool_schema("do_work", "Different tool")
        manifest2 = _make_manifest("my_agent")
        gw._registry.register_agent(manifest2, deferred=False)
        info2 = gw._registry.get_agent_info("my_agent")
        assert info2 is not None
        info2.tool_schemas = [schema2]
        adapter2 = McpToolAdapter(server_name="my_agent", tool_schema=schema2)
        gw._registry._tool_adapters["my_agent"] = [adapter2]
        await gw._register_agent_tools("my_agent")

        # Second adapter should have been renamed
        assert adapter2.full_name.endswith("_2")


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
            adapter, "execute", new_callable=AsyncMock,
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
            adapter, "execute", new_callable=AsyncMock,
            return_value={"success": False},
        ):
            func = gw._make_tool_func(adapter)
            result = await func()

        assert "Error" in result
        assert "unknown failure" in result


# ============================================================================
# Coverage: _register_agent_tools FastMCP registration error (lines 274-280)
# ============================================================================


class TestRegisterAgentToolsFastMCPError:
    """_register_agent_tools catches FastMCP tool registration errors."""

    @pytest.mark.asyncio
    async def test_fastmcp_registration_error_handled(self) -> None:
        pm = MagicMock(spec=ProcessManager)
        router = MagicMock()
        gw = MCPGateway(pm, router)

        manifest = _make_manifest("mcp-err-agent")
        gw._registry.register_agent(manifest, deferred=False)
        info = gw._registry.get_agent_info("mcp-err-agent")
        assert info is not None
        info.tool_schemas = [{"name": "tool1", "description": "T"}]

        schema = _make_tool_schema("tool1", "T")
        adapter = McpToolAdapter(server_name="mcp-err-agent", tool_schema=schema)
        gw._registry._tool_adapters["mcp-err-agent"] = [adapter]

        # Make FastMCP.tool raise when trying to register
        original_tool = gw._mcp.tool

        def failing_tool_register(func):
            raise ValueError("tool name already registered")

        gw._mcp.tool = failing_tool_register

        # Should not raise — error is caught and logged
        await gw._register_agent_tools("mcp-err-agent")

        # Restore to avoid affecting other tests
        gw._mcp.tool = original_tool
