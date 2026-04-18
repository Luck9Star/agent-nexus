"""Content auditing tool — check text/markup content against WCAG criteria.

Performs accessibility checks on general content, computing a compliance
score and identifying specific WCAG 2.2 AA violations.
"""

from __future__ import annotations

import re

from agent_accessibility_auditor.models import AccessibilityIssue, AuditResult


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
    img_pattern = re.compile(r"<img\s[^>]*>", re.IGNORECASE)

    for match in img_pattern.finditer(html):
        img_tag = match.group(0)
        has_alt = re.search(r'\balt\s*=\s*["\']', img_tag, re.IGNORECASE)
        alt_empty = re.search(r'\balt\s*=\s*["\']["\']', img_tag, re.IGNORECASE)
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
            role_present = re.search(r'\brole\s*=\s*["\']presentation["\']', img_tag, re.IGNORECASE)
            if not role_present:
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
    input_pattern = re.compile(
        r"<input\s[^>]*type\s*=\s*['\"]?(\w+)['\"]?[^>]*>", re.IGNORECASE
    )
    # Skip hidden, submit, reset, button types
    skip_types = {"hidden", "submit", "reset", "button"}

    for match in input_pattern.finditer(html):
        input_type = match.group(1).lower()
        if input_type in skip_types:
            continue

        input_tag = match.group(0)
        has_label_for = re.search(
            r'<label\s[^>]*for\s*=\s*["\'](\w+)["\']', html, re.IGNORECASE
        )
        has_aria_label = re.search(
            r'aria-label\s*=\s*["\']', input_tag, re.IGNORECASE
        )
        has_aria_labelledby = re.search(
            r'aria-labelledby\s*=\s*["\']', input_tag, re.IGNORECASE
        )
        has_id = re.search(r'\bid\s*=\s*["\'](\w+)["\']', input_tag, re.IGNORECASE)

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
    heading_pattern = re.compile(r"<(h[1-6])\b[^>]*>", re.IGNORECASE)
    headings = heading_pattern.findall(html)

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

    if not re.search(r"<h1\b", html, re.IGNORECASE):
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
    link_pattern = re.compile(r"<a\s[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)

    for match in link_pattern.finditer(html):
        link_text = re.sub(r"<[^>]+>", "", match.group(1)).strip()
        aria_label = re.search(r'aria-label\s*=\s*["\']([^"\']*)["\']', match.group(0), re.IGNORECASE)
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
    for match in link_pattern.finditer(html):
        link_text = re.sub(r"<[^>]+>", "", match.group(1)).strip().lower()
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
    if re.search(r"<html\b", html, re.IGNORECASE) and not re.search(
        r"<html\s[^>]*lang\s*=", html, re.IGNORECASE
    ):
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
    div_with_button_role = re.findall(
        r'<div\s[^>]*role\s*=\s*["\']button["\'][^>]*>', html, re.IGNORECASE
    )
    for elem in div_with_button_role:
        if not re.search(r'tabindex\s*=', elem, re.IGNORECASE):
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
    table_pattern = re.compile(r"<table\b[^>]*>", re.IGNORECASE)
    for match in table_pattern.finditer(html):
        table_start = match.start()
        # Find the end of this table
        table_end = html.find("</table>", table_start)
        if table_end == -1:
            continue
        table_content = html[table_start : table_end + len("</table>")]
        has_th = re.search(r"<th\b", table_content, re.IGNORECASE)
        has_scope = re.search(r'scope\s*=', table_content, re.IGNORECASE)
        if not has_th and re.search(r"<td\b", table_content, re.IGNORECASE):
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
