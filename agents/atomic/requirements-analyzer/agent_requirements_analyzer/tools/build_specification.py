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


_FUNCTIONAL_KEYWORDS = [
    "功能",
    "角色",
    "认证",
    "数据",
    "流程",
    "feature",
    "role",
    "auth",
    "data",
    "process",
]
_NON_FUNCTIONAL_KEYWORDS = ["性能", "安全", "可用", "perf", "security", "avail"]


def _build_functional_section(
    analysis: RequirementAnalysis, answers: dict[str, str]
) -> RequirementSection | None:
    """Build the functional requirements section."""
    items = list(analysis.priorities.get("high", []) + analysis.priorities.get("medium", []))
    for key, value in answers.items():
        if any(kw in key for kw in _FUNCTIONAL_KEYWORDS):
            items.append(f"{key}: {value}")
    return RequirementSection(title="功能需求", items=items, priority="high") if items else None


def _build_non_functional_section(
    analysis: RequirementAnalysis, answers: dict[str, str]
) -> RequirementSection | None:
    """Build the non-functional requirements section."""
    items = list(analysis.priorities.get("low", []))
    for key, value in answers.items():
        if any(kw in key for kw in _NON_FUNCTIONAL_KEYWORDS):
            items.append(f"{key}: {value}")
    return RequirementSection(title="非功能需求", items=items, priority="medium") if items else None


def _build_constraint_section(
    analysis: RequirementAnalysis, answers: dict[str, str]
) -> RequirementSection | None:
    """Build the constraints section from contradictions and gap answers."""
    items: list[str] = []
    for contradiction in analysis.contradictions:
        if contradiction in answers:
            items.append(f"矛盾解决: {answers[contradiction]}")
        else:
            items.append(contradiction)
    return RequirementSection(title="约束条件", items=items, priority="high") if items else None


def _build_sections(
    analysis: RequirementAnalysis,
    answers: dict[str, str],
) -> list[RequirementSection]:
    """Build requirement sections from analysis and answers."""
    builders = [_build_functional_section, _build_non_functional_section, _build_constraint_section]
    return [s for builder in builders if (s := builder(analysis, answers)) is not None]


_LEVEL_TO_MOSCOW: dict[str, str] = {
    "high": "must",
    "medium": "should",
    "low": "could",
}

_OVERRIDE_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("不需要", "不做", "wont"), "wont"),
    (("必须", "must"), "must"),
    (("应该", "should"), "should"),
    (("可以", "could"), "could"),
]


def _classify_override(value: str) -> str:
    """Classify an answer value into a MoSCoW priority category."""
    lower = value.lower()
    for keywords, moscow in _OVERRIDE_KEYWORDS:
        if any(kw in lower for kw in keywords):
            return moscow
    return "could"


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

    for level, moscow in _LEVEL_TO_MOSCOW.items():
        priorities[moscow].extend(analysis.priorities.get(level, []))

    for key, value in answers.items():
        moscow = _classify_override(value)
        priorities[moscow].append(f"{key}: {value}")

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
        if any(
            kw in key
            for kw in ["约束", "限制", "constraint", "限制", "技术", "tech", "时间", "time"]
        ):
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
