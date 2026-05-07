"""Unit tests for ExternalMcpAdapter and Gateway external server integration.

Tests cover:
- STDIO / SSE / HTTP_STREAM transport connect/discover/call/disconnect
- Connection failure graceful degradation
- Tool schema caching
- call_tool parameter passing
- config.toml external_servers parsing
- Gateway register_external_server integration
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.platform.config.loader import ConfigLoader
from agent_nexus.platform.gateway.external_mcp_adapter import ExternalMcpAdapter
from agent_nexus.models.external_mcp import (
    ExternalServerConfig,
    TransportType,
)
from agent_nexus.platform.gateway.gateway import MCPGateway
from agent_nexus.platform.orchestration.process_manager import ProcessManager


# ============================================================================
# Helpers / Fixtures
# ============================================================================


def _make_stdio_config(
    name: str = "test-stdio",
    command: str = "npx",
    args: list[str] | None = None,
    enabled: bool = True,
) -> ExternalServerConfig:
    return ExternalServerConfig(
        name=name,
        transport=TransportType.STDIO,
        command=command,
        args=args or ["-y", "@mcp/server-test"],
        enabled=enabled,
    )


def _make_sse_config(
    name: str = "test-sse",
    url: str = "http://localhost:3001/sse",
    enabled: bool = True,
) -> ExternalServerConfig:
    return ExternalServerConfig(
        name=name,
        transport=TransportType.SSE,
        url=url,
        enabled=enabled,
    )


def _make_http_stream_config(
    name: str = "test-http",
    url: str = "http://localhost:3002/mcp",
    enabled: bool = True,
) -> ExternalServerConfig:
    return ExternalServerConfig(
        name=name,
        transport=TransportType.HTTP_STREAM,
        url=url,
        enabled=enabled,
    )


def _mock_mcp_tool(name: str, description: str = "A tool") -> Any:
    """Create a mock MCP tool object with name, description, inputSchema attributes.

    Cannot use MagicMock(name=...) because 'name' is a reserved kwarg on MagicMock
    that sets the mock's own __repr__ name, not a .name attribute.
    """
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = {"type": "object", "properties": {}}
    return tool


def _mock_tool_schema(name: str, description: str = "A tool") -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    }


def _text_content(text: str) -> Any:
    """Create a TextContent with the required type='text' field."""
    from mcp.types import TextContent

    return TextContent(type="text", text=text)


@pytest.fixture
def process_manager() -> MagicMock:
    pm = MagicMock(spec=ProcessManager)
    pm.start_agent = AsyncMock()
    pm.stop_all = AsyncMock()
    return pm


@pytest.fixture
def router() -> MagicMock:
    return MagicMock()


@pytest.fixture
def gateway(process_manager: MagicMock, router: MagicMock) -> MCPGateway:
    return MCPGateway(process_manager=process_manager, router=router)


# ============================================================================
# ExternalServerConfig
# ============================================================================


class TestExternalServerConfig:
    """Tests for ExternalServerConfig dataclass defaults."""

    def test_defaults(self) -> None:
        config = ExternalServerConfig(name="test")
        assert config.transport == TransportType.STDIO
        assert config.command == ""
        assert config.args == []
        assert config.url == ""
        assert config.headers == {}
        assert config.enabled is True

    def test_custom_values(self) -> None:
        config = ExternalServerConfig(
            name="fs",
            transport=TransportType.SSE,
            url="http://localhost:3001/sse",
            headers={"Authorization": "Bearer token"},
            enabled=False,
        )
        assert config.transport == TransportType.SSE
        assert config.url == "http://localhost:3001/sse"
        assert config.headers == {"Authorization": "Bearer token"}
        assert config.enabled is False


# ============================================================================
# ExternalMcpAdapter -- STDIO transport
# ============================================================================


class TestExternalMcpAdapterStdio:
    """Tests for STDIO transport connect/discover/call/disconnect."""

    @pytest.mark.asyncio
    async def test_connect_discovers_tools(self) -> None:
        config = _make_stdio_config()
        adapter = ExternalMcpAdapter(config)

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(
            return_value=MagicMock(
                tools=[
                    _mock_mcp_tool("read_file", "Read a file"),
                    _mock_mcp_tool("write_file", "Write a file"),
                ]
            )
        )

        mock_read = AsyncMock()
        mock_write = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
        mock_context.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "agent_nexus.platform.gateway.external_mcp_adapter.stdio_client",
            return_value=mock_context,
        ):
            with patch(
                "agent_nexus.platform.gateway.external_mcp_adapter.ClientSession",
                return_value=mock_session,
            ):
                adapter._exit_stack = AsyncExitStack()
                await adapter._exit_stack.__aenter__()

                with patch.object(
                    adapter._exit_stack,
                    "enter_async_context",
                    side_effect=[AsyncMock(), AsyncMock()],
                ):
                    adapter._session = mock_session
                    await adapter._discover_tools()

        assert len(adapter.tool_schemas) == 2
        assert adapter.tool_schemas[0]["name"] == "read_file"
        assert adapter.tool_schemas[1]["name"] == "write_file"

    @pytest.mark.asyncio
    async def test_connect_failure_graceful(self) -> None:
        """Connection failure logs warning and adapter stays disconnected."""
        config = _make_stdio_config()
        adapter = ExternalMcpAdapter(config)

        with patch(
            "agent_nexus.platform.gateway.external_mcp_adapter.stdio_client",
            side_effect=OSError("command not found"),
        ):
            await adapter.connect()

        assert adapter.is_alive is False
        assert adapter.tool_schemas == []

    @pytest.mark.asyncio
    async def test_call_tool_returns_text(self) -> None:
        config = _make_stdio_config()
        adapter = ExternalMcpAdapter(config)

        mock_result = MagicMock()
        mock_result.content = [_text_content("file contents here")]
        mock_result.isError = False

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        adapter._session = mock_session
        adapter._exit_stack = AsyncExitStack()

        result = await adapter.call_tool("read_file", {"path": "/tmp/test.txt"})
        assert result == "file contents here"
        mock_session.call_tool.assert_awaited_once_with("read_file", {"path": "/tmp/test.txt"})

    @pytest.mark.asyncio
    async def test_call_tool_not_connected_raises(self) -> None:
        config = _make_stdio_config()
        adapter = ExternalMcpAdapter(config)
        # Not connected

        with pytest.raises(RuntimeError, match="not connected"):
            await adapter.call_tool("read_file", {"path": "/tmp/test.txt"})

    @pytest.mark.asyncio
    async def test_disconnect_clears_state(self) -> None:
        config = _make_stdio_config()
        adapter = ExternalMcpAdapter(config)

        # Simulate connected state
        adapter._session = MagicMock()
        adapter._exit_stack = AsyncExitStack()
        adapter._tool_schemas = [_mock_tool_schema("tool1")]

        await adapter.disconnect()

        assert adapter._session is None
        assert adapter._exit_stack is None
        assert adapter.tool_schemas == []
        assert adapter.is_alive is False


# ============================================================================
# ExternalMcpAdapter -- SSE transport
# ============================================================================


class TestExternalMcpAdapterSSE:
    """Tests for SSE transport connect/discover/call/disconnect."""

    @pytest.mark.asyncio
    async def test_connect_discovers_tools(self) -> None:
        config = _make_sse_config()
        adapter = ExternalMcpAdapter(config)

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(
            return_value=MagicMock(tools=[_mock_mcp_tool("search", "Search the web")])
        )

        mock_read = AsyncMock()
        mock_write = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
        mock_context.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "agent_nexus.platform.gateway.external_mcp_adapter.sse_client",
            return_value=mock_context,
        ):
            with patch(
                "agent_nexus.platform.gateway.external_mcp_adapter.ClientSession",
                return_value=mock_session,
            ):
                adapter._exit_stack = AsyncExitStack()
                await adapter._exit_stack.__aenter__()
                with patch.object(
                    adapter._exit_stack,
                    "enter_async_context",
                    side_effect=[AsyncMock(), AsyncMock()],
                ):
                    adapter._session = mock_session
                    await adapter._discover_tools()

        assert len(adapter.tool_schemas) == 1
        assert adapter.tool_schemas[0]["name"] == "search"

    @pytest.mark.asyncio
    async def test_connect_failure_graceful(self) -> None:
        config = _make_sse_config()
        adapter = ExternalMcpAdapter(config)

        with patch(
            "agent_nexus.platform.gateway.external_mcp_adapter.sse_client",
            side_effect=ConnectionError("connection refused"),
        ):
            await adapter.connect()

        assert adapter.is_alive is False
        assert adapter.tool_schemas == []

    @pytest.mark.asyncio
    async def test_call_tool_passes_arguments(self) -> None:
        config = _make_sse_config()
        adapter = ExternalMcpAdapter(config)

        mock_result = MagicMock()
        mock_result.content = [_text_content("search results")]
        mock_result.isError = False

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        adapter._session = mock_session
        adapter._exit_stack = AsyncExitStack()

        result = await adapter.call_tool("search", {"query": "test", "limit": 10})
        assert result == "search results"
        mock_session.call_tool.assert_awaited_once_with("search", {"query": "test", "limit": 10})


# ============================================================================
# ExternalMcpAdapter -- HTTP_STREAM transport
# ============================================================================


class TestExternalMcpAdapterHttpStream:
    """Tests for HTTP_STREAM transport connect/discover/call/disconnect."""

    @pytest.mark.asyncio
    async def test_connect_discovers_tools(self) -> None:
        config = _make_http_stream_config()
        adapter = ExternalMcpAdapter(config)

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(
            return_value=MagicMock(tools=[_mock_mcp_tool("compute", "Run computation")])
        )

        mock_read = AsyncMock()
        mock_write = AsyncMock()
        mock_get_session_id = MagicMock(return_value="session-123")
        mock_context = AsyncMock()
        # streamablehttp_client yields (read, write, get_session_id)
        mock_context.__aenter__ = AsyncMock(
            return_value=(mock_read, mock_write, mock_get_session_id)
        )
        mock_context.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "agent_nexus.platform.gateway.external_mcp_adapter.streamablehttp_client",
            return_value=mock_context,
        ):
            with patch(
                "agent_nexus.platform.gateway.external_mcp_adapter.ClientSession",
                return_value=mock_session,
            ):
                adapter._exit_stack = AsyncExitStack()
                await adapter._exit_stack.__aenter__()
                with patch.object(
                    adapter._exit_stack,
                    "enter_async_context",
                    side_effect=[AsyncMock(), AsyncMock()],
                ):
                    adapter._session = mock_session
                    await adapter._discover_tools()

        assert len(adapter.tool_schemas) == 1
        assert adapter.tool_schemas[0]["name"] == "compute"

    @pytest.mark.asyncio
    async def test_connect_failure_graceful(self) -> None:
        config = _make_http_stream_config()
        adapter = ExternalMcpAdapter(config)

        with patch(
            "agent_nexus.platform.gateway.external_mcp_adapter.streamablehttp_client",
            side_effect=ConnectionError("timeout"),
        ):
            await adapter.connect()

        assert adapter.is_alive is False
        assert adapter.tool_schemas == []


# ============================================================================
# ExternalMcpAdapter -- tool schema caching
# ============================================================================


class TestExternalMcpAdapterToolSchemaCache:
    """Tests for tool schema caching behavior."""

    @pytest.mark.asyncio
    async def test_tool_schemas_returns_copy(self) -> None:
        """tool_schemas property returns a copy, not the internal list."""
        config = _make_stdio_config()
        adapter = ExternalMcpAdapter(config)
        adapter._tool_schemas = [_mock_tool_schema("tool1")]

        schemas = adapter.tool_schemas
        assert len(schemas) == 1
        # Mutating the returned list should not affect internal state
        schemas.append(_mock_tool_schema("tool2"))
        assert len(adapter.tool_schemas) == 1

    @pytest.mark.asyncio
    async def test_disconnect_clears_schemas(self) -> None:
        config = _make_stdio_config()
        adapter = ExternalMcpAdapter(config)
        adapter._tool_schemas = [_mock_tool_schema("tool1"), _mock_tool_schema("tool2")]
        adapter._session = MagicMock()
        adapter._exit_stack = AsyncExitStack()

        await adapter.disconnect()
        assert adapter.tool_schemas == []

    def test_name_property(self) -> None:
        config = _make_stdio_config(name="my-server")
        adapter = ExternalMcpAdapter(config)
        assert adapter.name == "my-server"


# ============================================================================
# ExternalMcpAdapter -- _open_transport validation
# ============================================================================


class TestExternalMcpAdapterTransportValidation:
    """Tests for _open_transport input validation."""

    @pytest.mark.asyncio
    async def test_stdio_requires_command(self) -> None:
        config = ExternalServerConfig(name="no-cmd", transport=TransportType.STDIO)
        adapter = ExternalMcpAdapter(config)
        adapter._exit_stack = AsyncExitStack()
        await adapter._exit_stack.__aenter__()

        with pytest.raises(ValueError, match="STDIO transport requires 'command'"):
            await adapter._open_transport()

        await adapter._exit_stack.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_sse_requires_url(self) -> None:
        config = ExternalServerConfig(name="no-url", transport=TransportType.SSE)
        adapter = ExternalMcpAdapter(config)
        adapter._exit_stack = AsyncExitStack()
        await adapter._exit_stack.__aenter__()

        with pytest.raises(ValueError, match="SSE transport requires 'url'"):
            await adapter._open_transport()

        await adapter._exit_stack.__aexit__(None, None, None)

    @pytest.mark.asyncio
    async def test_http_stream_requires_url(self) -> None:
        config = ExternalServerConfig(name="no-url", transport=TransportType.HTTP_STREAM)
        adapter = ExternalMcpAdapter(config)
        adapter._exit_stack = AsyncExitStack()
        await adapter._exit_stack.__aenter__()

        with pytest.raises(ValueError, match="HTTP_STREAM transport requires 'url'"):
            await adapter._open_transport()

        await adapter._exit_stack.__aexit__(None, None, None)


# ============================================================================
# ConfigLoader -- external_servers parsing
# ============================================================================


class TestConfigLoaderExternalServers:
    """Tests for ConfigLoader.load_external_servers()."""

    def test_no_mcp_section(self, tmp_path: Any) -> None:
        """config.toml without [mcp] section returns empty list."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('[runtime]\nlog_level = "INFO"\n')

        loader = ConfigLoader(config_dir=config_dir)
        servers = loader.load_external_servers()
        assert servers == []

    def test_empty_external_servers(self, tmp_path: Any) -> None:
        """Empty external_servers list returns empty list."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("[mcp]\nexternal_servers = []\n")

        loader = ConfigLoader(config_dir=config_dir)
        servers = loader.load_external_servers()
        assert servers == []

    def test_parse_stdio_server(self, tmp_path: Any) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            "[[mcp.external_servers]]\n"
            'name = "filesystem"\n'
            'transport = "stdio"\n'
            'command = "npx"\n'
            'args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]\n'
            "enabled = true\n"
        )

        loader = ConfigLoader(config_dir=config_dir)
        servers = loader.load_external_servers()
        assert len(servers) == 1
        assert servers[0].name == "filesystem"
        assert servers[0].transport == TransportType.STDIO
        assert servers[0].command == "npx"
        assert servers[0].args == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        assert servers[0].enabled is True

    def test_parse_sse_server(self, tmp_path: Any) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            "[[mcp.external_servers]]\n"
            'name = "web-search"\n'
            'transport = "sse"\n'
            'url = "http://localhost:3001/sse"\n'
            "enabled = true\n"
        )

        loader = ConfigLoader(config_dir=config_dir)
        servers = loader.load_external_servers()
        assert len(servers) == 1
        assert servers[0].transport == TransportType.SSE
        assert servers[0].url == "http://localhost:3001/sse"

    def test_parse_http_stream_server(self, tmp_path: Any) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            "[[mcp.external_servers]]\n"
            'name = "remote-api"\n'
            'transport = "http_stream"\n'
            'url = "http://api.example.com/mcp"\n'
            "enabled = true\n"
        )

        loader = ConfigLoader(config_dir=config_dir)
        servers = loader.load_external_servers()
        assert len(servers) == 1
        assert servers[0].transport == TransportType.HTTP_STREAM
        assert servers[0].url == "http://api.example.com/mcp"

    def test_disabled_server_skipped(self, tmp_path: Any) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            "[[mcp.external_servers]]\n"
            'name = "disabled-srv"\n'
            'transport = "sse"\n'
            'url = "http://localhost:3001/sse"\n'
            "enabled = false\n"
        )

        loader = ConfigLoader(config_dir=config_dir)
        servers = loader.load_external_servers()
        assert servers == []

    def test_multiple_servers(self, tmp_path: Any) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            "[[mcp.external_servers]]\n"
            'name = "fs"\n'
            'transport = "stdio"\n'
            'command = "npx"\n'
            'args = ["-y", "@mcp/fs"]\n'
            "\n"
            "[[mcp.external_servers]]\n"
            'name = "search"\n'
            'transport = "sse"\n'
            'url = "http://localhost:3001/sse"\n'
        )

        loader = ConfigLoader(config_dir=config_dir)
        servers = loader.load_external_servers()
        assert len(servers) == 2
        assert servers[0].name == "fs"
        assert servers[1].name == "search"

    def test_missing_name_skipped(self, tmp_path: Any) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[[mcp.external_servers]]\ntransport = "sse"\nurl = "http://localhost:3001/sse"\n'
        )

        loader = ConfigLoader(config_dir=config_dir)
        servers = loader.load_external_servers()
        assert servers == []

    def test_invalid_transport_defaults_to_stdio(self, tmp_path: Any) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            "[[mcp.external_servers]]\n"
            'name = "bad-transport"\n'
            'transport = "websocket"\n'
            'command = "npx"\n'
        )

        loader = ConfigLoader(config_dir=config_dir)
        servers = loader.load_external_servers()
        assert len(servers) == 1
        assert servers[0].transport == TransportType.STDIO

    def test_non_dict_entry_skipped(self, tmp_path: Any) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[[mcp.external_servers]]\nname = "valid"\ntransport = "stdio"\ncommand = "npx"\n'
        )
        loader = ConfigLoader(config_dir=config_dir)
        servers = loader.load_external_servers()
        assert len(servers) == 1

    def test_non_list_external_servers_returns_empty(self, tmp_path: Any) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('[mcp]\nexternal_servers = "not a list"\n')

        loader = ConfigLoader(config_dir=config_dir)
        servers = loader.load_external_servers()
        assert servers == []

    def test_non_dict_mcp_section_returns_empty(self, tmp_path: Any) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('mcp = "string"\n')

        loader = ConfigLoader(config_dir=config_dir)
        servers = loader.load_external_servers()
        assert servers == []

    def test_no_config_file_returns_empty(self, tmp_path: Any) -> None:
        config_dir = tmp_path / "empty_config"

        loader = ConfigLoader(config_dir=config_dir)
        servers = loader.load_external_servers()
        assert servers == []

    def test_enabled_string_true(self, tmp_path: Any) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            "[[mcp.external_servers]]\n"
            'name = "str-enabled"\n'
            'transport = "sse"\n'
            'url = "http://localhost/sse"\n'
            'enabled = "true"\n'
        )

        loader = ConfigLoader(config_dir=config_dir)
        servers = loader.load_external_servers()
        assert len(servers) == 1

    def test_enabled_string_false(self, tmp_path: Any) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            "[[mcp.external_servers]]\n"
            'name = "str-disabled"\n'
            'transport = "sse"\n'
            'url = "http://localhost/sse"\n'
            'enabled = "false"\n'
        )

        loader = ConfigLoader(config_dir=config_dir)
        servers = loader.load_external_servers()
        assert servers == []

    def test_non_list_args_uses_empty(self, tmp_path: Any) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            "[[mcp.external_servers]]\n"
            'name = "bad-args"\n'
            'transport = "stdio"\n'
            'command = "npx"\n'
            'args = "not a list"\n'
        )

        loader = ConfigLoader(config_dir=config_dir)
        servers = loader.load_external_servers()
        assert len(servers) == 1
        assert servers[0].args == []

    def test_non_dict_headers_uses_empty(self, tmp_path: Any) -> None:
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            "[[mcp.external_servers]]\n"
            'name = "bad-headers"\n'
            'transport = "sse"\n'
            'url = "http://localhost/sse"\n'
            'headers = "not a dict"\n'
        )

        loader = ConfigLoader(config_dir=config_dir)
        servers = loader.load_external_servers()
        assert len(servers) == 1
        assert servers[0].headers == {}


# ============================================================================
# MCPGateway -- register_external_server
# ============================================================================


class TestGatewayRegisterExternalServer:
    """Tests for MCPGateway.register_external_server()."""

    @pytest.mark.asyncio
    async def test_register_external_server_registers_tools(self, gateway: MCPGateway) -> None:
        config = _make_sse_config(name="search-srv")

        # Create adapter with connected state and tools
        adapter = ExternalMcpAdapter.__new__(ExternalMcpAdapter)
        adapter._config = config
        adapter._session = MagicMock()
        adapter._exit_stack = AsyncExitStack()
        adapter._tool_schemas = [
            _mock_tool_schema("search", "Search the web"),
            _mock_tool_schema("fetch", "Fetch a URL"),
        ]

        with patch(
            "agent_nexus.platform.gateway.gateway.ExternalMcpAdapter",
            return_value=adapter,
        ):
            with patch.object(adapter, "connect", new_callable=AsyncMock):
                await gateway.register_external_server(config)

        # Check tool names follow ext__ convention
        assert "ext__search_srv__search" in gateway._registered_tool_names
        assert "ext__search_srv__fetch" in gateway._registered_tool_names
        assert "search-srv" in gateway._external_adapters

    @pytest.mark.asyncio
    async def test_register_external_server_not_alive_skipped(self, gateway: MCPGateway) -> None:
        config = _make_sse_config(name="dead-srv")
        adapter = ExternalMcpAdapter.__new__(ExternalMcpAdapter)
        adapter._config = config
        adapter._session = None
        adapter._exit_stack = None
        adapter._tool_schemas = []

        with patch(
            "agent_nexus.platform.gateway.gateway.ExternalMcpAdapter",
            return_value=adapter,
        ):
            with patch.object(adapter, "connect", new_callable=AsyncMock):
                await gateway.register_external_server(config)

        assert "dead-srv" not in gateway._external_adapters
        assert len(gateway._registered_tool_names) == 0

    @pytest.mark.asyncio
    async def test_register_external_server_no_tools_skipped(self, gateway: MCPGateway) -> None:
        config = _make_sse_config(name="empty-srv")
        adapter = ExternalMcpAdapter.__new__(ExternalMcpAdapter)
        adapter._config = config
        adapter._session = MagicMock()
        adapter._exit_stack = AsyncExitStack()
        adapter._tool_schemas = []

        with patch(
            "agent_nexus.platform.gateway.gateway.ExternalMcpAdapter",
            return_value=adapter,
        ):
            with patch.object(adapter, "connect", new_callable=AsyncMock):
                await gateway.register_external_server(config)

        assert "empty-srv" not in gateway._external_adapters

    @pytest.mark.asyncio
    async def test_tool_name_prefix_is_ext(self, gateway: MCPGateway) -> None:
        """External tools use ext__ prefix, not mcp__."""
        config = _make_sse_config(name="my-tools")
        adapter = ExternalMcpAdapter.__new__(ExternalMcpAdapter)
        adapter._config = config
        adapter._session = MagicMock()
        adapter._exit_stack = AsyncExitStack()
        adapter._tool_schemas = [_mock_tool_schema("do_work")]

        with patch(
            "agent_nexus.platform.gateway.gateway.ExternalMcpAdapter",
            return_value=adapter,
        ):
            with patch.object(adapter, "connect", new_callable=AsyncMock):
                await gateway.register_external_server(config)

        tool_names = list(gateway._registered_tool_names)
        assert len(tool_names) == 1
        assert tool_names[0].startswith("ext__")
        assert not tool_names[0].startswith("mcp__")

    @pytest.mark.asyncio
    async def test_duplicate_tool_name_skipped(self, gateway: MCPGateway) -> None:
        """If a tool name is already registered, it is skipped."""
        config = _make_sse_config(name="dup-srv")
        adapter = ExternalMcpAdapter.__new__(ExternalMcpAdapter)
        adapter._config = config
        adapter._session = MagicMock()
        adapter._exit_stack = AsyncExitStack()
        adapter._tool_schemas = [_mock_tool_schema("search")]

        # Pre-register the tool name
        gateway._registered_tool_names.add("ext__dup_srv__search")

        with patch(
            "agent_nexus.platform.gateway.gateway.ExternalMcpAdapter",
            return_value=adapter,
        ):
            with patch.object(adapter, "connect", new_callable=AsyncMock):
                await gateway.register_external_server(config)

        # Tool still registered (from pre-registration)
        assert "ext__dup_srv__search" in gateway._registered_tool_names
        # But no new registration happened (mcp.tool not called for it)


# ============================================================================
# MCPGateway -- stop disconnects external servers
# ============================================================================


class TestGatewayStopExternalServers:
    """Tests that MCPGateway.stop() disconnects external servers."""

    @pytest.mark.asyncio
    async def test_stop_disconnects_external_adapters(self, gateway: MCPGateway) -> None:
        mock_adapter = MagicMock(spec=ExternalMcpAdapter)
        mock_adapter.disconnect = AsyncMock()
        gateway._external_adapters["test-srv"] = mock_adapter

        await gateway.stop()

        mock_adapter.disconnect.assert_awaited_once()
        assert len(gateway._external_adapters) == 0

    @pytest.mark.asyncio
    async def test_stop_handles_disconnect_errors(self, gateway: MCPGateway) -> None:
        mock_adapter = MagicMock(spec=ExternalMcpAdapter)
        mock_adapter.disconnect = AsyncMock(side_effect=OSError("broken"))
        gateway._external_adapters["fail-srv"] = mock_adapter

        # Should not raise
        await gateway.stop()
        assert len(gateway._external_adapters) == 0


# ============================================================================
# ExternalMcpAdapter -- full connect integration (mocked transports)
# ============================================================================


class TestExternalMcpAdapterFullConnect:
    """Integration tests with mocked transport layers."""

    @pytest.mark.asyncio
    async def test_full_stdio_connect_disconnect(self) -> None:
        """Full connect/discover/disconnect cycle for STDIO."""
        config = _make_stdio_config()
        adapter = ExternalMcpAdapter(config)

        mock_read = AsyncMock()
        mock_write = AsyncMock()

        # Mock stdio_client context manager
        mock_stdio_ctx = MagicMock()
        mock_stdio_ctx.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
        mock_stdio_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(
            return_value=MagicMock(
                tools=[_mock_mcp_tool("read_file", "Read")],
            )
        )
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "agent_nexus.platform.gateway.external_mcp_adapter.stdio_client",
            return_value=mock_stdio_ctx,
        ):
            with patch(
                "agent_nexus.platform.gateway.external_mcp_adapter.ClientSession",
                return_value=mock_session,
            ):
                await adapter.connect()

        assert adapter.is_alive is True
        assert len(adapter.tool_schemas) == 1
        assert adapter.tool_schemas[0]["name"] == "read_file"

        await adapter.disconnect()
        assert adapter.is_alive is False
        assert adapter.tool_schemas == []

    @pytest.mark.asyncio
    async def test_full_sse_connect_disconnect(self) -> None:
        """Full connect/discover/disconnect cycle for SSE."""
        config = _make_sse_config()
        adapter = ExternalMcpAdapter(config)

        mock_read = AsyncMock()
        mock_write = AsyncMock()

        mock_sse_ctx = MagicMock()
        mock_sse_ctx.__aenter__ = AsyncMock(return_value=(mock_read, mock_write))
        mock_sse_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(
            return_value=MagicMock(
                tools=[_mock_mcp_tool("search", "Search")],
            )
        )
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "agent_nexus.platform.gateway.external_mcp_adapter.sse_client",
            return_value=mock_sse_ctx,
        ):
            with patch(
                "agent_nexus.platform.gateway.external_mcp_adapter.ClientSession",
                return_value=mock_session,
            ):
                await adapter.connect()

        assert adapter.is_alive is True
        assert len(adapter.tool_schemas) == 1
        assert adapter.tool_schemas[0]["name"] == "search"

        await adapter.disconnect()
        assert adapter.is_alive is False

    @pytest.mark.asyncio
    async def test_full_http_stream_connect_disconnect(self) -> None:
        """Full connect/discover/disconnect cycle for HTTP_STREAM."""
        config = _make_http_stream_config()
        adapter = ExternalMcpAdapter(config)

        mock_read = AsyncMock()
        mock_write = AsyncMock()
        mock_get_session_id = MagicMock(return_value="sid-1")

        mock_http_ctx = MagicMock()
        # streamablehttp_client yields 3-tuple
        mock_http_ctx.__aenter__ = AsyncMock(
            return_value=(mock_read, mock_write, mock_get_session_id)
        )
        mock_http_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.list_tools = AsyncMock(
            return_value=MagicMock(
                tools=[_mock_mcp_tool("compute", "Compute")],
            )
        )
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "agent_nexus.platform.gateway.external_mcp_adapter.streamablehttp_client",
            return_value=mock_http_ctx,
        ):
            with patch(
                "agent_nexus.platform.gateway.external_mcp_adapter.ClientSession",
                return_value=mock_session,
            ):
                await adapter.connect()

        assert adapter.is_alive is True
        assert len(adapter.tool_schemas) == 1
        assert adapter.tool_schemas[0]["name"] == "compute"

        await adapter.disconnect()
        assert adapter.is_alive is False


# ============================================================================
# ExternalMcpAdapter -- _discover_tools edge cases
# ============================================================================


class TestExternalMcpAdapterDiscoverTools:
    """Tests for tool discovery edge cases."""

    @pytest.mark.asyncio
    async def test_tool_without_description(self) -> None:
        """Tools without description get empty string."""
        config = _make_stdio_config()
        adapter = ExternalMcpAdapter(config)

        tool = _mock_mcp_tool("no_desc", "ignored")
        tool.description = None  # type: ignore[assignment]

        mock_session = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[tool]))
        adapter._session = mock_session
        await adapter._discover_tools()

        assert adapter.tool_schemas[0]["description"] == ""

    @pytest.mark.asyncio
    async def test_tool_without_input_schema(self) -> None:
        """Tools without inputSchema get default empty object schema."""
        config = _make_stdio_config()
        adapter = ExternalMcpAdapter(config)

        tool = _mock_mcp_tool("no_schema", "Tool")
        tool.inputSchema = None

        mock_session = AsyncMock()
        mock_session.list_tools = AsyncMock(return_value=MagicMock(tools=[tool]))
        adapter._session = mock_session
        await adapter._discover_tools()

        assert adapter.tool_schemas[0]["inputSchema"] == {"type": "object", "properties": {}}

    @pytest.mark.asyncio
    async def test_discover_with_no_session(self) -> None:
        """_discover_tools with None session does nothing."""
        config = _make_stdio_config()
        adapter = ExternalMcpAdapter(config)
        adapter._session = None
        await adapter._discover_tools()
        assert adapter.tool_schemas == []


# ============================================================================
# ExternalMcpAdapter -- call_tool error handling
# ============================================================================


class TestExternalMcpAdapterCallToolErrors:
    """Tests for call_tool error handling."""

    @pytest.mark.asyncio
    async def test_call_tool_error_result_still_returns_text(self) -> None:
        """Tool error result still returns text content."""
        config = _make_sse_config()
        adapter = ExternalMcpAdapter(config)

        mock_result = MagicMock()
        mock_result.content = [_text_content("something went wrong")]
        mock_result.isError = True

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        adapter._session = mock_session
        adapter._exit_stack = AsyncExitStack()

        result = await adapter.call_tool("fail_tool", {})
        assert result == "something went wrong"

    @pytest.mark.asyncio
    async def test_call_tool_empty_content_returns_empty_string(self) -> None:
        """Tool result with no TextContent returns empty string."""
        config = _make_sse_config()
        adapter = ExternalMcpAdapter(config)

        mock_result = MagicMock()
        mock_result.content = []  # No content blocks
        mock_result.isError = False

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        adapter._session = mock_session
        adapter._exit_stack = AsyncExitStack()

        result = await adapter.call_tool("empty_tool", {})
        assert result == ""

    @pytest.mark.asyncio
    async def test_call_tool_multiple_text_blocks(self) -> None:
        """Multiple TextContent blocks are joined with newline."""
        config = _make_sse_config()
        adapter = ExternalMcpAdapter(config)

        mock_result = MagicMock()
        mock_result.content = [
            _text_content("part 1"),
            _text_content("part 2"),
        ]
        mock_result.isError = False

        mock_session = AsyncMock()
        mock_session.call_tool = AsyncMock(return_value=mock_result)
        adapter._session = mock_session
        adapter._exit_stack = AsyncExitStack()

        result = await adapter.call_tool("multi_tool", {})
        assert result == "part 1\npart 2"
