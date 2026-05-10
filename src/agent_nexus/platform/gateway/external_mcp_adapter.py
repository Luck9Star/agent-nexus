"""ExternalMcpAdapter -- connect to external MCP Servers and proxy their tools.

Supports three transport protocols:
- STDIO: spawn a subprocess and communicate over stdin/stdout
- SSE: connect via Server-Sent Events (HTTP)
- HTTP_STREAM: connect via Streamable HTTP transport

The adapter discovers tools via ``tools/list`` and caches their schemas.
Tool invocation is delegated to the external server via ``tools/call``.

Connection failures are logged as warnings and do not propagate -- this
allows the gateway to degrade gracefully when an external server is
unavailable.
"""

from __future__ import annotations

import logging
import os
import re
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import TextContent

from agent_nexus.models.external_mcp import (
    ExternalServerConfig,
    TransportType,
)

# Pattern for ${ENV_VAR} references in auth credentials
_ENV_VAR_RE = re.compile(r"\$\{([^}]+)\}")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ExternalMcpAdapter
# ---------------------------------------------------------------------------


class ExternalMcpAdapter:
    """Connect to an external MCP Server, discover and cache tool schemas.

    Usage::

        config = ExternalServerConfig(name="fs", transport="stdio", command="npx", args=[...])
        adapter = ExternalMcpAdapter(config)
        await adapter.connect()
        print(adapter.tool_schemas)
        result = await adapter.call_tool("read_file", {"path": "/tmp/test.txt"})
        await adapter.disconnect()
    """

    def __init__(self, config: ExternalServerConfig) -> None:
        self._config = config
        self._session: ClientSession | None = None
        self._tool_schemas: list[dict[str, Any]] = []
        self._exit_stack: AsyncExitStack | None = None

    # -- Lifecycle -----------------------------------------------------------

    async def connect(self) -> None:
        """Establish connection and discover tools.

        On failure, logs a warning and returns without raising.  The adapter
        will remain in a disconnected state (``is_alive`` returns False).
        """
        try:
            self._exit_stack = AsyncExitStack()
            await self._exit_stack.__aenter__()

            read_stream, write_stream = await self._open_transport()

            self._session = ClientSession(read_stream, write_stream)
            await self._exit_stack.enter_async_context(self._session)

            await self._session.initialize()
            await self._discover_tools()

            logger.info(
                "Connected to external MCP server '%s' (%s), discovered %d tool(s)",
                self._config.name,
                self._config.transport,
                len(self._tool_schemas),
            )
        except Exception as exc:
            logger.warning(
                "Failed to connect to external MCP server '%s' (%s): %s [%s]",
                self._config.name,
                self._config.transport,
                exc,
                type(exc).__name__,
            )
            # Clean up partial state
            await self._safe_disconnect()
        except BaseException:
            # CancelledError (or other BaseException) — clean up to prevent
            # AsyncExitStack / stream resource leak, then re-raise.
            await self._safe_disconnect()
            raise

    async def disconnect(self) -> None:
        """Disconnect from the external MCP Server."""
        await self._safe_disconnect()

    async def _safe_disconnect(self) -> None:
        """Disconnect, swallowing any errors."""
        if self._exit_stack is not None:
            try:
                await self._exit_stack.__aexit__(None, None, None)
            except Exception:
                logger.debug(
                    "Error during disconnect of '%s'",
                    self._config.name,
                    exc_info=True,
                )
            finally:
                self._exit_stack = None
        self._session = None
        self._tool_schemas = []

    # -- Tool invocation -----------------------------------------------------

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on the external MCP Server.

        Args:
            tool_name: The tool name (as reported by the external server).
            arguments: Tool input arguments.

        Returns:
            Concatenated text content from the tool result.

        Raises:
            RuntimeError: If the adapter is not connected or the tool
                returns an error response.
        """
        if self._session is None or not self.is_alive:
            raise RuntimeError(f"External MCP server '{self._config.name}' is not connected")

        # Enforce allowed_tools restriction
        allowed = self._config.allowed_tools
        if allowed and tool_name not in allowed:
            raise PermissionError(
                f"Tool '{tool_name}' is not in the allowed list for server "
                f"'{self._config.name}'. Allowed: {allowed}"
            )

        result = await self._session.call_tool(tool_name, arguments)

        # Extract text from content blocks; non-text blocks are represented
        # as placeholders so callers know content was present but omitted.
        text_parts: list[str] = []
        for block in result.content:
            if isinstance(block, TextContent):
                text_parts.append(block.text)
            else:
                text_parts.append(f"[{block.type} content omitted]")

        output = "\n".join(text_parts) if text_parts else ""

        if result.isError:
            logger.warning(
                "External MCP server '%s' tool '%s' returned error: %s",
                self._config.name,
                tool_name,
                output,
            )
            raise RuntimeError(
                f"External MCP server '{self._config.name}' "
                f"tool '{tool_name}' returned error: {output}"
            )

        return output

    # -- Properties ----------------------------------------------------------

    @property
    def tool_schemas(self) -> list[dict[str, Any]]:
        """Return discovered tool schema list."""
        return list(self._tool_schemas)

    @property
    def is_alive(self) -> bool:
        """Whether the connection is alive."""
        return self._session is not None and self._exit_stack is not None

    @property
    def name(self) -> str:
        """Server name from config."""
        return self._config.name

    # -- Internal helpers ----------------------------------------------------

    @staticmethod
    def _resolve_env_vars(value: str) -> str:
        """Resolve ``${ENV_VAR}`` references in a string from ``os.environ``."""
        return _ENV_VAR_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)

    def _build_auth_headers(self) -> dict[str, str]:
        """Build HTTP headers from auth configuration.

        Returns extra headers to merge with any user-supplied headers.
        Note: mTLS auth uses certificates at the transport level (see
        ``_open_sse_transport`` / ``_open_http_transport``), not headers.
        """
        auth = self._config.auth
        if auth is None:
            return {}
        if auth.method == "bearer":
            token = self._resolve_env_vars(auth.bearer_token)
            if token:
                return {"Authorization": f"Bearer {token}"}
        elif auth.method == "api_key":
            key = self._resolve_env_vars(auth.api_key)
            if key:
                return {"X-API-Key": key}
        elif auth.method == "mtls":
            logger.info(
                "mTLS auth configured for '%s' — certificates applied at transport level",
                self._config.name,
            )
        return {}

    async def _open_transport(
        self,
    ) -> tuple[Any, Any]:
        """Open the appropriate transport and return (read_stream, write_stream).

        The stream types are anyio MemoryObject streams -- we use Any to
        avoid exposing anyio types in our public API.
        """
        assert self._exit_stack is not None  # set in connect() before this is called

        handlers = {
            TransportType.STDIO: self._open_stdio_transport,
            TransportType.SSE: self._open_sse_transport,
            TransportType.HTTP_STREAM: self._open_http_transport,
        }
        handler = handlers.get(self._config.transport)
        if handler is None:
            raise ValueError(
                f"Unsupported transport '{self._config.transport}' for server '{self._config.name}'"
            )
        return await handler()

    async def _open_stdio_transport(self) -> tuple[Any, Any]:
        """Open STDIO transport with shell character validation."""
        assert self._exit_stack is not None  # guarded by connect()
        if not self._config.command:
            raise ValueError(f"STDIO transport requires 'command' for server '{self._config.name}'")
        _shell_chars = ("|", ">", "<", "&", ";", "`", "$")
        if any(c in self._config.command for c in _shell_chars):
            raise ValueError(
                f"STDIO command contains disallowed shell characters for server "
                f"'{self._config.name}': {self._config.command!r}"
            )
        for i, arg in enumerate(self._config.args or ()):
            if any(c in arg for c in _shell_chars):
                raise ValueError(
                    f"STDIO args[{i}] contains disallowed shell characters for "
                    f"server '{self._config.name}': {arg!r}"
                )
        logger.info(
            "STDIO transport for '%s': command=%s args=%s",
            self._config.name,
            self._config.command,
            self._config.args,
        )
        server_params = StdioServerParameters(
            command=self._config.command,
            args=self._config.args,
        )
        return await self._exit_stack.enter_async_context(stdio_client(server_params))

    async def _open_sse_transport(self) -> tuple[Any, Any]:
        """Open SSE transport."""
        assert self._exit_stack is not None  # guarded by connect()
        if not self._config.url:
            raise ValueError(f"SSE transport requires 'url' for server '{self._config.name}'")
        headers = {**self._build_auth_headers(), **(self._config.headers or {})}

        import httpx

        def _httpx_factory(
            headers: dict[str, str] | None = None,
            timeout: Any = None,
            auth: Any = None,
        ) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                headers=headers, timeout=timeout, auth=auth,
                verify=self._config.tls_verify,
            )

        return await self._exit_stack.enter_async_context(
            sse_client(
                url=self._config.url,
                headers=headers or None,
                httpx_client_factory=_httpx_factory,
            )
        )

    async def _open_http_transport(self) -> tuple[Any, Any]:
        """Open HTTP Stream transport."""
        assert self._exit_stack is not None  # guarded by connect()
        if not self._config.url:
            raise ValueError(
                f"HTTP_STREAM transport requires 'url' for server '{self._config.name}'"
            )
        headers = {**self._build_auth_headers(), **(self._config.headers or {})}

        import httpx

        def _httpx_factory(
            headers: dict[str, str] | None = None,
            timeout: Any = None,
            auth: Any = None,
        ) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                headers=headers, timeout=timeout, auth=auth,
                verify=self._config.tls_verify,
            )

        streams = await self._exit_stack.enter_async_context(
            streamablehttp_client(
                url=self._config.url,
                headers=headers or None,
                httpx_client_factory=_httpx_factory,
            )
        )
        # streamablehttp_client yields (read, write, get_session_id)
        read_stream, write_stream, _ = streams
        return read_stream, write_stream

    async def _discover_tools(self) -> None:
        """Discover tools via ``tools/list`` and cache schemas."""
        if self._session is None:
            return

        result = await self._session.list_tools()
        self._tool_schemas = []
        for tool in result.tools:
            schema: dict[str, Any] = {
                "name": tool.name,
                "description": tool.description or "",
            }
            # Normalize inputSchema: must be a dict with type=object
            raw_schema = tool.inputSchema
            if not isinstance(raw_schema, dict):
                raw_schema = dict(raw_schema) if hasattr(raw_schema, "__iter__") else {}
            if raw_schema.get("type") != "object" and "properties" not in raw_schema:
                raw_schema = {"type": "object", "properties": {}}
            schema["inputSchema"] = raw_schema
            self._tool_schemas.append(schema)
