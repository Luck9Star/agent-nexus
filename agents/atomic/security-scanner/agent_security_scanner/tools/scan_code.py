"""Code scanning tool — detect security vulnerabilities using pattern matching.

Scans source code for OWASP Top 10 vulnerabilities including SQL injection,
XSS, path traversal, command injection, and hardcoded credentials.
"""

from __future__ import annotations

import bisect
import re
from pathlib import Path

from agent_security_scanner.models import SecurityFinding, SecurityScanResult

# Vulnerability detection rules: (pattern, category, cwe_id, severity, description, remediation)
_SQL_INJECTION_PATTERNS: list[tuple[str, str, str, str, str, str]] = [
    (
        r'(?:execute|cursor\.execute)\s*\(\s*["\'].*(?:%s|\bformat\b|\bf["\'])',
        "injection",
        "CWE-89",
        "critical",
        "Potential SQL injection via string formatting in query execution",
        "Use parameterized queries with placeholders instead of string formatting",
    ),
    (
        r"(?:SELECT|INSERT|UPDATE|DELETE)\s+.*(?:\+\s*\w+|\.format\()",
        "injection",
        "CWE-89",
        "high",
        "Potential SQL injection via string concatenation or format",
        "Use parameterized queries and never concatenate user input into SQL",
    ),
    (
        r'f["\'].*(?:SELECT|INSERT|UPDATE|DELETE)',
        "injection",
        "CWE-89",
        "high",
        "Potential SQL injection via f-string in SQL statement",
        "Avoid f-strings for SQL queries; use parameterized queries instead",
    ),
]

_XSS_PATTERNS: list[tuple[str, str, str, str, str, str]] = [
    (
        r"(?:render_template_string|Markup)\s*\(.*(?:request\.|user_input)",
        "xss",
        "CWE-79",
        "high",
        "Potential XSS via unescaped user input in template rendering",
        "Always escape user input; use template auto-escaping",
    ),
    (
        r"\.innerHTML\s*=.*(?:document\.|user_input|params)",
        "xss",
        "CWE-79",
        "high",
        "Potential XSS via innerHTML assignment with user input",
        "Use textContent instead of innerHTML, or sanitize input with DOMPurify",
    ),
]

_PATH_TRAVERSAL_PATTERNS: list[tuple[str, str, str, str, str, str]] = [
    (
        r"open\s*\(\s*(?:request\.|user_input|os\.path\.join\(.*request)",
        "path_traversal",
        "CWE-22",
        "high",
        "Potential path traversal via user-controlled file path",
        "Validate and sanitize file paths; use allowlists for permitted directories",
    ),
    (
        r"\.\./|\.\.\\",
        "path_traversal",
        "CWE-22",
        "medium",
        "Potential directory traversal sequence detected",
        "Reject paths containing traversal sequences; use os.path.realpath to normalize",
    ),
]

_COMMAND_INJECTION_PATTERNS: list[tuple[str, str, str, str, str, str]] = [
    (
        r"(?:os\.system|subprocess\.(?:call|run|Popen))\s*\(.*(?:request\.|user_input|input\()",
        "command_injection",
        "CWE-78",
        "critical",
        "Potential command injection via user input in shell command",
        "Avoid shell commands with user input; use subprocess with shell=False and list args",
    ),
    (
        r"(?:eval|exec)\s*\(.*(?:request\.|user_input|input\()",
        "command_injection",
        "CWE-78",
        "critical",
        "Potential code injection via eval/exec with user input",
        "Never use eval/exec with untrusted input; use ast.literal_eval for simple parsing",
    ),
]

_HARDCODED_CREDENTIALS_PATTERNS: list[tuple[str, str, str, str, str, str]] = [
    (
        r'(?:password|passwd|pwd|secret|api_key|apikey|token)\s*=\s*["\'][^"\']{4,}["\']',
        "hardcoded_credentials",
        "CWE-798",
        "high",
        "Hardcoded credential detected in source code",
        "Move credentials to environment variables or a secrets manager",
    ),
    (
        r'(?:Authorization|Bearer)\s*:\s*["\'][^"\']{8,}["\']',
        "hardcoded_credentials",
        "CWE-798",
        "high",
        "Hardcoded authorization token detected",
        "Use environment variables or configuration files for tokens",
    ),
]

_ALL_RULES = (
    _SQL_INJECTION_PATTERNS
    + _XSS_PATTERNS
    + _PATH_TRAVERSAL_PATTERNS
    + _COMMAND_INJECTION_PATTERNS
    + _HARDCODED_CREDENTIALS_PATTERNS
)

# Pre-compiled rules to avoid recompilation on every line x rule match
_COMPILED_RULES: list[tuple[re.Pattern[str], str, str, str, str, str]] = [
    (re.compile(p, re.IGNORECASE), cat, cwe, sev, desc, rem)
    for p, cat, cwe, sev, desc, rem in _ALL_RULES
]


def _severity_rank(severity: str) -> int:
    """Return numeric rank for severity (higher = more severe)."""
    return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(severity, 0)


def scan_code(file_path: str) -> SecurityScanResult:
    """Scan a file for security vulnerabilities.

    Uses pattern-based detection to identify common vulnerability patterns
    including SQL injection, XSS, path traversal, command injection, and
    hardcoded credentials.

    Args:
        file_path: Path to the source file to scan.

    Returns:
        SecurityScanResult with all findings and a severity summary.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    if path.is_dir():
        return _scan_directory(path)
    return _scan_file(path)


def _scan_file(path: Path) -> SecurityScanResult:
    """Scan a single file for vulnerabilities."""
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return SecurityScanResult()

    # Build line offset table once for O(log n) line-number lookup
    line_offsets = [0]
    for i, ch in enumerate(content):
        if ch == "\n":
            line_offsets.append(i + 1)

    findings: list[SecurityFinding] = []
    for compiled_pattern, category, cwe_id, severity, description, remediation in _COMPILED_RULES:
        for match in compiled_pattern.finditer(content):
            line_no = bisect.bisect_right(line_offsets, match.start())
            findings.append(
                SecurityFinding(
                    severity=severity,
                    category=category,
                    location=f"{path}:{line_no}",
                    description=description,
                    remediation=remediation,
                    cwe_id=cwe_id,
                )
            )

    # Sort by severity (most severe first)
    findings.sort(key=lambda f: _severity_rank(f.severity), reverse=True)

    summary = _build_summary(findings)
    return SecurityScanResult(findings=findings, summary=summary)


def _scan_directory(dir_path: Path) -> SecurityScanResult:
    """Scan all Python files in a directory."""
    all_findings: list[SecurityFinding] = []
    for py_file in dir_path.rglob("*.py"):
        result = _scan_file(py_file)
        all_findings.extend(result.findings)

    all_findings.sort(key=lambda f: _severity_rank(f.severity), reverse=True)
    summary = _build_summary(all_findings)
    return SecurityScanResult(findings=all_findings, summary=summary)


def _build_summary(findings: list[SecurityFinding]) -> dict:
    """Build severity summary from findings."""
    summary: dict[str, int] = {
        "critical": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "total": len(findings),
    }
    for f in findings:
        key = f.severity.lower()
        if key in summary:
            summary[key] += 1
    return summary
