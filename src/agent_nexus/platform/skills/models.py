"""Parsed skill types for SKILL.md 3-tier loading.

Tier 0 (SkillMetadata): YAML frontmatter — loaded every turn (~100 tokens).
Tier 1 (SkillBody): Main content after frontmatter — loaded on first interaction.
Tier 2 (SkillResources): Examples, templates, references — loaded on demand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SkillMetadata:
    """Tier 0: SKILL.md YAML frontmatter.

    This is the always-loaded identity card of a skill — name, type,
    triggers, capabilities, and model preferences. Kept under ~100 tokens.
    """

    name: str
    agent_type: str
    triggers: list[str]
    capabilities: list[str]
    model_config: dict[str, Any]
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillBody:
    """Tier 1: Main SKILL.md content (role, workflow, constraints).

    Everything between the YAML frontmatter and the first ``# Resources``
    heading. Loaded on first interaction with an agent.
    """

    content: str


@dataclass(frozen=True)
class SkillResources:
    """Tier 2: Examples, templates, references.

    Everything from ``# Resources`` onward. Subsections are parsed lazily
    on first access via ``SkillLoader._parse_resource_sections``.
    """

    content: str
    sections: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedSkill:
    """Complete parsed SKILL.md with tiered access.

    Attributes:
        metadata: Tier 0 frontmatter (always available).
        body: Tier 1 content, or None if SKILL.md has no body.
        resources: Tier 2 resources, or None if SKILL.md has no Resources section.
        raw: Original file content preserved for debugging and evolution.
    """

    metadata: SkillMetadata
    body: SkillBody | None
    resources: SkillResources | None
    raw: str

    def tier0_summary(self) -> str:
        """Format Tier 0 metadata for LLM context injection (~100 tokens).

        Returns a compact multi-line string suitable for system prompt
        injection every turn.
        """
        lines = [
            f"Skill: {self.metadata.name}",
            f"Type: {self.metadata.agent_type}",
        ]
        if self.metadata.triggers:
            lines.append(f"Triggers: {', '.join(self.metadata.triggers)}")
        if self.metadata.capabilities:
            lines.append(f"Capabilities: {', '.join(self.metadata.capabilities)}")
        if self.metadata.model_config:
            rec = self.metadata.model_config.get("recommended")
            if rec:
                lines.append(f"Model: {rec}")
        return "\n".join(lines)

    def tier1_summary(self) -> str:
        """Format Tier 1 body for LLM context injection.

        Returns the raw body markdown content, or an empty string if
        the skill has no body section.
        """
        if self.body is None:
            return ""
        return self.body.content.strip()

    def tier2_section(self, section_name: str) -> str | None:
        """Get a specific Tier 2 resource section by name.

        Section names are matched case-insensitively against the parsed
        subsection headings.

        Args:
            section_name: The heading text to look up (e.g. "Example Fill").

        Returns:
            The section content, or None if not found.
        """
        if self.resources is None:
            return None
        return self.resources.sections.get(section_name)
