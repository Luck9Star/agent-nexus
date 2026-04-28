"""MCP adapter — expose accessibility-auditor as an MCP Server using FastMCP.

Provides three MCP tools:
- audit_content: Audit content against WCAG 2.2 AA criteria.
- check_html: HTML-specific accessibility checks.
- generate_remediation: Generate prioritized fix plan.
"""

from __future__ import annotations

from agent_accessibility_auditor.tools.audit_content import audit_content as _audit
from agent_accessibility_auditor.tools.check_html import check_html as _check_html
from agent_accessibility_auditor.tools.generate_remediation import (
    generate_remediation as _remediate,
)


def create_mcp_server() -> object:
    """Create and return a FastMCP server for accessibility-auditor.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-accessibility-auditor[full]

    Returns:
        A FastMCP server instance with audit_content, check_html, and
        generate_remediation tools.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-accessibility-auditor[full]"
        )

    mcp = FastMCP("accessibility-auditor")

    @mcp.tool()
    def audit_content(content: str, content_type: str = "html") -> dict:
        """Audit content against WCAG 2.2 AA criteria.

        Returns compliance score and list of accessibility issues found.
        """
        result = _audit(content, content_type)
        return result.model_dump()

    @mcp.tool()
    def check_html(html: str) -> list:
        """Check HTML code for accessibility issues.

        Performs comprehensive HTML-specific checks covering images, forms,
        headings, links, language, ARIA, and tables.
        """
        issues = _check_html(html)
        return [i.model_dump() for i in issues]

    @mcp.tool()
    def generate_remediation(issues: list) -> dict:
        """Generate a prioritized remediation plan for accessibility issues.

        Orders issues by WCAG level priority and estimates total effort.
        """
        result = _remediate(issues)
        return result.model_dump()

    return mcp
