"""agent-market-intelligence-analyst — Market research analysis specialist.

A three-phase agent that applies analytical frameworks (Porter's Five Forces,
SWOT, PESTEL) to market data, identifies trends, and generates briefings.
"""

from agent_market_intelligence_analyst.agent import MarketIntelligenceAgent
from agent_market_intelligence_analyst.models import (
    BriefingReport,
    MarketAnalysis,
    TrendItem,
    TrendReport,
)

__all__ = [
    "MarketIntelligenceAgent",
    "BriefingReport",
    "MarketAnalysis",
    "TrendItem",
    "TrendReport",
]
