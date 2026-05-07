"""MCP adapter — expose market-intelligence-analyst as an MCP Server using FastMCP.

Provides three MCP tools:
- analyze_market: Apply analytical framework to market data.
- identify_trends: Extract and evaluate market trends.
- generate_briefing: Synthesize analysis into a briefing report.
"""

from __future__ import annotations

from agent_market_intelligence_analyst.models import (
    MarketAnalysis,
)
from agent_market_intelligence_analyst.tools.analyze_market import (
    analyze_market as _analyze,
)
from agent_market_intelligence_analyst.tools.generate_briefing import (
    generate_briefing as _briefing,
)
from agent_market_intelligence_analyst.tools.identify_trends import (
    identify_trends as _trends,
)


def create_mcp_server() -> object:
    """Create and return a FastMCP server for market-intelligence-analyst.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-market-intelligence-analyst[full]

    Returns:
        A FastMCP server instance with analyze_market, identify_trends,
        and generate_briefing tools.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-market-intelligence-analyst[full]"
        ) from None

    mcp = FastMCP("market-intelligence-analyst")

    @mcp.tool()
    def analyze_market(data: str, framework: str = "porter") -> dict:
        """Apply an analytical framework (porter/swot/pestel) to market data."""
        result = _analyze(data, framework)
        return result.model_dump()

    @mcp.tool()
    def identify_trends(data: str) -> dict:
        """Identify and evaluate market trends from data."""
        result = _trends(data)
        return result.model_dump()

    @mcp.tool()
    def generate_briefing(analysis: dict) -> dict:
        """Synthesize market analysis into a structured briefing report."""
        analysis_obj = MarketAnalysis.model_validate(analysis)
        result = _briefing(analysis_obj)
        return result.model_dump()

    return mcp
