"""MCP adapter — expose dependency-auditor as an MCP Server using FastMCP.

Provides one MCP tool:
- audit_dependencies: Parse dependency files and check for known CVEs.
"""

from __future__ import annotations

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
    def audit_dependencies(source: str | dict, fmt: str = "auto") -> dict:
        """Parse dependency files and check for known CVEs.

        Accepts dict {package: version} or string content from
        requirements.txt / pyproject.toml. Returns structured audit report.
        """
        result = _audit(source, fmt)
        return result.model_dump()

    return mcp
