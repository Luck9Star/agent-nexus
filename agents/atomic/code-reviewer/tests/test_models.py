"""Tests for code-reviewer data models.

Covers construction, validation, serialization, and immutability for all models:
CodeMetrics, CodeIssue, CodeAnalysis, PatternMatch, ReviewReport.
"""

from __future__ import annotations

import json

import pytest

from agent_code_reviewer.models import (
    CodeAnalysis,
    CodeIssue,
    CodeMetrics,
    PatternMatch,
    ReviewReport,
)

# ---------------------------------------------------------------------------
# CodeMetrics
# ---------------------------------------------------------------------------


class TestCodeMetrics:
    """Tests for CodeMetrics model."""

    def test_default_values(self) -> None:
        m = CodeMetrics()
        assert m.lines_of_code == 0
        assert m.total_lines == 0
        assert m.function_count == 0
        assert m.class_count == 0
        assert m.max_complexity == 0
        assert m.max_nesting_depth == 0
        assert m.avg_function_length == 0.0
        assert m.import_count == 0

    def test_full_construction(self) -> None:
        m = CodeMetrics(
            lines_of_code=100,
            total_lines=150,
            function_count=10,
            class_count=3,
            max_complexity=8,
            max_nesting_depth=4,
            avg_function_length=12.5,
            import_count=6,
        )
        assert m.lines_of_code == 100
        assert m.total_lines == 150
        assert m.function_count == 10
        assert m.class_count == 3
        assert m.max_complexity == 8
        assert m.max_nesting_depth == 4
        assert m.avg_function_length == 12.5
        assert m.import_count == 6

    def test_frozen(self) -> None:
        m = CodeMetrics()
        with pytest.raises(Exception):
            m.lines_of_code = 999  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        m = CodeMetrics(lines_of_code=42, total_lines=60)
        data = m.model_dump()
        m2 = CodeMetrics.model_validate(data)
        assert m == m2

    def test_json_serialization(self) -> None:
        m = CodeMetrics(lines_of_code=10, function_count=2)
        json_str = m.model_dump_json()
        data = json.loads(json_str)
        assert data["lines_of_code"] == 10
        assert data["function_count"] == 2


# ---------------------------------------------------------------------------
# CodeIssue
# ---------------------------------------------------------------------------


class TestCodeIssue:
    """Tests for CodeIssue model."""

    def test_default_values(self) -> None:
        issue = CodeIssue()
        assert issue.line == 0
        assert issue.severity == "info"
        assert issue.category == "style"
        assert issue.message == ""
        assert issue.rule_id == ""

    def test_full_construction(self) -> None:
        issue = CodeIssue(
            line=42,
            severity="critical",
            category="security",
            message="Hardcoded secret",
            rule_id="PY005",
        )
        assert issue.line == 42
        assert issue.severity == "critical"
        assert issue.category == "security"
        assert issue.message == "Hardcoded secret"
        assert issue.rule_id == "PY005"

    def test_frozen(self) -> None:
        issue = CodeIssue(line=1, severity="warning")
        with pytest.raises(Exception):
            issue.severity = "critical"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        issue = CodeIssue(line=10, severity="warning", category="bug", rule_id="PY001")
        data = issue.model_dump()
        issue2 = CodeIssue.model_validate(data)
        assert issue == issue2

    def test_json_serialization(self) -> None:
        issue = CodeIssue(line=5, severity="info", message="test message")
        json_str = issue.model_dump_json()
        data = json.loads(json_str)
        assert data["line"] == 5
        assert data["message"] == "test message"


# ---------------------------------------------------------------------------
# CodeAnalysis
# ---------------------------------------------------------------------------


