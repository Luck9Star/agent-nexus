"""HTML checking tool — perform HTML-specific accessibility checks.

Focused on structural HTML checks: forms, images, headings, links,
tables, ARIA, and keyboard accessibility.
"""

from __future__ import annotations

from agent_accessibility_auditor.models import AccessibilityIssue
from agent_accessibility_auditor.tools.audit_content import (
    _check_aria,
    _check_forms,
    _check_headings,
    _check_images,
    _check_language,
    _check_links,
    _check_tables,
)


def check_html(html: str) -> list[AccessibilityIssue]:
    """Check HTML code for accessibility issues.

    Performs comprehensive HTML-specific accessibility checks covering
    images, forms, headings, links, language, ARIA, and tables.

    Args:
        html: HTML string to check.

    Returns:
        List of AccessibilityIssue objects found in the HTML.
    """
    if not html.strip():
        return []

    issues: list[AccessibilityIssue] = []
    issues.extend(_check_images(html))
    issues.extend(_check_forms(html))
    issues.extend(_check_headings(html))
    issues.extend(_check_links(html))
    issues.extend(_check_language(html))
    issues.extend(_check_aria(html))
    issues.extend(_check_tables(html))

    return issues
