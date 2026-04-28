"""SecurityScannerAgent — Application security scanning specialist.

Three-phase pipeline:
  1. scan_code()       — detect vulnerabilities in source code
  2. check_dependencies() — check known CVEs in dependencies
  3. generate_report() — compile findings into a structured report

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_security_scanner.models import (
    DependencyReport,
    SecurityReport,
    SecurityScanResult,
)
from agent_security_scanner.tools.check_dependencies import check_dependencies as _check_deps
from agent_security_scanner.tools.generate_report import generate_report as _gen_report
from agent_security_scanner.tools.scan_code import scan_code as _scan_code


class SecurityScannerAgent:
    """Application security scanning specialist.

    This agent provides a three-phase pipeline for security analysis:
    Phase 1 (scan_code) scans source files for vulnerability patterns.
    Phase 2 (check_dependencies) checks declared dependencies against a CVE
    database. Phase 3 (generate_report) compiles all findings into a
    comprehensive security report with prioritized recommendations.

    Usage:
        agent = SecurityScannerAgent()
        scan = agent.scan_code("app.py")
        deps = agent.check_dependencies({"flask": "2.0.1"})
        all_findings = scan.findings + [
            SecurityFinding(severity=v.severity, category="dependency",
                           location=v.package, cwe_id=v.cve)
            for v in deps.vulnerabilities
        ]
        report = agent.generate_report(all_findings)
        print(report.critical_count, report.high_count)
    """

    def scan_code(self, file_path: str) -> SecurityScanResult:
        """Phase 1: Scan source code for security vulnerabilities.

        Scans a file or directory for OWASP Top 10 vulnerability patterns
        including SQL injection, XSS, path traversal, command injection,
        and hardcoded credentials.

        Args:
            file_path: Path to the source file or directory to scan.

        Returns:
            SecurityScanResult with all findings and severity summary.

        Raises:
            FileNotFoundError: If the file or directory does not exist.
        """
        return _scan_code(file_path)

    def check_dependencies(self, deps: dict) -> DependencyReport:
        """Phase 2: Check project dependencies for known CVEs.

        Compares declared dependency versions against the built-in CVE
        database and reports any known vulnerabilities.

        Args:
            deps: Mapping of package names to version strings.

        Returns:
            DependencyReport with vulnerabilities and scan statistics.
        """
        return _check_deps(deps)

    def generate_report(self, findings: list) -> SecurityReport:
        """Phase 3: Compile findings into a structured security report.

        Aggregates all findings by severity, generates prioritized
        remediation recommendations.

        Args:
            findings: List of SecurityFinding objects or dicts.

        Returns:
            SecurityReport with severity counts and recommendations.
        """
        return _gen_report(findings)
