"""Remediation generation tool — create prioritized fix plans for accessibility issues.

Orders issues by WCAG level priority (A before AA), groups by criterion,
and estimates remediation effort.
"""

from __future__ import annotations

from agent_accessibility_auditor.models import AccessibilityIssue, RemediationPlan

# Effort estimates by category of issues
_EFFORT_MAP: dict[str, str] = {
    "images": "30 minutes",
    "forms": "1-2 hours",
    "headings": "30 minutes",
    "links": "1 hour",
    "language": "5 minutes",
    "aria": "1-2 hours",
    "tables": "1-2 hours",
    "keyboard": "2-4 hours",
}


def _categorize_issue(issue: AccessibilityIssue) -> str:
    """Categorize an issue for effort estimation."""
    desc = (issue.description + " " + issue.element).lower()
    if "image" in desc or "alt" in desc or "img" in desc:
        return "images"
    if "form" in desc or "input" in desc or "label" in desc:
        return "forms"
    if "heading" in desc or "h1" in desc or "h2" in desc:
        return "headings"
    if "link" in desc:
        return "links"
    if "lang" in desc or "language" in desc:
        return "language"
    if "aria" in desc or "role" in desc or "tabindex" in desc:
        return "aria"
    if "table" in desc or "th" in desc:
        return "tables"
    if "keyboard" in desc or "focus" in desc:
        return "keyboard"
    return "general"


def generate_remediation(issues: list) -> RemediationPlan:
    """Generate a prioritized remediation plan from accessibility issues.

    Orders issues by WCAG conformance level (Level A first, then AA),
    deduplicates by criterion, and estimates total remediation effort.

    Args:
        issues: List of AccessibilityIssue objects or dicts.

    Returns:
        RemediationPlan with prioritized issues, order, and effort estimate.
    """
    # Normalize issues
    normalized: list[AccessibilityIssue] = []
    for issue in issues:
        if isinstance(issue, AccessibilityIssue):
            normalized.append(issue)
        elif isinstance(issue, dict):
            normalized.append(AccessibilityIssue(**issue))
        else:
            raise TypeError(f"Expected AccessibilityIssue or dict, got {type(issue).__name__}")

    # Sort: Level A first (must fix for any conformance), then AA
    level_order = {"A": 0, "AA": 1, "AAA": 2}
    sorted_issues = sorted(
        normalized, key=lambda i: (level_order.get(i.level, 99), i.criterion)
    )

    # Build priority order (unique criteria in fix order)
    seen_criteria: set[str] = set()
    priority_order: list[str] = []
    for issue in sorted_issues:
        if issue.criterion not in seen_criteria:
            seen_criteria.add(issue.criterion)
            priority_order.append(issue.criterion)

    # Estimate effort
    effort = _estimate_effort(sorted_issues)

    return RemediationPlan(
        issues=sorted_issues,
        priority_order=priority_order,
        estimated_effort=effort,
    )


def _estimate_effort(issues: list[AccessibilityIssue]) -> str:
    """Estimate total remediation effort.

    Args:
        issues: All issues to estimate effort for.

    Returns:
        Human-readable effort estimate string.
    """
    if not issues:
        return "No issues to remediate"

    categories: set[str] = {_categorize_issue(i) for i in issues}

    # Sum up effort ranges (simplified: take the max of ranges)
    total_minutes_min = 0
    total_minutes_max = 0
    for cat in categories:
        estimate = _EFFORT_MAP.get(cat, "1 hour")
        parts = estimate.replace("minutes", "min").replace("hours", "hrs").split("-")
        for part in parts:
            part = part.strip()
            if "min" in part:
                val = int(part.replace("min", "").strip())
                total_minutes_min += val
                total_minutes_max += val
            elif "hrs" in part:
                val = int(part.replace("hrs", "").strip())
                total_minutes_min += val * 60
                total_minutes_max += val * 120

    if total_minutes_min == total_minutes_max:
        if total_minutes_max < 60:
            return f"{total_minutes_max} minutes"
        return f"{total_minutes_max // 60} hours"

    if total_minutes_max < 60:
        return f"{total_minutes_min}-{total_minutes_max} minutes"
    return f"{total_minutes_min // 60}-{total_minutes_max // 60} hours"
