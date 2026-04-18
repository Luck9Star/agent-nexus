"""agent-localization-specialist — Translation and localization specialist.

Manages terminology glossaries, detects text register/formality, and provides
context-aware translation with glossary matching.
"""

from agent_localization_specialist.agent import LocalizationSpecialistAgent
from agent_localization_specialist.models import (
    Glossary,
    LocalizationResult,
    TermEntry,
    TextAnalysis,
)

__all__ = [
    "LocalizationSpecialistAgent",
    "TermEntry",
    "TextAnalysis",
    "Glossary",
    "LocalizationResult",
]
