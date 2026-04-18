"""Unit tests for agent_nexus.platform.skills — models and loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_nexus.platform.skills.loader import SkillLoader
from agent_nexus.platform.skills.models import (
    ParsedSkill,
    SkillBody,
    SkillMetadata,
    SkillResources,
)


# ---------------------------------------------------------------------------
# SkillMetadata
# ---------------------------------------------------------------------------

class TestSkillMetadata:
    def test_creation_with_all_fields(self):
        meta = SkillMetadata(
            name="doc-filler",
            agent_type="atomic",
            triggers=["fill docs"],
            capabilities=["write"],
            model_config={"recommended": "openai:gpt-4o"},
            extra={"version": "1.0"},
        )
        assert meta.name == "doc-filler"
        assert meta.agent_type == "atomic"
        assert meta.triggers == ["fill docs"]
        assert meta.capabilities == ["write"]
        assert meta.model_config == {"recommended": "openai:gpt-4o"}
        assert meta.extra == {"version": "1.0"}

    def test_defaults(self):
        meta = SkillMetadata(
            name="test-agent",
            agent_type="atomic",
            triggers=[],
            capabilities=[],
            model_config={},
        )
        assert meta.extra == {}
        assert meta.triggers == []
        assert meta.capabilities == []
        assert meta.model_config == {}

    def test_frozen_cannot_modify(self):
        meta = SkillMetadata(
            name="test", agent_type="atomic", triggers=[], capabilities=[], model_config={}
        )
        with pytest.raises(AttributeError):
            meta.name = "changed"

    def test_frozen_cannot_modify_list(self):
        meta = SkillMetadata(
            name="test", agent_type="atomic", triggers=["a"], capabilities=[], model_config={}
        )
        # Frozen dataclass prevents reassignment of the field itself
        with pytest.raises(AttributeError):
            meta.triggers = ["b"]


# ---------------------------------------------------------------------------
# SkillBody
# ---------------------------------------------------------------------------

class TestSkillBody:
    def test_creation(self):
        body = SkillBody(content="# Role\nYou are a helper.")
        assert body.content == "# Role\nYou are a helper."

    def test_frozen(self):
        body = SkillBody(content="some content")
        with pytest.raises(AttributeError):
            body.content = "new content"


# ---------------------------------------------------------------------------
# SkillResources
# ---------------------------------------------------------------------------

class TestSkillResources:
    def test_creation_with_sections(self):
        res = SkillResources(
            content="# Resources\n## Example\nHello",
            sections={"Example": "Hello"},
        )
        assert res.content == "# Resources\n## Example\nHello"
        assert res.sections == {"Example": "Hello"}

    def test_default_sections(self):
        res = SkillResources(content="# Resources")
        assert res.sections == {}

    def test_frozen(self):
        res = SkillResources(content="x")
        with pytest.raises(AttributeError):
            res.content = "y"


# ---------------------------------------------------------------------------
# ParsedSkill — tier methods
# ---------------------------------------------------------------------------

class TestParsedSkillTier0:
    def test_tier0_summary_full(self):
        meta = SkillMetadata(
            name="doc-filler",
            agent_type="atomic",
            triggers=["fill docs", "auto fill"],
            capabilities=["write"],
            model_config={"recommended": "openai:gpt-4o"},
        )
        skill = ParsedSkill(
            metadata=meta, body=None, resources=None, raw=""
        )
        summary = skill.tier0_summary()
        assert "Skill: doc-filler" in summary
        assert "Type: atomic" in summary
        assert "Triggers: fill docs, auto fill" in summary
        assert "Capabilities: write" in summary
        assert "Model: openai:gpt-4o" in summary

    def test_tier0_summary_without_model_recommendation(self):
        meta = SkillMetadata(
            name="reviewer",
            agent_type="atomic",
            triggers=["review"],
            capabilities=["read"],
            model_config={},
        )
        skill = ParsedSkill(
            metadata=meta, body=None, resources=None, raw=""
        )
        summary = skill.tier0_summary()
        assert "Model:" not in summary

    def test_tier0_summary_model_config_without_recommended(self):
        meta = SkillMetadata(
            name="reviewer",
            agent_type="atomic",
            triggers=[],
            capabilities=[],
            model_config={"fallback": "gpt-3.5"},
        )
        skill = ParsedSkill(
            metadata=meta, body=None, resources=None, raw=""
        )
        summary = skill.tier0_summary()
        assert "Model:" not in summary

    def test_tier0_summary_empty_triggers_and_capabilities(self):
        meta = SkillMetadata(
            name="minimal",
            agent_type="composite",
            triggers=[],
            capabilities=[],
            model_config={},
        )
        skill = ParsedSkill(
            metadata=meta, body=None, resources=None, raw=""
        )
        summary = skill.tier0_summary()
        assert "Skill: minimal" in summary
        assert "Type: composite" in summary
        assert "Triggers:" not in summary
        assert "Capabilities:" not in summary


class TestParsedSkillTier1:
    def test_tier1_summary_with_body(self):
        meta = SkillMetadata(name="t", agent_type="a", triggers=[], capabilities=[], model_config={})
        body = SkillBody(content="  Hello world  \n")
        skill = ParsedSkill(metadata=meta, body=body, resources=None, raw="")
        assert skill.tier1_summary() == "Hello world"

    def test_tier1_summary_no_body(self):
        meta = SkillMetadata(name="t", agent_type="a", triggers=[], capabilities=[], model_config={})
        skill = ParsedSkill(metadata=meta, body=None, resources=None, raw="")
        assert skill.tier1_summary() == ""


class TestParsedSkillTier2:
    def test_tier2_section_found(self):
        meta = SkillMetadata(name="t", agent_type="a", triggers=[], capabilities=[], model_config={})
        res = SkillResources(
            content="# Resources",
            sections={"Example Fill": "Here is an example...", "Template": "Template content"},
        )
        skill = ParsedSkill(metadata=meta, body=None, resources=res, raw="")
        assert skill.tier2_section("Example Fill") == "Here is an example..."
        assert skill.tier2_section("Template") == "Template content"

    def test_tier2_section_not_found(self):
        meta = SkillMetadata(name="t", agent_type="a", triggers=[], capabilities=[], model_config={})
        res = SkillResources(content="# Resources", sections={"Example": "data"})
        skill = ParsedSkill(metadata=meta, body=None, resources=res, raw="")
        assert skill.tier2_section("Nonexistent") is None

    def test_tier2_section_no_resources(self):
        meta = SkillMetadata(name="t", agent_type="a", triggers=[], capabilities=[], model_config={})
        skill = ParsedSkill(metadata=meta, body=None, resources=None, raw="")
        assert skill.tier2_section("anything") is None

    def test_tier2_section_case_insensitive(self):
        meta = SkillMetadata(name="t", agent_type="a", triggers=[], capabilities=[], model_config={})
        res = SkillResources(
            content="# Resources",
            sections={"Example Fill": "data"},
        )
        skill = ParsedSkill(metadata=meta, body=None, resources=res, raw="")
        assert skill.tier2_section("example fill") == "data"
        assert skill.tier2_section("Example Fill") == "data"
        assert skill.tier2_section("EXAMPLE FILL") == "data"
        assert skill.tier2_section("nonexistent") is None


# ---------------------------------------------------------------------------
# SkillLoader — _parse_frontmatter
# ---------------------------------------------------------------------------

class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        content = "---\nname: test\nagent_type: atomic\n---\nbody here"
        data, remaining = SkillLoader._parse_frontmatter(content)
        assert data == {"name": "test", "agent_type": "atomic"}
        assert remaining == "body here"

    def test_no_opening_delimiter(self):
        content = "name: test\n---\nbody"
        data, remaining = SkillLoader._parse_frontmatter(content)
        assert data is None
        assert remaining == content

    def test_no_closing_delimiter(self):
        content = "---\nname: test\nagent_type: atomic"
        data, remaining = SkillLoader._parse_frontmatter(content)
        assert data is None
        assert remaining == content

    def test_non_dict_yaml(self):
        content = "---\njust a string\n---\nbody"
        data, remaining = SkillLoader._parse_frontmatter(content)
        assert data is None
        assert remaining == content

    def test_empty_frontmatter(self):
        content = "---\n---\nbody here"
        data, remaining = SkillLoader._parse_frontmatter(content)
        assert data is None
        assert remaining == content

    def test_leading_newlines_stripped(self):
        content = "\n\n---\nname: test\n---\nbody"
        data, remaining = SkillLoader._parse_frontmatter(content)
        assert data == {"name": "test"}
        assert remaining == "body"

    def test_frontmatter_with_code_block_containing_dashes(self):
        """Dashes inside a fenced code block must not end the frontmatter."""
        content = (
            "---\n"
            "name: doc-filler\n"
            "agent_type: atomic\n"
            "---\n"
            "\n"
            "# Role\n"
            "Example:\n"
            "```\n"
            "some --- dashes\n"
            "---\n"
            "more content\n"
            "```\n"
        )
        data, remaining = SkillLoader._parse_frontmatter(content)
        assert data is not None
        assert data == {"name": "doc-filler", "agent_type": "atomic"}
        assert "# Role" in remaining
        assert "some --- dashes" in remaining


# ---------------------------------------------------------------------------
# SkillLoader — _split_body_resources
# ---------------------------------------------------------------------------

class TestSplitBodyResources:
    def test_with_resources_heading(self):
        content = "# Role\nYou are a helper.\n\n# Resources\n\n## Example\ndata"
        body, resources = SkillLoader._split_body_resources(content)
        assert body == "# Role\nYou are a helper."
        assert resources is not None
        assert "# Resources" in resources

    def test_without_resources_heading(self):
        content = "# Role\nYou are a helper."
        body, resources = SkillLoader._split_body_resources(content)
        assert body == "# Role\nYou are a helper."
        assert resources is None

    def test_starts_with_resources(self):
        content = "# Resources\n\n## Example\ndata"
        body, resources = SkillLoader._split_body_resources(content)
        assert body is None
        assert resources is not None

    def test_sub_heading_resources_not_split(self):
        content = "# Role\n## Resources sub-heading\nsome text"
        body, resources = SkillLoader._split_body_resources(content)
        assert body is not None
        assert "## Resources sub-heading" in body
        assert resources is None

    def test_split_body_resources_with_duplicate_line(self):
        """Body containing a line identical to '# Resources' inside a code block
        must NOT trigger a split; only the real '# Resources' heading outside a
        fence is used."""
        content = (
            "# Role\n"
            "You are a helper.\n"
            "\n"
            "```\n"
            "# Resources\n"
            "```\n"
            "\n"
            "More body text.\n"
            "\n"
            "# Resources\n"
            "\n"
            "## Example\n"
            "data here"
        )
        body, resources = SkillLoader._split_body_resources(content)
        # The body should contain everything up to the real # Resources heading
        assert body is not None
        assert "# Resources\n```" in body  # the one inside the code block
        assert "More body text." in body
        # The resources section should start at the real heading
        assert resources is not None
        assert resources.startswith("# Resources")
        assert "## Example" in resources
        assert "data here" in resources


# ---------------------------------------------------------------------------
# SkillLoader — _parse_resource_sections
# ---------------------------------------------------------------------------

class TestParseResourceSections:
    def test_multiple_sections(self):
        content = "# Resources\n\n## Example Fill\nHere is an example.\n\n## Template\nTemplate content here"
        sections = SkillLoader._parse_resource_sections(content)
        assert "Example Fill" in sections
        assert "Template" in sections
        assert "Here is an example." in sections["Example Fill"]
        assert "Template content here" in sections["Template"]

    def test_no_sub_headings(self):
        content = "# Resources\nJust some text without subsections."
        sections = SkillLoader._parse_resource_sections(content)
        assert sections == {}

    def test_single_section(self):
        content = "# Resources\n\n## Only One\nContent for this section."
        sections = SkillLoader._parse_resource_sections(content)
        assert sections == {"Only One": "Content for this section."}


# ---------------------------------------------------------------------------
# SkillLoader — _build_metadata
# ---------------------------------------------------------------------------

class TestBuildMetadata:
    def test_full_frontmatter(self):
        fm = {
            "name": "doc-filler",
            "agent_type": "atomic",
            "triggers": ["fill docs"],
            "capabilities": ["write"],
            "model_config": {"recommended": "openai:gpt-4o"},
        }
        meta = SkillLoader._build_metadata(fm)
        assert meta.name == "doc-filler"
        assert meta.agent_type == "atomic"
        assert meta.triggers == ["fill docs"]
        assert meta.capabilities == ["write"]
        assert meta.model_config == {"recommended": "openai:gpt-4o"}
        assert meta.extra == {}

    def test_minimal_frontmatter(self):
        fm = {"name": "reviewer", "agent_type": "atomic"}
        meta = SkillLoader._build_metadata(fm)
        assert meta.name == "reviewer"
        assert meta.triggers == []
        assert meta.capabilities == []
        assert meta.model_config == {}

    def test_extra_fields_captured(self):
        fm = {
            "name": "test",
            "agent_type": "atomic",
            "custom_field": "value",
            "another": 42,
        }
        meta = SkillLoader._build_metadata(fm)
        assert meta.extra == {"custom_field": "value", "another": 42}

    def test_missing_name_raises(self):
        fm = {"agent_type": "atomic"}
        with pytest.raises(ValueError, match="missing required field"):
            SkillLoader._build_metadata(fm)

    def test_empty_name_raises(self):
        """An empty-string name must be rejected."""
        fm = {"name": "", "agent_type": "atomic"}
        with pytest.raises(ValueError, match="must not be empty"):
            SkillLoader._build_metadata(fm)

    def test_whitespace_name_raises(self):
        """A whitespace-only name must be rejected."""
        fm = {"name": "   \t  ", "agent_type": "atomic"}
        with pytest.raises(ValueError, match="must not be empty"):
            SkillLoader._build_metadata(fm)


# ---------------------------------------------------------------------------
# SkillLoader — parse_string (integration of above parts)
# ---------------------------------------------------------------------------

VALID_SKILL_MD = """\
---
name: doc-filler
agent_type: atomic
triggers: ["fill docs"]
capabilities: ["write"]
model_config:
  recommended: openai:gpt-4o
