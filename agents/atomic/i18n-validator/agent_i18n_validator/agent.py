"""I18nValidatorAgent — Internationalization completeness checking specialist.

Single-phase pipeline:
  1. validate_i18n() — check translation files for completeness and consistency

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_i18n_validator.models import I18nReport
from agent_i18n_validator.tools.validate_i18n import validate_i18n as _validate


class I18nValidatorAgent:
    """Internationalization completeness checking specialist.

    This agent validates translation files for key coverage, missing
    translations, empty values, and format consistency.

    Usage:
        agent = I18nValidatorAgent()
        report = agent.validate_i18n(
            locales={"en": {"hello": "Hello"}, "zh": {"hello": "你好"}},
            base_locale="en",
        )
        print(report.overall_coverage, report.total_keys)
    """

    def validate_i18n(
        self, locales: dict[str, dict[str, str]], base_locale: str = "en"
    ) -> I18nReport:
        """Validate translation files for completeness.

        Checks key coverage across locales, detects missing translations,
        empty values, and extra keys not present in the base locale.

        Args:
            locales: Mapping of locale codes to their key-value translations.
            base_locale: The reference locale to compare against.

        Returns:
            I18nReport with findings, per-locale stats, and overall coverage.
        """
        return _validate(locales, base_locale)
