"""Tests for CodeReviewerAgent class.

Covers the three-phase pipeline: analyze -> check -> review,
plus the full end-to-end pipeline.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from agent_code_reviewer.agent import CodeReviewerAgent
from agent_code_reviewer.models import (
    CodeAnalysis,
    CodeIssue,
    CodeMetrics,
    PatternMatch,
    ReviewReport,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent() -> CodeReviewerAgent:
    """Provide a CodeReviewerAgent instance."""
    return CodeReviewerAgent()


@pytest.fixture
def tmp_dir():
    """Provide a temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as d:
        yield d


def _write_file(dir_path: str, filename: str, content: str) -> str:
    """Write a file and return its absolute path."""
    filepath = os.path.join(dir_path, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath


# ---------------------------------------------------------------------------
# Phase 1: analyze
# ---------------------------------------------------------------------------


class TestAgentAnalyze:
    """Tests for CodeReviewerAgent.analyze."""

    def test_analyze_returns_code_analysis(self, agent: CodeReviewerAgent, tmp_dir: str) -> None:
        path = _write_file(tmp_dir, "test.py", "x = 1\n")
        result = agent.analyze(path)
        assert isinstance(result, CodeAnalysis)

    def test_analyze_detects_language(self, agent: CodeReviewerAgent, tmp_dir: str) -> None:
        path = _write_file(tmp_dir, "test.py", "import os\n")
        result = agent.analyze(path)
        assert result.language == "python"

    def test_analyze_file_not_found(self, agent: CodeReviewerAgent) -> None:
        result = agent.analyze("/nonexistent/file.py")
        assert isinstance(result, CodeAnalysis)
        assert result.file_path == "/nonexistent/file.py"
        assert result.issues == []

    def test_analyze_with_language_hint(self, agent: CodeReviewerAgent, tmp_dir: str) -> None:
        path = _write_file(tmp_dir, "code.txt", "fn main() {}")
        result = agent.analyze(path, language="rust")
        assert result.language == "rust"

    def test_analyze_finds_issues(self, agent: CodeReviewerAgent, tmp_dir: str) -> None:
        code = 'password = "secret_value_abc"\n'
        path = _write_file(tmp_dir, "secret.py", code)
        result = agent.analyze(path)
        assert len(result.issues) >= 1

    def test_analyze_populates_metrics(self, agent: CodeReviewerAgent, tmp_dir: str) -> None:
        code = "import os\n\ndef foo():\n    pass\n"
        path = _write_file(tmp_dir, "metrics.py", code)
        result = agent.analyze(path)
        assert result.metrics.total_lines > 0


# ---------------------------------------------------------------------------
# Phase 2: check
# ---------------------------------------------------------------------------


class TestAgentCheck:
    """Tests for CodeReviewerAgent.check."""

    def test_check_returns_pattern_matches(self, agent: CodeReviewerAgent) -> None:
        code = 'password = "super_secret_value_123"\n'
        result = agent.check(code)
        assert isinstance(result, list)
        assert all(isinstance(p, PatternMatch) for p in result)

    def test_check_empty_code(self, agent: CodeReviewerAgent) -> None:
        result = agent.check("")
        assert result == []

    def test_check_finds_sql_injection(self, agent: CodeReviewerAgent) -> None:
        code = 'cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")\n'
        result = agent.check(code)
        names = [p.pattern for p in result]
        assert "sql_injection" in names

    def test_check_finds_hardcoded_secret(self, agent: CodeReviewerAgent) -> None:
        code = 'api_key = "sk-1234567890abcdef"\n'
        result = agent.check(code)
        names = [p.pattern for p in result]
        assert "hardcoded_secret" in names

    def test_check_clean_code(self, agent: CodeReviewerAgent) -> None:
        code = "x = 1\ny = 2\nz = x + y\n"
        result = agent.check(code)
        critical = [p for p in result if p.severity == "critical"]
        assert len(critical) == 0


# ---------------------------------------------------------------------------
# Phase 3: review
# ---------------------------------------------------------------------------


class TestAgentReview:
    """Tests for CodeReviewerAgent.review."""

    def test_review_returns_review_report(self, agent: CodeReviewerAgent) -> None:
        analysis = CodeAnalysis(file_path="test.py", language="python")
        result = agent.review(analysis)
        assert isinstance(result, ReviewReport)

    def test_review_with_no_issues(self, agent: CodeReviewerAgent) -> None:
        analysis = CodeAnalysis(
            file_path="clean.py",
            language="python",
            metrics=CodeMetrics(lines_of_code=10, total_lines=15),
        )
        report = agent.review(analysis)
        assert report.overall_score == 100
        assert "No issues" in report.summary

    def test_review_with_issues_and_patterns(self, agent: CodeReviewerAgent) -> None:
        issues = [CodeIssue(severity="critical", category="security", rule_id="PY005")]
        analysis = CodeAnalysis(
            file_path="vuln.py",
            language="python",
            issues=issues,
            metrics=CodeMetrics(lines_of_code=20, total_lines=30),
        )
        patterns = [PatternMatch(pattern="sql_injection", severity="critical")]
        report = agent.review(analysis, patterns)
        assert len(report.findings) == 2
        assert report.severity_counts["critical"] == 2
        assert report.overall_score == 70  # 100 - 2*15

    def test_review_without_patterns(self, agent: CodeReviewerAgent) -> None:
        issues = [CodeIssue(severity="warning", category="bug", rule_id="PY001")]
        analysis = CodeAnalysis(file_path="warn.py", language="python", issues=issues)
        report = agent.review(analysis, patterns=None)
        assert len(report.findings) == 1
        assert report.severity_counts["warning"] == 1

    def test_review_suggestions_populated(self, agent: CodeReviewerAgent) -> None:
        issues = [CodeIssue(severity="critical", category="security")]
        analysis = CodeAnalysis(file_path="test.py", language="python", issues=issues)
        report = agent.review(analysis)
        assert len(report.suggestions) >= 1


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


class TestAgentFullPipeline:
    """Tests for the full analyze -> check -> review pipeline."""

    def test_full_pipeline_with_issues(self, agent: CodeReviewerAgent, tmp_dir: str) -> None:
        code = 'password = "super_secret_value_abc"\nimport os\n'
        path = _write_file(tmp_dir, "secret.py", code)

        # Phase 1: analyze
        analysis = agent.analyze(path)
        assert isinstance(analysis, CodeAnalysis)
        assert analysis.language == "python"
        assert len(analysis.issues) >= 1

        # Phase 2: check
        patterns = agent.check(code)
        assert len(patterns) >= 1

        # Phase 3: review
        report = agent.review(analysis, patterns)
        assert isinstance(report, ReviewReport)
        assert report.overall_score < 100
        assert len(report.findings) >= 1
        assert len(report.suggestions) >= 1

    def test_full_pipeline_clean_code(self, agent: CodeReviewerAgent, tmp_dir: str) -> None:
        code = "def add(a, b):\n    return a + b\n"
        path = _write_file(tmp_dir, "clean.py", code)

        analysis = agent.analyze(path)
        patterns = agent.check(code)
        report = agent.review(analysis, patterns)

        assert report.overall_score == 100 or report.overall_score > 0

    def test_full_pipeline_javascript(self, agent: CodeReviewerAgent, tmp_dir: str) -> None:
        code = 'console.log("debug");\nvar x = 1;\n'
        path = _write_file(tmp_dir, "debug.js", code)

        analysis = agent.analyze(path)
        assert analysis.language == "javascript"

        patterns = agent.check(code)
        report = agent.review(analysis, patterns)

        assert isinstance(report, ReviewReport)
        assert len(report.findings) >= 1
