"""E2E: Gateway full lifecycle — DeferredAgentRegistry + SchemaTransformer + McpToolAdapter.

TRUE E2E tests verifying the complete tool call chain:
  agent registration -> tool discovery -> tool adapter creation -> result return

All internal modules are real (no mocks on registry, transformer, adapter).
Only the subprocess layer (ProcessManager.start_agent, AgentHandle) is mocked
because we cannot run real agent subprocesses in CI.

Test sections:
  1. DeferredAgentRegistry full lifecycle (real ProcessManager, real in-memory state)
  2. SchemaTransformer with real MCP-style schemas (no mocks)
  3. McpToolAdapter with real schema conversion (no mocks on adapter)
  4. Full chain: register -> activate -> discover -> adapter -> tool definition
"""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from agent_nexus.models.agent import AgentManifest, AgentType
from agent_nexus.platform.gateway.deferred_registry import DeferredAgentRegistry
from agent_nexus.platform.gateway.schema_transformer import SchemaTransformer
from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter
from agent_nexus.platform.orchestration.process_manager import (
    AgentHandle,
    ProcessManager,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_manifest(
    name: str = "test-agent",
    description: str = "A test agent",
    *,
    agent_type: AgentType = AgentType.ATOMIC,
) -> AgentManifest:
    return AgentManifest(
        name=name,
        version="1.0.0",
        type=agent_type,
        description=description,
    )


def _make_tool_schema(
    name: str = "greet",
    description: str = "Say hello",
    properties: dict | None = None,
    required: list[str] | None = None,
    extra: dict | None = None,
) -> dict:
    """Build a realistic MCP tool schema."""
    props = properties or {
        "name": {"type": "string", "description": "Name to greet"},
        "greeting": {"type": "string", "description": "Custom greeting"},
    }
    req = required or ["name"]
    schema: dict = {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": props,
            "required": req,
        },
    }
    if extra:
        schema.update(extra)
    return schema


def _make_mock_handle(name: str = "test-agent") -> MagicMock:
    """Create a mock AgentHandle with is_alive=True and IPC protocol."""
    handle = MagicMock(spec=AgentHandle)
    handle.name = name
    handle.is_alive = True
    handle.ipc = MagicMock()
    handle.ipc.send_chat = AsyncMock()
    handle.ipc.receive_until_result = AsyncMock()
    handle.ipc.stream = MagicMock()
    handle.ipc.stream.close_sync = MagicMock()
    handle.process = MagicMock()
    handle.process.returncode = None
    return handle


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def process_manager() -> Generator[ProcessManager, None, None]:
    pm = ProcessManager()
    yield pm


@pytest.fixture()
def registry(process_manager: ProcessManager) -> Generator[DeferredAgentRegistry, None, None]:
    reg = DeferredAgentRegistry(process_manager)
    yield reg


# ===========================================================================
# 1. DeferredAgentRegistry full lifecycle
# ===========================================================================


