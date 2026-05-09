"""Fixed Integrator Agent — merges multi-expert artifacts into unified output."""

from __future__ import annotations

import logging
import string as _string
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Artifact:
    """A single expert's output artifact."""

    source_agent: str
    artifact_type: str
    sections: dict[str, object]
    metadata: dict[str, object] | None = None


@dataclass
class ConflictItem:
    """A detected conflict between two or more expert artifacts."""

    field: str
    description: str
    agents: list[str]


@dataclass
class IntegratedArtifact:
    """The unified output from merging multiple expert artifacts."""

    artifact_type: str = "integrated_plan"
    source_agents: list[str] = field(default_factory=list)
    merged_sections: dict[str, object] = field(default_factory=dict)
    conflicts: list[ConflictItem] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)


class Integrator:
    """Fixed integrator that merges expert artifacts.

    Follows doc §9.2 responsibilities:
    1. Read all expert artifacts
    2. Check output contract for missing content
    3. Merge consistent conclusions
    4. Flag conflicting viewpoints
    5. Select final recommendation
    6. Output unified plan
    """

    # Maximum size (in characters) for a single section value to prevent
    # memory exhaustion from oversized artifacts.
    MAX_SECTION_VALUE_SIZE: int = 500_000

    @staticmethod
    def _validate_section_value(key: str, value: object, source_agent: str, max_size: int) -> None:
        if isinstance(value, str) and len(value) > max_size:
            raise ValueError(
                f"Section '{key}' in artifact from '{source_agent}' "
                f"exceeds max size ({len(value)} > {max_size})"
            )
        if isinstance(value, (list, dict)):
            total_chars = sum(
                len(str(v)) for v in (value.values() if isinstance(value, dict) else value)
            )
            if total_chars > max_size:
                raise ValueError(
                    f"Section '{key}' in artifact from '{source_agent}' "
                    f"exceeds max aggregate size ({total_chars} > {max_size})"
                )

    @staticmethod
    def _merge_section_value(merged_sections: dict[str, object], key: str, value: object) -> None:
        if key not in merged_sections:
            merged_sections[key] = value
            return
        existing = merged_sections[key]
        if isinstance(existing, list) and isinstance(value, list):
            merged_sections[key] = existing + value
        elif isinstance(existing, dict) and isinstance(value, dict):
            merged_sections[key] = {**existing, **value}
        else:
            logger.warning(
                "Type mismatch for section '%s': existing=%s, new=%s; converting to list",
                key,
                type(existing).__name__,
                type(value).__name__,
            )
            converted = list(existing) if isinstance(existing, list) else [existing]
            converted.append(value)
            merged_sections[key] = converted

    @staticmethod
    def merge(
        artifacts: list[Artifact],
        expected_sections: list[str] | None = None,
    ) -> IntegratedArtifact:
        if not artifacts:
            raise ValueError("Need at least one artifact to merge")

        if len(artifacts) > 50:
            raise ValueError("Cannot merge more than 50 artifacts at once")

        source_agents = [a.source_agent for a in artifacts]

        merged_sections: dict[str, object] = {}
        risks: list[str] = []

        for artifact in artifacts:
            if len(artifact.sections) > 100:
                raise ValueError(
                    f"Artifact from '{artifact.source_agent}' has too many sections "
                    f"({len(artifact.sections)}); max 100"
                )
            for key, value in artifact.sections.items():
                Integrator._validate_section_value(
                    key, value, artifact.source_agent, Integrator.MAX_SECTION_VALUE_SIZE
                )
                Integrator._merge_section_value(merged_sections, key, value)

            _extract_risks(artifact, risks)

        conflicts = _detect_conflicts(artifacts)

        Integrator._collect_recommendations(artifacts, merged_sections)
        Integrator._add_decision_summary(merged_sections, len(artifacts), source_agents)
        open_questions = Integrator._check_expected_sections(merged_sections, expected_sections)

        return IntegratedArtifact(
            artifact_type="integrated_plan",
            source_agents=source_agents,
            merged_sections=merged_sections,
            conflicts=conflicts,
            risks=risks,
            open_questions=open_questions,
        )

    @staticmethod
    def _collect_recommendations(
        artifacts: list[Artifact], merged_sections: dict[str, object]
    ) -> None:
        recommendations: list[str] = []
        for artifact in artifacts:
            rec = artifact.sections.get("recommendation")
            if isinstance(rec, str):
                recommendations.append(f"{artifact.source_agent}: {rec}")
            elif isinstance(rec, list):
                for r in rec:
                    recommendations.append(f"{artifact.source_agent}: {r}")
        merged_sections["final_recommendation"] = (
            " | ".join(recommendations)
            if recommendations
            else "No explicit recommendations from experts"
        )

    @staticmethod
    def _add_decision_summary(
        merged_sections: dict[str, object], count: int, source_agents: list[str]
    ) -> None:
        merged_sections["decision_summary"] = (
            f"Integrated {count} expert artifacts from: " + ", ".join(source_agents)
        )

    @staticmethod
    def _check_expected_sections(
        merged_sections: dict[str, object],
        expected_sections: list[str] | None,
    ) -> list[str]:
        if not expected_sections:
            return []
        present_keys = set(merged_sections.keys())
        return [
            f"Missing section: '{section}' — no expert provided this content"
            for section in expected_sections
            if section not in present_keys
        ]


