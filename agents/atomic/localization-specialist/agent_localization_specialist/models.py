"""Data models for localization-specialist Agent.

Pydantic v2 frozen models for text analysis, glossary management,
and localization results.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TermEntry(BaseModel):
    """A single term entry in the glossary.

    Attributes:
        source: Source language term.
        target: Target language translation.
        context: Usage context or notes for the term.
        domain: Subject domain (e.g. "tech", "legal", "medical", "business", "general").
    """

    model_config = ConfigDict(frozen=True)

    source: str
    target: str = ""
    context: str = ""
    domain: str = "general"


class TextAnalysis(BaseModel):
    """Result of analyzing source text for localization.

    Attributes:
        formality: Formality level — "formal", "neutral", or "informal".
        domain: Detected subject domain.
        key_terms: Important terms identified in the text.
        complexity: Estimated translation complexity — "low", "medium", or "high".
    """

    model_config = ConfigDict(frozen=True)

    formality: str = "neutral"
    domain: str = "general"
    key_terms: list[str] = Field(default_factory=list)
    complexity: str = "medium"


class Glossary(BaseModel):
    """A terminology glossary mapping source terms to translations.

    Attributes:
        source_lang: Source language code (e.g. "en", "zh", "ja").
        target_lang: Target language code.
        entries: List of term entries in the glossary.
    """

    model_config = ConfigDict(frozen=True)

    source_lang: str = "en"
    target_lang: str = "zh"
    entries: list[TermEntry] = Field(default_factory=list)


class LocalizationResult(BaseModel):
    """Result of a localization/translation operation.

    Attributes:
        translated_text: The translated text (simulated for offline mode).
        glossary_matches: Terms matched from the glossary during translation.
        warnings: Warnings about uncertain translations or missing terms.
    """

    model_config = ConfigDict(frozen=True)

    translated_text: str = ""
    glossary_matches: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
