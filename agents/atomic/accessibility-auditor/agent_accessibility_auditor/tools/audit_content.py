"""Content auditing tool — check text/markup content against WCAG criteria.

Performs accessibility checks on general content, computing a compliance
score and identifying specific WCAG 2.2 AA violations.
"""

from __future__ import annotations

import re

from agent_accessibility_auditor.models import AccessibilityIssue, AuditResult

# Pre-compiled module-level regexes for accessibility checks
_IMG_TAG_RE = re.compile(r"<img\s[^>]*>", re.IGNORECASE)
_IMG_ALT_RE = re.compile(r'\balt\s*=\s*["\']', re.IGNORECASE)
_IMG_ALT_EMPTY_RE = re.compile(r'\balt\s*=\s*["\']["\']', re.IGNORECASE)
_IMG_ROLE_PRESENTATION_RE = re.compile(r'\brole\s*=\s*["\']presentation["\']', re.IGNORECASE)

_INPUT_TAG_RE = re.compile(
    r"<input\s[^>]*type\s*=\s*['\"]?(\w+)['\"]?[^>]*>", re.IGNORECASE
)
_LABEL_FOR_RE = re.compile(
    r'<label\s[^>]*for\s*=\s*["\'](\w+)["\']', re.IGNORECASE
)
_ARIA_LABEL_RE = re.compile(
    r'aria-label\s*=\s*["\']', re.IGNORECASE
)
_ARIA_LABELLEDBY_RE = re.compile(
    r'aria-labelledby\s*=\s*["\']', re.IGNORECASE
)
_ID_ATTR_RE = re.compile(r'\bid\s*=\s*["\'](\w+)["\']', re.IGNORECASE)

_HEADING_TAG_RE = re.compile(r"<(h[1-6])\b[^>]*>", re.IGNORECASE)
_H1_RE = re.compile(r"<h1\b", re.IGNORECASE)