class TestDeferredRegistryLifecycle:
    """Full agent lifecycle: register core -> register deferred -> activate -> remove."""

    def test_register_core_agent_with_tools(self, registry: DeferredAgentRegistry) -> None:
        """Core agent registration: manifest stored, appears in core list."""
        manifest = _make_manifest("core-agent", "Core agent for critical tasks")
        registry.register_agent(manifest, deferred=False)

        core = registry.list_core_agents()
        assert len(core) == 1
        assert core[0].name == "core-agent"
        assert core[0].manifest.description == "Core agent for critical tasks"

        # Not yet activated (no subprocess), so tool_schemas is None
        info = registry.get_agent_info("core-agent")
        assert info is not None
        assert info.is_activated is False
        assert info.tool_schemas is None

    def test_register_deferred_agent_not_in_initial_tools(
        self, registry: DeferredAgentRegistry
    ) -> None:
        """Deferred agents contribute zero tools before activation."""
        registry.register_agent(
            _make_manifest("deferred-code-reviewer", "Reviews source code"), deferred=True
        )
        registry.register_agent(
            _make_manifest("deferred-doc-writer", "Writes documentation"), deferred=True
        )

        # No tools available yet
        tools = registry.get_tools_for_llm()
        assert tools == []

        # Agents exist but are dormant
        all_agents = registry.list_all_agents()
        assert len(all_agents) == 2
        for agent in all_agents:
            assert agent.is_activated is False

    async def test_activate_deferred_agent_discovers_tools(
        self, registry: DeferredAgentRegistry
    ) -> None:
        """Activate a deferred agent via mock subprocess, verify tools appear."""
        manifest = _make_manifest("active-agent", "Agent that gets activated")
        tool_schema = _make_tool_schema("analyze-code", "Analyze code quality")
        mock_handle = _make_mock_handle("active-agent")

        registry.register_agent(
            manifest,
            deferred=True,
            start_command=["python", "-m", "fake"],
        )

        # Patch ProcessManager.start_agent to return our mock handle
        with patch.object(
            registry._pm,
            "start_agent",
            new=AsyncMock(return_value=mock_handle),
        ):
            # Patch _fetch_agent_tools to return known schemas (simulates IPC discovery)
            with patch.object(
                registry,
                "_fetch_agent_tools",
                new=AsyncMock(return_value=[tool_schema]),
            ):
                schemas = await registry.activate_agent("active-agent")

        assert len(schemas) == 1
        assert schemas[0]["name"] == "analyze-code"

        # Now tools should be available
        info = registry.get_agent_info("active-agent")
        assert info is not None
        assert info.is_activated is True
        assert info.tool_schemas is not None
        assert len(info.tool_schemas) == 1

        # Tool adapter created automatically
        adapters = registry.get_tool_adapters("active-agent")
        assert len(adapters) == 1
        assert adapters[0].full_name == "mcp__active_agent__analyze_code"

    async def test_activate_agent_creates_tool_adapters(
        self, registry: DeferredAgentRegistry
    ) -> None:
        """Activation creates McpToolAdapter instances with correct names."""
        manifest = _make_manifest("multi-tool-agent", "Has multiple tools")
        tools = [
            _make_tool_schema("read-file", "Read a file"),
            _make_tool_schema("write-file", "Write a file"),
            _make_tool_schema("search", "Search codebase"),
        ]
        mock_handle = _make_mock_handle("multi-tool-agent")

        registry.register_agent(manifest, deferred=True, start_command=["cmd"])

        with patch.object(registry._pm, "start_agent", new=AsyncMock(return_value=mock_handle)):
            with patch.object(registry, "_fetch_agent_tools", new=AsyncMock(return_value=tools)):
                await registry.activate_agent("multi-tool-agent")

        adapters = registry.get_tool_adapters("multi-tool-agent")
        assert len(adapters) == 3

        full_names = {a.full_name for a in adapters}
        assert full_names == {
            "mcp__multi_tool_agent__read_file",
            "mcp__multi_tool_agent__write_file",
            "mcp__multi_tool_agent__search",
        }

        # Reverse index populated
        for name in full_names:
            assert registry.get_tool_adapter(name) is not None

    async def test_activate_then_remove_cleans_up(self, registry: DeferredAgentRegistry) -> None:
        """Remove after activation cleans adapters and reverse index."""
        manifest = _make_manifest("temporary-agent", "Temporary")
        tool = _make_tool_schema("temp-tool", "Temporary tool")
        mock_handle = _make_mock_handle("temporary-agent")

        registry.register_agent(manifest, deferred=True, start_command=["cmd"])

        with patch.object(registry._pm, "start_agent", new=AsyncMock(return_value=mock_handle)):
            with patch.object(registry, "_fetch_agent_tools", new=AsyncMock(return_value=[tool])):
                await registry.activate_agent("temporary-agent")

        # Verify activation worked
        assert registry.get_tool_adapter("mcp__temporary_agent__temp_tool") is not None

        # Remove
        registry.remove_agent_tools("temporary-agent")

        # Everything cleaned up
        assert registry.get_agent_info("temporary-agent") is None
        assert registry.get_tool_adapters("temporary-agent") == []
        assert registry.get_tool_adapter("mcp__temporary_agent__temp_tool") is None

    async def test_activated_deferred_agent_tools_in_llm_list(
        self, registry: DeferredAgentRegistry
    ) -> None:
        """Activated deferred agents contribute tools to get_tools_for_llm."""
        registry.register_agent(_make_manifest("core-1", "Core"), deferred=False)
        tool_schema = _make_tool_schema("core-tool", "Core tool")
        core_info = registry.get_agent_info("core-1")
        assert core_info is not None
        core_info.tool_schemas = [tool_schema]

        manifest = _make_manifest("deferred-1", "Deferred")
        mock_handle = _make_mock_handle("deferred-1")
        deferred_tool = _make_tool_schema("deferred-tool", "Deferred tool")
        registry.register_agent(manifest, deferred=True, start_command=["cmd"])

        with patch.object(registry._pm, "start_agent", new=AsyncMock(return_value=mock_handle)):
            with patch.object(
                registry, "_fetch_agent_tools", new=AsyncMock(return_value=[deferred_tool])
            ):
                await registry.activate_agent("deferred-1")

        tools = registry.get_tools_for_llm()
        assert len(tools) == 2
        tool_names = [t["name"] for t in tools]
        assert "core-tool" in tool_names
        assert "mcp__deferred_1__deferred_tool" in tool_names

    def test_search_agents_by_keyword(self, registry: DeferredAgentRegistry) -> None:
        """search_agents finds agents across tiers by keyword.

        Note: search splits on whitespace into words, so hyphenated names
        like "python-reviewer" become single tokens. "python" does NOT match
        "python-reviewer" — the search is word-level, not substring.
        """
        registry.register_agent(
            _make_manifest("code-reviewer", "Reviews Python source code"), deferred=False
        )
        registry.register_agent(
            _make_manifest("rust-analyzer", "Reviews Rust source code"), deferred=True
        )
        registry.register_agent(
            _make_manifest("doc-writer", "Writes markdown documentation"), deferred=True
        )

        # "reviews" matches both reviewers (word in description)
        results = registry.search_agents("reviews")
        names = {m.name for m in results}
        assert "code-reviewer" in names
        assert "rust-analyzer" in names
        assert "doc-writer" not in names

        # "python" matches only code-reviewer (word in description)
        results = registry.search_agents("python")
        assert len(results) == 1
        assert results[0].name == "code-reviewer"

        # "source" matches both reviewers (word in description)
        results = registry.search_agents("source")
        assert len(results) == 2

    def test_search_agents_no_results(self, registry: DeferredAgentRegistry) -> None:
        """search_agents returns empty list when nothing matches."""
        registry.register_agent(_make_manifest("agent-1"), deferred=False)
        assert registry.search_agents("nonexistent xyz") == []

    def test_full_manifest_round_trip(self, registry: DeferredAgentRegistry) -> None:
        """Register agent with full manifest, verify all fields preserved."""
        manifest = AgentManifest(
            name="full-agent",
            version="2.3.1",
            type=AgentType.COMPOSITE,
            description="A comprehensive agent for testing",
            capabilities=["code-review", "documentation"],
        )
        registry.register_agent(manifest, deferred=False)

        info = registry.get_agent_info("full-agent")
        assert info is not None
        assert info.manifest.name == "full-agent"
        assert info.manifest.version == "2.3.1"
        assert info.manifest.type == AgentType.COMPOSITE
        assert info.manifest.description == "A comprehensive agent for testing"
        assert "code-review" in info.manifest.capabilities

    def test_build_manifest_shows_tiers(self, registry: DeferredAgentRegistry) -> None:
        """build_manifest text distinguishes core/available/activated tiers."""
        registry.register_agent(_make_manifest("core-a", "Core agent A"), deferred=False)
        registry.register_agent(_make_manifest("deferred-b", "Deferred agent B"), deferred=True)

        text = registry.build_manifest()
        assert "core-a" in text
        assert "core" in text
        assert "deferred-b" in text
        assert "available" in text

    async def test_activate_without_subprocess_uses_placeholder(
        self, registry: DeferredAgentRegistry
    ) -> None:
        """Activating an agent with no start_command gets placeholder chat tool."""
        manifest = _make_manifest("no-cmd-agent", "No start command")
        registry.register_agent(manifest, deferred=True)

        # No start_command -> no subprocess -> placeholder tool
        schemas = await registry.activate_agent("no-cmd-agent")
        assert len(schemas) == 1
        assert schemas[0]["name"] == "no-cmd-agent__chat"
        assert "message" in schemas[0]["inputSchema"]["properties"]

    async def test_activate_nonexistent_agent_raises(self, registry: DeferredAgentRegistry) -> None:
        """Activating an unregistered agent raises KeyError."""
        with pytest.raises(KeyError, match="not registered"):
            await registry.activate_agent("ghost")


