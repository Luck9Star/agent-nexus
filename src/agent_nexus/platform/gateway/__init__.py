"""MCP Gateway -- aggregate multiple agent MCP servers into one FastMCP server.

Public API:
- MCPGateway: the main gateway class
- DeferredAgentRegistry: agent-level deferred loading
- McpToolAdapter: dynamic MCP tool bridge
- AgentInfo: lightweight agent metadata
- ExternalMcpAdapter: connect to external MCP servers
- ExternalServerConfig: external server configuration
"""

from agent_nexus.models.external_mcp import (
    ExternalServerConfig,
    TransportType,
)
from agent_nexus.platform.gateway.deferred_registry import (
    AgentInfo,
    DeferredAgentRegistry,
)
from agent_nexus.platform.gateway.external_mcp_adapter import ExternalMcpAdapter
from agent_nexus.platform.gateway.gateway import MCPGateway
from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter

__all__ = [
    "AgentInfo",
    "DeferredAgentRegistry",
    "ExternalMcpAdapter",
    "ExternalServerConfig",
    "MCPGateway",
    "McpToolAdapter",
    "TransportType",
]
