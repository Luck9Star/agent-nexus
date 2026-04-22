"""MCP adapter — expose good-skill as an MCP Server.

Requires the ``fastmcp`` package.
"""

from __future__ import annotations


def create_mcp_server() -> object:
    """Create and return a FastMCP server for good-skill."""
    try:
        from fastmcp import FastMCP
    except ImportError as exc:
        raise ImportError(
            "fastmcp is required for MCP server mode. "
            "Install with: pip install fastmcp"
        ) from exc

    mcp = FastMCP("good-skill")

    @mcp.tool()
    def run(task: str, context: dict | None = None) -> str:
        """Execute the good-skill agent task."""
        from agent_good_skill.agent import good_skill_run
        return good_skill_run(task, context)

    return mcp
