"""agent-security-scanner — Application security scanning specialist.

Scans code for OWASP Top 10 vulnerabilities, checks dependencies for known CVEs,
and generates structured security reports with severity ratings and remediation.
"""

from agent_security_scanner.agent import SecurityScannerAgent
from agent_security_scanner.models import (
    DependencyReport,
    DependencyVulnerability,
    SecurityFinding,
    SecurityReport,
    SecurityScanResult,
)

__all__ = [
    "SecurityScannerAgent",
    "SecurityFinding",
    "SecurityScanResult",
    "DependencyVulnerability",
    "DependencyReport",
    "SecurityReport",
]
