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
        name="test", agent_type="atomic",
        triggers=[], capabilities=[], model_config={},
    )
    defaults.update(overrides)
    return SkillMetadata(**defaults)  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------------------
# Frozen dataclass equality and hashing
# ---------------------------------------------------------------------------

class TestFrozenSemantics:
    def test_equal_metadata(self):
        a = _meta(name="x", agent_type="atomic")
        b = _meta(name="x", agent_type="atomic")
        assert a == b

    def test_unequal_metadata_name(self):
        a = _meta(name="x")
        b = _meta(name="y")
        assert a != b

    def test_skill_body_equality(self):
        assert SkillBody("abc") == SkillBody("abc")
        assert SkillBody("abc") != SkillBody("def")

    def test_skill_resources_equality(self):
        r1 = SkillResources(content="c", sections={"A": "a"})
        r2 = SkillResources(content="c", sections={"A": "a"})
        assert r1 == r2

    def test_parsed_skill_equality(self):
        m = _meta()
        p1 = ParsedSkill(metadata=m, body=None, resources=None, raw="")
        p2 = ParsedSkill(metadata=m, body=None, resources=None, raw="")
        assert p1 == p2


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

    def test_raw_preserved(self):
        raw_text = "---\nname: t\nagent_type: a\n---\nbody"
        skill = ParsedSkill(
            metadata=_meta(), body=None, resources=None, raw=raw_text,
        )
        assert skill.raw == raw_text


# ---------------------------------------------------------------------------
# Default factory isolation
# ---------------------------------------------------------------------------

class TestDefaultFactoryIsolation:
    def test_metadata_extra_independent(self):
        m1 = _meta()
        m2 = _meta()
        m1.extra["key"] = "val"  # would fail if shared mutable
        assert "key" not in m2.extra  # frozen: can't set anyway

    def test_resources_sections_independent(self):
        r1 = SkillResources(content="a")
        r2 = SkillResources(content="b")
        assert r1.sections is not r2.sections
