"""Data models for requirements-analyzer Agent.

Pydantic v2 frozen models for requirement analysis, question generation,
and specification building.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RequirementAnalysis(BaseModel):
    """Result of analyzing a requirement text.

    Attributes:
        text: The original requirement text.
        gaps: Identified missing information or incomplete areas.
        ambiguities: Ambiguous statements found in the text.
        priorities: Categorized priorities (high, medium, low).
        key_terms: Extracted key terms and concepts.
        contradictions: Contradictory statements found in the text.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    gaps: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    priorities: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "high": [],
            "medium": [],
            "low": [],
        }
    )
    key_terms: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)


class Question(BaseModel):
    """A clarifying question generated from requirement analysis.

    Attributes:
        text: The question text.
        category: Category of the question (functional, non_functional,
            constraint, priority, terminology).
        priority: Importance level of this question (high, medium, low).
        context: Related requirement context for this question.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    category: str = "functional"
    priority: str = "medium"
    context: str = ""


class RequirementSection(BaseModel):
    """A section within a requirement specification.

    Attributes:
        title: Section title.
        items: Requirement items within this section.
        priority: Overall priority of this section.
    """

    model_config = ConfigDict(frozen=True)

    title: str
    items: list[str] = Field(default_factory=list)
    priority: str = "medium"


class RequirementSpec(BaseModel):
    """Structured requirement specification document.

    Attributes:
        title: Specification title.
        sections: Requirement sections grouped by module/function.
        priorities: Priority matrix (must/should/could/wont).
        constraints: Technical and business constraints.
        acceptance_criteria: Verifiable acceptance criteria per requirement.
        glossary: Standardized term definitions.
    """

    model_config = ConfigDict(frozen=True)

    title: str
    sections: list[RequirementSection] = Field(default_factory=list)
    priorities: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "must": [],
            "should": [],
            "could": [],
            "wont": [],
        }
    )
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
