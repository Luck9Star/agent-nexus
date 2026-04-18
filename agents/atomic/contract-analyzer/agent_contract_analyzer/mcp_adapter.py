"""MCP adapter — expose contract-analyzer as an MCP Server using FastMCP.

Provides three MCP tools:
- extract_clauses: Extract and categorize contract clauses.
- analyze_risks: Identify legal risks in clauses.
- check_compliance: Verify compliance against jurisdiction regulations.
"""

from __future__ import annotations

from agent_contract_analyzer.models import ClauseInfo, RiskAnalysis, ComplianceReport
from agent_contract_analyzer.tools.extract_clauses import extract_clauses as _extract
from agent_contract_analyzer.tools.analyze_risks import analyze_risks as _analyze_risks
from agent_contract_analyzer.tools.check_compliance import check_compliance as _check


def create_mcp_server() -> object:
    """Create and return a FastMCP server for contract-analyzer.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-contract-analyzer[full]

    Returns:
        A FastMCP server instance with extract_clauses, analyze_risks,
        and check_compliance tools.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-contract-analyzer[full]"
        )

    mcp = FastMCP("contract-analyzer")

    @mcp.tool()
    def extract_clauses(text: str) -> list[dict]:
        """Extract and categorize clauses from contract text.

        Identifies clause boundaries, classifies types, and extracts
        dependencies, obligations, and party references.
        """
        result = _extract(text)
        return [c.model_dump() for c in result]

    @mcp.tool()
    def analyze_risks(clauses: list[dict]) -> dict:
        """Analyze extracted clauses for legal risks.

        Identifies risks such as unequal terms, ambiguous language,
        and missing mandatory clauses.
        """
        clause_objects = [ClauseInfo.model_validate(c) for c in clauses]
        result = _analyze_risks(clause_objects)
        return result.model_dump()

    @mcp.tool()
    def check_compliance(clauses: list[dict], jurisdiction: str) -> dict:
        """Check clauses against jurisdiction-specific regulations.

        Validates compliance with mandatory clause types and content
        requirements for the specified jurisdiction.
        """
        clause_objects = [ClauseInfo.model_validate(c) for c in clauses]
        result = _check(clause_objects, jurisdiction)
        return result.model_dump()

    return mcp
