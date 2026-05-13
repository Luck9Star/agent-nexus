"""E2E: Gateway tool call chain — McpToolAdapter + SchemaTransformer + DeferredAgentRegistry.

Tests the complete gateway tool call pipeline using real components:
  - McpToolAdapter: schema wrapping, name sanitization, execution on dead process
  - SchemaTransformer: JSON Schema -> Python type conversion
  - DeferredAgentRegistry: agent registration, tool discovery, lifecycle queries

Only external concerns are mocked (subprocess spawning, IPC). All internal
modules use real instances backed by real SQLite databases.
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock

import pytest

from agent_nexus.models.agent import AgentManifest, AgentType
from agent_nexus.platform.gateway.deferred_registry import DeferredAgentRegistry
from agent_nexus.platform.gateway.schema_transformer import SchemaTransformer
from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter
from agent_nexus.platform.orchestration.process_manager import (
    AgentHandle,
    ProcessManager,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_manifest(
    name: str = "test-agent",
    description: str = "A test agent",
) -> AgentManifest:
    return AgentManifest(
        name=name,
        version="1.0.0",
        type=AgentType.ATOMIC,
        description=description,
    )


def _make_tool_schema(
    name: str = "greet",
    description: str = "Say hello",
    properties: dict | None = None,
    required: list[str] | None = None,
) -> dict:
    props = properties or {"name": {"type": "string"}}
    req = required or ["name"]
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": props,
            "required": req,
        },
    }


@pytest.fixture()
def process_manager() -> Generator[ProcessManager, None, None]:
    pm = ProcessManager()
    yield pm


@pytest.fixture()
def registry(process_manager: ProcessManager) -> Generator[DeferredAgentRegistry, None, None]:
    reg = DeferredAgentRegistry(process_manager)
    yield reg


# ---------------------------------------------------------------------------
# TestToolAdapterContract — real McpToolAdapter
# ---------------------------------------------------------------------------


class TestToolAdapterContract:
    """McpToolAdapter naming, schema preservation, and dead-process execution."""

    def test_name_sanitization(self) -> None:
        """Hyphens and special characters are replaced with underscores."""
        schema = _make_tool_schema(name="my-tool")
        adapter = McpToolAdapter(server_name="my-agent", tool_schema=schema)

        assert adapter.server_name == "my_agent"
        assert adapter.tool_name == "my_tool"
        assert adapter.full_name == "mcp__my_agent__my_tool"

    def test_full_name_format(self) -> None:
        """Full name follows mcp__server__tool convention."""
        schema = _make_tool_schema(name="greet")
        adapter = McpToolAdapter(server_name="test-agent", tool_schema=schema)

        assert adapter.full_name == "mcp__test_agent__greet"
        assert adapter.full_name.startswith("mcp__")

    def test_schema_preservation(self) -> None:
        """inputSchema round-trips through get_tool_definition()."""
        input_schema = {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "count": {"type": "integer"},
            },
            "required": ["message"],
        }
        schema = {"name": "chat", "description": "Chat tool", "inputSchema": input_schema}
        adapter = McpToolAdapter(server_name="srv", tool_schema=schema)

        # get_tool_definition preserves inputSchema
        tool_def = adapter.get_tool_definition()
        assert tool_def["name"] == "mcp__srv__chat"
        assert tool_def["inputSchema"] == input_schema

    async def test_execute_on_dead_process_returns_error(self) -> None:
        """Execute on a dead handle returns error result (no crash)."""
        schema = _make_tool_schema(name="run")
        adapter = McpToolAdapter(server_name="dead-agent", tool_schema=schema)

        # Create a mock AgentHandle whose is_alive returns False
        handle = MagicMock(spec=AgentHandle)
        handle.is_alive = False

        result = await adapter.execute(handle, {"task": "do something"})
        assert result["success"] is False
        assert "not alive" in result["error"]

    def test_adapter_repr(self) -> None:
        """__repr__ includes the full name."""
        schema = _make_tool_schema(name="go")
        adapter = McpToolAdapter(server_name="abc", tool_schema=schema)
        assert repr(adapter) == "McpToolAdapter('mcp__abc__go')"

    def test_tool_schema_missing_name_raises(self) -> None:
        """McpToolAdapter raises ValueError when schema has no name."""
        with pytest.raises(ValueError, match="missing required 'name'"):
            McpToolAdapter(server_name="srv", tool_schema={"description": "no name"})

    def test_original_name_preserved(self) -> None:
        """Original tool name is preserved unsanitized for IPC."""
        schema = _make_tool_schema(name="my-special-tool")
        adapter = McpToolAdapter(server_name="my-agent", tool_schema=schema)

        assert adapter.tool_name == "my_special_tool"
        assert adapter._original_tool_name == "my-special-tool"


# ---------------------------------------------------------------------------
# TestSchemaTransformerReal — real SchemaTransformer
# ---------------------------------------------------------------------------


class TestSchemaTransformerReal:
    """SchemaTransformer converts JSON Schema to Python types."""

    def test_simple_object_schema_produces_params(self) -> None:
        """Simple object schema resolves to a Pydantic model with fields."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
            "required": ["name"],
        }
        t = SchemaTransformer({})
        result = t.resolve(schema, name="Person")

        # Should be a BaseModel subclass
        from pydantic import BaseModel

        assert isinstance(result, type)
        assert issubclass(result, BaseModel)

        # Required field
        instance = result(name="Alice")
        assert instance.name == "Alice"  # type: ignore[attr-defined]

        # Optional field
        assert not hasattr(instance, "age") or getattr(instance, "age", None) is None

    def test_nested_object_with_ref(self) -> None:
        """$ref resolution produces a model with nested model fields."""
        full_schema = {
            "$defs": {
                "Address": {
                    "type": "object",
                    "properties": {
                        "street": {"type": "string"},
                        "city": {"type": "string"},
                    },
                    "required": ["street", "city"],
                },
            },
        }
        schema = {
            "type": "object",
            "properties": {
                "home": {"$ref": "#/$defs/Address"},
            },
            "required": ["home"],
        }
        t = SchemaTransformer(full_schema)
        result = t.resolve(schema, name="Person")

        from pydantic import BaseModel

        assert issubclass(result, BaseModel)
        instance = result(home={"street": "123 Main", "city": "NYC"})
        assert instance.home.street == "123 Main"  # type: ignore[attr-defined]

    def test_array_type(self) -> None:
        """Array schema resolves to list[item_type]."""
        schema = {"type": "array", "items": {"type": "string"}}
        t = SchemaTransformer({})
        result = t.resolve(schema, name="Tags")

        # Should be list[str]
        assert result == list[str]

    def test_enum_type(self) -> None:
        """Enum-like string with const/enum — resolves via oneOf or plain string."""
        # JSON Schema enum at the property level
        schema = {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "inactive", "pending"],
                },
            },
            "required": ["status"],
        }
        t = SchemaTransformer({})
        result = t.resolve(schema, name="StatusModel")

        from pydantic import BaseModel

        assert issubclass(result, BaseModel)
        # "active" is a valid value
        instance = result(status="active")
        assert instance.status == "active"  # type: ignore[attr-defined]

    def test_oneOf_produces_union(self) -> None:
        """oneOf with multiple types produces a Union type."""
        schema = {
            "oneOf": [
                {"type": "string"},
                {"type": "integer"},
            ],
        }
        t = SchemaTransformer({})
        result = t.resolve(schema, name="Flexible")

        # Should be str | int — in Python 3.10+ this is types.UnionType
        import types as builtin_types

        assert isinstance(result, builtin_types.UnionType)
        assert str in result.__args__
        assert int in result.__args__

    def test_empty_schema_produces_no_params(self) -> None:
        """Empty object schema produces a model with no fields."""
        schema = {"type": "object", "properties": {}}
        t = SchemaTransformer({})
        result = t.resolve(schema, name="Empty")

        from pydantic import BaseModel

        assert issubclass(result, BaseModel)
        result()
        assert len(result.model_fields) == 0


