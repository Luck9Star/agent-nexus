"""Code analysis tool -- static analysis with multi-language support.

Performs rule-based static analysis using text pattern matching.
Supports Python, JavaScript/TypeScript, Rust, and Java.
"""

from __future__ import annotations

import bisect
import re
from pathlib import Path

from agent_code_reviewer.models import CodeAnalysis, CodeIssue, CodeMetrics

# Language detection from file extension
EXTENSION_LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".go": "go",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
}

# Python-specific rules
PYTHON_RULES: list[dict[str, str | re.Pattern[str]]] = [
    {
        "rule_id": "PY001",
        "pattern": re.compile(r"^(\s*)except\s*:", re.MULTILINE),
        "severity": "warning",
        "category": "bug",
        "message": "Bare except clause catches all exceptions including KeyboardInterrupt",
    },
    {
        "rule_id": "PY002",
        "pattern": re.compile(r"exec\s*\(|eval\s*\("),
        "severity": "critical",
        "category": "security",
        "message": "Use of exec/eval can lead to code injection vulnerabilities",
    },
    {
        "rule_id": "PY003",
        "pattern": re.compile(r"import\s+\*|from\s+\w+\s+import\s+\*"),
        "severity": "warning",
        "category": "style",
        "message": "Wildcard imports pollute namespace and make dependencies unclear",
    },
    {
        "rule_id": "PY004",
        "pattern": re.compile(r"print\s*\("),
        "severity": "info",
        "category": "style",
        "message": "Consider using logging instead of print statements",
    },
    {
        "rule_id": "PY005",
        "pattern": re.compile(r"(?:password|secret|api_key|token)\s*=\s*['\"]"),
        "severity": "critical",
        "category": "security",
        "message": "Hardcoded secret or credential detected",
    },
    {
        "rule_id": "PY006",
        "pattern": re.compile(r"=\s*\[\s*\]"),
        "severity": "info",
        "category": "bug",
        "message": "Mutable default argument detected (potential bug)",
    },
    {
        "rule_id": "PY007",
        "pattern": re.compile(r"#\s*TODO|#\s*FIXME|#\s*HACK", re.IGNORECASE),
        "severity": "info",
        "category": "maintainability",
        "message": "TODO/FIXME/HACK comment found",
    },
]

# JavaScript/TypeScript rules
JS_RULES: list[dict[str, str | re.Pattern[str]]] = [
    {
        "rule_id": "JS001",
        "pattern": re.compile(r"console\.log\s*\("),
        "severity": "info",
        "category": "style",
        "message": "console.log statement found (remove before production)",
    },
    {
        "rule_id": "JS002",
        "pattern": re.compile(r"var\s+\w+"),
        "severity": "warning",
        "category": "style",
        "message": "Use const or let instead of var",
    },
    {
        "rule_id": "JS003",
        "pattern": re.compile(r"==(?!=)|!=(?!=)"),
        "severity": "warning",
        "category": "bug",
        "message": "Use === or !== for strict equality comparison",
    },
    {
        "rule_id": "JS004",
        "pattern": re.compile(r"(?:password|secret|api_key|token)\s*[:=]\s*['\"]"),
        "severity": "critical",
        "category": "security",
        "message": "Hardcoded secret or credential detected",
    },
    {
        "rule_id": "JS005",
        "pattern": re.compile(r"eval\s*\("),
        "severity": "critical",
        "category": "security",
        "message": "Use of eval can lead to code injection vulnerabilities",
    },
]

# Rust rules
RUST_RULES: list[dict[str, str | re.Pattern[str]]] = [
    {
        "rule_id": "RS001",
        "pattern": re.compile(r"unsafe\s*\{"),
        "severity": "warning",
        "category": "security",
        "message": "Unsafe block detected -- review carefully",
    },
    {
        "rule_id": "RS002",
        "pattern": re.compile(r"unwrap\s*\(\)"),
        "severity": "info",
        "category": "bug",
        "message": "unwrap() can panic; consider using expect() or pattern matching",
    },
    {
        "rule_id": "RS003",
        "pattern": re.compile(r"todo!\s*\(\)|unimplemented!\s*\(\)"),
        "severity": "warning",
        "category": "maintainability",
        "message": "todo! or unimplemented! macro found",
    },
]

# Java rules
JAVA_RULES: list[dict[str, str | re.Pattern[str]]] = [
    {
        "rule_id": "JV001",
        "pattern": re.compile(r"System\.out\.print"),
        "severity": "info",
        "category": "style",
        "message": "Use logging framework instead of System.out",
    },
    {
        "rule_id": "JV002",
        "pattern": re.compile(r"catch\s*\(\s*Exception\s+\w+\s*\)"),
        "severity": "warning",
        "category": "bug",
        "message": "Catching generic Exception is too broad",
    },
    {
        "rule_id": "JV003",
        "pattern": re.compile(r"(?:password|secret|apiKey|token)\s*=\s*\""),
        "severity": "critical",
        "category": "security",
        "message": "Hardcoded secret or credential detected",
    },
]

LANGUAGE_RULES: dict[str, list[dict[str, str | re.Pattern[str]]]] = {
    "python": PYTHON_RULES,
    "javascript": JS_RULES,
    "typescript": JS_RULES,
    "rust": RUST_RULES,
    "java": JAVA_RULES,
    "kotlin": JAVA_RULES,
}


def _detect_language(file_path: str, content: str) -> str:
    """Detect programming language from file extension and content."""
    ext = Path(file_path).suffix.lower()
    if ext in EXTENSION_LANGUAGE_MAP:
        return EXTENSION_LANGUAGE_MAP[ext]

    # Content-based heuristics
    if "def " in content or "import " in content:
        return "python"
    if "function " in content or "const " in content:
        return "javascript"
    if "fn " in content and "let " in content:
        return "rust"
    if "public class" in content or "private " in content:
        return "java"
    return "unknown"


