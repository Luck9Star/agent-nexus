"""Tests for config-linter agent.

Covers:
- Models: construction, validation, immutability
- Format detection: TOML, YAML, JSON, unknown
- TOML linting: missing keys, empty sections
- YAML linting: duplicate keys, tab indentation
- JSON linting: trailing commas, null values, parse errors
- Agent: full pipeline
- MCP adapter: server creation
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_config_linter.agent import ConfigLinterAgent
from agent_config_linter.models import LintIssue, LintReport
from agent_config_linter.tools.lint_config import (
    _detect_format,
    _lint_json,
    _lint_toml,
    _lint_yaml,
    lint_config,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent() -> ConfigLinterAgent:
    """Provide a ConfigLinterAgent instance."""
    return ConfigLinterAgent()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestLintIssue:
    """Tests for LintIssue model."""

    def test_basic(self) -> None:
        issue = LintIssue(severity="error", category="missing_key")
        assert issue.severity == "error"
        assert issue.category == "missing_key"
        assert issue.location == ""
        assert issue.message == ""

    def test_full(self) -> None:
        issue = LintIssue(
            severity="warning",
            category="deprecated",
            location="line 10",
            message="Use of deprecated option",
            suggestion="Remove the option",
        )
        assert issue.suggestion == "Remove the option"

    def test_frozen(self) -> None:
        issue = LintIssue(severity="info", category="style")
        with pytest.raises(ValidationError):
            issue.severity = "error"  # type: ignore[misc]


class TestLintReport:
    """Tests for LintReport model."""

    def test_empty(self) -> None:
        report = LintReport()
        assert report.issues == []
        assert report.total_issues == 0
        assert report.format_detected == "unknown"

    def test_with_issues(self) -> None:
        issues = [LintIssue(severity="error", category="test")]
        report = LintReport(issues=issues, total_issues=1, error_count=1)
        assert report.error_count == 1


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------


class TestDetectFormat:
    """Tests for _detect_format."""

    def test_json_object(self) -> None:
        assert _detect_format('{"key": "value"}') == "json"

    def test_json_array(self) -> None:
        assert _detect_format("[1, 2, 3]") == "json"

    def test_toml(self) -> None:
        assert _detect_format('[project]\nname = "test"\n') == "toml"

    def test_yaml(self) -> None:
        assert _detect_format("name: test\nversion: 1.0\n") == "yaml"

    def test_unknown(self) -> None:
        assert _detect_format("random text") == "unknown"


# ---------------------------------------------------------------------------
# TOML linting
# ---------------------------------------------------------------------------


class TestLintToml:
    """Tests for _lint_toml."""

    def test_missing_required_keys(self) -> None:
        content = '[project]\ndescription = "test"\n'
        issues = _lint_toml(content)
        categories = {i.category for i in issues}
        assert "missing_key" in categories

    def test_valid_project_section(self) -> None:
        content = '[project]\nname = "test"\nversion = "1.0.0"\n'
        issues = _lint_toml(content)
        missing_key_issues = [i for i in issues if i.category == "missing_key"]
        assert len(missing_key_issues) == 0

    def test_empty_section(self) -> None:
        content = '[project]\nname = "test"\nversion = "1.0.0"\n\n[tool.empty]\n'
        issues = _lint_toml(content)
        categories = {i.category for i in issues}
        assert "empty_section" in categories


# ---------------------------------------------------------------------------
# YAML linting
# ---------------------------------------------------------------------------


class TestLintYaml:
    """Tests for _lint_yaml."""

    def test_duplicate_keys(self) -> None:
        content = "name: test\nname: other\n"
        issues = _lint_yaml(content)
        categories = {i.category for i in issues}
        assert "duplicate_key" in categories

    def test_tab_indentation(self) -> None:
        content = "name: test\n\tkey: value\n"
        issues = _lint_yaml(content)
        categories = {i.category for i in issues}
        assert "indentation" in categories

    def test_clean_yaml(self) -> None:
        content = "name: test\nversion: 1.0\n"
        issues = _lint_yaml(content)
        assert all(i.severity != "error" for i in issues)

    def test_unquoted_special_chars(self) -> None:
        content = "data: {key: value}\n"
        issues = _lint_yaml(content)
        categories = {i.category for i in issues}
        assert "unquoted_special" in categories


# ---------------------------------------------------------------------------
# JSON linting
# ---------------------------------------------------------------------------


class TestLintJson:
    """Tests for _lint_json."""

    def test_trailing_comma(self) -> None:
        content = '{"a": 1, "b": 2,\n}'
        issues = _lint_json(content)
        categories = {i.category for i in issues}
        assert "trailing_comma" in categories

    def test_null_value(self) -> None:
        content = '{"name": null}'
        issues = _lint_json(content)
        categories = {i.category for i in issues}
        assert "null_value" in categories

    def test_valid_json(self) -> None:
        content = '{"name": "test", "version": "1.0.0"}'
        issues = _lint_json(content)
        assert all(i.category != "parse_error" for i in issues)

    def test_parse_error(self) -> None:
        content = "{invalid json"
        issues = _lint_json(content)
        categories = {i.category for i in issues}
        assert "parse_error" in categories


# ---------------------------------------------------------------------------
# lint_config integration
# ---------------------------------------------------------------------------


class TestLintConfig:
    """Tests for lint_config tool."""

    def test_auto_detect_json(self) -> None:
        report = lint_config('{"key": "value"}')
        assert report.format_detected == "json"

    def test_auto_detect_toml(self) -> None:
        report = lint_config('[project]\nname = "test"\n')
        assert report.format_detected == "toml"

    def test_explicit_format(self) -> None:
        report = lint_config("name: test\n", fmt="yaml")
        assert report.format_detected == "yaml"

    def test_unknown_format(self) -> None:
        report = lint_config("random gibberish")
        assert report.total_issues >= 1
        assert report.format_detected == "unknown"

    def test_report_counts(self) -> None:
        report = lint_config('[project]\ndescription = "test"\n')
        assert report.total_issues == report.error_count + report.warning_count + report.info_count


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class TestConfigLinterAgent:
    """Tests for ConfigLinterAgent class."""

    def test_lint_json(self, agent: ConfigLinterAgent) -> None:
        report = agent.lint_config('{"name": null}')
        assert isinstance(report, LintReport)
        assert report.format_detected == "json"

    def test_lint_toml(self, agent: ConfigLinterAgent) -> None:
        report = agent.lint_config('[project]\nname = "test"\nversion = "1.0"\n')
        assert report.format_detected == "toml"

    def test_lint_yaml(self, agent: ConfigLinterAgent) -> None:
        report = agent.lint_config("name: test\nversion: 1.0\n", fmt="yaml")
        assert report.format_detected == "yaml"


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        try:
            from agent_config_linter.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        import agent_config_linter.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")
