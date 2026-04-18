"""Data models for contract-analyzer Agent.

Pydantic v2 frozen models for contract clause extraction, risk analysis,
and compliance checking operations.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ClauseInfo(BaseModel):
    """Information about a single contract clause.

    Attributes:
        clause_id: Clause identifier (e.g. "3.1", "Article II").
        type: Clause type category.
        content: Original text of the clause.
        dependencies: IDs of other clauses referenced by this clause.
        obligations: Obligations extracted from this clause.
        parties: Parties mentioned in this clause.
    """

    model_config = ConfigDict(frozen=True)

    clause_id: str
    type: str = "other"
    content: str = ""
    dependencies: list[str] = Field(default_factory=list)
    obligations: list[str] = Field(default_factory=list)
    parties: list[str] = Field(default_factory=list)


class RiskItem(BaseModel):
    """A single identified risk in a contract.

    Attributes:
        category: Risk category (e.g. "unequal_terms", "ambiguity", "missing_clause").
        severity: Risk severity level.
        description: Human-readable description of the risk.
        affected_clauses: IDs of clauses affected by this risk.
        mitigation: Suggested mitigation or fix.
    """

    model_config = ConfigDict(frozen=True)

    category: str
    severity: str = "medium"
    description: str = ""
    affected_clauses: list[str] = Field(default_factory=list)
    mitigation: str = ""


class RiskAnalysis(BaseModel):
    """Result of contract risk analysis.

    Attributes:
        risks: All identified risks.
        severity_map: Count of risks per severity level.
        recommendations: Overall recommendations for the contract.
    """

    model_config = ConfigDict(frozen=True)

    risks: list[RiskItem] = Field(default_factory=list)
    severity_map: dict[str, int] = Field(default_factory=dict)
    recommendations: list[str] = Field(default_factory=list)


class ComplianceReport(BaseModel):
    """Result of compliance checking against a jurisdiction.

    Attributes:
        compliant: Whether the contract is overall compliant.
        violations: List of violation descriptions.
        suggestions: List of suggestions for achieving compliance.
    """

    model_config = ConfigDict(frozen=True)

    compliant: bool = True
    violations: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
