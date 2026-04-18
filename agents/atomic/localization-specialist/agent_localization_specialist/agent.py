"""LocalizationSpecialistAgent — Translation and localization specialist.

Three-phase pipeline:
  1. analyze_text()      — detect register, domain, key terms
  2. manage_glossary()   — CRUD for terminology glossary
  3. localize()          — translate with glossary and register awareness

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_localization_specialist.models import (
    Glossary,
    LocalizationResult,
    TextAnalysis,
)
from agent_localization_specialist.tools.analyze_text import analyze_text as _analyze
from agent_localization_specialist.tools.localize import localize as _localize
from agent_localization_specialist.tools.manage_glossary import (
    manage_glossary as _manage,
)


class LocalizationSpecialistAgent:
    """Translation and localization specialist with glossary management.

    This agent provides a three-phase pipeline for localization:
    Phase 1 (analyze_text) detects formality, domain, and key terms.
    Phase 2 (manage_glossary) maintains terminology consistency via CRUD.
    Phase 3 (localize) performs glossary-aware translation.

    Usage:
        agent = LocalizationSpecialistAgent()
        analysis = agent.analyze_text("The API endpoint requires authentication.", "en")
        print(analysis.formality, analysis.domain, analysis.key_terms)
        glossary = agent.manage_glossary("add", entries=[
            {"source": "API", "target": "API", "domain": "tech"},
            {"source": "endpoint", "target": "端点", "domain": "tech"},
        ])
        result = agent.localize(
            "The API endpoint requires authentication.",
            "zh",
            {"API": "API", "endpoint": "端点", "authentication": "认证"},
        )
        print(result.translated_text, result.glossary_matches)
    """

    def analyze_text(self, text: str, source_lang: str = "en") -> TextAnalysis:
        """Phase 1: Analyze source text for localization preparation.

        Detects register, domain, key terms, and complexity.

        Args:
            text: Source text to analyze.
            source_lang: Source language code.

        Returns:
            TextAnalysis with register, domain, key terms, and complexity.
        """
        return _analyze(text, source_lang)

    def manage_glossary(
        self,
        action: str,
        entries: list | None = None,
        glossary: Glossary | None = None,
        source_lang: str = "en",
        target_lang: str = "zh",
    ) -> Glossary:
        """Phase 2: Manage terminology glossary with CRUD operations.

        Args:
            action: "add", "list", "search", "delete", or "clear".
            entries: List of TermEntry objects or dicts.
            glossary: Existing glossary to operate on.
            source_lang: Source language code.
            target_lang: Target language code.

        Returns:
            Updated Glossary.
        """
        return _manage(action, entries, glossary, source_lang, target_lang)

    def localize(
        self,
        text: str,
        target_lang: str,
        glossary: dict | None = None,
    ) -> LocalizationResult:
        """Phase 3: Translate text using glossary for term consistency.

        Applies glossary term substitutions to produce localized text.

        Args:
            text: Source text to localize.
            target_lang: Target language code.
            glossary: Mapping of source terms to target translations.

        Returns:
            LocalizationResult with translated text and glossary matches.
        """
        return _localize(text, target_lang, glossary)
