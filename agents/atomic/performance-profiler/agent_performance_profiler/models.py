"""Data models for performance-profiler Agent.

Pydantic v2 frozen models for performance analysis findings
and report generation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PerformanceFinding(BaseModel):
    """A single performance issue finding.

    Attributes:
        severity: Issue severity — critical, high, medium, or low.
        category: Issue category (e.g. "n_plus_one", "inefficient_loop", "memory_inefficient").
        location: Line number where the issue was detected.
        description: Human-readable description of the issue.
        remediation: Suggested fix for the issue.
        complexity: Estimated time complexity impact (e.g. "O(n^2)").
    """

    model_config = ConfigDict(frozen=True)

    severity: str
    category: str
    location: str
    description: str = ""
    remediation: str = ""
    complexity: str = ""


class PerformanceReport(BaseModel):
    """Comprehensive performance analysis report.

    Attributes:
        critical_count: Number of critical severity findings.
        high_count: Number of high severity findings.
        medium_count: Number of medium severity findings.
        low_count: Number of low severity findings.
        findings: All performance findings.
        recommendations: Prioritized list of remediation recommendations.
        lines_analyzed: Number of source lines analyzed.
    """

    model_config = ConfigDict(frozen=True)

    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    findings: list[PerformanceFinding] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    lines_analyzed: int = 0
