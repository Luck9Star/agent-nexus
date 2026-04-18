"""agent-competitive-intelligence-briefing -- Competitive intelligence briefing pipeline.

Orchestrates three Atomic Agents in a sequential chain:
  1. market-intelligence-analyst  -- gather and analyze market data
  2. doc-filler                   -- fill report template with analysis
  3. localization-specialist      -- localize the final report
"""

from agent_competitive_intelligence_briefing.coordinator import (
    CompetitiveIntelCoordinator,
)
from agent_competitive_intelligence_briefing.models import (
    BriefingResult,
    PipelineStep,
)

__all__ = [
    "CompetitiveIntelCoordinator",
    "BriefingResult",
    "PipelineStep",
]
