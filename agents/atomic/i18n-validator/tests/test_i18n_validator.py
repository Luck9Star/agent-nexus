"""Tests for i18n-validator agent.

Covers:
- Models: construction, validation, serialization, immutability
- validate_i18n: missing keys, empty values, extra keys, coverage
- Agent: full pipeline
- MCP adapter: server creation
- Local adapter: message dispatch
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_i18n_validator.agent import I18nValidatorAgent
from agent_i18n_validator.local_adapter import handle_message
from agent_i18n_validator.models import I18nFinding, I18nLocaleStats, I18nReport
from agent_i18n_validator.tools.validate_i18n import validate_i18n

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent() -> I18nValidatorAgent:
    """Provide an I18nValidatorAgent instance."""
    return I18nValidatorAgent()


def _make_locales() -> dict[str, dict[str, str]]:
    """Build sample locale data."""
    return {
        "en": {"greeting": "Hello", "farewell": "Goodbye", "thanks": "Thank you"},
        "zh": {"greeting": "你好", "farewell": "再见", "thanks": "谢谢"},
    }


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestI18nFinding:
    """Tests for I18nFinding model."""

    def test_basic_construction(self) -> None:
        f = I18nFinding(severity="error", category="missing_key", locale="zh", key="hello")
        assert f.severity == "error"
        assert f.category == "missing_key"
        assert f.locale == "zh"
        assert f.key == "hello"

    def test_full_construction(self) -> None:
        f = I18nFinding(
            severity="warning",
            category="empty_value",
            locale="ja",
            key="farewell",
            description="Empty translation",
            remediation="Add translation",
        )
        assert f.description == "Empty translation"

    def test_frozen(self) -> None:
        f = I18nFinding(severity="info", category="x", locale="en", key="k")
        with pytest.raises(ValidationError):
            f.severity = "error"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        f = I18nFinding(severity="error", category="missing_key", locale="zh", key="hi")
        data = f.model_dump()
        f2 = I18nFinding.model_validate(data)
        assert f == f2


class TestI18nLocaleStats:
    """Tests for I18nLocaleStats model."""

    def test_basic(self) -> None:
        s = I18nLocaleStats(locale="zh", total_keys=10, translated_keys=8, missing_keys=2)
        assert s.coverage_percent == 0.0
        assert s.missing_keys == 2

    def test_frozen(self) -> None:
        s = I18nLocaleStats(locale="en")
        with pytest.raises(ValidationError):
            s.locale = "zh"  # type: ignore[misc]


class TestI18nReport:
    """Tests for I18nReport model."""

    def test_empty(self) -> None:
        r = I18nReport()
        assert r.findings == []
        assert r.total_locales == 0

    def test_with_findings(self) -> None:
        f = I18nFinding(severity="error", category="missing_key", locale="zh", key="hi")
        r = I18nReport(findings=[f], total_locales=2, total_keys=5)
        assert len(r.findings) == 1
        assert r.total_keys == 5


# ---------------------------------------------------------------------------
# validate_i18n
# ---------------------------------------------------------------------------


class TestValidateI18n:
    """Tests for validate_i18n tool."""

    def test_complete_locales(self) -> None:
        report = validate_i18n(_make_locales(), "en")
        assert report.findings == []
        assert report.overall_coverage == 100.0
        assert report.total_keys == 3

    def test_missing_keys(self) -> None:
        locales = {
            "en": {"greeting": "Hello", "farewell": "Goodbye"},
            "zh": {"greeting": "你好"},
        }
        report = validate_i18n(locales, "en")
        assert any(f.category == "missing_key" for f in report.findings)
        assert any(f.key == "farewell" for f in report.findings)

    def test_empty_values(self) -> None:
        locales = {
            "en": {"greeting": "Hello"},
            "zh": {"greeting": ""},
        }
        report = validate_i18n(locales, "en")
        assert any(f.category == "empty_value" for f in report.findings)

    def test_extra_keys(self) -> None:
        locales = {
            "en": {"greeting": "Hello"},
            "zh": {"greeting": "你好", "extra_key": "额外"},
        }
        report = validate_i18n(locales, "en")
        assert any(f.category == "extra_key" for f in report.findings)
        assert any(f.key == "extra_key" for f in report.findings)

    def test_empty_base_locale(self) -> None:
        locales = {"en": {}, "zh": {"greeting": "你好"}}
        report = validate_i18n(locales, "en")
        assert any("no translation keys" in f.description for f in report.findings)

    def test_coverage_calculation(self) -> None:
        locales = {
            "en": {"a": "A", "b": "B", "c": "C", "d": "D"},
            "zh": {"a": "甲", "b": "乙"},
        }
        report = validate_i18n(locales, "en")
        # zh has 2/4 = 50%
        zh_stats = [s for s in report.locale_stats if s.locale == "zh"][0]
        assert zh_stats.coverage_percent == 50.0
        assert report.overall_coverage == 50.0

    def test_base_locale_stats(self) -> None:
        report = validate_i18n(_make_locales(), "en")
        en_stats = [s for s in report.locale_stats if s.locale == "en"][0]
        assert en_stats.coverage_percent == 100.0
        assert en_stats.missing_keys == 0

    def test_multiple_non_base_locales(self) -> None:
        locales = {
            "en": {"greeting": "Hello", "farewell": "Goodbye"},
            "zh": {"greeting": "你好"},
            "ja": {"greeting": "こんにちは", "farewell": "さようなら"},
        }
        report = validate_i18n(locales, "en")
        assert report.total_locales == 3
        assert len(report.locale_stats) == 3

    def test_single_locale(self) -> None:
        locales = {"en": {"hello": "Hello"}}
        report = validate_i18n(locales, "en")
        assert report.findings == []
        assert report.overall_coverage == 100.0


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class TestI18nValidatorAgent:
    """Tests for I18nValidatorAgent class."""

    def test_validate_complete(self, agent: I18nValidatorAgent) -> None:
        result = agent.validate_i18n(_make_locales(), "en")
        assert isinstance(result, I18nReport)
        assert result.overall_coverage == 100.0

    def test_validate_missing(self, agent: I18nValidatorAgent) -> None:
        locales = {"en": {"hello": "Hello", "bye": "Bye"}, "zh": {"hello": "你好"}}
        result = agent.validate_i18n(locales, "en")
        assert any(f.category == "missing_key" for f in result.findings)

    def test_validate_default_base(self, agent: I18nValidatorAgent) -> None:
        result = agent.validate_i18n(_make_locales())
        assert result.base_locale == "en"


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        try:
            from agent_i18n_validator.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        import agent_i18n_validator.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")


# ---------------------------------------------------------------------------
# Local adapter
# ---------------------------------------------------------------------------


class TestLocalAdapter:
    """Tests for local adapter message handling."""

    def test_handle_validate(self, agent: I18nValidatorAgent) -> None:
        response = handle_message(
            agent,
            {
                "method": "validate_i18n",
                "params": {"locales": _make_locales(), "base_locale": "en"},
            },
        )
        assert response["status"] == "ok"
        assert "result" in response

    def test_handle_validate_missing(self, agent: I18nValidatorAgent) -> None:
        response = handle_message(agent, {"method": "validate_i18n", "params": {}})
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_validate_with_issues(self, agent: I18nValidatorAgent) -> None:
        locales = {"en": {"hello": "Hello"}, "zh": {}}
        response = handle_message(
            agent,
            {"method": "validate_i18n", "params": {"locales": locales, "base_locale": "en"}},
        )
        assert response["status"] == "ok"
        assert response["result"]["overall_coverage"] == 0.0

    def test_handle_unknown_method(self, agent: I18nValidatorAgent) -> None:
        response = handle_message(agent, {"method": "unknown", "params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]
