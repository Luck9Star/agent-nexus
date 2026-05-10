"""MCP adapter — expose db-schema-analyzer as an MCP Server using FastMCP.

Provides one MCP tool:
- review_schema: Parse SQL DDL and check for design anti-patterns.
"""

from __future__ import annotations

from agent_db_schema_analyzer.tools.review_schema import review_schema as _review


def create_mcp_server() -> object:
    """Create and return a FastMCP server for db-schema-analyzer.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-db-schema-analyzer[full]

    Returns:
        A FastMCP server instance with review_schema tool.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-db-schema-analyzer[full]"
        ) from None

    mcp = FastMCP("db-schema-analyzer")

    @mcp.tool()
    def review_schema(ddl_text: str, dialect: str = "generic") -> dict:
        """Parse SQL DDL, check for design anti-patterns.

        Detects missing primary keys, naming violations, missing indexes,
        type issues, and normalization concerns.
        """
        result = _review(ddl_text, dialect)
        return result.model_dump()

    return mcp
