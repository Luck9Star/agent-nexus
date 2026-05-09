"""Pattern checking tool -- detect anti-patterns in code.

Identifies common anti-patterns across multiple languages:
security issues, performance problems, and maintainability concerns.
"""

from __future__ import annotations

import bisect
import re

from agent_code_reviewer.models import PatternMatch

# Anti-pattern definitions
ANTI_PATTERNS: list[dict[str, str | re.Pattern[str]]] = [
    # Security patterns
    {
        "name": "sql_injection",
        "pattern": re.compile(
            r"(?:execute|cursor\.execute|query)\s*\(\s*[\"'].*%[sd].*[\"']\s*%|"
            r"(?:execute|cursor\.execute|query)\s*\(\s*f[\"']",
        ),
        "severity": "critical",
        "description": "Potential SQL injection: string formatting in SQL query",
    },
    {
        "name": "hardcoded_secret",
        "pattern": re.compile(
            r"(?:password|passwd|secret|api[_-]?key|token|auth)"
            r"\s*[:=]\s*[\"'][^\"']{8,}[\"']",
            re.IGNORECASE,
        ),
        "severity": "critical",
        "description": "Hardcoded secret or credential found in source code",
    },
    {
        "name": "insecure_random",
        "pattern": re.compile(r"\brandom\.(?:random|randint|choice|shuffle)\s*\("),
        "severity": "warning",
        "description": (
            "Using non-cryptographic random for potentially security-sensitive operations"
        ),
    },
    # Performance patterns
    {
        "name": "n_plus_one",
        "pattern": re.compile(
            r"for\s+\w+\s+in\s+.*:\s*\n(?:.*\n){0,5}.*(?:query|fetch|get|find|select)\s*\(",
        ),
        "severity": "warning",
        "description": "Potential N+1 query pattern: database query inside a loop",
    },
    {
        "name": "unnecessary_list_comp",
        "pattern": re.compile(r"\[\s*\w+\s+for\s+\w+\s+in\s+.*\]\s*"),
        "severity": "info",
        "description": (
            "List comprehension used without storing result; consider generator expression"
        ),
    },
    {
        "name": "string_concat_in_loop",
        "pattern": re.compile(r"(?:for|while)\s+.*:\s*\n(?:.*\n){0,3}.*\+="),
        "severity": "warning",
        "description": (
            "String concatenation in loop; use join() or StringIO for better performance"
        ),
    },
    # Maintainability patterns
    {
        "name": "magic_number",
        "pattern": re.compile(r"(?<![.\w])\d{2,}(?![.\w])"),
        "severity": "info",
        "description": "Magic number found; consider extracting to a named constant",
    },
    {
        "name": "deep_nesting",
        "pattern": re.compile(r"(?:    |\t){4,}\S"),
        "severity": "warning",
        "description": (
            "Deep nesting detected (4+ levels); consider extracting to helper functions"
        ),
    },
    {
        "name": "long_function",
        "pattern": re.compile(
            r"(?:def|function|fn)\s+\w+[^{]*(?:\{|:)\s*\n(?:.*\n){49,}",
        ),
        "severity": "warning",
        "description": (
            "Function is very long (50+ lines); consider breaking into smaller functions"
        ),
    },
    {
        "name": "god_class",
        "pattern": re.compile(
            r"(?:class|struct)\s+\w+[^{]*(?:\{|:)\s*\n(?:.*\n){199,}",
        ),
        "severity": "warning",
        "description": "Very large class/struct (200+ lines); may have too many responsibilities",
    },
    # Error handling patterns
    {
        "name": "empty_catch",
        "pattern": re.compile(r"catch\s*\([^)]*\)\s*\{\s*\}|except\s*[^:]*:\s*pass"),
        "severity": "warning",
        "description": "Empty catch/except block; errors are silently swallowed",
    },
    {
        "name": "broad_exception",
        "pattern": re.compile(r"catch\s*\(\s*Exception\s|except\s+Exception\s*:|except\s*:"),
        "severity": "warning",
        "description": "Catching broad Exception type; consider catching specific exceptions",
    },
]


def check_patterns(code: str, language: str = "") -> list[PatternMatch]:
    """Detect anti-patterns in code.

    Scans the code for known anti-patterns including security issues,
    performance problems, and maintainability concerns.

    Args:
        code: The source code to scan.
        language: Programming language hint (affects which patterns apply).

    Returns:
        List of PatternMatch objects for each detected pattern.
    """
    if not code or not code.strip():
        return []

    matches: list[PatternMatch] = []

    # Pre-compute line start offsets for O(log n) line-number lookup
    line_offsets = [0]
    for i, ch in enumerate(code):
        if ch == "\n":
            line_offsets.append(i + 1)

    for pattern_def in ANTI_PATTERNS:
        pattern = pattern_def["pattern"]
        assert isinstance(pattern, re.Pattern)

        for match in pattern.finditer(code):
            line_num = bisect.bisect_right(line_offsets, match.start()) - 1
            matches.append(
                PatternMatch(
                    pattern=pattern_def["name"],
                    line=line_num,
                    severity=pattern_def["severity"],
                    description=pattern_def["description"],
                )
            )

    return matches
