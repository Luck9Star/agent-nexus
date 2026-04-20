"""MCP adapter -- expose product-documentation-suite as an MCP Server.

Provides one MCP tool:
- generate_docs: Run the full documentation suite pipeline.
"""

from __future__ import annotations

from agent_product_documentation_suite.coordinator import (
    DocumentationSuiteCoordinator,
)


def create_mcp_server() -> object:
    """Create and return a FastMCP server for product-documentation-suite.

    Requires the ``fastmcp`` package. Install with:
        pip install agent-product-documentation-suite[full]

    Returns:
        A FastMCP server instance with generate_docs tool.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-product-documentation-suite[full]"
        )

    mcp = FastMCP("product-documentation-suite")
    coordinator = DocumentationSuiteCoordinator()

    @mcp.tool()
    async def generate_docs(
        code_path: str,
        target_langs: list[str] | None = None,
    ) -> dict:
        """Generate API documentation and code review for a source file.

        Runs the pipeline: [API Doc Gen + Code Review] in parallel -> Localization.

        Args:
            code_path: Path to the source code file.
            target_langs: Language codes for localization (default: ["en"]).
        """
        result = await coordinator.generate_docs_async(
            code_path=code_path,
            target_langs=target_langs,
        )
        return result.model_dump()

    return mcp
