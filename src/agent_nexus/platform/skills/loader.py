"""SkillLoader: parse SKILL.md files with 3-tier access.

SKILL.md follows this structure::

    ---
    name: doc-filler
    agent_type: atomic
    triggers: [...]
    capabilities: [...]
    model_config: {...}
    ---

    # Role
    ...body content...

    # Resources

    ## Example Fill
    ...resource sections...
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from agent_nexus.platform.skills.models import (
    ParsedSkill,
    SkillBody,
    SkillMetadata,
    SkillResources,
)

logger = logging.getLogger(__name__)

# Regex to split at a top-level "# Resources" heading (must be at start of line,
# optionally preceded by blank lines, but NOT a sub-heading like "## Resources").
_RESOURCES_SPLIT_RE = re.compile(r"^#\s+Resources\s*$", re.MULTILINE)

# Regex to find sub-headings within the Resources section (## Heading).
_SUBSECTION_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


class SkillLoader:
    """Parse SKILL.md files into :class:`ParsedSkill` objects with 3-tier access.

    Usage::

        loader = SkillLoader()
        skill = loader.parse_file(Path("agents/atomic/doc-filler/SKILL.md"))
        print(skill.metadata.name)        # Tier 0
        print(skill.tier1_summary())       # Tier 1
        print(skill.tier2_section("..."))  # Tier 2
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_file(self, path: Path) -> ParsedSkill:
        """Parse a SKILL.md file on disk into a :class:`ParsedSkill`.

        Args:
            path: Absolute or relative path to the SKILL.md file.

        Raises:
            FileNotFoundError: If *path* does not exist.
            ValueError: If the file has no valid YAML frontmatter.
        """
        content = path.read_text(encoding="utf-8")
        return self.parse_string(content, source=str(path))

    def parse_string(self, content: str, source: str = "<string>") -> ParsedSkill:
        """Parse a SKILL.md content string into a :class:`ParsedSkill`.

        Args:
            content: The full text of a SKILL.md file.
            source: Descriptive label used in log/error messages.

        Raises:
            ValueError: If no valid YAML frontmatter is found.
        """
        raw = content

        frontmatter, remaining = self._parse_frontmatter(content)
        if frontmatter is None:
            raise ValueError(f"No valid YAML frontmatter found in {source}")

        metadata = self._build_metadata(frontmatter)

        body_content, resources_content = self._split_body_resources(remaining)

        body = SkillBody(content=body_content) if body_content is not None else None

        resources: SkillResources | None = None
        if resources_content is not None:
            sections = self._parse_resource_sections(resources_content)
            resources = SkillResources(content=resources_content, sections=sections)

        return ParsedSkill(
            metadata=metadata,
            body=body,
            resources=resources,
            raw=raw,
        )

    def load_agent_skills(self, agent_dir: Path) -> list[ParsedSkill]:
        """Load all SKILL.md files from an agent package directory.

        Recursively searches *agent_dir* for files named ``SKILL.md``
        (case-sensitive).  Duplicate skill names (across multiple files)
        are detected and only the first occurrence is kept.

        Args:
            agent_dir: Root directory of an agent package.

        Returns:
            A list of parsed skills (may be empty if no SKILL.md found).
        """
        skills: list[ParsedSkill] = []
        seen_names: set[str] = set()
        for skill_file in sorted(agent_dir.rglob("SKILL.md")):
            try:
                skill = self.parse_file(skill_file)
            except (ValueError, OSError):
                logger.warning("Failed to parse %s, skipping", skill_file, exc_info=True)
                continue

            name = skill.metadata.name
            if name in seen_names:
                logger.warning(
                    "Duplicate skill name '%s' from %s, keeping first occurrence",
                    name,
                    skill_file,
                )
                continue
            seen_names.add(name)
            skills.append(skill)
        return skills

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_frontmatter(content: str) -> tuple[dict | None, str]:
        """Extract YAML frontmatter delimited by ``---``.

        Returns:
            A tuple of (frontmatter_dict, remaining_content).
            If no valid frontmatter is found, returns (None, content).
        """
        stripped = content.lstrip("\n")
        if not stripped.startswith("---"):
            return None, content

        # Find the closing delimiter.  The YAML frontmatter spec requires the
        # closing ``---`` to be on its own line.  A simple ``.find()`` would
        # incorrectly match ``---`` that appears inside fenced code blocks or
        # as a horizontal rule in the Markdown body, so we use a regex that
        # anchors to end-of-line (with optional trailing whitespace).
        match = re.search(r"\n---\s*$", stripped[3:], re.MULTILINE)
        if match is None:
            return None, content

        close_idx = 3 + match.start()

        yaml_text = stripped[3:close_idx]  # text between opening and closing ---
        remaining = stripped[close_idx + 4 :].lstrip("\n")  # after closing ---

        try:
            data = yaml.safe_load(yaml_text)
        except yaml.YAMLError:
            return None, content

        if not isinstance(data, dict):
            return None, content

        return data, remaining

    @staticmethod
    def _split_body_resources(content: str) -> tuple[str | None, str | None]:
        """Split content at the first top-level ``# Resources`` heading.

        Skips ``# Resources`` matches that occur inside fenced code blocks.

        Returns:
            (body_content, resources_content). Either may be None.
        """
        resources_start: int | None = None
        in_fence = False
        offset = 0
        for line in content.splitlines():
            line_len = len(line) + 1  # +1 for the newline stripped by splitlines()
            if in_fence:
                if line.lstrip().startswith("```"):
                    in_fence = False
            else:
                if line.lstrip().startswith("```"):
                    in_fence = True
                elif _RESOURCES_SPLIT_RE.match(line):
                    resources_start = offset
                    break
            offset += line_len

        if resources_start is None:
            # No Resources section -- everything is body.
            body = content.strip()
            return (body if body else None), None

        body = content[:resources_start].strip()
        resources = content[resources_start:].strip()

        return (body if body else None), (resources if resources else None)

    @staticmethod
    def _parse_resource_sections(resources_content: str) -> dict[str, str]:
        """Parse Tier 2 into subsections by ``##`` headings.

        Returns:
            A dict mapping heading text (stripped) to the content under it.
        """
        sections: dict[str, str] = {}
        matches = list(_SUBSECTION_RE.finditer(resources_content))

        for i, m in enumerate(matches):
            heading = m.group(1).strip()
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(resources_content)
            section_body = resources_content[start:end].strip()
            sections[heading] = section_body

        return sections

    @staticmethod
    def _build_metadata(frontmatter: dict) -> SkillMetadata:
        """Build a :class:`SkillMetadata` from raw frontmatter dict.

        Required fields: ``name``, ``agent_type``.
        Optional fields: ``triggers``, ``capabilities``, ``model_config``.
        Any extra fields are captured in ``extra``.

        Raises:
            ValueError: If ``name`` or ``agent_type`` is missing from frontmatter.
        """
        missing = [k for k in ("name", "agent_type") if k not in frontmatter]
        if missing:
            raise ValueError(
                f"SKILL.md frontmatter missing required field(s): {', '.join(missing)}"
            )

        name_val = str(frontmatter["name"]).strip()
        if not name_val:
            raise ValueError("SKILL.md frontmatter 'name' must not be empty or whitespace-only")

        type_val = str(frontmatter["agent_type"]).strip()
        if not type_val:
            raise ValueError(
                "SKILL.md frontmatter 'agent_type' must not be empty or whitespace-only"
            )

        known_keys = {"name", "agent_type", "triggers", "capabilities", "model_config"}
        extra = {k: v for k, v in frontmatter.items() if k not in known_keys}

        raw_triggers = frontmatter.get("triggers", [])
        if isinstance(raw_triggers, list):
            raw_triggers = [str(t) for t in raw_triggers if t is not None]
        elif raw_triggers is not None:
            raw_triggers = [str(raw_triggers)]
        else:
            raw_triggers = []
        raw_capabilities = frontmatter.get("capabilities", [])
        raw_model_config = frontmatter.get("model_config", {})

        return SkillMetadata(
            name=name_val,
            agent_type=type_val,
            triggers=raw_triggers,
            capabilities=(
                raw_capabilities if isinstance(raw_capabilities, list) else [raw_capabilities]
            ),
            model_config=raw_model_config if isinstance(raw_model_config, dict) else {},
            extra=extra,
        )
