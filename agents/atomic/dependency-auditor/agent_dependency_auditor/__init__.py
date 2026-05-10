"""agent-dependency-auditor — Dependency vulnerability scanner for Python projects.

Parses requirements.txt and pyproject.toml, checks for known CVE patterns,
and generates structured audit reports with severity levels and fix suggestions.
"""

from agent_dependency_auditor.agent import DependencyAuditorAgent
from agent_dependency_auditor.models import (
    AuditReport,
    DependencyVulnerability,
)

__all__ = [
    "DependencyAuditorAgent",
    "DependencyVulnerability",
    "AuditReport",
]
