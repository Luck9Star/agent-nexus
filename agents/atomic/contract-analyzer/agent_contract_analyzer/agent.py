"""ContractAnalyzerAgent — Contract clause analysis specialist.

Three-phase pipeline:
  1. extract_clauses() — identify and categorize contract clauses
  2. analyze_risks()    — identify legal risks and their severity
  3. check_compliance() — jurisdiction-specific compliance verification

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_contract_analyzer.models import (
    ClauseInfo,
    ComplianceReport,
    RiskAnalysis,
)
from agent_contract_analyzer.tools.check_compliance import check_compliance
from agent_contract_analyzer.tools.analyze_risks import analyze_risks
from agent_contract_analyzer.tools.extract_clauses import extract_clauses


class ContractAnalyzerAgent:
    """Contract clause analysis specialist.

    This agent provides a three-phase pipeline for contract analysis:
    Phase 1 (extract) parses contract text to identify clauses with their
    types, dependencies, obligations, and parties. Phase 2 (risk) analyzes
    the extracted clauses for legal risks. Phase 3 (compliance) checks
    the clauses against jurisdiction-specific regulations.

    Usage:
        agent = ContractAnalyzerAgent()
        clauses = agent.extract_clauses(contract_text)
        risks = agent.analyze_risks(clauses)
        compliance = agent.check_compliance(clauses, jurisdiction="CN")
    """

    def extract_clauses(self, text: str) -> list[ClauseInfo]:
        """Phase 1: Extract and categorize clauses from contract text.

        Parses contract text to identify clause boundaries, classify types,
        extract dependencies, obligations, and parties.

        Args:
            text: Full contract text to analyze.

        Returns:
            List of ClauseInfo with all identified clauses.
        """
        return extract_clauses(text)

    def analyze_risks(self, clauses: list[ClauseInfo]) -> RiskAnalysis:
        """Phase 2: Analyze extracted clauses for legal risks.

        Identifies potential risks such as unequal terms, ambiguous language,
        missing mandatory clauses, and excessive liability.

        Args:
            clauses: List of extracted ClauseInfo to analyze.

        Returns:
            RiskAnalysis with identified risks and recommendations.
        """
        return analyze_risks(clauses)

    def check_compliance(
        self, clauses: list[ClauseInfo], jurisdiction: str
    ) -> ComplianceReport:
        """Phase 3: Check clauses against jurisdiction-specific regulations.

        Validates whether the contract clauses comply with the regulations
        of the specified jurisdiction.

        Args:
            clauses: List of extracted ClauseInfo to check.
            jurisdiction: Jurisdiction code (e.g. "CN", "US", "UK", "EU").

        Returns:
            ComplianceReport with compliance status and suggestions.
        """
        return check_compliance(clauses, jurisdiction)