# ===========================================================================
# 2. SchemaTransformer with real MCP-style schemas
# ===========================================================================


class TestSchemaTransformerMcpStyle:
    """SchemaTransformer handles complex real-world MCP tool schemas."""

    def test_complex_nested_mcp_schema(self) -> None:
        """Deeply nested MCP tool schema resolves to nested Pydantic models."""
        full_schema: dict = {
            "$defs": {
                "CodeLocation": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "line": {"type": "integer"},
                        "column": {"type": "integer"},
                    },
                    "required": ["file", "line"],
                },
                "CodeIssue": {
                    "type": "object",
                    "properties": {
                        "severity": {"type": "string", "enum": ["error", "warning", "info"]},
                        "message": {"type": "string"},
                        "location": {"$ref": "#/$defs/CodeLocation"},
                    },
                    "required": ["severity", "message", "location"],
                },
            },
        }
        schema = {
            "type": "object",
            "properties": {
                "issues": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/CodeIssue"},
                },
            },
            "required": ["issues"],
        }
        t = SchemaTransformer(full_schema)
        result = t.resolve(schema, name="CodeReviewResult")

        assert issubclass(result, BaseModel)
        instance = result(
            issues=[
                {
                    "severity": "error",
                    "message": "Undefined variable",
                    "location": {"file": "main.py", "line": 42},
                }
            ]
        )
        assert len(instance.issues) == 1  # type: ignore[attr-defined]
        issue = instance.issues[0]  # type: ignore[attr-defined]
        assert issue.severity == "error"
        assert issue.location.file == "main.py"
        assert issue.location.line == 42

    def test_ref_resolution_with_full_paths(self) -> None:
        """$ref with full paths (#/components/schemas/X) resolves correctly."""
        full_schema: dict = {
            "components": {
                "schemas": {
                    "Address": {
                        "type": "object",
                        "properties": {
                            "street": {"type": "string"},
                            "city": {"type": "string"},
                            "zip": {"type": "string"},
                        },
                        "required": ["street", "city"],
                    },
                },
            },
        }
        schema = {"$ref": "#/components/schemas/Address"}
        t = SchemaTransformer(full_schema)
        result = t.resolve(schema, name="MyAddress")

        assert issubclass(result, BaseModel)
        instance = result(street="123 Main St", city="Springfield")
        assert instance.street == "123 Main St"  # type: ignore[attr-defined]
        assert instance.city == "Springfield"  # type: ignore[attr-defined]

    def test_all_of_with_required_field_merging(self) -> None:
        """allOf merges properties from multiple sub-schemas and required lists."""
        full_schema: dict = {
            "$defs": {
                "Identifiable": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                    },
                    "required": ["id"],
                },
            },
        }
        schema = {
            "allOf": [
                {"$ref": "#/$defs/Identifiable"},
                {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "email": {"type": "string"},
                    },
                    "required": ["name"],
                },
                {"required": ["email"]},
            ],
        }
        t = SchemaTransformer(full_schema)
        result = t.resolve(schema, name="User")

        assert issubclass(result, BaseModel)
        # All three fields should be required after promotion
        instance = result(id="123", name="Alice", email="alice@example.com")
        assert instance.id == "123"  # type: ignore[attr-defined]
        assert instance.name == "Alice"  # type: ignore[attr-defined]

    def test_one_of_any_of_union_types(self) -> None:
        """oneOf and anyOf produce Union types."""
        # oneOf: string or integer
        schema = {
            "oneOf": [
                {"type": "string"},
                {"type": "integer"},
            ],
        }
        t = SchemaTransformer({})
        result = t.resolve(schema, name="FlexVal")

        import types as builtin_types

        assert isinstance(result, builtin_types.UnionType)
        assert str in result.__args__
        assert int in result.__args__

    def test_any_of_with_null_produces_optional(self) -> None:
        """anyOf with null type produces Optional[X]."""
        schema = {
            "anyOf": [
                {"type": "string"},
                {"type": "null"},
            ],
        }
        t = SchemaTransformer({})
        result = t.resolve(schema, name="MaybeString")

        # Should be str | None
        assert result == str | None

    def test_empty_schema_falls_back_to_str(self) -> None:
        """Completely empty schema resolves to str."""
        t = SchemaTransformer({})
        result = t.resolve({}, name="Empty")
        assert result is str

    def test_null_default_produces_optional_type(self) -> None:
        """Property with default: null produces Optional type."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "nickname": {"type": "string", "default": None},
            },
            "required": ["name"],
        }
        t = SchemaTransformer({})
        result = t.resolve(schema, name="Person")

        assert issubclass(result, BaseModel)
        # nickname should accept None
        instance = result(name="Alice")
        assert instance.name == "Alice"  # type: ignore[attr-defined]
        assert instance.nickname is None  # type: ignore[attr-defined]

    def test_extra_properties_ignored(self) -> None:
        """Extra JSON Schema keywords (examples, etc.) are silently ignored."""
        schema = {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path",
                    "examples": ["/usr/bin/python", "/home/user/file.txt"],
                    "minLength": 1,
                    "maxLength": 4096,
                },
            },
            "required": ["path"],
        }
        t = SchemaTransformer({})
        result = t.resolve(schema, name="FilePath")

        assert issubclass(result, BaseModel)
        instance = result(path="/usr/bin/python")
        assert instance.path == "/usr/bin/python"  # type: ignore[attr-defined]

    def test_circular_ref_does_not_crash(self) -> None:
        """Circular $ref references are handled without infinite recursion."""
        full_schema: dict = {
            "$defs": {
                "TreeNode": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"},
                        "children": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/TreeNode"},
                        },
                    },
                    "required": ["value"],
                },
            },
        }
        schema = {"$ref": "#/$defs/TreeNode"}
        t = SchemaTransformer(full_schema)

        # Should not raise — circular refs are handled via cache
        result = t.resolve(schema, name="TreeNode")
        assert issubclass(result, BaseModel)

        instance = result(value="root")
        assert instance.value == "root"  # type: ignore[attr-defined]

    def test_openapi_nullable_type_list(self) -> None:
        """OpenAPI 3.1 style ['string', 'null'] type produces Optional[str]."""
        schema = {
            "type": ["string", "null"],
        }
        t = SchemaTransformer({})
        result = t.resolve(schema, name="NullableString")
        assert result == str | None

    def test_string_format_preserved_as_str(self) -> None:
        """String formats (date-time, email, uri) resolve to str."""
        for fmt in ("date-time", "date", "email", "uri", "uuid"):
            schema = {"type": "string", "format": fmt}
            t = SchemaTransformer({})
            result = t.resolve(schema, name=fmt.replace("-", "_"))
            assert result is str, f"format={fmt} should resolve to str"

    def test_array_of_objects(self) -> None:
        """Array of objects resolves to list[BaseModel]."""
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "score": {"type": "integer"},
                },
                "required": ["name", "score"],
            },
        }
        t = SchemaTransformer({})
        result = t.resolve(schema, name="ScoreList")

        # Should be list[SomeModel]
        assert hasattr(result, "__origin__")
        assert result.__origin__ is list
        item_type = result.__args__[0]
        assert issubclass(item_type, BaseModel)

    def test_resolve_model_wraps_primitive(self) -> None:
        """resolve_model wraps a primitive into a single-field Pydantic model."""
        schema = {"type": "string"}
        t = SchemaTransformer({})
        model = t.resolve_model(schema, name="WrappedString")

        assert issubclass(model, BaseModel)
        instance = model(value="hello")
        assert instance.value == "hello"  # type: ignore[attr-defined]


# ===========================================================================
# 3. McpToolAdapter with real schema conversion
# ===========================================================================


class TestMcpToolAdapterRealSchemas:
    """McpToolAdapter handles real MCP tool schemas without mocks."""

    def test_adapter_from_complex_mcp_schema(self) -> None:
        """Complex MCP tool schema creates adapter with correct metadata."""
        schema = {
            "name": "analyze-code",
            "description": "Analyze source code for issues",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to source file",
                    },
                    "rules": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Lint rules to apply",
                    },
                    "severity_threshold": {
                        "type": "string",
                        "enum": ["error", "warning", "info"],
                        "default": "warning",
                    },
                },
                "required": ["file_path"],
            },
        }
        adapter = McpToolAdapter(server_name="code-reviewer", tool_schema=schema)

        assert adapter.full_name == "mcp__code_reviewer__analyze_code"
        assert adapter.description == "Analyze source code for issues"
        assert adapter._original_tool_name == "analyze-code"

    def test_tool_definition_mcp_compliant(self) -> None:
        """get_tool_definition() returns MCP-compliant structure."""
        schema = {
            "name": "search",
            "description": "Search codebase",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        }
        adapter = McpToolAdapter(server_name="search-svc", tool_schema=schema)
        tool_def = adapter.get_tool_definition()

        # MCP tool definition format
        assert "name" in tool_def
        assert "description" in tool_def
        assert "inputSchema" in tool_def
        assert tool_def["name"] == "mcp__search_svc__search"
        assert tool_def["inputSchema"]["type"] == "object"
        assert "query" in tool_def["inputSchema"]["properties"]

    def test_adapter_missing_input_schema_defaults_to_empty_object(self) -> None:
        """Missing inputSchema is normalized to empty object type."""
        schema = {"name": "simple-tool", "description": "No input schema"}
        adapter = McpToolAdapter(server_name="srv", tool_schema=schema)

        tool_def = adapter.get_tool_definition()
        assert tool_def["inputSchema"]["type"] == "object"
        assert tool_def["inputSchema"]["properties"] == {}

    def test_adapter_invalid_input_schema_normalizes(self) -> None:
        """Non-object inputSchema is normalized to empty object."""
        schema = {
            "name": "bad-schema",
            "description": "Bad schema",
            "inputSchema": {"type": "string"},  # Invalid for MCP
        }
        adapter = McpToolAdapter(server_name="srv", tool_schema=schema)

        tool_def = adapter.get_tool_definition()
        assert tool_def["inputSchema"]["type"] == "object"
        assert tool_def["inputSchema"]["properties"] == {}

    async def test_execute_on_dead_handle_returns_error(self) -> None:
        """Execute on dead handle returns structured error, no exception."""
        schema = _make_tool_schema("work")
        adapter = McpToolAdapter(server_name="dead-srv", tool_schema=schema)

        handle = _make_mock_handle("dead-srv")
        handle.is_alive = False

        result = await adapter.execute(handle, {"name": "test"})

        assert result["success"] is False
        assert "not alive" in result["error"]
        assert "ProcessNotAliveError" in result.get("error_type", "")

    def test_multiple_adapters_same_server(self) -> None:
        """Multiple tools from same server get unique full names."""
        schemas = [
            _make_tool_schema("read", "Read file"),
            _make_tool_schema("write", "Write file"),
            _make_tool_schema("delete", "Delete file"),
        ]
        adapters = [McpToolAdapter(server_name="file-agent", tool_schema=s) for s in schemas]

        full_names = {a.full_name for a in adapters}
        assert len(full_names) == 3
        assert "mcp__file_agent__read" in full_names
        assert "mcp__file_agent__write" in full_names
        assert "mcp__file_agent__delete" in full_names

    def test_special_characters_in_names(self) -> None:
        """Special characters in server/tool names are sanitized."""
        schema = _make_tool_schema("my-special-tool.v2")
        adapter = McpToolAdapter(server_name="my-cool-agent", tool_schema=schema)

        assert adapter.server_name == "my_cool_agent"
        assert adapter.tool_name == "my_special_tool_v2"
        assert adapter.full_name == "mcp__my_cool_agent__my_special_tool_v2"


# ===========================================================================
# 4. Full chain: register -> activate -> discover -> adapter -> tool definition
# ===========================================================================


class TestFullGatewayChain:
    """End-to-end chain: register agent -> activate -> discover tools -> adapter -> definition."""

    async def test_core_agent_chain(self, registry: DeferredAgentRegistry) -> None:
        """Core agent: register -> inject schemas -> tools appear in LLM list."""
        manifest = _make_manifest("core-chain-agent", "Core chain test")
        registry.register_agent(manifest, deferred=False)

        # Simulate tool discovery for core agent (normally done by gateway)
        tool = _make_tool_schema(
            "execute-task",
            "Execute a task",
            properties={
                "task_name": {"type": "string"},
                "priority": {"type": "integer", "default": 5},
            },
            required=["task_name"],
        )
        info = registry.get_agent_info("core-chain-agent")
        assert info is not None
        info.tool_schemas = [tool]

        # Simulate adapter creation (gateway does this during registration)
        adapter = McpToolAdapter(server_name="core-chain-agent", tool_schema=tool)
        registry._tool_adapters["core-chain-agent"] = [adapter]
        registry._tool_by_name[adapter.full_name] = adapter

        # Verify full chain
        tools = registry.get_tools_for_llm()
        assert len(tools) >= 1

        tool_def = adapter.get_tool_definition()
        assert tool_def["name"] == "mcp__core_chain_agent__execute_task"
        assert tool_def["inputSchema"]["type"] == "object"
        assert "task_name" in tool_def["inputSchema"]["properties"]

    async def test_deferred_agent_full_chain(self, registry: DeferredAgentRegistry) -> None:
        """Deferred agent: register -> activate -> adapter -> tool definition -> remove."""
        manifest = _make_manifest("chain-agent", "Full chain agent")
        tools_schemas = [
            _make_tool_schema(
                "process",
                "Process data",
                properties={
                    "input_data": {"type": "string"},
                    "format": {"type": "string", "default": "json"},
                },
                required=["input_data"],
            ),
            _make_tool_schema(
                "validate",
                "Validate output",
                properties={"schema_type": {"type": "string"}},
                required=["schema_type"],
            ),
        ]
        mock_handle = _make_mock_handle("chain-agent")

        # Step 1: Register deferred
        registry.register_agent(manifest, deferred=True, start_command=["cmd"])

        # Verify: not in tools yet
        assert registry.get_tools_for_llm() == []

        # Step 2: Activate
        with patch.object(registry._pm, "start_agent", new=AsyncMock(return_value=mock_handle)):
            with patch.object(
                registry, "_fetch_agent_tools", new=AsyncMock(return_value=tools_schemas)
            ):
                schemas = await registry.activate_agent("chain-agent")

        # Step 3: Verify discovered tools
        assert len(schemas) == 2
        adapters = registry.get_tool_adapters("chain-agent")
        assert len(adapters) == 2

        # Step 4: Tool definitions are MCP-compliant
        for adapter in adapters:
            tool_def = adapter.get_tool_definition()
            assert tool_def["name"].startswith("mcp__chain_agent__")
            assert "inputSchema" in tool_def
            assert tool_def["inputSchema"]["type"] == "object"

        # Step 5: Tools available in LLM list
        llm_tools = registry.get_tools_for_llm()
        assert len(llm_tools) == 2

        # Step 6: Search finds the agent
        results = registry.search_agents("chain")
        assert len(results) == 1
        assert results[0].name == "chain-agent"

        # Step 7: Agent info shows activated
        info = registry.get_agent_info("chain-agent")
        assert info is not None
        assert info.is_activated is True

        # Step 8: Remove cleans everything
        registry.remove_agent_tools("chain-agent")
        assert registry.get_agent_info("chain-agent") is None
        assert registry.get_tools_for_llm() == []

    async def test_multiple_agents_full_chain(self, registry: DeferredAgentRegistry) -> None:
        """Multiple agents registered and activated in sequence."""
        # Core agent
        core_manifest = _make_manifest("core-svc", "Core service")
        registry.register_agent(core_manifest, deferred=False)
        core_tool = _make_tool_schema("health-check", "Check health")
        core_info = registry.get_agent_info("core-svc")
        assert core_info is not None
        core_info.tool_schemas = [core_tool]

        # Deferred agent 1
        d1_manifest = _make_manifest("deferred-svc-1", "Deferred service 1")
        registry.register_agent(d1_manifest, deferred=True, start_command=["cmd1"])
        d1_tools = [_make_tool_schema("analyze", "Analyze data")]
        mock_h1 = _make_mock_handle("deferred-svc-1")

        # Deferred agent 2
        d2_manifest = _make_manifest("deferred-svc-2", "Deferred service 2")
        registry.register_agent(d2_manifest, deferred=True, start_command=["cmd2"])
        d2_tools = [
            _make_tool_schema("render", "Render output"),
            _make_tool_schema("export", "Export data"),
        ]
        mock_h2 = _make_mock_handle("deferred-svc-2")

        # Activate deferred agents
        with patch.object(registry._pm, "start_agent", new=AsyncMock(return_value=mock_h1)):
            with patch.object(registry, "_fetch_agent_tools", new=AsyncMock(return_value=d1_tools)):
                await registry.activate_agent("deferred-svc-1")

        with patch.object(registry._pm, "start_agent", new=AsyncMock(return_value=mock_h2)):
            with patch.object(registry, "_fetch_agent_tools", new=AsyncMock(return_value=d2_tools)):
                await registry.activate_agent("deferred-svc-2")

        # All tools available
        llm_tools = registry.get_tools_for_llm()
        assert len(llm_tools) == 4  # 1 core + 1 deferred-1 + 2 deferred-2

        # All agents in list
        all_agents = registry.list_all_agents()
        assert len(all_agents) == 3

        # Manifest text includes all tiers
        text = registry.build_manifest()
        assert "core-svc" in text
        assert "deferred-svc-1" in text
        assert "deferred-svc-2" in text
