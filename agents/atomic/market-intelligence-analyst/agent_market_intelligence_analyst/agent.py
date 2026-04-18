"""MarketIntelligenceAgent — Market research analysis specialist.

Three-phase pipeline:
  1. analyze_market()    — apply analytical framework to market data
  2. identify_trends()   — extract and evaluate market trends
  3. generate_briefing() — synthesize findings into a briefing report

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_market_intelligence_analyst.models import (
    BriefingReport,
    MarketAnalysis,
    TrendReport,
)
from agent_market_intelligence_analyst.tools.analyze_market import analyze_market
from agent_market_intelligence_analyst.tools.generate_briefing import generate_briefing
from agent_market_intelligence_analyst.tools.identify_trends import identify_trends


class MarketIntelligenceAgent:
    """Market research analysis specialist.

    This agent provides a three-phase pipeline for market intelligence:
    Phase 1 (analyze) applies a framework (Porter/SWOT/PESTEL) to market data.
    Phase 2 (trends) identifies and evaluates market trends. Phase 3 (briefing)
    synthesizes everything into a structured briefing report.

    Usage:
        agent = MarketIntelligenceAgent()
        analysis = agent.analyze_market(market_data, framework="porter")
        trends = agent.identify_trends(market_data)
        briefing = agent.generate_briefing(analysis)
    """

    def analyze_market(self, data: str, framework: str = "porter") -> MarketAnalysis:
        """Phase 1: Apply analytical framework to market data.

        Analyzes market data using the specified framework and returns
        structured results with factor assessments and scores.

        Args:
            data: Market data text to analyze.
            framework: Framework to use ("porter", "swot", "pestel").

        Returns:
            MarketAnalysis with factor assessments and insights.
        """
        return analyze_market(data, framework)

    def identify_trends(self, data: str) -> TrendReport:
        """Phase 2: Identify and evaluate market trends.

        Extracts trend signals from market data, evaluates their direction
        and impact, and produces a trend report.

        Args:
            data: Market data text to analyze.

        Returns:
            TrendReport with identified trends and confidence score.
        """
        return identify_trends(data)

    def generate_briefing(self, analysis: MarketAnalysis) -> BriefingReport:
        """Phase 3: Generate a structured briefing from analysis.

        Synthesizes market analysis results into a structured briefing
        with executive summary, sections, and recommendations.

        Args:
            analysis: MarketAnalysis results to synthesize.

        Returns:
            BriefingReport with sections and recommendations.
        """
        return generate_briefing(analysis)
