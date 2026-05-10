"""MCP adapter — expose error-analyzer as an MCP Server using FastMCP.

Provides one MCP tool:
- analyze_error: Parse error messages and suggest fixes.
"""

from __future__ import annotations

from agent_error_analyzer.tools.analyze_error import analyze_error as _analyze


def create_mcp_server() -> object:
    """Create and return a FastMCP server for error-analyzer.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-error-analyzer[full]

    Returns:
        A FastMCP server instance with analyze_error tool.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. Install with: pip install agent-error-analyzer[full]"
        ) from None

    mcp = FastMCP("error-analyzer")

    @mcp.tool()
    def analyze_error(error_text: str, language: str = "auto") -> dict:
        """Parse error messages/stack traces, categorize and suggest fixes.

        Extracts error type, location, and context. Matches against known
        patterns to provide actionable fix suggestions.
        """
        result = _analyze(error_text, language)
        return result.model_dump()

    return mcp
