"""MCP adapter — expose dependency-auditor as an MCP Server using FastMCP.

Provides one MCP tool:
- audit_dependencies: Parse dependency files and check for known CVEs.
"""

from __future__ import annotations

import json

from agent_dependency_auditor.tools.audit_dependencies import (
    audit_dependencies as _audit,
)


def create_mcp_server() -> object:
    """Create and return a FastMCP server for dependency-auditor.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-dependency-auditor[full]

    Returns:
        A FastMCP server instance with audit_dependencies tool.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-dependency-auditor[full]"
        ) from None

    mcp = FastMCP("dependency-auditor")

    @mcp.tool()
    def audit_dependencies(source: str, fmt: str = "auto") -> dict:
        """Parse dependency files and check for known CVEs.

        Accepts a JSON string (will be parsed to dict {package: version})
        or plain string content from requirements.txt / pyproject.toml.
        Returns structured audit report.
        """
        # MCP inputSchema: str avoids ambiguous anyOf.
        # Try JSON parse first; fall back to raw string.
        parsed_source: str | dict
        try:
            parsed_source = json.loads(source)
        except (json.JSONDecodeError, TypeError):
            parsed_source = source
        result = _audit(parsed_source, fmt)
        return result.model_dump()

    return mcp
