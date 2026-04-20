"""Localization tool — translate text using glossary and register awareness.

Matches source text terms against the provided glossary, applies case-preserving
substitutions, and produces a LocalizationResult with matched terms and warnings.
"""

from __future__ import annotations

import re

from agent_localization_specialist.models import LocalizationResult


def _preserve_case(match: re.Match, replacement: str) -> str:
    """Preserve the casing of the original match."""
    original = match.group(0)
    if original.isupper():
        return replacement.upper()
    if original[0].isupper():
        return replacement[0].upper() + replacement[1:]
    return replacement.lower()


def localize(
    text: str,
    target_lang: str,
    glossary: dict | None = None,
) -> LocalizationResult:
    """Localize text using a glossary for term consistency.

    Applies glossary term substitutions to the source text. For each glossary
    entry (source -> target), replaces occurrences of the source term with
    the target translation, preserving surrounding formatting.

    Args:
        text: Source text to localize.
        target_lang: Target language code (e.g. "zh", "ja", "ko").
        glossary: Mapping of source terms to target translations.

    Returns:
        LocalizationResult with translated text, matched terms, and warnings.
    """
    if not text.strip():
        return LocalizationResult(translated_text=text)

    if not glossary:
        return LocalizationResult(
            translated_text=text,
            warnings=["No glossary provided — text returned unchanged"],
        )

    translated = text
    matches: list[str] = []
    warnings: list[str] = []

    # Sort glossary entries by key length (longest first) to avoid partial matches
    sorted_entries = sorted(glossary.items(), key=lambda x: len(x[0]), reverse=True)

    # Pre-compile all glossary patterns once
    compiled = [
        (re.compile(r"\b" + re.escape(src) + r"\b", re.IGNORECASE), src, tgt)
        for src, tgt in sorted_entries
        if src
    ]

    for pattern, source_term, target_term in compiled:
        occurrences = pattern.findall(translated)
        if occurrences:
            matches.append(source_term)
            translated = pattern.sub(lambda m: _preserve_case(m, target_term), translated)

    # Check for terms that might need glossary entries
    _detect_untranslated(translated, target_lang, warnings)

    return LocalizationResult(
        translated_text=translated,
        glossary_matches=matches,
        warnings=warnings,
    )


def _detect_untranslated(
    text: str, target_lang: str, warnings: list[str]
) -> None:
    """Detect potentially untranslated technical terms.

    Heuristic: if target language is CJK and text still contains long
    English words, they may need glossary entries.
    """
    cjk_langs = {"zh", "ja", "ko", "zh-cn", "zh-tw"}
    if target_lang.lower() not in cjk_langs:
        return

    # Find English words longer than 6 chars that might be untranslated
    long_en_words = re.findall(r"\b[a-zA-Z]{7,}\b", text)
    seen: set[str] = set()
    for word in long_en_words:
        w = word.lower()
        if w not in seen:
            seen.add(w)
            if len(seen) > 5:
                warnings.append(
                    f"More untranslated terms detected (showing first 5): {', '.join(sorted(seen))}"
                )
                return
            warnings.append(
                f"Potentially untranslated term: '{word}' — consider adding to glossary"
            )
