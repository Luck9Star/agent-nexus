"""I18n validation tool — check translation files for completeness.

Compares key coverage across locales, detects missing translations,
empty values, and extra keys not present in the base locale.
"""

from __future__ import annotations

from agent_i18n_validator.models import I18nFinding, I18nLocaleStats, I18nReport


def validate_i18n(locales: dict[str, dict[str, str]], base_locale: str = "en") -> I18nReport:
    """Validate translation files for completeness and consistency.

    Compares each locale's keys against the base locale, detects missing
    translations, empty values, and extra keys.

    Args:
        locales: Mapping of locale codes to their key-value translations.
            e.g. {"en": {"hello": "Hello"}, "zh": {"hello": "你好"}}
        base_locale: The reference locale to compare against.

    Returns:
        I18nReport with findings, per-locale stats, and overall coverage.
    """
    findings: list[I18nFinding] = []
    locale_stats: list[I18nLocaleStats] = []

    # Get base locale keys
    base_keys = set(locales.get(base_locale, {}).keys())
    if not base_keys:
        findings.append(
            I18nFinding(
                severity="error",
                category="structure",
                locale=base_locale,
                key="<all>",
                description=f"Base locale '{base_locale}' has no translation keys",
                remediation="Add translation keys to the base locale",
            )
        )

    # Analyze each locale
    for locale, translations in locales.items():
        locale_keys = set(translations.keys())

        if locale == base_locale:
            # Check base locale for empty values
            for key in sorted(base_keys):
                value = translations.get(key, "")
                if not value:
                    findings.append(
                        I18nFinding(
                            severity="warning",
                            category="empty_value",
                            locale=locale,
                            key=key,
                            description=f"Empty value for key '{key}' in base locale",
                            remediation=f"Provide a value for '{key}' in '{locale}'",
                        )
                    )
            continue

        # Missing keys
        missing = base_keys - locale_keys
        for key in sorted(missing):
            findings.append(
                I18nFinding(
                    severity="error",
                    category="missing_key",
                    locale=locale,
                    key=key,
                    description=f"Missing translation key '{key}' in locale '{locale}'",
                    remediation=f"Add translation for '{key}' in '{locale}' locale",
                )
            )

        # Empty values in non-base locales
        for key in sorted(locale_keys & base_keys):
            value = translations.get(key, "")
            if not value:
                findings.append(
                    I18nFinding(
                        severity="warning",
                        category="empty_value",
                        locale=locale,
                        key=key,
                        description=f"Empty value for key '{key}' in locale '{locale}'",
                        remediation=f"Provide a translation for '{key}' in '{locale}'",
                    )
                )

        # Extra keys (not in base)
        extra = locale_keys - base_keys
        for key in sorted(extra):
            findings.append(
                I18nFinding(
                    severity="info",
                    category="extra_key",
                    locale=locale,
                    key=key,
                    description=f"Extra key '{key}' in locale '{locale}' not in base locale",
                    remediation=f"Consider adding '{key}' to base locale '{base_locale}'",
                )
            )

        # Compute stats for this locale
        translated = len(locale_keys & base_keys)
        total = len(base_keys)
        coverage = (translated / total * 100) if total > 0 else 0.0
        locale_stats.append(
            I18nLocaleStats(
                locale=locale,
                total_keys=total,
                translated_keys=translated,
                missing_keys=len(missing),
                coverage_percent=round(coverage, 1),
            )
        )

    # Add base locale stats (always 100% for itself)
    locale_stats.insert(
        0,
        I18nLocaleStats(
            locale=base_locale,
            total_keys=len(base_keys),
            translated_keys=len(base_keys),
            missing_keys=0,
            coverage_percent=100.0,
        ),
    )

    # Compute overall coverage (average of non-base locales)
    non_base_stats = [s for s in locale_stats if s.locale != base_locale]
    if non_base_stats:
        overall = sum(s.coverage_percent for s in non_base_stats) / len(non_base_stats)
    else:
        overall = 100.0

    return I18nReport(
        findings=findings,
        locale_stats=locale_stats,
        base_locale=base_locale,
        total_locales=len(locales),
        total_keys=len(base_keys),
        overall_coverage=round(overall, 1),
    )
