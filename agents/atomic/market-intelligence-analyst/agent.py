"""Top-level entry point for market-intelligence-analyst agent."""

from __future__ import annotations


async def run(task: str, _context: dict | None = None) -> str:
    """Execute the market-intelligence-analyst agent task.

    Args:
        task: Task description (e.g. market data to analyze).
        _context: Optional context dictionary.

    Returns:
        Task result as string.
    """
    from agent_market_intelligence_analyst.agent import MarketIntelligenceAgent

    agent = MarketIntelligenceAgent()
    result = agent.analyze_market(task)
    return result.model_dump_json()
