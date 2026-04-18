"""Unit tests for gateway module — tool_adapter, deferred_registry, gateway.

Tests McpToolAdapter (name sanitization, tool definitions, execute),
DeferredAgentRegistry (registration tiers, activation, search, manifest),
and MCPGateway (core tools, agent registration, tool forwarding).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.models.agent import AgentManifest, AgentType
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
        assert call_args[1]["conversation_id"] == "__gateway_tool__"

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

    def test_core_takes_priority(self, registry: DeferredAgentRegistry) -> None:
        """If an agent name appears in both core and deferred, core is returned."""
        registry.register_agent(_make_manifest("x"), deferred=False)
        registry.register_agent(_make_manifest("x"), deferred=True)
        info = registry.get_agent_info("x")
        assert info is not None
        # Core is checked first
        assert info in registry.list_core_agents()


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
    """execute returns success=False when response.status is None."""

    @pytest.mark.asyncio
    async def test_status_none_returns_success_false(self) -> None:
        adapter = _make_bare_adapter()
        handle = _make_mock_handle_for_status()

        response = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            status=None,
            content="test",
        )
        handle.ipc.receive_until_result = AsyncMock(return_value=response)

        result = await adapter.execute(handle, {})
        assert result["success"] is False


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