_LINK_TAG_RE = re.compile(r"<a\s[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_LINK_ARIA_LABEL_RE = re.compile(r'aria-label\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE)
_LINK_INNER_TAG_RE = re.compile(r"<[^>]+>")

_HTML_TAG_RE = re.compile(r"<html\b", re.IGNORECASE)
_HTML_LANG_RE = re.compile(r"<html\s[^>]*lang\s*=", re.IGNORECASE)

_DIV_BUTTON_ROLE_RE = re.compile(
    r'<div\s[^>]*role\s*=\s*["\']button["\'][^>]*>', re.IGNORECASE
)
_TABINDEX_RE = re.compile(r'tabindex\s*=', re.IGNORECASE)

_TABLE_TAG_RE = re.compile(r"<table\b[^>]*>", re.IGNORECASE)
_TH_RE = re.compile(r"<th\b", re.IGNORECASE)
_SCOPE_RE = re.compile(r'scope\s*=', re.IGNORECASE)
_TD_RE = re.compile(r"<td\b", re.IGNORECASE)


def audit_content(content: str, content_type: str = "html") -> AuditResult:
    """Audit content for WCAG 2.2 AA accessibility compliance.

    Scans the provided content for common accessibility issues based on
    content_type. For HTML content, checks for missing alt text, form labels,
    heading hierarchy, ARIA usage, and more. For plain text, performs
    basic readability and structure checks.

    Args:
        content: The content string to audit.
        content_type: Type of content — "html" or "text".

    Returns:
        AuditResult with all discovered issues, compliance score, and WCAG level.
    """
    if not content.strip():
        return AuditResult(issues=[], compliance_score=100.0, wcag_level="AA")

    if content_type.lower() == "html":
        return _audit_html(content)
    return _audit_text(content)


def _audit_html(content: str) -> AuditResult:
    """Audit HTML content for accessibility issues."""
    issues: list[AccessibilityIssue] = []

    issues.extend(_check_images(content))
    issues.extend(_check_forms(content))
    issues.extend(_check_headings(content))
    issues.extend(_check_links(content))
    issues.extend(_check_language(content))
    issues.extend(_check_aria(content))
    issues.extend(_check_tables(content))

    score, level = _compute_compliance(issues)
    return AuditResult(issues=issues, compliance_score=score, wcag_level=level)


def _audit_text(content: str) -> AuditResult:
    """Audit plain text content for basic accessibility."""
    issues: list[AccessibilityIssue] = []

    # Check for very long paragraphs (readability)
    paragraphs = content.split("\n\n")
    for i, para in enumerate(paragraphs):
        if len(para) > 1000:
            issues.append(
                AccessibilityIssue(
                    criterion="1.4.8",
                    level="AAA",
                    element=f"paragraph {i + 1}",
                    description="Paragraph exceeds 1000 characters, may reduce readability",
                    fix_suggestion="Break into shorter paragraphs",
                )
            )

    score, level = _compute_compliance(issues)
    return AuditResult(issues=issues, compliance_score=score, wcag_level=level)


def _check_images(html: str) -> list[AccessibilityIssue]:
    """Check img elements for alt text (WCAG 1.1.1)."""
    issues: list[AccessibilityIssue] = []

    for match in _IMG_TAG_RE.finditer(html):
        img_tag = match.group(0)
        has_alt = _IMG_ALT_RE.search(img_tag)
        alt_empty = _IMG_ALT_EMPTY_RE.search(img_tag)
        if not has_alt:
            issues.append(
                AccessibilityIssue(
                    criterion="1.1.1",
                    level="A",
                    element=img_tag[:50],
                    description="Image missing alt attribute",
                    fix_suggestion='Add alt="description" to the img element',
                )
            )
        elif alt_empty:
            # Empty alt is acceptable for decorative images, but flag for review
            if not _IMG_ROLE_PRESENTATION_RE.search(img_tag):
                issues.append(
                    AccessibilityIssue(
                        criterion="1.1.1",
                        level="A",
                        element=img_tag[:50],
                        description="Image has empty alt but no role='presentation'",
                        fix_suggestion="Add role='presentation' for decorative images or provide meaningful alt text",
                    )
                )
    return issues


def _check_forms(html: str) -> list[AccessibilityIssue]:
    """Check form elements for labels (WCAG 1.3.1, 3.3.2)."""
    issues: list[AccessibilityIssue] = []
    # Skip hidden, submit, reset, button types
    skip_types = {"hidden", "submit", "reset", "button"}

    for match in _INPUT_TAG_RE.finditer(html):
        input_type = match.group(1).lower()
        if input_type in skip_types:
            continue

        input_tag = match.group(0)
        has_aria_label = _ARIA_LABEL_RE.search(input_tag)
        has_aria_labelledby = _ARIA_LABELLEDBY_RE.search(input_tag)
        has_id = _ID_ATTR_RE.search(input_tag)

        if has_aria_label or has_aria_labelledby:
            continue

        if has_id:
            id_val = has_id.group(1)
            if re.search(
                rf'<label\s[^>]*for\s*=\s*["\']({ id_val })["\']', html, re.IGNORECASE
            ):
                continue

        issues.append(
            AccessibilityIssue(
                criterion="1.3.1",
                level="A",
                element=input_tag[:50],
                description=f"Form input (type={input_type}) missing associated label",
                fix_suggestion="Add a <label> element with 'for' matching the input's 'id', or use aria-label",
            )
        )
    return issues


def _check_headings(html: str) -> list[AccessibilityIssue]:
    """Check heading hierarchy (WCAG 1.3.1, 2.4.6)."""
    issues: list[AccessibilityIssue] = []
    headings = _HEADING_TAG_RE.findall(html)

    if not headings:
        return issues

    prev_level = 0
    for h in headings:
        level = int(h[1])
        if prev_level > 0 and level > prev_level + 1:
            issues.append(
                AccessibilityIssue(
                    criterion="1.3.1",
                    level="A",
                    element=f"<{h}>",
                    description=f"Heading level skipped: {prev_level} -> {level}",
                    fix_suggestion="Use sequential heading levels (h1->h2->h3) without skipping",
                )
            )
        prev_level = level

    if not _H1_RE.search(html):
        issues.append(
            AccessibilityIssue(
                criterion="2.4.6",
                level="AA",
                element="<head>",
                description="Missing h1 heading — page should have exactly one h1",
                fix_suggestion="Add an h1 element as the main page heading",
            )
        )
    return issues


def _check_links(html: str) -> list[AccessibilityIssue]:
    """Check links for accessible text (WCAG 2.4.4)."""
    issues: list[AccessibilityIssue] = []

    for match in _LINK_TAG_RE.finditer(html):
        link_text = _LINK_INNER_TAG_RE.sub("", match.group(1)).strip()
        aria_label = _LINK_ARIA_LABEL_RE.search(match.group(0))
        if not link_text and not aria_label:
            issues.append(
                AccessibilityIssue(
                    criterion="2.4.4",
                    level="A",
                    element=match.group(0)[:60],
                    description="Link has no accessible text",
                    fix_suggestion="Add visible link text or an aria-label attribute",
                )
            )

    # Check for ambiguous link text
    ambiguous = {"click here", "here", "read more", "more", "link"}
    for match in _LINK_TAG_RE.finditer(html):
        link_text = _LINK_INNER_TAG_RE.sub("", match.group(1)).strip().lower()
        if link_text in ambiguous:
            issues.append(
                AccessibilityIssue(
                    criterion="2.4.4",
                    level="A",
                    element=match.group(0)[:60],
                    description=f"Ambiguous link text: '{link_text}'",
                    fix_suggestion="Use descriptive link text that makes sense out of context",
                )
            )
    return issues


def _check_language(html: str) -> list[AccessibilityIssue]:
    """Check for lang attribute on html element (WCAG 3.1.1)."""
    issues: list[AccessibilityIssue] = []
    if _HTML_TAG_RE.search(html) and not _HTML_LANG_RE.search(html):
        issues.append(
            AccessibilityIssue(
                criterion="3.1.1",
                level="A",
                element="<html>",
                description="Missing lang attribute on html element",
                fix_suggestion='Add lang="en" (or appropriate language code) to the html element',
            )
        )
    return issues


def _check_aria(html: str) -> list[AccessibilityIssue]:
    """Check for common ARIA misuse (WCAG 4.1.2)."""
    issues: list[AccessibilityIssue] = []
    # Check for role on elements that have implicit roles
    div_with_button_role = _DIV_BUTTON_ROLE_RE.findall(html)
    for elem in div_with_button_role:
        if not _TABINDEX_RE.search(elem):
            issues.append(
                AccessibilityIssue(
                    criterion="4.1.2",
                    level="A",
                    element=elem[:50],
                    description="Element with role='button' missing tabindex for keyboard access",
                    fix_suggestion="Add tabindex='0' to make the element keyboard-focusable",
                )
            )
    return issues


def _check_tables(html: str) -> list[AccessibilityIssue]:
    """Check tables for headers (WCAG 1.3.1)."""
    issues: list[AccessibilityIssue] = []
    for match in _TABLE_TAG_RE.finditer(html):
        table_start = match.start()
        # Find the end of this table
        table_end = html.find("</table>", table_start)
        if table_end == -1:
            continue
        table_content = html[table_start : table_end + len("</table>")]
        has_th = _TH_RE.search(table_content)
        has_scope = _SCOPE_RE.search(table_content)
        if not has_th and _TD_RE.search(table_content):
            issues.append(
                AccessibilityIssue(
                    criterion="1.3.1",
                    level="A",
                    element="<table>",
                    description="Data table missing header cells (<th>)",
                    fix_suggestion="Use <th> elements for header cells with appropriate scope attributes",
                )
            )
    return issues


def _compute_compliance(issues: list[AccessibilityIssue]) -> tuple[float, str]:
    """Compute compliance score and WCAG level from issues.

    Args:
        issues: All discovered accessibility issues.

    Returns:
        Tuple of (score, level) where score is 0-100 and level is "AA", "A", or "None".
    """
    if not issues:
        return 100.0, "AA"

    level_a_issues = sum(1 for i in issues if i.level == "A")
    level_aa_issues = sum(1 for i in issues if i.level == "AA")
    total = len(issues)

    # Score: start at 100, deduct per issue
    deduction = (level_a_issues * 5) + (level_aa_issues * 3)
    score = max(0.0, 100.0 - deduction)

    if level_a_issues > 0:
        wcag_level = "None"  # Must pass all Level A to claim A
    elif level_aa_issues > 0:
        wcag_level = "A"  # Passes A but not AA
    else:
        wcag_level = "AA"

    return score, wcag_level
