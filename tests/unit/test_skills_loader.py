"""Unit tests for agent_nexus.platform.skills.loader — parse_file edge cases and load_agent_skills error paths.

Supplements test_skills.py by testing: file I/O edge cases, encoding,
recursive directory scanning, and malformed YAML variants.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_nexus.platform.skills.loader import SkillLoader


# ---------------------------------------------------------------------------
# parse_file — I/O edge cases
# ---------------------------------------------------------------------------

class TestParseFileEdgeCases:
    def test_utf8_content(self, tmp_path: Path):
        f = tmp_path / "SKILL.md"
        f.write_text(
            "---\nname: utf8-agent\nagent_type: atomic\n---\n\n# Role\n中文内容测试",
            encoding="utf-8",
        )
        skill = SkillLoader().parse_file(f)
        assert "中文内容测试" in skill.body.content

    def test_empty_file_raises(self, tmp_path: Path):
        f = tmp_path / "SKILL.md"
        f.write_text("", encoding="utf-8")
        with pytest.raises(ValueError, match="No valid YAML frontmatter"):
            SkillLoader().parse_file(f)

    def test_whitespace_only_file_raises(self, tmp_path: Path):
        f = tmp_path / "SKILL.md"
        f.write_text("   \n  \n  ", encoding="utf-8")
        with pytest.raises(ValueError, match="No valid YAML frontmatter"):
            SkillLoader().parse_file(f)


# ---------------------------------------------------------------------------
# parse_string — YAML edge cases
# ---------------------------------------------------------------------------

class TestParseStringYamlEdgeCases:
    def test_scalar_triggers_wrapped_in_list(self):
        """When triggers is a string instead of list, it gets wrapped."""
        content = "---\nname: t\nagent_type: a\ntriggers: single\n---\nbody"
        skill = SkillLoader().parse_string(content)
        assert skill.metadata.triggers == ["single"]

    def test_scalar_capabilities_wrapped_in_list(self):
        content = "---\nname: t\nagent_type: a\ncapabilities: one\n---\nbody"
        skill = SkillLoader().parse_string(content)
        assert skill.metadata.capabilities == ["one"]

    def test_non_dict_model_config_ignored(self):
        content = "---\nname: t\nagent_type: a\nmodel_config: string\n---\nbody"
        skill = SkillLoader().parse_string(content)
        assert skill.metadata.model_config == {}

    def test_yaml_with_list_values(self):
        content = "---\nname: t\nagent_type: a\ntriggers:\n  - a\n  - b\n---\nbody"
        skill = SkillLoader().parse_string(content)
        assert skill.metadata.triggers == ["a", "b"]

    def test_frontmatter_with_multiline_string(self):
        content = "---\nname: t\nagent_type: a\ndescription: |\n  Line one\n  Line two\n---\nbody"
        skill = SkillLoader().parse_string(content)
        assert "Line one" in skill.metadata.extra.get("description", "")


# ---------------------------------------------------------------------------
# load_agent_skills — recursive scanning and error resilience
# ---------------------------------------------------------------------------

class TestLoadAgentSkillsRecursive:
    def test_nested_directories(self, tmp_path: Path):
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        (deep / "SKILL.md").write_text(
            "---\nname: deep-skill\nagent_type: atomic\n---\ndeep body",
            encoding="utf-8",
        )
        skills = SkillLoader().load_agent_skills(tmp_path)
        assert len(skills) == 1
        assert skills[0].metadata.name == "deep-skill"

    def test_non_skill_md_files_ignored(self, tmp_path: Path):
        """Only files named exactly SKILL.md are picked up by rglob."""
        agent_dir = tmp_path / "agent"
        agent_dir.mkdir()
        (agent_dir / "SKILL.md").write_text(
            "---\nname: real\nagent_type: atomic\n---\nbody", encoding="utf-8",
        )
        (agent_dir / "README.md").write_text("not a skill", encoding="utf-8")
        (agent_dir / "NOTES.md").write_text("also not a skill", encoding="utf-8")
        skills = SkillLoader().load_agent_skills(tmp_path)
        assert len(skills) == 1
        assert skills[0].metadata.name == "real"

    def test_oserror_file_skipped(self, tmp_path: Path):
        """A file that raises OSError during read is silently skipped."""
        good_dir = tmp_path / "good"
        good_dir.mkdir()
        (good_dir / "SKILL.md").write_text(
            "---\nname: ok\nagent_type: atomic\n---\nbody", encoding="utf-8",
        )
        loader = SkillLoader()
        with pytest.MonkeyPatch.context() as mp:
            original_read = Path.read_text

            def bad_read(self_path, *args, **kwargs):
                # Only raise for files NOT in the "good" directory
                if "SKILL.md" in str(self_path) and "good" not in str(self_path):
                    raise OSError("mock IO error")
                return original_read(self_path, *args, **kwargs)

            mp.setattr(Path, "read_text", bad_read)
            skills = loader.load_agent_skills(tmp_path)
        assert len(skills) == 1
        assert skills[0].metadata.name == "ok"

    def test_multiple_skills_sorted_by_path(self, tmp_path: Path):
        for name in ("z-agent", "a-agent", "m-agent"):
            d = tmp_path / name
            d.mkdir()
            (d / "SKILL.md").write_text(
                f"---\nname: {name}\nagent_type: atomic\n---\nbody",
                encoding="utf-8",
            )
        skills = SkillLoader().load_agent_skills(tmp_path)
        names = [s.metadata.name for s in skills]
        assert len(names) == 3
        # sorted() on rglob results: a-agent, m-agent, z-agent
        assert names == ["a-agent", "m-agent", "z-agent"]


# ---------------------------------------------------------------------------
# _split_body_resources — fenced code block edge cases
# ---------------------------------------------------------------------------

class TestSplitBodyResourcesFenced:
    def test_code_block_with_language_tag(self):
        content = "# Role\n```python\n# Resources\nprint('hi')\n```\n\n# Resources\n\n## Ex\ndata"
        body, resources = SkillLoader._split_body_resources(content)
        assert body is not None
        assert "```python" in body
        assert resources is not None
        assert resources.startswith("# Resources")

    def test_multiple_fenced_blocks(self):
        content = (
            "# Role\n```python\n# Resources\n```\n"
            "```js\n# Resources\n```\n\n"
            "# Resources\n\n## Ex\ndata"
        )
        body, resources = SkillLoader._split_body_resources(content)
        assert body is not None
        assert resources is not None
        assert "## Ex" in resources


# ---------------------------------------------------------------------------
# iter122 regression: non-string triggers are coerced to strings
# ---------------------------------------------------------------------------


class TestBuildMetadataNonStringTriggers:
    """Non-string trigger items in frontmatter are coerced to strings."""

    def test_integer_triggers_coerced(self):
        """Integer triggers like [1, 2] become ['1', '2']."""
        frontmatter = {"name": "t", "agent_type": "a", "triggers": [1, 2, 3]}
        meta = SkillLoader._build_metadata(frontmatter)
        assert meta.triggers == ["1", "2", "3"]

    def test_mixed_triggers_coerced(self):
        """Mixed types in triggers list are coerced to strings."""
        frontmatter = {"name": "t", "agent_type": "a", "triggers": ["text", 42, None]}
        meta = SkillLoader._build_metadata(frontmatter)
        assert meta.triggers == ["text", "42"]
        # None is filtered out by the `if t is not None` guard


# iter122 regression: triggers type coercion

class TestTriggersTypeCoercion:
    """SkillLoader coerces non-list triggers to list of strings."""

    def test_triggers_string_coerced_to_list(self, tmp_path: Path) -> None:
        f = tmp_path / "SKILL.md"
        f.write_text(
            "---\nname: str-triggers\nagent_type: atomic\ntriggers: hello\n---\n\n# Role\ntest",
            encoding="utf-8",
        )
        skill = SkillLoader().parse_file(f)
        assert skill.metadata.triggers == ["hello"]

    def test_triggers_numeric_coerced_to_str(self, tmp_path: Path) -> None:
        f = tmp_path / "SKILL.md"
        f.write_text(
            "---\nname: num-triggers\nagent_type: atomic\ntriggers:\n  - 42\n  - 3.14\n---\n\n# Role\ntest",
            encoding="utf-8",
        )
        skill = SkillLoader().parse_file(f)
        assert skill.metadata.triggers == ["42", "3.14"]

    def test_triggers_null_becomes_empty_list(self, tmp_path: Path) -> None:
        f = tmp_path / "SKILL.md"
        f.write_text(
            "---\nname: null-triggers\nagent_type: atomic\ntriggers:\n---\n\n# Role\ntest",
            encoding="utf-8",
        )
        skill = SkillLoader().parse_file(f)
        assert skill.metadata.triggers == []
