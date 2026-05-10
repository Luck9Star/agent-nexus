"""Tests for performance-profiler agent.

Covers:
- Models: construction, validation, serialization, immutability
- analyze_performance: N+1 detection, loop patterns, memory issues
- Agent: full pipeline
- MCP adapter: server creation
- Local adapter: message dispatch
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_performance_profiler.agent import PerformanceProfilerAgent
from agent_performance_profiler.local_adapter import handle_message
from agent_performance_profiler.models import PerformanceFinding, PerformanceReport
from agent_performance_profiler.tools.analyze_performance import (
    _severity_rank,
    analyze_performance,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent() -> PerformanceProfilerAgent:
    """Provide a PerformanceProfilerAgent instance."""
    return PerformanceProfilerAgent()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestPerformanceFinding:
    """Tests for PerformanceFinding model."""

    def test_basic_construction(self) -> None:
        f = PerformanceFinding(severity="high", category="n_plus_one", location="line 5")
        assert f.severity == "high"
        assert f.category == "n_plus_one"
        assert f.location == "line 5"
        assert f.description == ""
        assert f.complexity == ""

    def test_full_construction(self) -> None:
        f = PerformanceFinding(
            severity="critical",
            category="n_plus_one",
            location="line 10",
            description="DB query in loop",
            remediation="Batch fetch",
            complexity="O(n)",
        )
        assert f.complexity == "O(n)"
        assert f.remediation == "Batch fetch"

    def test_frozen(self) -> None:
        f = PerformanceFinding(severity="low", category="x", location="line 1")
        with pytest.raises(ValidationError):
            f.severity = "critical"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        f = PerformanceFinding(severity="medium", category="a", location="line 2")
        data = f.model_dump()
        f2 = PerformanceFinding.model_validate(data)
        assert f == f2


class TestPerformanceReport:
    """Tests for PerformanceReport model."""

    def test_empty(self) -> None:
        r = PerformanceReport()
        assert r.findings == []
        assert r.critical_count == 0
        assert r.lines_analyzed == 0

    def test_with_findings(self) -> None:
        f = PerformanceFinding(severity="high", category="a", location="line 5")
        r = PerformanceReport(high_count=1, findings=[f], lines_analyzed=100)
        assert r.high_count == 1
        assert r.lines_analyzed == 100


# ---------------------------------------------------------------------------
# analyze_performance
# ---------------------------------------------------------------------------


class TestAnalyzePerformance:
    """Tests for analyze_performance tool."""

    def test_empty_source(self) -> None:
        report = analyze_performance("")
        assert report.findings == []
        assert report.lines_analyzed == 0

    def test_clean_code(self) -> None:
        code = "x = 1\ny = 2\nprint(x + y)\n"
        report = analyze_performance(code)
        assert report.findings == []

    def test_n_plus_one_for_loop(self) -> None:
        code = "for user in users:\n    db.query(user.id)\n"
        report = analyze_performance(code)
        assert len(report.findings) >= 1
        cats = {f.category for f in report.findings}
        assert "n_plus_one" in cats

    def test_n_plus_one_filter(self) -> None:
        code = "for item in items:\n    result = session.filter(item.name)\n"
        report = analyze_performance(code)
        assert len(report.findings) >= 1
        assert any(f.category == "n_plus_one" for f in report.findings)

    def test_string_concat_in_loop(self) -> None:
        code = "for line in lines:\n    result += 'text'\n"
        report = analyze_performance(code)
        assert len(report.findings) >= 1
        assert any(f.category == "inefficient_loop" for f in report.findings)

    def test_list_concat_in_loop(self) -> None:
        code = "for item in items:\n    result = result + [item]\n"
        report = analyze_performance(code)
        assert len(report.findings) >= 1
        assert any("concatenation" in f.description.lower() for f in report.findings)

    def test_nested_loops(self) -> None:
        code = "for i in range(n):\n    for j in range(m):\n        pass\n"
        report = analyze_performance(code)
        assert len(report.findings) >= 1
        assert any("Nested" in f.description for f in report.findings)

    def test_large_range(self) -> None:
        code = "for i in range(1000000):\n    pass\n"
        report = analyze_performance(code)
        assert len(report.findings) >= 1
        assert any(f.category == "memory_inefficient" for f in report.findings)

    def test_io_in_loop(self) -> None:
        code = "for name in files:\n    f = open(name)\n"
        report = analyze_performance(code)
        assert len(report.findings) >= 1
        assert any("I/O" in f.description for f in report.findings)

    def test_severity_ordering(self) -> None:
        code = (
            "for user in users:\n    db.query(user.id)\nfor line in lines:\n    result += 'text'\n"
        )
        report = analyze_performance(code)
        if len(report.findings) >= 2:
            assert _severity_rank(report.findings[0].severity) >= _severity_rank(
                report.findings[-1].severity
            )

    def test_lines_analyzed(self) -> None:
        code = "x = 1\ny = 2\nz = 3\n"
        report = analyze_performance(code)
        assert report.lines_analyzed == 4


class TestHelpers:
    """Tests for helper functions."""

    def test_severity_rank(self) -> None:
        assert _severity_rank("critical") == 4
        assert _severity_rank("high") == 3
        assert _severity_rank("medium") == 2
        assert _severity_rank("low") == 1
        assert _severity_rank("unknown") == 0


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class TestPerformanceProfilerAgent:
    """Tests for PerformanceProfilerAgent class."""

    def test_analyze_clean(self, agent: PerformanceProfilerAgent) -> None:
        result = agent.analyze_performance("x = 1\n")
        assert isinstance(result, PerformanceReport)
        assert result.findings == []

    def test_analyze_issues(self, agent: PerformanceProfilerAgent) -> None:
        code = "for user in users:\n    db.query(user.id)\n"
        result = agent.analyze_performance(code)
        assert isinstance(result, PerformanceReport)
        assert len(result.findings) >= 1

    def test_analyze_empty(self, agent: PerformanceProfilerAgent) -> None:
        result = agent.analyze_performance("")
        assert isinstance(result, PerformanceReport)
        assert result.lines_analyzed == 0


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        try:
            from agent_performance_profiler.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        import agent_performance_profiler.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")


# ---------------------------------------------------------------------------
# Local adapter
# ---------------------------------------------------------------------------


class TestLocalAdapter:
    """Tests for local adapter message handling."""

    def test_handle_analyze(self, agent: PerformanceProfilerAgent) -> None:
        response = handle_message(
            agent,
            {"method": "analyze_performance", "params": {"source_code": "x = 1\n"}},
        )
        assert response["status"] == "ok"
        assert "result" in response

    def test_handle_analyze_missing(self, agent: PerformanceProfilerAgent) -> None:
        response = handle_message(agent, {"method": "analyze_performance", "params": {}})
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_analyze_with_issues(self, agent: PerformanceProfilerAgent) -> None:
        code = "for user in users:\n    db.query(user.id)\n"
        response = handle_message(
            agent,
            {"method": "analyze_performance", "params": {"source_code": code}},
        )
        assert response["status"] == "ok"
        assert response["result"]["critical_count"] >= 1

    def test_handle_unknown_method(self, agent: PerformanceProfilerAgent) -> None:
        response = handle_message(agent, {"method": "unknown", "params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]
