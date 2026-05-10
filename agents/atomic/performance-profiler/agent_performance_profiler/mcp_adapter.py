"""MCP adapter — expose performance-profiler as an MCP Server using FastMCP.

Provides one MCP tool:
- analyze_performance: Analyze source code for performance anti-patterns.
"""

from __future__ import annotations

from agent_performance_profiler.tools.analyze_performance import (
    analyze_performance as _analyze,
)


def create_mcp_server() -> object:
    """Create and return a FastMCP server for performance-profiler.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-performance-profiler[full]

    Returns:
        A FastMCP server instance with analyze_performance tool.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-performance-profiler[full]"
        ) from None

    mcp = FastMCP("performance-profiler")

    @mcp.tool()
    def analyze_performance(source_code: str) -> dict:
        """Analyze source code for common performance anti-patterns.

        Detects N+1 queries, inefficient loops, and memory-inefficient operations.
        """
        result = _analyze(source_code)
        return result.model_dump()

    return mcp
