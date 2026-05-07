"""Tests for code-reviewer local adapter.

Covers message dispatch for all three methods (analyze, check, review)
and error handling for invalid inputs.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

from agent_code_reviewer.agent import CodeReviewerAgent
from agent_code_reviewer.local_adapter import handle_message

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
    filepath = os.path.join(dir_path, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


class TestLocalAdapterAnalyze:
    """Tests for local adapter analyze method dispatch."""

    def test_handle_analyze_success(
        self, agent: CodeReviewerAgent, tmp_dir: str
    ) -> None:
        path = _write_file(tmp_dir, "test.py", "x = 1\n")
        response = handle_message(
            agent,
            {"method": "analyze", "params": {"file_path": path}},
        )
        assert response["status"] == "ok"
        assert "result" in response
        assert response["result"]["file_path"] == path

    def test_handle_analyze_missing_file_path(self, agent: CodeReviewerAgent) -> None:
        response = handle_message(
            agent,
            {"method": "analyze", "params": {}},
        )
        assert response["status"] == "error"
        assert "Missing" in response["error"]
        assert "file_path" in response["error"]

    def test_handle_analyze_with_language(
        self, agent: CodeReviewerAgent, tmp_dir: str
    ) -> None:
        path = _write_file(tmp_dir, "code.txt", "fn main() {}")
        response = handle_message(
            agent,
            {"method": "analyze", "params": {"file_path": path, "language": "rust"}},
        )
        assert response["status"] == "ok"
        assert response["result"]["language"] == "rust"


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


class TestLocalAdapterCheck:
    """Tests for local adapter check method dispatch."""

    def test_handle_check_success(self, agent: CodeReviewerAgent) -> None:
        code = 'password = "secret_value_abc"\n'
        response = handle_message(
            agent,
            {"method": "check", "params": {"code": code}},
        )
        assert response["status"] == "ok"
        assert "result" in response
        assert "patterns" in response["result"]

    def test_handle_check_missing_code(self, agent: CodeReviewerAgent) -> None:
        response = handle_message(
            agent,
            {"method": "check", "params": {}},
        )
        assert response["status"] == "error"
        assert "Missing" in response["error"]
        assert "code" in response["error"]

    def test_handle_check_empty_code(self, agent: CodeReviewerAgent) -> None:
        response = handle_message(
            agent,
            {"method": "check", "params": {"code": ""}},
        )
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_check_with_language(self, agent: CodeReviewerAgent) -> None:
        response = handle_message(
            agent,
            {"method": "check", "params": {"code": "x = 1", "language": "python"}},
        )
        assert response["status"] == "ok"


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------


class TestLocalAdapterReview:
    """Tests for local adapter review method dispatch."""

    def test_handle_review_success(self, agent: CodeReviewerAgent) -> None:
        analysis = {
            "file_path": "test.py",
            "language": "python",
            "issues": [],
            "metrics": {"lines_of_code": 10, "total_lines": 15},
        }
        response = handle_message(
            agent,
            {"method": "review", "params": {"analysis": analysis}},
        )
        assert response["status"] == "ok"
        assert "result" in response
        assert response["result"]["overall_score"] == 100

    def test_handle_review_missing_analysis(self, agent: CodeReviewerAgent) -> None:
        response = handle_message(
            agent,
            {"method": "review", "params": {}},
        )
        assert response["status"] == "error"
        assert "Missing" in response["error"]
        assert "analysis" in response["error"]

    def test_handle_review_with_patterns(self, agent: CodeReviewerAgent) -> None:
        analysis = {
            "file_path": "vuln.py",
            "language": "python",
            "issues": [
                {"line": 1, "severity": "critical", "category": "security", "rule_id": "PY005"}
            ],
            "metrics": {"lines_of_code": 10, "total_lines": 20},
        }
        patterns = [
            {"pattern": "sql_injection", "line": 5, "severity": "critical", "description": "SQL injection"}
        ]
        response = handle_message(
            agent,
            {"method": "review", "params": {"analysis": analysis, "patterns": patterns}},
        )
        assert response["status"] == "ok"
        assert len(response["result"]["findings"]) == 2

    def test_handle_review_with_issues(self, agent: CodeReviewerAgent) -> None:
        analysis = {
            "file_path": "warn.py",
            "language": "python",
            "issues": [
                {"line": 1, "severity": "warning", "category": "bug", "rule_id": "PY001"}
            ],
            "metrics": {},
        }
        response = handle_message(
            agent,
            {"method": "review", "params": {"analysis": analysis}},
        )
        assert response["status"] == "ok"
        assert response["result"]["severity_counts"]["warning"] == 1


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestLocalAdapterErrors:
    """Tests for local adapter error handling."""

    def test_handle_unknown_method(self, agent: CodeReviewerAgent) -> None:
        response = handle_message(agent, {"method": "unknown", "params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]

    def test_handle_empty_method(self, agent: CodeReviewerAgent) -> None:
        response = handle_message(agent, {"method": "", "params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]

    def test_handle_missing_method_key(self, agent: CodeReviewerAgent) -> None:
        response = handle_message(agent, {"params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]

    def test_handle_analyze_exception_caught(self, agent: CodeReviewerAgent) -> None:
        """Exceptions from agent.analyze should be caught and returned as errors."""
        with patch.object(
            agent, "analyze", side_effect=RuntimeError("test error")
        ):
            response = handle_message(
                agent,
                {"method": "analyze", "params": {"file_path": "/tmp/test.py"}},
            )
        assert response["status"] == "error"
        assert "test error" in response["error"]
        assert response["error_type"] == "RuntimeError"

    def test_handle_check_exception_caught(self, agent: CodeReviewerAgent) -> None:
        """Exceptions from agent.check should be caught and returned as errors."""
        with patch.object(
            agent, "check", side_effect=ValueError("bad input")
        ):
            response = handle_message(
                agent,
                {"method": "check", "params": {"code": "x = 1"}},
            )
        assert response["status"] == "error"
        assert "bad input" in response["error"]
        assert response["error_type"] == "ValueError"
