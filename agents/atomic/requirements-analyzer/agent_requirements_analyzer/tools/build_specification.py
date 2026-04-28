"""Specification building tool -- assemble final structured requirement specification.

Takes analyzed requirements and user answers to produce a RequirementSpec.
"""

from __future__ import annotations

from agent_requirements_analyzer.models import (
    RequirementAnalysis,
    RequirementSection,
    RequirementSpec,
)


def _build_glossary(
    key_terms: list[str],
    answers: dict[str, str],
) -> dict[str, str]:
    """Build a glossary from key terms and user-provided answers."""
    glossary: dict[str, str] = {}
    for term in key_terms:
        if term in answers:
            glossary[term] = answers[term]
    return glossary


def _build_sections(
    analysis: RequirementAnalysis,
    answers: dict[str, str],
) -> list[RequirementSection]:
    """Build requirement sections from analysis and answers.

    Groups requirements into logical sections based on content.
    """
    sections: list[RequirementSection] = []

    # Functional requirements section
    functional_items: list[str] = []
    for item in analysis.priorities.get("high", []) + analysis.priorities.get("medium", []):
        functional_items.append(item)
    # Incorporate answers that relate to functionality
    for key, value in answers.items():
        if any(kw in key for kw in ["功能", "角色", "认证", "数据", "流程", "feature", "role", "auth", "data", "process"]):
            functional_items.append(f"{key}: {value}")

    if functional_items:
        sections.append(RequirementSection(
            title="功能需求",
            items=functional_items,
            priority="high",
        ))

    # Non-functional requirements section
    non_functional_items: list[str] = []
    for item in analysis.priorities.get("low", []):
        non_functional_items.append(item)
    for key, value in answers.items():
        if any(kw in key for kw in ["性能", "安全", "可用", "性能", "perf", "security", "avail"]):
            non_functional_items.append(f"{key}: {value}")

    if non_functional_items:
        sections.append(RequirementSection(
            title="非功能需求",
            items=non_functional_items,
            priority="medium",
        ))

    # Constraints section (from contradictions and gap answers)
    constraint_items: list[str] = []
    for contradiction in analysis.contradictions:
        if contradiction in answers:
            constraint_items.append(f"矛盾解决: {answers[contradiction]}")
        else:
            constraint_items.append(contradiction)

    if constraint_items:
        sections.append(RequirementSection(
            title="约束条件",
            items=constraint_items,
            priority="high",
        ))

    return sections


def _build_priorities(
    analysis: RequirementAnalysis,
    answers: dict[str, str],
) -> dict[str, list[str]]:
    """Build the MoSCoW priority matrix from analysis and answers."""
    priorities: dict[str, list[str]] = {
        "must": [],
        "should": [],
        "could": [],
        "wont": [],
    }

    # High priority items become "must"
    for item in analysis.priorities.get("high", []):
        priorities["must"].append(item)

    # Medium priority items become "should"
    for item in analysis.priorities.get("medium", []):
        priorities["should"].append(item)

    # Low priority items become "could"
    for item in analysis.priorities.get("low", []):
        priorities["could"].append(item)

    # Check answers for explicit priority overrides
    for key, value in answers.items():
        lower_value = value.lower()
        if "不需要" in value or "不做" in value or "wont" in lower_value:
            priorities["wont"].append(f"{key}: {value}")
        elif "必须" in value or "must" in lower_value:
            priorities["must"].append(f"{key}: {value}")
        elif "应该" in value or "should" in lower_value:
            priorities["should"].append(f"{key}: {value}")
        elif "可以" in value or "could" in lower_value:
            priorities["could"].append(f"{key}: {value}")

    return priorities


def _build_constraints(
    analysis: RequirementAnalysis,
    answers: dict[str, str],
) -> list[str]:
    """Build the constraints list from analysis and answers."""
    constraints: list[str] = []

    # Add contradictions as constraints
    for contradiction in analysis.contradictions:
        constraints.append(contradiction)

    # Add constraint-related answers
    for key, value in answers.items():
        if any(kw in key for kw in ["约束", "限制", "constraint", "限制", "技术", "tech", "时间", "time"]):
            constraints.append(f"{key}: {value}")

    return constraints


def _build_acceptance_criteria(
    analysis: RequirementAnalysis,
    answers: dict[str, str],
) -> list[str]:
    """Build acceptance criteria from analysis and answers."""
    criteria: list[str] = []

    # Generate criteria from high-priority items
    for item in analysis.priorities.get("high", []):
        criteria.append(f"验收标准: {item} — 功能完整实现并通过测试")

    # Check for explicit acceptance criteria in answers
    for key, value in answers.items():
        if "验收" in key or "acceptance" in key.lower() or "标准" in key:
            criteria.append(value)

    return criteria


def build_specification(
    answers: dict[str, str],
    analysis: RequirementAnalysis | None = None,
    title: str = "需求说明书",
) -> RequirementSpec:
    """Build a structured requirement specification from analysis and answers.

    Args:
        answers: User-provided answers to clarifying questions.
        analysis: The RequirementAnalysis from phase 1. If None, an empty
            analysis is used.
        title: Title for the specification document.

    Returns:
        RequirementSpec with sections, priorities, constraints, acceptance
        criteria, and glossary.
    """
    if analysis is None:
        analysis = RequirementAnalysis(text="")

    sections = _build_sections(analysis, answers)
    priorities = _build_priorities(analysis, answers)
    constraints = _build_constraints(analysis, answers)
    acceptance_criteria = _build_acceptance_criteria(analysis, answers)
    glossary = _build_glossary(analysis.key_terms, answers)

    return RequirementSpec(
        title=title,
        sections=sections,
        priorities=priorities,
        constraints=constraints,
        acceptance_criteria=acceptance_criteria,
        glossary=glossary,
    )
