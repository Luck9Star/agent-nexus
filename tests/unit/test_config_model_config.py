"""Unit tests for ModelConfigManager: model resolution, provider lookup, API key resolution.

Focuses on resolve_model priority chain, get_provider_config, resolve_api_key
fallback logic, and parse_model_string edge cases.
"""

from __future__ import annotations

import pytest

from agent_nexus.models.agent import ModelTier
from agent_nexus.models.config import (
    ModelConfig,
    PlatformConfig,
    ProviderApiType,
    ProviderConfig,
    RuntimeConfig,
)
from agent_nexus.platform.config import DEFAULT_MODEL_STRING, MODEL_TIER_MAP, ModelConfigManager


def _make_config(**overrides) -> PlatformConfig:
    """Build a PlatformConfig for testing."""
    runtime = RuntimeConfig()
    models = ModelConfig(
        default=overrides.get("default", DEFAULT_MODEL_STRING),
        providers=overrides.get("providers", {}),
    )
    return PlatformConfig(runtime=runtime, models=models)


_CONFIG_ENV_VARS = (
    "AGENT_MODEL",
    "DEFAULT_MODEL",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "MINIMAX_API_KEY",
    "DASHSCOPE_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _CONFIG_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ============================================================================
# resolve_model — priority chain
# ============================================================================


class TestResolveModelPriority:
    """Verify resolve_model follows correct priority: env > recommended > tier > config default."""

    def test_env_var_wins_over_all(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT_MODEL", "env:model")
        config = _make_config(default="config:default")
        mgr = ModelConfigManager(config)
        result = mgr.resolve_model(
            "agent",
            recommended="manifest:model",
            recommended_tier=ModelTier.POWERFUL,
        )
        assert result == "env:model"

    def test_recommended_wins_over_tier(self) -> None:
        config = _make_config(default="config:default")
        mgr = ModelConfigManager(config)
        result = mgr.resolve_model(
            "agent",
            recommended="manifest:model",
            recommended_tier=ModelTier.LIGHTWEIGHT,
        )
        assert result == "manifest:model"

    def test_tier_wins_over_config_default(self) -> None:
        config = _make_config(default="config:default")
        mgr = ModelConfigManager(config)
        result = mgr.resolve_model("agent", recommended_tier=ModelTier.PREMIUM)
        assert result == MODEL_TIER_MAP[ModelTier.PREMIUM]

    def test_config_default_used_as_last_resort(self) -> None:
        config = _make_config(default="config:default")
        mgr = ModelConfigManager(config)
        result = mgr.resolve_model("agent")
        assert result == "config:default"

    def test_config_default_fallback_when_empty(self) -> None:
        """When config.models.default is empty, falls back to DEFAULT_MODEL_STRING."""
        config = _make_config(default="")
        mgr = ModelConfigManager(config)
        result = mgr.resolve_model("agent")
        assert result == DEFAULT_MODEL_STRING


# ============================================================================
# resolve_model — tier handling
# ============================================================================


class TestResolveModelTier:
    """Verify tier resolution with string and enum inputs."""

    def test_tier_as_string(self) -> None:
        config = _make_config()
        mgr = ModelConfigManager(config)
        result = mgr.resolve_model("agent", recommended_tier="lightweight")
        assert result == MODEL_TIER_MAP[ModelTier.LIGHTWEIGHT]

    def test_tier_as_enum(self) -> None:
        config = _make_config()
        mgr = ModelConfigManager(config)
        result = mgr.resolve_model("agent", recommended_tier=ModelTier.STANDARD)
        assert result == MODEL_TIER_MAP[ModelTier.STANDARD]

    def test_unknown_tier_string_falls_back(self) -> None:
        config = _make_config(default="fallback:model")
        mgr = ModelConfigManager(config)
        result = mgr.resolve_model("agent", recommended_tier="nonexistent_tier")
        assert result == "fallback:model"

    def test_all_four_tiers_resolve(self) -> None:
        config = _make_config()
        mgr = ModelConfigManager(config)
        for tier in ModelTier:
            result = mgr.resolve_model("agent", recommended_tier=tier)
            assert result == MODEL_TIER_MAP[tier]


# ============================================================================
# get_provider_config
# ============================================================================


class TestGetProviderConfig:
    """Verify get_provider_config lookup and default behavior."""

    def test_known_provider_returned(self) -> None:
        provider = ProviderConfig(
            base_url="https://api.test.com/v1",
            api_key_env="TEST_KEY",
            api=ProviderApiType.OPENAI_COMPATIBLE,
        )
        config = _make_config(providers={"test": provider})
        mgr = ModelConfigManager(config)
        result = mgr.get_provider_config("test")
        assert result is provider

    def test_unknown_provider_returns_empty_default(self) -> None:
        config = _make_config(providers={})
        mgr = ModelConfigManager(config)
        result = mgr.get_provider_config("missing")
        assert result.base_url == ""
        assert result.api_key_env == ""
        assert result.api == ProviderApiType.OPENAI_COMPATIBLE


# ============================================================================
# resolve_api_key
# ============================================================================


class TestResolveApiKey:
    """Verify resolve_api_key with string lookup, object lookup, and fallbacks."""

    def test_key_from_provider_config_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_KEY", "secret-123")
        provider = ProviderConfig(api_key_env="MY_KEY")
        config = _make_config(providers={"custom": provider})
        mgr = ModelConfigManager(config)
        assert mgr.resolve_api_key("custom") == "secret-123"

    def test_fallback_to_well_known_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic")
        provider = ProviderConfig()  # no api_key_env
        config = _make_config(providers={"anthropic": provider})
        mgr = ModelConfigManager(config)
        assert mgr.resolve_api_key("anthropic") == "sk-anthropic"

    def test_empty_when_no_env_set(self) -> None:
        config = _make_config(providers={"openai": ProviderConfig(api_key_env="OPENAI_API_KEY")})
        mgr = ModelConfigManager(config)
        assert mgr.resolve_api_key("openai") == ""

    def test_object_lookup_uses_identity(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        provider = ProviderConfig(api_key_env="")
        config = _make_config(providers={"deepseek": provider})
        mgr = ModelConfigManager(config)
        # Pass the same object — identity match enables fallback lookup
        assert mgr.resolve_api_key(provider) == "sk-ds"

    def test_string_lookup_unknown_provider_returns_empty(self) -> None:
        config = _make_config(providers={})
        mgr = ModelConfigManager(config)
        assert mgr.resolve_api_key("unknown_provider") == ""


# ============================================================================
# parse_model_string
# ============================================================================


class TestParseModelString:
    """Verify parse_model_string splitting logic."""

    def test_standard_provider_model(self) -> None:
        config = _make_config()
        mgr = ModelConfigManager(config)
        provider, model = mgr.parse_model_string("deepseek:deepseek-chat")
        assert provider == "deepseek"
        assert model == "deepseek-chat"

    def test_no_colon_defaults_openai(self) -> None:
        config = _make_config()
        mgr = ModelConfigManager(config)
        provider, model = mgr.parse_model_string("gpt-4o-mini")
        assert provider == "openai"
        assert model == "gpt-4o-mini"

    def test_model_with_colon_in_name(self) -> None:
        """Only the first colon is used to split."""
        config = _make_config()
        mgr = ModelConfigManager(config)
        provider, model = mgr.parse_model_string("ollama:llama3:8b")
        assert provider == "ollama"
        assert model == "llama3:8b"


# ============================================================================
# resolve_api_key empty string logging (iter85 fix)
# ============================================================================


class TestResolveApiKeyLogging:
    """resolve_api_key logs warning when returning empty string."""

    def test_empty_api_key_logs_warning(self) -> None:
        """When no API key is found, a warning is logged before returning ''."""
        import logging
        from unittest.mock import patch

        config = _make_config()
        mgr = ModelConfigManager(config)

        with patch.dict("os.environ", {}, clear=True):
            with patch.object(
                logging.getLogger("agent_nexus.platform.config.model_config"),
                "warning",
            ) as mock_warn:
                result = mgr.resolve_api_key("openai")

        assert result == ""
        # Check that our new "No API key found" warning was logged
        assert any("No API key found" in str(call) for call in mock_warn.call_args_list)
