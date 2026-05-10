"""DependencyAuditorAgent — Dependency vulnerability scanner for Python projects.

Single-phase pipeline:
  audit_dependencies() — parse dependency files, check for known CVEs, return report

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_dependency_auditor.models import AuditReport
from agent_dependency_auditor.tools.audit_dependencies import (
    audit_dependencies as _audit,
)


class DependencyAuditorAgent:
    """Dependency vulnerability scanner for Python projects.

    This agent parses Python dependency declarations (requirements.txt,
    pyproject.toml), compares declared versions against a built-in CVE
    database, and generates structured audit reports.

    Usage:
        agent = DependencyAuditorAgent()
        report = agent.audit_dependencies({"flask": "2.0.1", "requests": "2.25.0"})
        print(report.vulnerable_count, report.summary)
    """

    def audit_dependencies(
        self,
        source: str | dict,
        fmt: str = "auto",
    ) -> AuditReport:
        """Audit dependencies for known vulnerabilities.

        Parses dependency declarations and checks against built-in CVE database.

        Args:
            source: Dependency data as dict {package: version} or file content string.
            fmt: Format hint — "auto", "requirements", "pyproject", or "dict".

        Returns:
            AuditReport with vulnerabilities, counts, and severity summary.
        """
        return _audit(source, fmt)
