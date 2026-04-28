"""MCP Gateway — aggregate multiple agent MCP servers into one FastMCP server.

Public API:
- MCPGateway: the main gateway class
- DeferredAgentRegistry: agent-level deferred loading
- McpToolAdapter: dynamic MCP tool bridge
- AgentInfo: lightweight agent metadata
"""

from agent_nexus.platform.gateway.deferred_registry import (
    AgentInfo,
    DeferredAgentRegistry,
)
from agent_nexus.platform.gateway.gateway import MCPGateway
from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter

__all__ = [
    "AgentInfo",
    "DeferredAgentRegistry",
    "MCPGateway",
    "McpToolAdapter",
]