---

# Role
You are a documentation filler.

# Resources

## Example Fill
Here is an example...

## Template
Template content here
"""

BODY_ONLY_MD = """\
---
name: doc-filler
agent_type: atomic
---

# Role
You are a documentation filler.
"""

RESOURCES_ONLY_MD = """\
---
name: doc-filler
agent_type: atomic
---

# Resources

## Example
Example data
"""

EMPTY_AFTER_FM_MD = """\
---
name: doc-filler
agent_type: atomic
---
"""


class TestParseString:
    def test_valid_full_skill_md(self):
        loader = SkillLoader()
        skill = loader.parse_string(VALID_SKILL_MD)

        assert skill.metadata.name == "doc-filler"
        assert skill.metadata.agent_type == "atomic"
        assert "fill docs" in skill.metadata.triggers
        assert "write" in skill.metadata.capabilities
        assert skill.metadata.model_config["recommended"] == "openai:gpt-4o"

        assert skill.body is not None
        assert "# Role" in skill.body.content
        assert "You are a documentation filler." in skill.body.content

        assert skill.resources is not None
        assert "Example Fill" in skill.resources.sections
        assert "Template" in skill.resources.sections
        assert "Here is an example..." in skill.resources.sections["Example Fill"]

        assert skill.raw == VALID_SKILL_MD

    def test_tier0_summary_from_parsed(self):
        loader = SkillLoader()
        skill = loader.parse_string(VALID_SKILL_MD)
        summary = skill.tier0_summary()
        assert "Skill: doc-filler" in summary
        assert "Type: atomic" in summary
        assert "Triggers: fill docs" in summary
        assert "Capabilities: write" in summary
        assert "Model: openai:gpt-4o" in summary

    def test_tier2_section_from_parsed(self):
        loader = SkillLoader()
        skill = loader.parse_string(VALID_SKILL_MD)
        assert skill.tier2_section("Example Fill") is not None
        assert "Here is an example..." in skill.tier2_section("Example Fill")
        assert skill.tier2_section("Template") is not None

    def test_body_only(self):
        loader = SkillLoader()
        skill = loader.parse_string(BODY_ONLY_MD)
        assert skill.body is not None
        assert "# Role" in skill.body.content
        assert skill.resources is None

    def test_resources_only(self):
        loader = SkillLoader()
        skill = loader.parse_string(RESOURCES_ONLY_MD)
        assert skill.body is None
        assert skill.resources is not None
        assert "Example" in skill.resources.sections

    def test_empty_after_frontmatter(self):
        loader = SkillLoader()
        skill = loader.parse_string(EMPTY_AFTER_FM_MD)
        assert skill.body is None
        assert skill.resources is None

    def test_no_frontmatter_raises(self):
        loader = SkillLoader()
        with pytest.raises(ValueError, match="No valid YAML frontmatter"):
            loader.parse_string("Just some text without frontmatter")

    def test_invalid_yaml_raises(self):
        loader = SkillLoader()
        content = "---\n: invalid yaml : [\n---\nbody"
        with pytest.raises(ValueError):
            loader.parse_string(content)

    def test_frontmatter_without_name_raises(self):
        loader = SkillLoader()
        content = "---\nagent_type: atomic\n---\nbody"
        with pytest.raises(ValueError, match="missing required field"):
            loader.parse_string(content)

    def test_frontmatter_empty_name_raises(self):
        """An empty name in frontmatter must be rejected at parse time."""
        loader = SkillLoader()
        content = "---\nname: ''\nagent_type: atomic\n---\nbody"
        with pytest.raises(ValueError, match="must not be empty"):
            loader.parse_string(content)

    def test_frontmatter_whitespace_name_raises(self):
        """A whitespace-only name in frontmatter must be rejected at parse time."""
        loader = SkillLoader()
        content = "---\nname: '   '\nagent_type: atomic\n---\nbody"
        with pytest.raises(ValueError, match="must not be empty"):
            loader.parse_string(content)

    def test_extra_fields_captured(self):
        loader = SkillLoader()
        content = "---\nname: test\nagent_type: atomic\ncustom: hello\n---\nbody"
        skill = loader.parse_string(content)
        assert skill.metadata.extra == {"custom": "hello"}


# ---------------------------------------------------------------------------
# SkillLoader — parse_file
# ---------------------------------------------------------------------------

class TestParseFile:
    def test_parse_real_file(self, tmp_path: Path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text(VALID_SKILL_MD, encoding="utf-8")

        loader = SkillLoader()
        skill = loader.parse_file(skill_file)
        assert skill.metadata.name == "doc-filler"
        assert skill.body is not None

    def test_file_not_found(self, tmp_path: Path):
        loader = SkillLoader()
        with pytest.raises(FileNotFoundError):
            loader.parse_file(tmp_path / "nonexistent.md")


# ---------------------------------------------------------------------------
# SkillLoader — load_agent_skills
# ---------------------------------------------------------------------------

class TestLoadAgentSkills:
    def test_directory_with_multiple_skills(self, tmp_path: Path):
        # Create two agent subdirectories each with a SKILL.md
        agent_a = tmp_path / "agent-a"
        agent_a.mkdir()
        (agent_a / "SKILL.md").write_text(
            "---\nname: agent-a\nagent_type: atomic\n---\nbody a",
            encoding="utf-8",
        )

        agent_b = tmp_path / "agent-b"
        agent_b.mkdir()
        (agent_b / "SKILL.md").write_text(
            "---\nname: agent-b\nagent_type: composite\n---\nbody b",
            encoding="utf-8",
        )

        loader = SkillLoader()
        skills = loader.load_agent_skills(tmp_path)
        assert len(skills) == 2
        names = {s.metadata.name for s in skills}
        assert names == {"agent-a", "agent-b"}

    def test_directory_with_no_skills(self, tmp_path: Path):
        loader = SkillLoader()
        skills = loader.load_agent_skills(tmp_path)
        assert skills == []

    def test_directory_with_invalid_skill_skips(self, tmp_path: Path):
        valid_dir = tmp_path / "valid"
        valid_dir.mkdir()
        (valid_dir / "SKILL.md").write_text(
            "---\nname: valid\nagent_type: atomic\n---\nbody",
            encoding="utf-8",
        )

        invalid_dir = tmp_path / "invalid"
        invalid_dir.mkdir()
        (invalid_dir / "SKILL.md").write_text(
            "No frontmatter here",
            encoding="utf-8",
        )

        loader = SkillLoader()
        skills = loader.load_agent_skills(tmp_path)
        assert len(skills) == 1
        assert skills[0].metadata.name == "valid"
