"""Data models for code-reviewer Agent.

Pydantic v2 frozen models for code analysis, pattern detection, and review reporting.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CodeMetrics(BaseModel):
    """Quantitative metrics for a code file.

    Attributes:
        lines_of_code: Total lines of code (excluding blank lines and comments).
        total_lines: Total lines in the file.
        function_count: Number of functions/methods detected.
        class_count: Number of classes detected.
        max_complexity: Estimated maximum cyclomatic complexity.
        max_nesting_depth: Maximum nesting depth found.
        avg_function_length: Average lines per function.
        import_count: Number of import statements.
    """

    model_config = ConfigDict(frozen=True)

    lines_of_code: int = 0
    total_lines: int = 0
    function_count: int = 0
    class_count: int = 0
    max_complexity: int = 0
    max_nesting_depth: int = 0
    avg_function_length: float = 0.0
    import_count: int = 0


class CodeIssue(BaseModel):
    """A single code quality issue found during analysis.

    Attributes:
        line: Line number where the issue was found (0-based).
        severity: Severity level (critical, warning, info).
        category: Issue category (style, security, performance, maintainability, bug).
        message: Human-readable description of the issue.
        rule_id: Identifier for the rule that was violated.
    """

    model_config = ConfigDict(frozen=True)

    line: int = 0
    severity: str = "info"
    category: str = "style"
    message: str = ""
    rule_id: str = ""


class CodeAnalysis(BaseModel):
    """Result of static code analysis.

    Attributes:
        file_path: Path to the analyzed file.
        language: Detected or specified programming language.
        issues: All issues found during analysis.
        metrics: Quantitative code metrics.
    """

    model_config = ConfigDict(frozen=True)

    file_path: str
    language: str = "unknown"
    issues: list[CodeIssue] = Field(default_factory=list)
    metrics: CodeMetrics = Field(default_factory=CodeMetrics)


class PatternMatch(BaseModel):
    """A matched anti-pattern in code.

    Attributes:
        pattern: Name of the matched pattern.
        line: Line number where the pattern was found.
        severity: Severity level (critical, warning, info).
        description: Description of why this pattern is problematic.
    """

    model_config = ConfigDict(frozen=True)

    pattern: str
    line: int = 0
    severity: str = "warning"
    description: str = ""


class ReviewReport(BaseModel):
    """Compiled code review report.

    Attributes:
        summary: High-level summary of the review.
        findings: All findings from the review.
        suggestions: Improvement suggestions.
        severity_counts: Count of issues by severity level.
        overall_score: Quality score from 0 to 100.
    """

    model_config = ConfigDict(frozen=True)

    summary: str = ""
    findings: list[CodeIssue | PatternMatch] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    severity_counts: dict[str, int] = Field(
        default_factory=lambda: {
            "critical": 0,
            "warning": 0,
            "info": 0,
        }
    )
    overall_score: int = 100
