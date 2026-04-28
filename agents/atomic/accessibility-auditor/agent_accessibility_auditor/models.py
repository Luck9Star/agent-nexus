"""Data models for accessibility-auditor Agent.

Pydantic v2 frozen models for accessibility auditing, issue tracking,
and remediation planning.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AccessibilityIssue(BaseModel):
    """A single accessibility issue found during auditing.

    Attributes:
        criterion: WCAG success criterion number (e.g. "1.1.1", "2.4.3").
        level: WCAG conformance level — "A" or "AA".
        element: The HTML element or content area where the issue was found.
        description: Human-readable description of the issue.
        fix_suggestion: Suggested code fix or remediation action.
    """

    model_config = ConfigDict(frozen=True)

    criterion: str
    level: str = "A"
    element: str = ""
    description: str = ""
    fix_suggestion: str = ""


class AuditResult(BaseModel):
    """Result of auditing content for accessibility compliance.

    Attributes:
        issues: All accessibility issues discovered during the audit.
        compliance_score: Overall compliance score from 0 to 100.
        wcag_level: Highest achieved conformance level — "A", "AA", or "None".
    """

    model_config = ConfigDict(frozen=True)

    issues: list[AccessibilityIssue] = Field(default_factory=list)
    compliance_score: float = 100.0
    wcag_level: str = "AA"


class RemediationPlan(BaseModel):
    """Prioritized plan for fixing accessibility issues.

    Attributes:
        issues: All issues with fix suggestions, ordered by priority.
        priority_order: Ordered list of criterion numbers to fix first.
        estimated_effort: Estimated effort description (e.g. "2-4 hours").
    """

    model_config = ConfigDict(frozen=True)

    issues: list[AccessibilityIssue] = Field(default_factory=list)
    priority_order: list[str] = Field(default_factory=list)
    estimated_effort: str = "TBD"
