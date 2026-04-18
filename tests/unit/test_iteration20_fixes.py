"""Iteration 20: HookDef enum types, ConfigLoader validation, SkillsLoader validation."""

import pytest

from agent_nexus.models.agent import AgentType, HookDef
from agent_nexus.models.hooks import HookEvent, HookType
from agent_nexus.platform.config.loader import ConfigLoader
from agent_nexus.platform.skills.loader import SkillLoader


# ---------------------------------------------------------------------------
# HookDef typed enums (was str, now HookType / HookEvent)
# ---------------------------------------------------------------------------

class TestHookDefEnumTypes:
    def test_type_must_be_hook_type_enum(self):
        h = HookDef(type=HookType.COMMAND, event=HookEvent.PRE_EXECUTION)
        assert isinstance(h.type, HookType)
        assert h.type == HookType.COMMAND

    def test_event_must_be_hook_event_enum(self):
        h = HookDef(type=HookType.HTTP, event=HookEvent.POST_EXECUTION)
        assert isinstance(h.event, HookEvent)

    def test_all_hook_type_values(self):
        for ht in HookType:
            h = HookDef(type=ht, event=HookEvent.PRE_EXECUTION)
            assert h.type is ht

    def test_all_hook_event_values(self):
        for he in HookEvent:
            h = HookDef(type=HookType.COMMAND, event=he)
            assert h.event is he

    def test_string_coerced_to_enum(self):
        """String literals are auto-coerced by Pydantic's StrEnum handling."""
        h = HookDef(type="command", event="pre_execution")
        assert isinstance(h.type, HookType)
        assert isinstance(h.event, HookEvent)
        assert h.type is HookType.COMMAND
        assert h.event is HookEvent.PRE_EXECUTION

    def test_serialization_round_trip(self):
        h = HookDef(type=HookType.AGENT, event=HookEvent.ON_ERROR)
        data = h.model_dump()
        h2 = HookDef(**data)
        assert h2 == h


# ---------------------------------------------------------------------------
# ConfigLoader ProviderApiType validation
# ---------------------------------------------------------------------------

class TestConfigLoaderProviderApiTypeValidation:
    def test_invalid_api_type_raises_clear_error(self, tmp_path):
        """An invalid api type string in config.toml should raise ValueError."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[models.providers.bad]\n'
            'api = "invalid_type"\n'
            'base_url = "http://localhost"\n'
        )
        loader = ConfigLoader(config_dir=config_dir)
        with pytest.raises(ValueError, match="Invalid api type 'invalid_type'"):
            loader.load_config()

    def test_valid_api_types_accepted(self, tmp_path):
        """All valid ProviderApiType values should be accepted."""
        from agent_nexus.models.config import ProviderApiType
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        # Write a config with each valid api type
        lines = []
        for i, pt in enumerate(ProviderApiType):
            lines.append(f'[models.providers.p{i}]')
            lines.append(f'api = "{pt.value}"')
            lines.append(f'base_url = "http://localhost/{i}"')
        (config_dir / "config.toml").write_text("\n".join(lines) + "\n")

        loader = ConfigLoader(config_dir=config_dir)
        config = loader.load_config()
        # The 3 test providers should all be present (merged with built-in defaults)
        for i, pt in enumerate(ProviderApiType):
            assert f"p{i}" in config.models.providers
            assert config.models.providers[f"p{i}"].api is pt

    def test_missing_config_file_still_works(self, tmp_path):
        """No config file at all should not raise — just use defaults."""
        config_dir = tmp_path / "empty_config"
        config_dir.mkdir()
        loader = ConfigLoader(config_dir=config_dir)
        config = loader.load_config()
        assert config.models.default is not None


# ---------------------------------------------------------------------------
# SkillsLoader._build_metadata KeyError protection
# ---------------------------------------------------------------------------

class TestSkillLoaderMetadataValidation:
    def test_missing_name_raises_value_error(self):
        with pytest.raises(ValueError, match="missing required field"):
            SkillLoader._build_metadata({"agent_type": "atomic"})

    def test_missing_agent_type_raises_value_error(self):
        with pytest.raises(ValueError, match="missing required field"):
            SkillLoader._build_metadata({"name": "test-skill"})

    def test_both_missing_raises_value_error(self):
        with pytest.raises(ValueError, match="name, agent_type"):
            SkillLoader._build_metadata({"triggers": ["test"]})

    def test_valid_frontmatter_succeeds(self):
        meta = SkillLoader._build_metadata(
            {"name": "my-skill", "agent_type": "atomic"}
        )
        assert meta.name == "my-skill"
        assert meta.agent_type == "atomic"

    def test_extra_fields_preserved(self):
        meta = SkillLoader._build_metadata(
            {"name": "s", "agent_type": "composite", "custom_field": 42}
        )
        assert meta.extra["custom_field"] == 42

    def test_parse_string_with_missing_name_raises(self):
        content = "---\nagent_type: atomic\n---\nBody text"
        with pytest.raises(ValueError, match="missing required field"):
            SkillLoader().parse_string(content)

    def test_parse_string_valid_skill(self):
        content = "---\nname: test-skill\nagent_type: atomic\n---\n# Role\nDo stuff"
        skill = SkillLoader().parse_string(content)
        assert skill.metadata.name == "test-skill"
        assert skill.body is not None
