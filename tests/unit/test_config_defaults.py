"""Unit tests for defaults module: constants, built-in providers, and tier map.

Focuses on structural correctness of DEFAULT_PROVIDERS, MODEL_TIER_MAP,
ENV_VAR_OVERRIDES, and path constants.
"""

from __future__ import annotations

from pathlib import Path

from agent_nexus.models.agent import ModelTier
from agent_nexus.models.config import ProviderApiType
from agent_nexus.platform.config import (
    CONFIG_FILE,
    DEFAULT_CONFIG_DIR,
    DEFAULT_MODEL_STRING,
    DEFAULT_PROVIDERS,
    ENV_VAR_OVERRIDES,
    LOCKFILE,
    MODEL_TIER_MAP,
    SOURCES_FILE,
)


# ============================================================================
# Path constants
# ============================================================================


class TestPathConstants:
    """Verify path constant values and types."""

    def test_config_file_is_toml(self) -> None:
        assert CONFIG_FILE == "config.toml"

    def test_sources_file_is_yaml(self) -> None:
        assert SOURCES_FILE == "sources.yaml"

    def test_lockfile_is_json(self) -> None:
        assert LOCKFILE == "lockfile.json"

    def test_default_config_dir_is_path(self) -> None:
        assert isinstance(DEFAULT_CONFIG_DIR, Path)

    def test_default_config_dir_has_agent_nexus(self) -> None:
        """Default config dir path ends with .agent-nexus."""
        assert DEFAULT_CONFIG_DIR.name == ".agent-nexus"

    def test_default_model_string_is_provider_model_format(self) -> None:
        assert ":" in DEFAULT_MODEL_STRING
        provider, model = DEFAULT_MODEL_STRING.split(":", 1)
        assert provider == "openai"
        assert model == "gpt-4o"


# ============================================================================
# DEFAULT_PROVIDERS
# ============================================================================


class TestDefaultProviders:
    """Verify structure and types of built-in provider definitions."""

    def test_six_built_in_providers(self) -> None:
        assert len(DEFAULT_PROVIDERS) == 6
        expected = {"openai", "anthropic", "deepseek", "minimax", "qwen", "ollama"}
        assert set(DEFAULT_PROVIDERS.keys()) == expected

    def test_openai_has_required_fields(self) -> None:
        openai = DEFAULT_PROVIDERS["openai"]
        assert "api_key_env" in openai
        assert "api" in openai
        assert openai["api"] == ProviderApiType.OPENAI_COMPATIBLE

    def test_anthropic_uses_messages_api(self) -> None:
        anthropic = DEFAULT_PROVIDERS["anthropic"]
        assert anthropic["api"] == ProviderApiType.ANTHROPIC_MESSAGES

    def test_ollama_has_local_base_url(self) -> None:
        ollama = DEFAULT_PROVIDERS["ollama"]
        assert "localhost" in ollama["base_url"]
        assert ollama["api_key_env"] == ""

    def test_deepseek_has_base_url(self) -> None:
        ds = DEFAULT_PROVIDERS["deepseek"]
        assert ds["base_url"].startswith("https://api.deepseek.com")

    def test_qwen_has_dashscope_url(self) -> None:
        qwen = DEFAULT_PROVIDERS["qwen"]
        assert "dashscope" in qwen["base_url"]

    def test_all_providers_have_api_field(self) -> None:
        for name, preset in DEFAULT_PROVIDERS.items():
            assert "api" in preset, f"Provider '{name}' missing 'api' field"


# ============================================================================
# MODEL_TIER_MAP
# ============================================================================


class TestModelTierMap:
    """Verify MODEL_TIER_MAP completeness and format."""

    def test_all_tiers_mapped(self) -> None:
        for tier in ModelTier:
            assert tier in MODEL_TIER_MAP, f"Tier {tier} missing from MODEL_TIER_MAP"

    def test_all_values_are_provider_model_format(self) -> None:
        for tier, model_string in MODEL_TIER_MAP.items():
            assert ":" in model_string, f"Tier {tier} value is not 'provider:model'"

    def test_lightweight_is_smallest(self) -> None:
        assert MODEL_TIER_MAP[ModelTier.LIGHTWEIGHT] == "openai:gpt-4o-mini"

    def test_premium_is_largest(self) -> None:
        assert MODEL_TIER_MAP[ModelTier.PREMIUM] == "anthropic:claude-opus-4-20250116"

    def test_standard_is_gpt4o(self) -> None:
        assert MODEL_TIER_MAP[ModelTier.STANDARD] == "openai:gpt-4o"

    def test_powerful_is_claude_sonnet(self) -> None:
        assert MODEL_TIER_MAP[ModelTier.POWERFUL] == "anthropic:claude-sonnet-4-20250514"


# ============================================================================
# ENV_VAR_OVERRIDES
# ============================================================================


class TestEnvVarOverrides:
    """Verify ENV_VAR_OVERRIDES mapping correctness."""

    def test_three_overrides_defined(self) -> None:
        assert len(ENV_VAR_OVERRIDES) == 3

    def test_agent_model_overrides_models_default(self) -> None:
        assert ENV_VAR_OVERRIDES["AGENT_MODEL"] == "models.default"

    def test_default_model_overrides_models_default(self) -> None:
        assert ENV_VAR_OVERRIDES["DEFAULT_MODEL"] == "models.default"

    def test_agent_nexus_home_overrides_config_dir(self) -> None:
        assert ENV_VAR_OVERRIDES["AGENT_NEXUS_HOME"] == "config_dir"

    def test_model_env_vars_target_same_path(self) -> None:
        """Both model env vars should map to the same config path."""
        assert ENV_VAR_OVERRIDES["AGENT_MODEL"] == ENV_VAR_OVERRIDES["DEFAULT_MODEL"]


# ============================================================================
# DEFAULT_MODEL_STRING
# ============================================================================


class TestDefaultModelString:
    """Verify the hardcoded default model string."""

    def test_default_model_string_value(self) -> None:
        assert DEFAULT_MODEL_STRING == "openai:gpt-4o"

    def test_default_matches_standard_tier(self) -> None:
        """The default model string matches the standard tier mapping."""
        assert DEFAULT_MODEL_STRING == MODEL_TIER_MAP[ModelTier.STANDARD]
