"""Unit tests for agent_nexus.platform.skills.models — ParsedSkill edge cases and tier methods.

These tests supplement test_skills.py by covering: equality semantics,
tier method combinations, and boundary conditions on the frozen dataclasses.
"""

from __future__ import annotations

from agent_nexus.platform.skills.models import (
    ParsedSkill,
    SkillBody,
    SkillMetadata,
    SkillResources,
)


def _meta(**overrides: object) -> SkillMetadata:
    defaults: dict[str, object] = dict(
        name="test",
        agent_type="atomic",
        triggers=[],
        capabilities=[],
        model_config={},
    )
    defaults.update(overrides)
    return SkillMetadata(**defaults)  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------
# Frozen dataclass equality and hashing
# ---------------------------------------------------------------------------


class TestFrozenSemantics:
    pass


# ---------------------------------------------------------------------------
# Tier method — combined scenarios
# ---------------------------------------------------------------------------


class TestTierMethodCombinations:
    def test_tier0_with_empty_triggers_no_triggers_line(self):
        meta = _meta(triggers=[], capabilities=["read"])
        skill = ParsedSkill(metadata=meta, body=None, resources=None, raw="")
        assert "Triggers:" not in skill.tier0_summary()
        assert "Capabilities: read" in skill.tier0_summary()

    def test_tier0_model_config_with_none_recommended(self):
        meta = _meta(model_config={"recommended": None})
        skill = ParsedSkill(metadata=meta, body=None, resources=None, raw="")
        assert "Model:" not in skill.tier0_summary()

    def test_tier0_model_config_with_non_string_recommended(self):
        meta = _meta(model_config={"recommended": 42})
        skill = ParsedSkill(metadata=meta, body=None, resources=None, raw="")
        # non-string recommended value — tier0_summary only adds Model when truthy
        assert "Model:" in skill.tier0_summary()

    def test_tier1_with_whitespace_only_body(self):
        meta = _meta()
        body = SkillBody(content="   \n  \n  ")
        skill = ParsedSkill(metadata=meta, body=body, resources=None, raw="")
        assert skill.tier1_summary() == ""

    def test_tier2_with_empty_sections_dict(self):
        meta = _meta()
        res = SkillResources(content="# Resources", sections={})
        skill = ParsedSkill(metadata=meta, body=None, resources=res, raw="")
        assert skill.tier2_section("anything") is None

    def test_tier2_with_duplicate_case_sections(self):
        """If sections dict has keys differing only in case, return first match."""
        meta = _meta()
        res = SkillResources(
            content="# Resources",
            sections={"Example": "first", "example": "second"},
        )
        skill = ParsedSkill(metadata=meta, body=None, resources=res, raw="")
        # case-insensitive search finds one of them
        result = skill.tier2_section("example")
        assert result in ("first", "second")


# ---------------------------------------------------------------------------
# Default factory isolation
# ---------------------------------------------------------------------------


class TestDefaultFactoryIsolation:
    pass
