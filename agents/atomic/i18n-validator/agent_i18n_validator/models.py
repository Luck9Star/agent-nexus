"""Data models for i18n-validator Agent.

Pydantic v2 frozen models for internationalization validation
findings and report generation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class I18nFinding(BaseModel):
    """A single i18n validation finding.

    Attributes:
        severity: Issue severity — error, warning, or info.
        category: Finding category (e.g. "missing_key", "empty_value", "extra_key").
        locale: The locale where the issue was found (e.g. "zh", "ja").
        key: The translation key affected.
        description: Human-readable description of the issue.
        remediation: Suggested fix for the issue.
    """

    model_config = ConfigDict(frozen=True)

    severity: str
    category: str
    locale: str
    key: str
    description: str = ""
    remediation: str = ""


class I18nLocaleStats(BaseModel):
    """Statistics for a single locale's translation coverage.

    Attributes:
        locale: Locale identifier (e.g. "en", "zh").
        total_keys: Total number of keys in the base locale.
        translated_keys: Number of keys present in this locale.
        missing_keys: Number of keys missing from this locale.
        coverage_percent: Translation coverage as a percentage (0-100).
    """

    model_config = ConfigDict(frozen=True)

    locale: str
    total_keys: int = 0
    translated_keys: int = 0
    missing_keys: int = 0
    coverage_percent: float = 0.0


class I18nReport(BaseModel):
    """Comprehensive i18n validation report.

    Attributes:
        findings: All validation findings.
        locale_stats: Per-locale coverage statistics.
        base_locale: The base/reference locale used.
        total_locales: Number of locales analyzed.
        total_keys: Number of keys in the base locale.
        overall_coverage: Average coverage across all locales.
    """

    model_config = ConfigDict(frozen=True)

    findings: list[I18nFinding] = Field(default_factory=list)
    locale_stats: list[I18nLocaleStats] = Field(default_factory=list)
    base_locale: str = "en"
    total_locales: int = 0
    total_keys: int = 0
    overall_coverage: float = 0.0
