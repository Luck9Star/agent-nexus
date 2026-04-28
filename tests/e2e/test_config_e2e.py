"""E2E tests for configuration system: loading, merging, env var overrides.

Tests the full config loading pipeline from TOML file through merge to runtime.
"""

import pytest


@pytest.fixture
def config_dir(tmp_path):
    """Create a config directory with test config.toml."""
    cfg = tmp_path / "config.toml"
    cfg.write_text("""
schema_version = "1.0"

[models]
default = "anthropic:claude-sonnet-4-20250514"

[models.stages]
planner = "anthropic:claude-sonnet-4-20250514"
executor = "openai:gpt-4o"

[providers.anthropic]
api_type = "anthropic"
api_key_env = "ANTHROPIC_API_KEY"

[providers.openai]
api_type = "openai"
api_key_env = "OPENAI_API_KEY"
""")
    return tmp_path


class TestConfigE2E:
    """E2E configuration scenarios."""

    def test_load_full_config(self, config_dir):
        """ConfigLoader loads and merges TOML with defaults."""
        from agent_nexus.platform.config.loader import ConfigLoader

        loader = ConfigLoader(config_dir=config_dir)
        config = loader.load_config()

        assert config.models.default == "anthropic:claude-sonnet-4-20250514"

    def test_env_var_overrides_config(self, config_dir, monkeypatch):
        """Environment variables override config.toml values."""
        from agent_nexus.platform.config.loader import ConfigLoader

        monkeypatch.setenv("AGENT_MODEL", "openai:gpt-4o-mini")

        loader = ConfigLoader(config_dir=config_dir)
        config = loader.load_config()

        # AGENT_MODEL should override config.toml default
        assert config.models.default == "openai:gpt-4o-mini"

    def test_missing_config_uses_defaults(self, tmp_path):
        """ConfigLoader returns defaults when config.toml doesn't exist."""
        from agent_nexus.platform.config.loader import ConfigLoader

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()

        loader = ConfigLoader(config_dir=empty_dir)
        config = loader.load_config()

        # Should use built-in defaults
        assert config.models.default is not None

    def test_model_capability_registry(self):
        """ModelCapabilityRegistry resolves known model strings."""
        from agent_nexus.models.capability import ModelCapabilityRegistry

        registry = ModelCapabilityRegistry()
        cap = registry.get("claude-sonnet-4-20250514")
        assert cap is not None
        assert cap.provider == "anthropic"
