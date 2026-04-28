"""agent-accessibility-auditor — WCAG 2.2 AA accessibility auditing specialist.

Audits HTML and web content against WCAG 2.2 AA criteria, identifies
accessibility issues, and generates prioritized remediation plans.
"""

from agent_accessibility_auditor.agent import AccessibilityAuditorAgent
from agent_accessibility_auditor.models import (
    AccessibilityIssue,
    AuditResult,
    RemediationPlan,
)

__all__ = [
    "AccessibilityAuditorAgent",
    "AccessibilityIssue",
    "AuditResult",
    "RemediationPlan",
]
