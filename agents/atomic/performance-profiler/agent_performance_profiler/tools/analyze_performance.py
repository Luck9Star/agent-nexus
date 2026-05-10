"""Performance analysis tool — detect common performance anti-patterns.

Scans Python source code for:
- N+1 query patterns (database queries inside loops)
- String concatenation in loops
- List concatenation in loops
- Synchronous I/O inside loops
- Large range literals
"""

from __future__ import annotations

import re
from collections import Counter

from agent_performance_profiler.models import PerformanceFinding, PerformanceReport

# Detection rules: (pattern, category, severity, description, remediation, complexity)
_RULES: list[tuple[str, str, str, str, str, str]] = [
    # N+1 query patterns
    (
        r"for\s+\w+\s+in\s+.*:\s*\n.*(?:\.query\(|\.execute\(|\.get\(|\.filter\(|\.all\(\)|\.find\()",
        "n_plus_one",
        "critical",
        "Database query inside loop — potential N+1 query pattern",
        "Fetch all data in a single query using JOIN or batch fetch",
        "O(n)",
    ),
    (
        r"while\s+.*:\s*\n.*(?:\.query\(|\.execute\(|\.get\(|\.filter\()",
        "n_plus_one",
        "high",
        "Database query inside while loop",
        "Batch database operations outside the loop",
        "O(n)",
    ),
    # String concatenation in loops
    (
        r"(?:for|while)\s+.*:\s*\n.*\w+\s*\+=\s*['\"]",
        "inefficient_loop",
        "medium",
        "String concatenation in loop — creates new string each iteration",
        "Use list append + ''.join() for string building",
        "O(n^2)",
    ),
    # List concatenation in loops
    (
        r"(?:for|while)\s+.*:\s*\n.*\w+\s*=\s*\w+\s*\+\s*\[",
        "inefficient_loop",
        "high",
        "List concatenation in loop — copies entire list each iteration",
        "Use list.extend() or list comprehension",
        "O(n^2)",
    ),
    (
        r"(?:for|while)\s+.*:\s*\n.*\w+\s*\+=\s*\[",
        "inefficient_loop",
        "medium",
        "List += in loop — consider list comprehension",
        "Use list comprehension or extend() for better performance",
        "O(n)",
    ),
    # Synchronous I/O in loops
    (
        r"(?:for|while)\s+.*:\s*\n.*(?:open\(|\.read\(\)|\.write\(\)|requests\.|urllib)",
        "inefficient_loop",
        "high",
        "I/O operation inside loop — blocking I/O degrades performance",
        "Use batch I/O or async operations",
        "O(n)",
    ),
    # Nested loops
    (
        r"for\s+\w+\s+in\s+.*:\s*\n(\s+.+\n)*?\s+for\s+\w+\s+in\s+.*:",
        "inefficient_loop",
        "medium",
        "Nested loop detected — consider algorithmic optimization",
        "Use a hash map or set for O(1) lookups instead of nested iteration",
        "O(n^2)",
    ),
    # Large range literals
    (
        r"range\s*\(\s*\d{5,}\s*\)",
        "memory_inefficient",
        "medium",
        "Large range with materialization risk",
        "Use generator expressions or itertools for large ranges",
        "O(n)",
    ),
    # Dict/list creation in loops (memory)
    (
        r"(?:for|while)\s+.*:\s*\n.*=\s*\{.*for\s+",
        "memory_inefficient",
        "low",
        "Dict comprehension inside loop may create unnecessary copies",
        "Build the dict once outside the loop if possible",
        "O(n)",
    ),
]

# Pre-compiled patterns
_COMPILED_RULES: list[tuple[re.Pattern[str], str, str, str, str, str]] = [
    (re.compile(p, re.MULTILINE), cat, sev, desc, rem, cx) for p, cat, sev, desc, rem, cx in _RULES
]


def analyze_performance(source_code: str) -> PerformanceReport:
    """Analyze source code for performance anti-patterns.

    Uses pattern-based detection to identify common performance issues
    including N+1 queries, inefficient loops, and memory-inefficient operations.

    Args:
        source_code: Python source code to analyze.

    Returns:
        PerformanceReport with all findings, severity counts, and recommendations.
    """
    if not source_code or not source_code.strip():
        return PerformanceReport(lines_analyzed=0)

    lines = source_code.split("\n")
    findings: list[PerformanceFinding] = []

    # Build line offset table for accurate line numbers
    line_offsets = [0]
    for i, ch in enumerate(source_code):
        if ch == "\n":
            line_offsets.append(i + 1)

    import bisect

    for compiled, category, severity, description, remediation, complexity in _COMPILED_RULES:
        for match in compiled.finditer(source_code):
            line_no = bisect.bisect_right(line_offsets, match.start())
            findings.append(
                PerformanceFinding(
                    severity=severity,
                    category=category,
                    location=f"line {line_no}",
                    description=description,
                    remediation=remediation,
                    complexity=complexity,
                )
            )

    # Sort by severity
    findings.sort(key=lambda f: _severity_rank(f.severity), reverse=True)

    # Build counts
    sev_counts = Counter(f.severity.lower() for f in findings)
    recommendations = _generate_recommendations(findings)

    return PerformanceReport(
        critical_count=sev_counts.get("critical", 0),
        high_count=sev_counts.get("high", 0),
        medium_count=sev_counts.get("medium", 0),
        low_count=sev_counts.get("low", 0),
        findings=findings,
        recommendations=recommendations,
        lines_analyzed=len(lines),
    )


def _severity_rank(severity: str) -> int:
    """Return numeric rank for severity (higher = more severe)."""
    return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(severity, 0)


def _generate_recommendations(findings: list[PerformanceFinding]) -> list[str]:
    """Generate prioritized recommendations from findings."""
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    sorted_findings = sorted(findings, key=lambda f: severity_order.get(f.severity.lower(), 99))

    recommendations: list[str] = []
    for finding in sorted_findings:
        prefix = f"[{finding.severity.upper()}] {finding.location}: "
        rec = finding.remediation or f"Review {finding.category} issue"
        recommendations.append(prefix + rec)

    return recommendations
