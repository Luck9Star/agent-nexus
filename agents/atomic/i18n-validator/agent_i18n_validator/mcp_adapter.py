"""MCP adapter — expose i18n-validator as an MCP Server using FastMCP.

Provides one MCP tool:
- validate_i18n: Check translation files for completeness and consistency.
"""

from __future__ import annotations

from agent_i18n_validator.tools.validate_i18n import validate_i18n as _validate


def create_mcp_server() -> object:
    """Create and return a FastMCP server for i18n-validator.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-i18n-validator[full]

    Returns:
        A FastMCP server instance with validate_i18n tool.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. Install with: pip install agent-i18n-validator[full]"
        ) from None

    mcp = FastMCP("i18n-validator")

    @mcp.tool()
    def validate_i18n(locales: dict, base_locale: str = "en") -> dict:
        """Check translation files for completeness and consistency.

        Compares key coverage across locales, detects missing translations.
        """
        result = _validate(locales, base_locale)
        return result.model_dump()

    return mcp
