"""Self-Evolution Engine -- skill evolution, health diagnostics, and promotion.

Public API:
    EvolutionEngine         -- unified facade for all evolution operations
    EvolutionStore           -- SQLite persistence for skill records
    ExecutionAnalyzer        -- post-task analysis and quality evaluation
    SkillEvolver             -- FIX / DERIVED / CAPTURED evolution execution
    CompactionGuard          -- context window protection against compaction loops
    HealthChecker            -- threshold-based evolution trigger diagnostics
    AgentPromoter            -- skill-to-agent promotion
    EvolutionContextDescriber -- tiered L0/L1/L2 evolution context for LLM injection
"""

from agent_nexus.platform.evolution.store import EvolutionStore
from agent_nexus.platform.evolution.analyzer import ExecutionAnalyzer
from agent_nexus.platform.evolution.evolver import SkillEvolver
from agent_nexus.platform.evolution.compaction import (
    AgentContext,
    CompactionGuard,
)
from agent_nexus.platform.evolution.health import (
    HealthChecker,
    HealthReport,
)
from agent_nexus.platform.evolution.promotion import (
    AgentPromoter,
    PromotionCandidate,
    PromotionResult,
)
from agent_nexus.platform.evolution.context_describer import (
    EvolutionContextDescriber,
)
from agent_nexus.platform.evolution.engine import EvolutionEngine

__all__ = [
    "EvolutionEngine",
    "EvolutionStore",
    "ExecutionAnalyzer",
    "SkillEvolver",
    "AgentContext",
    "CompactionGuard",
    "HealthChecker",
    "HealthReport",
    "AgentPromoter",
    "PromotionCandidate",
    "PromotionResult",
    "EvolutionContextDescriber",
]
