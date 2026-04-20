"""MCP adapter -- expose competitive-intelligence-briefing as an MCP Server.

Provides one MCP tool:
- generate_briefing: Run the full competitive intelligence pipeline.
"""

from __future__ import annotations

from agent_competitive_intelligence_briefing.coordinator import (
    CompetitiveIntelCoordinator,
)


def create_mcp_server() -> object:
    """Create and return a FastMCP server for competitive-intelligence-briefing.

    Requires the ``fastmcp`` package. Install with:
        pip install agent-competitive-intelligence-briefing[full]

    Returns:
        A FastMCP server instance with generate_briefing tool.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-competitive-intelligence-briefing[full]"
        )

    mcp = FastMCP("competitive-intelligence-briefing")
    coordinator = CompetitiveIntelCoordinator()

    @mcp.tool()
    async def generate_briefing(
        query: str,
        target_langs: list[str] | None = None,
        template_path: str | None = None,
        framework: str = "porter",
    ) -> dict:
        """Generate a competitive intelligence briefing from a research query.

        Runs the full pipeline: Market Intel -> Doc Filler -> Localization.

        Args:
            query: Research query (e.g. "EV market in China").
            target_langs: Language codes for localization (default: ["en"]).
            template_path: Optional .docx template path.
            framework: Analysis framework (porter/swot/pestel).
        """
        result = await coordinator.generate_briefing_async(
            query=query,
            target_langs=target_langs,
            template_path=template_path,
            framework=framework,
        )
        return result.model_dump()

    return mcp
