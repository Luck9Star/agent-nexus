"""agent-contract-analyzer — Contract clause analysis specialist.

A three-phase agent that extracts clauses from contract text, identifies
risks, and checks compliance against jurisdiction-specific regulations.
"""

from agent_contract_analyzer.agent import ContractAnalyzerAgent
from agent_contract_analyzer.models import (
    ClauseInfo,
    ComplianceReport,
    RiskAnalysis,
    RiskItem,
)

__all__ = [
    "ContractAnalyzerAgent",
    "ClauseInfo",
    "ComplianceReport",
    "RiskAnalysis",
    "RiskItem",
]