# ---------------------------------------------------------------------------
# TestDeferredRegistryLifecycle — real DeferredAgentRegistry + real ProcessManager
# ---------------------------------------------------------------------------


class TestDeferredRegistryLifecycle:
    """DeferredAgentRegistry registration, query, and tier management."""

    def test_register_core_agent(self, registry: DeferredAgentRegistry) -> None:
        """Register a core agent and verify it appears in list."""
        manifest = _make_manifest("core-agent", "Core test agent")
        registry.register_agent(manifest, deferred=False)

        agents = registry.list_core_agents()
        assert len(agents) == 1
        assert agents[0].name == "core-agent"

    def test_register_deferred_agent(self, registry: DeferredAgentRegistry) -> None:
        """Register a deferred agent and verify it appears in deferred list."""
        manifest = _make_manifest("deferred-agent", "Deferred test agent")
        registry.register_agent(manifest, deferred=True)

        agents = registry.list_deferred_agents()
        assert len(agents) == 1
        assert agents[0].name == "deferred-agent"

    def test_deferred_agent_not_in_tools_initially(self, registry: DeferredAgentRegistry) -> None:
        """Deferred agents contribute zero tools before activation."""
        manifest = _make_manifest("dormant-agent", "Dormant")
        registry.register_agent(manifest, deferred=True)

        tools = registry.get_tools_for_llm()
        assert tools == []

    def test_agent_manifest_round_trip(self, registry: DeferredAgentRegistry) -> None:
        """Register agent, retrieve info, verify manifest fields."""
        manifest = _make_manifest("round-trip", "Round trip agent")
        registry.register_agent(manifest, deferred=False)

        info = registry.get_agent_info("round-trip")
        assert info is not None
        assert info.manifest.name == "round-trip"
        assert info.manifest.description == "Round trip agent"
        assert info.manifest.version == "1.0.0"
        assert info.manifest.type == AgentType.ATOMIC

    def test_get_agent_info_nonexistent(self, registry: DeferredAgentRegistry) -> None:
        """get_agent_info returns None for unregistered agent."""
        info = registry.get_agent_info("ghost-agent")
        assert info is None

    def test_list_all_agents_combined(self, registry: DeferredAgentRegistry) -> None:
        """list_all_agents returns both core and deferred agents."""
        registry.register_agent(_make_manifest("core-1"), deferred=False)
        registry.register_agent(_make_manifest("deferred-1"), deferred=True)

        all_agents = registry.list_all_agents()
        names = {a.name for a in all_agents}
        assert names == {"core-1", "deferred-1"}

    def test_remove_agent_tools(self, registry: DeferredAgentRegistry) -> None:
        """remove_agent_tools removes agent from all tiers."""
        manifest = _make_manifest("removable", "Will be removed")
        registry.register_agent(manifest, deferred=False)

        # Confirm it exists
        assert registry.get_agent_info("removable") is not None

        # Remove
        registry.remove_agent_tools("removable")
        assert registry.get_agent_info("removable") is None

    def test_search_agents_by_keyword(self, registry: DeferredAgentRegistry) -> None:
        """search_agents finds agents by keyword in description."""
        registry.register_agent(
            _make_manifest("code-reviewer", "Reviews Python code"), deferred=False
        )
        registry.register_agent(_make_manifest("doc-writer", "Writes documentation"), deferred=True)

        results = registry.search_agents("code")
        assert len(results) == 1
        assert results[0].name == "code-reviewer"

    def test_build_manifest_text(self, registry: DeferredAgentRegistry) -> None:
        """build_manifest produces text summary for LLM context."""
        registry.register_agent(_make_manifest("core-1", "Core agent for testing"), deferred=False)
        registry.register_agent(_make_manifest("deferred-1", "Deferred agent"), deferred=True)

        text = registry.build_manifest()
        assert "core-1" in text
        assert "deferred-1" in text
        assert "core" in text
        assert "available" in text

    def test_reregister_from_core_to_deferred(self, registry: DeferredAgentRegistry) -> None:
        """Re-registering a core agent as deferred moves it correctly."""
        manifest = _make_manifest("switch", "Switchable")
        registry.register_agent(manifest, deferred=False)
        assert len(registry.list_core_agents()) == 1

        registry.register_agent(manifest, deferred=True)
        assert len(registry.list_core_agents()) == 0
        assert len(registry.list_deferred_agents()) == 1

    def test_core_agent_in_tools_for_llm(self, registry: DeferredAgentRegistry) -> None:
        """Core agent with manually set tool_schemas appears in get_tools_for_llm."""
        manifest = _make_manifest("tooled-agent", "Has tools")
        registry.register_agent(manifest, deferred=False)

        # Manually inject tool schemas (simulating activation)
        info = registry.get_agent_info("tooled-agent")
        assert info is not None
        info.tool_schemas = [_make_tool_schema("do-work")]

        tools = registry.get_tools_for_llm()
        assert len(tools) == 1
        assert tools[0]["name"] == "do-work"

    def test_get_tool_adapter_returns_none_for_unknown(
        self, registry: DeferredAgentRegistry
    ) -> None:
        """get_tool_adapter returns None for unregistered tool name."""
        assert registry.get_tool_adapter("mcp__nonexistent__tool") is None

    def test_get_tool_adapters_returns_empty_for_unknown_agent(
        self, registry: DeferredAgentRegistry
    ) -> None:
        """get_tool_adapters returns empty list for unregistered agent."""
        assert registry.get_tool_adapters("nonexistent") == []