def _count_lines(lines: list[str]) -> tuple[int, int]:
    """Count lines of code and total lines."""
    total = len(lines)
    code_lines = 0
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("//"):
            code_lines += 1
    return code_lines, total


def _count_functions(content: str, language: str) -> int:
    """Count function definitions."""
    if language == "python":
        return len(re.findall(r"^\s*def\s+\w+", content, re.MULTILINE))
    elif language in ("javascript", "typescript"):
        return len(
            re.findall(
                r"(?:function\s+\w+|(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?(?:\([^)]*\)|[^=])\s*=>)",
                content,
            )
        )
    elif language == "rust":
        return len(re.findall(r"fn\s+\w+", content))
    elif language in ("java", "kotlin"):
        return len(re.findall(r"(?:public|private|protected)?\s*\w+\s+\w+\s*\(", content))
    return 0


def _count_classes(content: str, language: str) -> int:
    """Count class definitions."""
    if language == "python":
        return len(re.findall(r"^\s*class\s+\w+", content, re.MULTILINE))
    elif language in ("javascript", "typescript"):
        return len(re.findall(r"class\s+\w+", content))
    elif language == "rust":
        return len(re.findall(r"(?:struct|enum|trait|impl)\s+\w+", content))
    elif language in ("java", "kotlin"):
        return len(re.findall(r"(?:public\s+)?class\s+\w+", content))
    return 0


_COMPLEXITY_RE = re.compile(r"\b(?:if|elif|else|for|while|and|or|except|case)\b")


def _estimate_complexity(content: str, language: str) -> int:
    """Estimate cyclomatic complexity based on decision points."""
    total = 1 + len(_COMPLEXITY_RE.findall(content))
    return total


def _measure_nesting(lines: list[str]) -> int:
    """Measure maximum nesting depth."""
    max_depth = 0
    for line in lines:
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        # Simple indentation-based nesting
        leading = len(line) - len(stripped)
        indent_unit = 4
        depth = leading // indent_unit
        max_depth = max(max_depth, depth)
    return max_depth


def _count_imports(content: str, language: str) -> int:
    """Count import statements."""
    if language == "python":
        return len(re.findall(r"^\s*(?:import|from)\s+", content, re.MULTILINE))
    elif language in ("javascript", "typescript") or language in ("java", "kotlin"):
        return len(re.findall(r"\bimport\s+", content))
    elif language == "rust":
        return len(re.findall(r"\buse\s+", content))
    return 0


def _run_rules(content: str, language: str) -> list[CodeIssue]:
    """Apply language-specific rules and return issues."""
    issues: list[CodeIssue] = []
    rules = LANGUAGE_RULES.get(language, [])

    # Pre-compute line start offsets for O(log n) line-number lookup
    line_offsets = [0]
    for i, ch in enumerate(content):
        if ch == "\n":
            line_offsets.append(i + 1)

    for rule in rules:
        pattern = rule["pattern"]
        assert isinstance(pattern, re.Pattern)
        severity = rule["severity"]
        assert isinstance(severity, str)
        for match in pattern.finditer(content):
            line_num = bisect.bisect_right(line_offsets, match.start()) - 1
            issues.append(
                CodeIssue(
                    line=line_num,
                    severity=severity,
                    category=rule["category"],  # type: ignore[arg-type]
                    message=rule["message"],  # type: ignore[arg-type]
                    rule_id=rule["rule_id"],  # type: ignore[arg-type]
                )
            )

    return issues


def _calculate_avg_function_length(
    content: str, language: str, lines: list[str] | None = None
) -> float:
    """Calculate average function length in lines."""
    if language == "python":
        # Find function start lines
        func_starts = [m.start() for m in re.finditer(r"^\s*def\s+\w+", content, re.MULTILINE)]
        if not func_starts:
            return 0.0
        lengths: list[float] = []
        for i, start in enumerate(func_starts):
            # Next function start or end of file
            end = func_starts[i + 1] if i + 1 < len(func_starts) else len(content)
            func_text = content[start:end]
            lengths.append(float(len(func_text.split("\n"))))
        return sum(lengths) / len(lengths) if lengths else 0.0
    return 0.0


def analyze_code(file_path: str, language: str = "") -> CodeAnalysis:
    """Analyze a code file for quality issues and metrics.

    Args:
        file_path: Path to the code file to analyze.
        language: Programming language hint. If empty, auto-detected.

    Returns:
        CodeAnalysis with issues and metrics.
    """
    path = Path(file_path)
    if not path.exists():
        return CodeAnalysis(
            file_path=file_path,
            language=language or "unknown",
        )

    content = path.read_text(encoding="utf-8", errors="replace")

    # Detect language
    if not language:
        language = _detect_language(file_path, content)

    # Split content once for shared use
    lines = content.split("\n")

    # Calculate metrics
    loc, total_lines = _count_lines(lines)
    metrics = CodeMetrics(
        lines_of_code=loc,
        total_lines=total_lines,
        function_count=_count_functions(content, language),
        class_count=_count_classes(content, language),
        max_complexity=_estimate_complexity(content, language),
        max_nesting_depth=_measure_nesting(lines),
        avg_function_length=_calculate_avg_function_length(content, language, lines),
        import_count=_count_imports(content, language),
    )

    # Run language-specific rules
    issues = _run_rules(content, language)

    return CodeAnalysis(
        file_path=file_path,
        language=language,
        issues=issues,
        metrics=metrics,
    )
