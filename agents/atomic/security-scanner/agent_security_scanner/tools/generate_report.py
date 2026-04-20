"""Report generation tool — compile findings into a structured security report.

Aggregates security findings by severity, generates prioritized remediation
recommendations, and produces a comprehensive SecurityReport.
"""

from __future__ import annotations

from collections import Counter

from agent_security_scanner.models import SecurityFinding, SecurityReport


def generate_report(findings: list) -> SecurityReport:
    """Generate a comprehensive security report from findings.

    Compiles all security findings, counts by severity, and generates
    prioritized remediation recommendations sorted by risk.

    Args:
        findings: List of SecurityFinding objects or dicts to compile.

    Returns:
        SecurityReport with severity counts, all findings, and recommendations.
    """
    # Normalize findings to SecurityFinding objects
    normalized: list[SecurityFinding] = []
    for f in findings:
        if isinstance(f, SecurityFinding):
            normalized.append(f)
        elif isinstance(f, dict):
            normalized.append(SecurityFinding(**f))
        else:
            raise TypeError(f"Expected SecurityFinding or dict, got {type(f).__name__}")

    # Count by severity
    sev_counts = Counter(f.severity.lower() for f in normalized)
    critical_count = sev_counts.get("critical", 0)
    high_count = sev_counts.get("high", 0)
    medium_count = sev_counts.get("medium", 0)
    low_count = sev_counts.get("low", 0)

    # Generate recommendations sorted by severity
    recommendations = _generate_recommendations(normalized)

    return SecurityReport(
        critical_count=critical_count,
        high_count=high_count,
        medium_count=medium_count,
        low_count=low_count,
        findings=normalized,
        recommendations=recommendations,
    )


def _generate_recommendations(findings: list[SecurityFinding]) -> list[str]:
    """Generate prioritized remediation recommendations.

    Args:
        findings: All security findings.

    Returns:
        List of recommendation strings, ordered by severity (most critical first).
    """
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_findings = sorted(
        findings, key=lambda f: severity_order.get(f.severity.lower(), 99)
    )

    recommendations: list[str] = []
    for f in sorted_findings:
        rec = f.remediation or f"No specific remediation provided for {f.category} at {f.location}"
        prefix = f"[{f.severity.upper()}] {f.location}: "
        recommendations.append(prefix + rec)

    return recommendations
