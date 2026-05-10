"""Lightweight types for external MCP Server configuration.

Extracted from ``external_mcp_adapter.py`` to avoid circular imports:
``config/loader.py`` needs these types but must not trigger the full
gateway import chain (gateway → deferred_registry → tool_adapter →
orchestration.ipc → config → loader).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class TransportType(StrEnum):
    """Supported MCP client transport protocols."""

    STDIO = "stdio"
    SSE = "sse"
    HTTP_STREAM = "http_stream"


@dataclass
class ExternalServerAuth:
    """Auth configuration for external MCP server connections."""

    method: str = "none"  # "none", "api_key", "bearer", "mtls"
    api_key: str = ""  # or env var reference like ${ENV_VAR}
    bearer_token: str = ""
    client_cert_path: str = ""
    client_key_path: str = ""


@dataclass
class ExternalServerConfig:
    """Configuration for a single external MCP Server connection.

    Maps to a ``[[mcp.external_servers]]`` entry in config.toml.
    """

    name: str
    transport: TransportType = TransportType.STDIO
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    auth: ExternalServerAuth = field(default_factory=ExternalServerAuth)
    tls_verify: bool = True
    allowed_tools: list[str] | None = None
