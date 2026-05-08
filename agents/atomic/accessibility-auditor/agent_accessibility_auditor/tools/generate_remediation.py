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


# Keyword-to-category mapping for issue classification
_CATEGORY_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("image", "alt", "img"), "images"),
    (("form", "input", "label"), "forms"),
    (("heading", "h1", "h2"), "headings"),
    (("link",), "links"),
    (("lang", "language"), "language"),
    (("aria", "role", "tabindex"), "aria"),
    (("table", "th"), "tables"),
    (("keyboard", "focus"), "keyboard"),
]


def _categorize_issue(issue: AccessibilityIssue) -> str:
    """Categorize an issue for effort estimation."""
    desc = (issue.description + " " + issue.element).lower()
    for keywords, category in _CATEGORY_KEYWORDS:
        if any(kw in desc for kw in keywords):
            return category
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
    sorted_issues = sorted(normalized, key=lambda i: (level_order.get(i.level, 99), i.criterion))

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

    total_min = 0
    total_max = 0
    for cat in categories:
        estimate = _EFFORT_MAP.get(cat, "1 hour")
        range_min, range_max = _parse_effort_to_minutes(estimate)
        total_min += range_min
        total_max += range_max

    return _format_effort_range(total_min, total_max)


def _parse_effort_to_minutes(estimate: str) -> tuple[int, int]:
    """Parse an effort estimate string into (min_minutes, max_minutes)."""
    total_min = 0
    total_max = 0
    parts = estimate.replace("minutes", "min").replace("hours", "hrs").split("-")
    for part in parts:
        part = part.strip()
        if "min" in part:
            val = int(part.replace("min", "").strip())
            total_min += val
            total_max += val
        elif "hrs" in part:
            val = int(part.replace("hrs", "").strip())
            total_min += val * 60
            total_max += val * 120
    return total_min, total_max


def _format_effort_range(min_minutes: int, max_minutes: int) -> str:
    """Format a minute range as a human-readable effort string."""
    if min_minutes == max_minutes:
        if max_minutes < 60:
            return f"{max_minutes} minutes"
        return f"{max_minutes // 60} hours"
    if max_minutes < 60:
        return f"{min_minutes}-{max_minutes} minutes"
    return f"{min_minutes // 60}-{max_minutes // 60} hours"
