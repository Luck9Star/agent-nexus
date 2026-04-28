"""AccessibilityAuditorAgent — WCAG 2.2 AA accessibility auditing specialist.

Three-phase pipeline:
  1. audit_content()       — check content against WCAG criteria
  2. check_html()          — HTML-specific accessibility checks
  3. generate_remediation() — produce prioritized fix plan

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_accessibility_auditor.models import (
    AccessibilityIssue,
    AuditResult,
    RemediationPlan,
)
from agent_accessibility_auditor.tools.audit_content import audit_content as _audit
from agent_accessibility_auditor.tools.check_html import check_html as _check_html
from agent_accessibility_auditor.tools.generate_remediation import (
    generate_remediation as _remediate,
)


class AccessibilityAuditorAgent:
    """WCAG 2.2 AA accessibility auditing specialist.

    This agent provides a three-phase pipeline for accessibility analysis:
    Phase 1 (audit_content) audits content for WCAG compliance and scores it.
    Phase 2 (check_html) performs detailed HTML-specific accessibility checks.
    Phase 3 (generate_remediation) creates a prioritized fix plan.

    Usage:
        agent = AccessibilityAuditorAgent()
        result = agent.audit_content("<html>...</html>", "html")
        print(result.compliance_score, result.wcag_level)
        issues = agent.check_html("<form>...</form>")
        plan = agent.generate_remediation(result.issues)
        print(plan.priority_order, plan.estimated_effort)
    """

    def audit_content(self, content: str, content_type: str = "html") -> AuditResult:
        """Phase 1: Audit content for WCAG 2.2 AA accessibility compliance.

        Scans the provided content for accessibility issues and computes
        a compliance score and conformance level.

        Args:
            content: The content string to audit.
            content_type: Type of content — "html" or "text".

        Returns:
            AuditResult with issues, compliance score, and WCAG level.
        """
        return _audit(content, content_type)

    def check_html(self, html: str) -> list[AccessibilityIssue]:
        """Phase 2: Perform HTML-specific accessibility checks.

        Comprehensive check of HTML structure covering images, forms,
        headings, links, language, ARIA, and tables.

        Args:
            html: HTML string to check.

        Returns:
            List of AccessibilityIssue objects.
        """
        return _check_html(html)

    def generate_remediation(self, issues: list) -> RemediationPlan:
        """Phase 3: Generate a prioritized remediation plan.

        Orders issues by WCAG level priority and estimates effort.

        Args:
            issues: List of AccessibilityIssue objects or dicts.

        Returns:
            RemediationPlan with prioritized issues and effort estimate.
        """
        return _remediate(issues)
