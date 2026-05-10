"""Report generation tool — compile findings into a structured pipeline report.

Aggregates pipeline findings by severity, generates prioritized remediation
recommendations, and produces a comprehensive PipelineReport.
"""

from __future__ import annotations

from collections import Counter

from agent_data_pipeline_validator.models import PipelineFinding, PipelineReport


def generate_report(findings: list) -> PipelineReport:
    """Generate a comprehensive pipeline report from findings.

    Compiles all pipeline findings, counts by severity, and generates
    prioritized remediation recommendations.

    Args:
        findings: List of PipelineFinding objects or dicts to compile.

    Returns:
        PipelineReport with severity counts, all findings, and recommendations.
    """
    # Normalize findings to PipelineFinding objects
    normalized: list[PipelineFinding] = []
    for f in findings:
        if isinstance(f, PipelineFinding):
            normalized.append(f)
        elif isinstance(f, dict):
            normalized.append(PipelineFinding(**f))
        else:
            raise TypeError(f"Expected PipelineFinding or dict, got {type(f).__name__}")

    # Count by severity
    sev_counts = Counter(f.severity.lower() for f in normalized)
    error_count = sev_counts.get("error", 0)
    warning_count = sev_counts.get("warning", 0)
    info_count = sev_counts.get("info", 0)

    # Generate recommendations sorted by severity
    recommendations = _generate_recommendations(normalized)

    return PipelineReport(
        error_count=error_count,
        warning_count=warning_count,
        info_count=info_count,
        findings=normalized,
        recommendations=recommendations,
    )


def _generate_recommendations(findings: list[PipelineFinding]) -> list[str]:
    """Generate prioritized remediation recommendations.

    Args:
        findings: All pipeline findings.

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