class TestCodeAnalysis:
    """Tests for CodeAnalysis model."""

    def test_minimal_construction(self) -> None:
        a = CodeAnalysis(file_path="test.py")
        assert a.file_path == "test.py"
        assert a.language == "unknown"
        assert a.issues == []
        assert a.metrics == CodeMetrics()

    def test_with_issues_and_metrics(self) -> None:
        metrics = CodeMetrics(lines_of_code=50, total_lines=80)
        issues = [
            CodeIssue(line=1, severity="warning", category="style", rule_id="PY004"),
            CodeIssue(line=10, severity="critical", category="security", rule_id="PY005"),
        ]
        a = CodeAnalysis(
            file_path="app.py",
            language="python",
            issues=issues,
            metrics=metrics,
        )
        assert a.language == "python"
        assert len(a.issues) == 2
        assert a.metrics.lines_of_code == 50

    def test_frozen(self) -> None:
        a = CodeAnalysis(file_path="f.py")
        with pytest.raises(Exception):
            a.language = "rust"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        a = CodeAnalysis(
            file_path="x.py",
            language="python",
            issues=[CodeIssue(line=1, severity="info", rule_id="PY007")],
            metrics=CodeMetrics(lines_of_code=10),
        )
        data = a.model_dump()
        a2 = CodeAnalysis.model_validate(data)
        assert a == a2

    def test_json_serialization(self) -> None:
        a = CodeAnalysis(file_path="test.py", language="python")
        json_str = a.model_dump_json()
        data = json.loads(json_str)
        assert data["file_path"] == "test.py"
        assert data["language"] == "python"


# ---------------------------------------------------------------------------
# PatternMatch
# ---------------------------------------------------------------------------


class TestPatternMatch:
    """Tests for PatternMatch model."""

    def test_default_values(self) -> None:
        p = PatternMatch(pattern="test_pattern")
        assert p.pattern == "test_pattern"
        assert p.line == 0
        assert p.severity == "warning"
        assert p.description == ""

    def test_full_construction(self) -> None:
        p = PatternMatch(
            pattern="sql_injection",
            line=15,
            severity="critical",
            description="SQL injection detected",
        )
        assert p.pattern == "sql_injection"
        assert p.line == 15
        assert p.severity == "critical"
        assert p.description == "SQL injection detected"

    def test_frozen(self) -> None:
        p = PatternMatch(pattern="x")
        with pytest.raises(Exception):
            p.pattern = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        p = PatternMatch(pattern="hardcoded_secret", line=3, severity="critical")
        data = p.model_dump()
        p2 = PatternMatch.model_validate(data)
        assert p == p2

    def test_json_serialization(self) -> None:
        p = PatternMatch(pattern="n_plus_one", severity="warning")
        json_str = p.model_dump_json()
        data = json.loads(json_str)
        assert data["pattern"] == "n_plus_one"


# ---------------------------------------------------------------------------
# ReviewReport
# ---------------------------------------------------------------------------


class TestReviewReport:
    """Tests for ReviewReport model."""

    def test_default_values(self) -> None:
        r = ReviewReport()
        assert r.summary == ""
        assert r.findings == []
        assert r.suggestions == []
        assert r.severity_counts == {"critical": 0, "warning": 0, "info": 0}
        assert r.overall_score == 100

    def test_with_findings_and_suggestions(self) -> None:
        findings = [
            CodeIssue(line=1, severity="critical", category="security"),
            PatternMatch(pattern="sql_injection", severity="critical"),
        ]
        r = ReviewReport(
            summary="Test summary",
            findings=findings,
            suggestions=["Fix security issues"],
            severity_counts={"critical": 2, "warning": 0, "info": 0},
            overall_score=70,
        )
        assert r.summary == "Test summary"
        assert len(r.findings) == 2
        assert len(r.suggestions) == 1
        assert r.overall_score == 70

    def test_frozen(self) -> None:
        r = ReviewReport()
        with pytest.raises(Exception):
            r.summary = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        r = ReviewReport(
            summary="review",
            findings=[CodeIssue(line=1, severity="info")],
            suggestions=["suggestion"],
            severity_counts={"critical": 0, "warning": 0, "info": 1},
            overall_score=99,
        )
        data = r.model_dump()
        r2 = ReviewReport.model_validate(data)
        assert r == r2

    def test_json_serialization(self) -> None:
        r = ReviewReport(summary="test", overall_score=85)
        json_str = r.model_dump_json()
        data = json.loads(json_str)
        assert data["summary"] == "test"
        assert data["overall_score"] == 85

    def test_severity_counts_default_factory(self) -> None:
        """Each instance should have its own severity_counts dict."""
        r1 = ReviewReport()
        r2 = ReviewReport()
        r1.severity_counts["critical"] = 5
        assert r2.severity_counts["critical"] == 0
