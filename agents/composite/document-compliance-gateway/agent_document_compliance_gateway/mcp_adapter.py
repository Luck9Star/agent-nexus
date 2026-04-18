"""MCP adapter -- expose document-compliance-gateway as an MCP Server using FastMCP.

Provides one MCP tool:
- check_compliance: Run full compliance check on a document.
"""

from __future__ import annotations

from agent_document_compliance_gateway.coordinator import ComplianceCoordinator


def create_mcp_server() -> object:
    """Create and return a FastMCP server for document-compliance-gateway.

    Requires the ``fastmcp`` package to be installed.

    Returns:
        A FastMCP server instance with check_compliance tool.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-document-compliance-gateway[full]"
        )

    mcp = FastMCP("document-compliance-gateway")
    coordinator = ComplianceCoordinator()

    @mcp.tool()
    def check_compliance(document: str, jurisdictions: list[str] | None = None) -> dict:
        """Run compliance check across legal, accessibility, and localization dimensions.

        Returns checks for each dimension, cross-dimension conflicts, and recommendations.
        """
        result = coordinator.check_compliance(document, jurisdictions)
        return result.model_dump()

    return mcp
