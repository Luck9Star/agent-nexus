"""MCP adapter — expose config-linter as an MCP Server using FastMCP.

Provides one MCP tool:
- lint_config: Parse and validate TOML/YAML/JSON config files.
"""

from __future__ import annotations

from agent_config_linter.tools.lint_config import lint_config as _lint


def create_mcp_server() -> object:
    """Create and return a FastMCP server for config-linter.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-config-linter[full]

    Returns:
        A FastMCP server instance with lint_config tool.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. Install with: pip install agent-config-linter[full]"
        ) from None

    mcp = FastMCP("config-linter")

    @mcp.tool()
    def lint_config(content: str, fmt: str = "auto") -> dict:
        """Parse and validate TOML, YAML, or JSON config files.

        Auto-detects format and checks for missing keys, type mismatches,
        deprecated options, and structural problems.
        """
        result = _lint(content, fmt)
        return result.model_dump()

    return mcp
