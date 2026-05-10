"""Tests for error-analyzer agent.

Covers:
- Models: construction, validation, immutability
- Error type extraction
- Stack trace parsing
- Pattern-based fix suggestions
- Agent: full pipeline
- MCP adapter: server creation
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_error_analyzer.agent import ErrorAnalyzerAgent
from agent_error_analyzer.models import AnalysisReport, FixSuggestion, StackFrame
from agent_error_analyzer.tools.analyze_error import (
    _extract_error_type,
    _extract_message,
    _extract_stack_trace,
    analyze_error,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent() -> ErrorAnalyzerAgent:
    """Provide an ErrorAnalyzerAgent instance."""
    return ErrorAnalyzerAgent()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestFixSuggestion:
    """Tests for FixSuggestion model."""

    def test_basic(self) -> None:
        s = FixSuggestion()
        assert s.confidence == "medium"
        assert s.description == ""

    def test_full(self) -> None:
        s = FixSuggestion(
            confidence="high",
            description="Install missing package",
            fix_example="pip install flask",
        )
        assert s.confidence == "high"
        assert s.fix_example == "pip install flask"

    def test_frozen(self) -> None:
        s = FixSuggestion()
        with pytest.raises(ValidationError):
            s.confidence = "low"  # type: ignore[misc]


class TestStackFrame:
    """Tests for StackFrame model."""

    def test_basic(self) -> None:
        f = StackFrame(file="app.py", line=42, function="main")
        assert f.file == "app.py"
        assert f.line == 42

    def test_frozen(self) -> None:
        f = StackFrame()
        with pytest.raises(ValidationError):
            f.file = "test.py"  # type: ignore[misc]


class TestAnalysisReport:
    """Tests for AnalysisReport model."""

    def test_empty(self) -> None:
        r = AnalysisReport()
        assert r.error_type == ""
        assert r.category == "unknown"
        assert r.stack_trace == []
        assert r.suggestions == []

    def test_with_data(self) -> None:
        r = AnalysisReport(
            error_type="TypeError",
            category="type_error",
            location="app.py:10",
            message="unsupported operand",
        )
        assert r.error_type == "TypeError"


# ---------------------------------------------------------------------------
# Error type extraction
# ---------------------------------------------------------------------------


class TestExtractErrorType:
    """Tests for _extract_error_type."""

    def test_type_error(self) -> None:
        assert _extract_error_type("TypeError: unsupported operand") == "TypeError"

    def test_module_not_found(self) -> None:
        assert (
            _extract_error_type("ModuleNotFoundError: No module named 'flask'")
            == "ModuleNotFoundError"
        )

    def test_file_not_found(self) -> None:
        assert (
            _extract_error_type("FileNotFoundError: [Errno 2] No such file") == "FileNotFoundError"
        )

    def test_no_error_type(self) -> None:
        assert _extract_error_type("some random text") == ""


# ---------------------------------------------------------------------------
# Stack trace extraction
# ---------------------------------------------------------------------------


class TestExtractStackTrace:
    """Tests for _extract_stack_trace."""

    def test_python_traceback(self) -> None:
        text = (
            "Traceback (most recent call last):\n"
            '  File "app.py", line 42, in main\n'
            "    result = func()\n"
            "TypeError: unsupported operand"
        )
        frames = _extract_stack_trace(text)
        assert len(frames) >= 1
        assert frames[0].file == "app.py"
        assert frames[0].line == 42
        assert frames[0].function == "main"

    def test_no_traceback(self) -> None:
        frames = _extract_stack_trace("TypeError: bad")
        assert frames == []


# ---------------------------------------------------------------------------
# Message extraction
# ---------------------------------------------------------------------------


class TestExtractMessage:
    """Tests for _extract_message."""

    def test_with_error_type(self) -> None:
        msg = _extract_message("TypeError: unsupported operand type(s)", "TypeError")
        assert "unsupported operand" in msg

    def test_without_error_type(self) -> None:
        msg = _extract_message("some error occurred", "")
        assert "some error occurred" in msg


# ---------------------------------------------------------------------------
# analyze_error tool
# ---------------------------------------------------------------------------


class TestAnalyzeError:
    """Tests for analyze_error tool."""

    def test_type_error(self) -> None:
        text = "TypeError: unsupported operand type(s) for +: 'int' and 'str'"
        result = analyze_error(text)
        assert result.error_type == "TypeError"
        assert result.category == "type_error"
        assert len(result.suggestions) >= 1

    def test_module_not_found(self) -> None:
        text = "ModuleNotFoundError: No module named 'flask'"
        result = analyze_error(text)
        assert result.error_type == "ModuleNotFoundError"
        assert result.category == "import_error"
        assert any("pip install" in s.fix_example for s in result.suggestions)

    def test_file_not_found(self) -> None:
        text = "FileNotFoundError: [Errno 2] No such file or directory: '/tmp/test.txt'"
        result = analyze_error(text)
        assert result.error_type == "FileNotFoundError"
        assert result.category == "io_error"

    def test_key_error(self) -> None:
        text = "KeyError: 'user_id'"
        result = analyze_error(text)
        assert result.error_type == "KeyError"
        assert result.category == "value_error"
        assert any("user_id" in s.fix_example for s in result.suggestions)

    def test_full_traceback(self) -> None:
        text = (
            "Traceback (most recent call last):\n"
            '  File "app.py", line 10, in handler\n'
            "    data = db.get(id)\n"
            '  File "db.py", line 55, in get\n'
            "    raise KeyError('not_found')\n"
            "KeyError: 'not_found'"
        )
        result = analyze_error(text)
        assert result.error_type == "KeyError"
        assert len(result.stack_trace) >= 2
        assert result.location != ""

    def test_zero_division(self) -> None:
        text = "ZeroDivisionError: division by zero"
        result = analyze_error(text)
        assert result.error_type == "ZeroDivisionError"
        assert result.category == "arithmetic_error"

    def test_suggestions_sorted_by_confidence(self) -> None:
        text = "ModuleNotFoundError: No module named 'flask'"
        result = analyze_error(text)
        if len(result.suggestions) > 1:
            order = {"high": 0, "medium": 1, "low": 2}
            for i in range(len(result.suggestions) - 1):
                assert order.get(result.suggestions[i].confidence, 3) <= order.get(
                    result.suggestions[i + 1].confidence, 3
                )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class TestErrorAnalyzerAgent:
    """Tests for ErrorAnalyzerAgent class."""

    def test_analyze(self, agent: ErrorAnalyzerAgent) -> None:
        result = agent.analyze_error("TypeError: bad types")
        assert isinstance(result, AnalysisReport)
        assert result.error_type == "TypeError"

    def test_analyze_with_traceback(self, agent: ErrorAnalyzerAgent) -> None:
        text = (
            "Traceback (most recent call last):\n"
            '  File "main.py", line 5, in run\n'
            "RecursionError: maximum recursion depth exceeded"
        )
        result = agent.analyze_error(text)
        assert result.error_type == "RecursionError"
        assert result.category == "memory_error"


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        try:
            from agent_error_analyzer.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        import agent_error_analyzer.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")
