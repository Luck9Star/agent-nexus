"""Data models for document-compliance-gateway Composite Agent.

Pydantic v2 frozen models for multi-dimension compliance checking.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CheckStatus(StrEnum):
    """Compliance check status."""

    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    ERROR = "error"


class ComplianceCheck(BaseModel):
    """Result of a single compliance dimension check.

    Attributes:
        dimension: The compliance dimension (e.g. "legal", "accessibility", "localization").
        status: Check result status.
        issues: List of issues found during the check.
        score: Compliance score for this dimension (0-100).
    """

    model_config = ConfigDict(frozen=True)

    dimension: str
    status: CheckStatus = CheckStatus.PASS
    issues: list[str] = Field(default_factory=list)
    score: float = 100.0


class ConflictItem(BaseModel):
    """A cross-dimension conflict detected between compliance checks.

    Attributes:
        dimensions: The dimensions involved in the conflict.
        description: Human-readable description of the conflict.
        resolution: Suggested resolution for the conflict.
    """

    model_config = ConfigDict(frozen=True)

    dimensions: list[str]
    description: str
    resolution: str = ""


class ComplianceResult(BaseModel):
    """Result of the full compliance check pipeline.

    Attributes:
        checks: Results from each compliance dimension.
        conflicts: Cross-dimension conflicts detected.
        overall_score: Weighted average compliance score (0-100).
        recommendations: Improvement suggestions.
    """

    model_config = ConfigDict(frozen=True)

    checks: list[ComplianceCheck] = Field(default_factory=list)
    conflicts: list[ConflictItem] = Field(default_factory=list)
    overall_score: float = 0.0
    recommendations: list[str] = Field(default_factory=list)