def _extract_strings_from_value(value: Any) -> list[str]:
    """Extract string descriptions from a section value (list or str)."""
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            if isinstance(item, dict) and "description" in item:
                result.append(item["description"])
            elif isinstance(item, str):
                result.append(item)
        return result
    if isinstance(value, str):
        return [value]
    return []


def _extract_risks(artifact: Artifact, risks: list[str]) -> None:
    """Extract risk descriptions from an artifact's sections."""
    for key, value in artifact.sections.items():
        if "risk" in key.lower() or "severity" in key.lower():
            risks.extend(_extract_strings_from_value(value))


def _normalize_severity(value: str) -> str:
    """Normalize severity terms to a standard high/medium/low scale."""
    lower = value.strip().lower()
    if lower in ("critical", "severe", "major", "high"):
        return "high"
    if lower in ("moderate", "warning", "medium"):
        return "medium"
    if lower in ("low", "minor", "info", "informational"):
        return "low"
    return lower


def _tokenize_risk(text: str) -> set[str]:
    """Normalize a risk description into a set of keyword tokens for comparison."""
    # Lowercase, strip punctuation, split into tokens
    lower = text.lower().strip()
    stripped = lower.translate(str.maketrans(_string.punctuation, " " * len(_string.punctuation)))
    tokens = set(stripped.split())
    # Remove common stop words that don't add meaning
    tokens -= {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "and",
        "or",
        "but",
        "not",
        "no",
        "if",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "as",
        "into",
        "through",
    }
    return tokens


def _risk_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not set_a and not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def _detect_conflicts(artifacts: list[Artifact]) -> list[ConflictItem]:
    """Detect conflicting viewpoints across artifacts."""
    if len(artifacts) < 2:
        return []

    return (
        _detect_severity_conflicts(artifacts)
        + _detect_risk_conflicts(artifacts)
        + _detect_recommendation_conflicts(artifacts)
    )


def _detect_severity_conflicts(artifacts: list[Artifact]) -> list[ConflictItem]:
    severity_by_agent: dict[str, str] = {}
    for artifact in artifacts:
        for key, value in artifact.sections.items():
            if key == "severity" and isinstance(value, str):
                severity_by_agent[artifact.source_agent] = _normalize_severity(value)

    if len(set(severity_by_agent.values())) <= 1:
        return []

    return [
        ConflictItem(
            field="severity",
            description=f"Disagreement on severity: {dict(severity_by_agent)}",
            agents=list(severity_by_agent.keys()),
        )
    ]


def _extract_risk_sets(artifacts: list[Artifact]) -> dict[str, list[str]]:
    """Extract per-agent risk descriptions from artifact sections."""
    risk_sets: dict[str, list[str]] = {}
    for artifact in artifacts:
        risks_found: list[str] = []
        for key, value in artifact.sections.items():
            if "risk" in key.lower() and isinstance(value, list):
                risks_found.extend(_extract_strings_from_value(value))
        if risks_found:
            risk_sets[artifact.source_agent] = risks_found
    return risk_sets


def _has_similar_risks(risk_sets: dict[str, list[str]]) -> bool:
    """Check if any pair of agents has Jaccard-similar risk tokens (>= 0.5)."""
    agent_risk_tokens: dict[str, set[str]] = {}
    for agent_id, risks in risk_sets.items():
        tokens: set[str] = set()
        for r in risks:
            tokens |= _tokenize_risk(r)
        agent_risk_tokens[agent_id] = tokens

    agent_ids = list(agent_risk_tokens.keys())
    for i in range(len(agent_ids)):
        for j in range(i + 1, len(agent_ids)):
            sim = _risk_similarity(agent_risk_tokens[agent_ids[i]], agent_risk_tokens[agent_ids[j]])
            if sim >= 0.5:
                return True
    return False


def _detect_risk_conflicts(artifacts: list[Artifact]) -> list[ConflictItem]:
    risk_sets = _extract_risk_sets(artifacts)

    if not _has_valid_risk_data(risk_sets):
        return []

    # No similar risks and all non-empty — check shared section overlap
    _structural_sections = frozenset({"final_recommendation", "decision_summary", "recommendation"})
    section_sets = [
        set(a.sections.keys()) - _structural_sections
        for a in artifacts
        if a.source_agent in risk_sets
    ]
    if not section_sets:
        return []

    shared = section_sets[0]
    for s in section_sets[1:]:
        shared = shared & s
    if not shared:
        return []

    return [
        ConflictItem(
            field="risks",
            description=("Experts have completely disjoint risk findings — potential blind spots"),
            agents=list(risk_sets.keys()),
        )
    ]


def _has_valid_risk_data(risk_sets: dict[str, list[str]]) -> bool:
    """Check if risk data is sufficient for conflict detection."""
    if len(risk_sets) < 2:
        return False
    if _has_similar_risks(risk_sets):
        return False
    return all(len(v) > 0 for v in risk_sets.values())


def _detect_recommendation_conflicts(artifacts: list[Artifact]) -> list[ConflictItem]:
    rec_by_agent: dict[str, str] = {}
    for artifact in artifacts:
        rec = artifact.sections.get("recommendation")
        if isinstance(rec, str):
            rec_by_agent[artifact.source_agent] = rec

    if len(set(rec_by_agent.values())) <= 1:
        return []

    return [
        ConflictItem(
            field="recommendation",
            description=f"Conflicting recommendations: {dict(rec_by_agent)}",
            agents=list(rec_by_agent.keys()),
        )
    ]
