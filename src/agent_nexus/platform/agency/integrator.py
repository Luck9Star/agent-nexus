"""Fixed Integrator Agent — merges multi-expert artifacts into unified output."""

from __future__ import annotations

import logging
import string as _string
from dataclasses import dataclass, field

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
    def merge(
        artifacts: list[Artifact],
        expected_sections: list[str] | None = None,
    ) -> IntegratedArtifact:
        """Merge multiple expert artifacts into a single integrated output.

        Parameters
        ----------
        artifacts:
            List of expert artifacts to merge.
        expected_sections:
            Optional list of section names that should be present. Missing
            sections are reported in ``open_questions``.

        Raises:
            ValueError: If artifacts is empty.
        """
        if not artifacts:
            raise ValueError("Need at least one artifact to merge")

        if len(artifacts) > 50:
            raise ValueError("Cannot merge more than 50 artifacts at once")

        source_agents = [a.source_agent for a in artifacts]

        # Collect all sections, merging dicts and extending lists
        merged_sections: dict[str, object] = {}
        risks: list[str] = []

        for artifact in artifacts:
            if len(artifact.sections) > 100:
                raise ValueError(
                    f"Artifact from '{artifact.source_agent}' has too many sections "
                    f"({len(artifact.sections)}); max 100"
                )
            # Validate and merge sections in a single pass
            for key, value in artifact.sections.items():
                # Validate section value sizes to prevent memory exhaustion
                if isinstance(value, str) and len(value) > Integrator.MAX_SECTION_VALUE_SIZE:
                    raise ValueError(
                        f"Section '{key}' in artifact from '{artifact.source_agent}' "
                        f"exceeds max size ({len(value)} > {Integrator.MAX_SECTION_VALUE_SIZE})"
                    )
                if isinstance(value, (list, dict)):
                    total_chars = sum(
                        len(str(v)) for v in (value.values() if isinstance(value, dict) else value)
                    )
                    if total_chars > Integrator.MAX_SECTION_VALUE_SIZE:
                        raise ValueError(
                            f"Section '{key}' in artifact from '{artifact.source_agent}' "
                            f"exceeds max aggregate size ({total_chars} > "
                            f"{Integrator.MAX_SECTION_VALUE_SIZE})"
                        )
                # Merge into consolidated sections
                if key in merged_sections:
                    existing = merged_sections[key]
                    if isinstance(existing, list) and isinstance(value, list):
                        merged_sections[key] = existing + value
                    elif isinstance(existing, dict) and isinstance(value, dict):
                        merged_sections[key] = {**existing, **value}
                    else:
                        logger.warning(
                            "Type mismatch for section '%s': existing=%s, new=%s; "
                            "converting to list",
                            key,
                            type(existing).__name__,
                            type(value).__name__,
                        )
                        converted: list[object] = (
                            existing if isinstance(existing, list) else [existing]
                        )
                        converted.append(value)
                        merged_sections[key] = converted
                else:
                    merged_sections[key] = value

            # Extract risks from relevant sections
            _extract_risks(artifact, risks)

        # Detect conflicts (severity + recommendation viewpoints)
        conflicts = _detect_conflicts(artifacts)

        # Build final_recommendation
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

        # Build decision summary
        merged_sections["decision_summary"] = (
            f"Integrated {len(artifacts)} expert artifacts from: " + ", ".join(source_agents)
        )

        # Compute open_questions for missing expected sections
        open_questions: list[str] = []
        if expected_sections:
            present_keys = set(merged_sections.keys())
            for section in expected_sections:
                if section not in present_keys:
                    open_questions.append(
                        f"Missing section: '{section}' — no expert provided this content"
                    )

        return IntegratedArtifact(
            artifact_type="integrated_plan",
            source_agents=source_agents,
            merged_sections=merged_sections,
            conflicts=conflicts,
            risks=risks,
            open_questions=open_questions,
        )


def _extract_risks(artifact: Artifact, risks: list[str]) -> None:
    """Extract risk descriptions from an artifact's sections."""
    for key, value in artifact.sections.items():
        if "risk" in key.lower() or "severity" in key.lower():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and "description" in item:
                        risks.append(item["description"])
                    elif isinstance(item, str):
                        risks.append(item)
            elif isinstance(value, str):
                risks.append(value)


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


def _detect_risk_conflicts(artifacts: list[Artifact]) -> list[ConflictItem]:
    risk_sets: dict[str, list[str]] = {}
    for artifact in artifacts:
        risks_found: list[str] = []
        for key, value in artifact.sections.items():
            if "risk" in key.lower() and isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and "description" in item:
                        risks_found.append(item["description"])
                    elif isinstance(item, str):
                        risks_found.append(item)
        if risks_found:
            risk_sets[artifact.source_agent] = risks_found

    if len(risk_sets) < 2:
        return []

    # Compare risk descriptions across agents using token overlap
    agent_risk_tokens: dict[str, set[str]] = {}
    for agent_id, risks in risk_sets.items():
        agent_tokens: set[str] = set()
        for r in risks:
            agent_tokens |= _tokenize_risk(r)
        agent_risk_tokens[agent_id] = agent_tokens

    # Check if any pair of agents has similar risks (Jaccard >= 0.5)
    agent_ids = list(agent_risk_tokens.keys())
    has_similar_risks = False
    for i in range(len(agent_ids)):
        for j in range(i + 1, len(agent_ids)):
            sim = _risk_similarity(
                agent_risk_tokens[agent_ids[i]],
                agent_risk_tokens[agent_ids[j]],
            )
            if sim >= 0.5:
                has_similar_risks = True
                break
        if has_similar_risks:
            break

    if has_similar_risks or not all(len(v) > 0 for v in risk_sets.values()):
        return []

    # No similar risks and all non-empty — check shared section overlap
    _structural_sections = frozenset(
        {"final_recommendation", "decision_summary", "recommendation"}
    )
    agent_section_keys: dict[str, set[str]] = {}
    for artifact in artifacts:
        if artifact.source_agent in risk_sets:
            agent_section_keys[artifact.source_agent] = (
                set(artifact.sections.keys()) - _structural_sections
            )
    section_sets = list(agent_section_keys.values())
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
            description=(
                "Experts have completely disjoint risk "
                "findings — potential blind spots"
            ),
            agents=list(risk_sets.keys()),
        )
    ]


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
