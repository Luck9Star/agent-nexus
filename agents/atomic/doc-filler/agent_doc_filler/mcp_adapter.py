"""MCP adapter — expose doc-filler as an MCP Server using FastMCP.

Provides two MCP tools:
- analyze_template: Analyze a .docx template to identify placeholders.
- fill_template: Fill a .docx template with provided values.
"""

from __future__ import annotations

from agent_doc_filler.tools.analyze_template import analyze_template as _analyze
from agent_doc_filler.tools.fill_template import fill_template as _fill


def create_mcp_server() -> object:
    """Create and return a FastMCP server for doc-filler.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-doc-filler[full]

    Returns:
        A FastMCP server instance with analyze_template and fill_template tools.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. Install with: pip install agent-doc-filler[full]"
        ) from None

    mcp = FastMCP("doc-filler")

    @mcp.tool()
    def analyze_template(template_path: str) -> dict:
        """Analyze a .docx template to identify all placeholders.

        Scans paragraphs, tables, and headers/footers for {{placeholder}} patterns.
        Returns structured analysis with placeholder names, types, and formatting context.
        """
        result = _analyze(template_path)
        return result.model_dump()

    @mcp.tool()
    def fill_template(
        template_path: str,
        values: dict[str, str],
        output_path: str | None = None,
    ) -> dict:
        """Fill a .docx template with provided values, preserving styles.

        Replaces placeholders with actual content. The filled document is saved
        to output_path (defaults to template_path with _filled suffix).
        """
        result = _fill(template_path, values, output_path)
        return result.model_dump()

    return mcp
