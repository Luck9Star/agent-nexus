"""Review generation tool -- compile findings into a structured review report.

Aggregates CodeAnalysis and PatternMatch results into a ReviewReport
with severity counts, suggestions, and an overall score.
"""

from __future__ import annotations

from agent_code_reviewer.models import (
    CodeAnalysis,
    CodeIssue,
    PatternMatch,
    ReviewReport,
)


def _count_severities(
    issues: list[CodeIssue],
    patterns: list[PatternMatch],
) -> dict[str, int]:
    """Count issues and patterns by severity level."""
    counts: dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
    for issue in issues:
        sev = issue.severity.lower()
        if sev in counts:
            counts[sev] += 1
    for pattern in patterns:
        sev = pattern.severity.lower()
        if sev in counts:
            counts[sev] += 1
    return counts


def _calculate_score(counts: dict[str, int]) -> int:
    """Calculate an overall quality score (0-100).

    Deductions:
    - Critical: -15 each
    - Warning: -5 each
    - Info: -1 each
    """
    score = 100
    score -= counts.get("critical", 0) * 15
    score -= counts.get("warning", 0) * 5
    score -= counts.get("info", 0) * 1
    return max(0, min(100, score))


def _generate_summary(
    analysis: CodeAnalysis,
    patterns: list[PatternMatch],
    score: int,
) -> str:
    """Generate a high-level summary of the review."""
    parts: list[str] = []

    parts.append(f"File: {analysis.file_path}")
    parts.append(f"Language: {analysis.language}")

    metrics = analysis.metrics
    if metrics.total_lines > 0:
        parts.append(f"Lines: {metrics.lines_of_code} code / {metrics.total_lines} total")
    if metrics.function_count > 0:
        parts.append(f"Functions: {metrics.function_count}")
    if metrics.class_count > 0:
        parts.append(f"Classes: {metrics.class_count}")

    total_issues = len(analysis.issues) + len(patterns)
    if total_issues == 0:
        parts.append("No issues found.")
    else:
        parts.append(f"Found {total_issues} issue(s).")

    parts.append(f"Quality score: {score}/100")

    return " | ".join(parts)


def _generate_suggestions(
    issues: list[CodeIssue],
    patterns: list[PatternMatch],
) -> list[str]:
    """Generate improvement suggestions from issues and patterns."""
    suggestions: list[str] = []

    # Group by category
    security_issues = [i for i in issues if i.category == "security"]
    security_patterns = [p for p in patterns if p.severity == "critical"]
    if security_issues or security_patterns:
        suggestions.append("Security: Address critical security findings before deployment.")

    if any(i.category == "performance" for i in issues) or any(
        p.pattern == "n_plus_one" for p in patterns
    ):
        suggestions.append(
            "Performance: Review database query patterns for optimization opportunities."
        )

    complex_issues = [i for i in issues if "complex" in i.message.lower()]
    deep_nesting = [p for p in patterns if p.pattern == "deep_nesting"]
    if complex_issues or deep_nesting:
        suggestions.append("Maintainability: Reduce complexity by extracting helper functions.")

    if any(p.pattern == "hardcoded_secret" for p in patterns):
        suggestions.append(
            "Security: Move all secrets to environment variables or a secrets manager."
        )

    if any(p.pattern == "empty_catch" for p in patterns):
        suggestions.append("Error handling: Add proper error handling in catch/except blocks.")

    if any(p.pattern == "magic_number" for p in patterns):
        suggestions.append("Readability: Extract magic numbers to named constants.")

    if not suggestions:
        suggestions.append("Code looks good! No major improvements needed.")

    return suggestions


def generate_review(
    analysis: CodeAnalysis,
    patterns: list[PatternMatch] | None = None,
) -> ReviewReport:
    """Generate a structured review report from analysis results.

    Args:
        analysis: The CodeAnalysis from phase 1.
        patterns: PatternMatch results from phase 2. If None, only
            analysis issues are included in the report.

    Returns:
        ReviewReport with summary, findings, suggestions, severity counts,
        and overall quality score.
    """
    if patterns is None:
        patterns = []

    # Combine findings
    findings: list[CodeIssue | PatternMatch] = list(analysis.issues) + patterns

    # Calculate metrics
    severity_counts = _count_severities(analysis.issues, patterns)
    score = _calculate_score(severity_counts)
    summary = _generate_summary(analysis, patterns, score)
    suggestions = _generate_suggestions(analysis.issues, patterns)

    return ReviewReport(
        summary=summary,
        findings=findings,
        suggestions=suggestions,
        severity_counts=severity_counts,
        overall_score=score,
    )
