"""Report generation tool — compile findings into a structured contract report.

Aggregates contract findings by severity, generates prioritized remediation
recommendations, and computes a coverage score.
"""

from __future__ import annotations

from collections import Counter

from agent_api_contract_tester.models import ContractFinding, ContractReport


def generate_report(findings: list) -> ContractReport:
    """Generate a comprehensive contract report from findings.

    Compiles all contract findings, counts by severity, generates
    prioritized remediation recommendations, and computes a coverage score.

    Args:
        findings: List of ContractFinding objects or dicts to compile.

    Returns:
        ContractReport with severity counts, all findings, recommendations, and score.
    """
    # Normalize findings to ContractFinding objects
    normalized: list[ContractFinding] = []
    for f in findings:
        if isinstance(f, ContractFinding):
            normalized.append(f)
        elif isinstance(f, dict):
            normalized.append(ContractFinding(**f))
        else:
            raise TypeError(f"Expected ContractFinding or dict, got {type(f).__name__}")

    # Count by severity
    sev_counts = Counter(f.severity.lower() for f in normalized)
    error_count = sev_counts.get("error", 0)
    warning_count = sev_counts.get("warning", 0)
    info_count = sev_counts.get("info", 0)

    # Generate recommendations sorted by severity
    recommendations = _generate_recommendations(normalized)

    # Compute coverage score (100 minus penalties)
    coverage_score = _compute_coverage_score(normalized)

    return ContractReport(
        error_count=error_count,
        warning_count=warning_count,
        info_count=info_count,
        findings=normalized,
        recommendations=recommendations,
        coverage_score=coverage_score,
    )


def _generate_recommendations(findings: list[ContractFinding]) -> list[str]:
    """Generate prioritized remediation recommendations.

    Args:
        findings: All contract findings.

    Returns:
        List of recommendation strings, ordered by severity (errors first).
    """
    severity_order = {"error": 0, "warning": 1, "info": 2}
    sorted_findings = sorted(findings, key=lambda f: severity_order.get(f.severity.lower(), 99))

    recommendations: list[str] = []
    for finding in sorted_findings:
        rec = (
            finding.remediation
            or f"No specific remediation for {finding.category} at {finding.location}"
        )
        prefix = f"[{finding.severity.upper()}] {finding.location}: "
        recommendations.append(prefix + rec)

    return recommendations


def _compute_coverage_score(findings: list[ContractFinding]) -> float:
    """Compute contract completeness score (0-100).

    Penalty: -10 per error, -3 per warning, -1 per info.
    Score is clamped to [0, 100].

    Args:
        findings: All contract findings.

    Returns:
        Coverage score between 0 and 100.
    """
    score = 100.0
    for f in findings:
        sev = f.severity.lower()
        if sev == "error":
            score -= 10
        elif sev == "warning":
            score -= 3
        elif sev == "info":
            score -= 1
    return max(0.0, min(100.0, score))
