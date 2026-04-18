"""Data models for security-scanner Agent.

Pydantic v2 frozen models for vulnerability scanning, dependency checking,
and security report generation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SecurityFinding(BaseModel):
    """A single security vulnerability finding.

    Attributes:
        severity: Risk severity — critical, high, medium, or low.
        category: Vulnerability category (e.g. "injection", "xss", "path_traversal").
        location: File path and line number where the finding was detected.
        description: Human-readable description of the vulnerability.
        remediation: Suggested fix for the vulnerability.
        cwe_id: CWE (Common Weakness Enumeration) identifier, e.g. "CWE-89".
    """

    model_config = ConfigDict(frozen=True)

    severity: str
    category: str
    location: str
    description: str = ""
    remediation: str = ""
    cwe_id: str = ""


class SecurityScanResult(BaseModel):
    """Result of scanning code for security vulnerabilities.

    Attributes:
        findings: All security findings discovered during the scan.
        summary: Aggregated counts by severity level.
    """

    model_config = ConfigDict(frozen=True)

    findings: list[SecurityFinding] = Field(default_factory=list)
    summary: dict = Field(default_factory=dict)


class DependencyVulnerability(BaseModel):
    """A known vulnerability in a dependency.

    Attributes:
        package: The vulnerable package name.
        version: The installed/declared version.
        cve: CVE identifier (e.g. "CVE-2023-12345").
        severity: Vulnerability severity — critical, high, medium, or low.
    """

    model_config = ConfigDict(frozen=True)

    package: str
    version: str
    cve: str = ""
    severity: str = "medium"


class DependencyReport(BaseModel):
    """Report on dependency vulnerability status.

    Attributes:
        vulnerabilities: All discovered dependency vulnerabilities.
        total_scanned: Number of dependencies scanned.
        vulnerable_count: Number of dependencies with known vulnerabilities.
    """

    model_config = ConfigDict(frozen=True)

    vulnerabilities: list[DependencyVulnerability] = Field(default_factory=list)
    total_scanned: int = 0
    vulnerable_count: int = 0


class SecurityReport(BaseModel):
    """Comprehensive security report with severity breakdown.

    Attributes:
        critical_count: Number of critical severity findings.
        high_count: Number of high severity findings.
        medium_count: Number of medium severity findings.
        low_count: Number of low severity findings.
        findings: All security findings.
        recommendations: Prioritized list of remediation recommendations.
    """

    model_config = ConfigDict(frozen=True)

    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    findings: list[SecurityFinding] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
