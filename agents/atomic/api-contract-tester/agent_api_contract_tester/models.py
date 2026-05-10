"""Data models for api-contract-tester Agent.

Pydantic v2 frozen models for OpenAPI contract validation,
schema reference checking, and report generation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ContractFinding(BaseModel):
    """A single contract validation finding.

    Attributes:
        severity: Issue severity — error, warning, or info.
        category: Finding category (e.g. "structure", "schema_ref", "missing_response").
        location: Location within the spec (e.g. "paths./users.get").
        description: Human-readable description of the issue.
        remediation: Suggested fix for the issue.
    """

    model_config = ConfigDict(frozen=True)

    severity: str
    category: str
    location: str
    description: str = ""
    remediation: str = ""


class ContractValidationResult(BaseModel):
    """Result of validating an OpenAPI specification.

    Attributes:
        findings: All validation findings discovered.
        is_valid: Whether the spec passed all critical checks.
        spec_version: The OpenAPI version detected (e.g. "3.0.0").
        endpoint_count: Number of endpoints found in the spec.
    """

    model_config = ConfigDict(frozen=True)

    findings: list[ContractFinding] = Field(default_factory=list)
    is_valid: bool = True
    spec_version: str = ""
    endpoint_count: int = 0


class ContractReport(BaseModel):
    """Comprehensive contract validation report.

    Attributes:
        error_count: Number of error-level findings.
        warning_count: Number of warning-level findings.
        info_count: Number of info-level findings.
        findings: All validation findings.
        recommendations: Prioritized list of remediation recommendations.
        coverage_score: Contract completeness score (0-100).
    """

    model_config = ConfigDict(frozen=True)

    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    findings: list[ContractFinding] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    coverage_score: float = 0.0
