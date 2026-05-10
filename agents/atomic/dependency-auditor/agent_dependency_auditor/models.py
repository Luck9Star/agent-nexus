"""Data models for dependency-auditor Agent.

Pydantic v2 frozen models for dependency vulnerability scanning and audit reporting.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DependencyVulnerability(BaseModel):
    """A known vulnerability in a dependency.

    Attributes:
        package: The vulnerable package name.
        installed_version: The currently installed/declared version.
        cve: CVE identifier (e.g. "CVE-2023-30861").
        severity: Vulnerability severity — critical, high, medium, or low.
        summary: Brief description of the vulnerability.
        fixed_in: The version where the vulnerability was fixed.
    """

    model_config = ConfigDict(frozen=True)

    package: str
    installed_version: str
    cve: str = ""
    severity: str = "medium"
    summary: str = ""
    fixed_in: str = ""


class AuditReport(BaseModel):
    """Result of auditing project dependencies.

    Attributes:
        vulnerabilities: All discovered dependency vulnerabilities.
        total_scanned: Number of dependencies scanned.
        vulnerable_count: Number of dependencies with known vulnerabilities.
        summary: Counts by severity level.
    """

    model_config = ConfigDict(frozen=True)

    vulnerabilities: list[DependencyVulnerability] = Field(default_factory=list)
    total_scanned: int = 0
    vulnerable_count: int = 0
    summary: dict[str, int] = Field(default_factory=dict)
