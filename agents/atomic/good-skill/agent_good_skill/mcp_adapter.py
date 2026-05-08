"""MCP adapter — expose good-skill as an MCP Server.

Requires the ``fastmcp`` package.
"""

from __future__ import annotations


def create_mcp_server() -> object:
    """Create and return a FastMCP server for good-skill."""
    from fastmcp import FastMCP

    mcp = FastMCP("good-skill")

    @mcp.tool()
    async def run(task: str, context: dict | None = None) -> str:
        """Execute the good-skill agent task."""
        from agent_good_skill.agent import good_skill_run
        return await good_skill_run(task, context)

    return mcp
