"""Unit tests for the platform config module.

Covers ConfigLoader, ModelConfigManager, and defaults — covering config
loading, env var overrides, model resolution, and provider API key resolution.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agent_nexus.models.agent import ModelTier
from agent_nexus.models.config import PlatformConfig, ProviderApiType, ProviderConfig
from agent_nexus.platform.config import (
    CONFIG_FILE,
    DEFAULT_CONFIG_DIR,
    DEFAULT_MODEL_STRING,
    DEFAULT_PROVIDERS,
    ENV_VAR_OVERRIDES,
    MODEL_TIER_MAP,
    SOURCES_FILE,
    ConfigLoader,
    ModelConfigManager,
)

# ============================================================================
# Helpers
# ============================================================================

# Env vars that tests may set / unset — clean up after every test to prevent
# leakage across test cases.
_CONFIG_ENV_VARS = (
    "AGENT_MODEL",
    "DEFAULT_MODEL",
    "AGENT_NEXUS_HOME",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "DEEPSEEK_API_KEY",
    "MINIMAX_API_KEY",
    "DASHSCOPE_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all config-related env vars before each test."""
    for var in _CONFIG_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _write_config(tmp_path: Path, content: str) -> Path:
    """Write a config.toml inside *tmp_path* and return its path."""
    cfg = tmp_path / CONFIG_FILE
    cfg.write_text(content, encoding="utf-8")
    return cfg


def _write_sources(tmp_path: Path, content: str) -> Path:
    """Write a sources.yaml inside *tmp_path* and return its path."""
    src = tmp_path / SOURCES_FILE
    src.write_text(content, encoding="utf-8")
    return src


# ============================================================================
# ConfigLoader Tests
# ============================================================================


class TestConfigLoader:
    """Tests for ConfigLoader.load_config, load_sources, and ensure_config_dir."""

    def test_load_empty_config(self, tmp_path: Path) -> None:
        """No config.toml present — returns built-in defaults."""
        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()

        assert config.models.default == DEFAULT_MODEL_STRING
        assert config.runtime.python_path == "python3"
        assert config.runtime.uv_path == "uv"
        # All 6 built-in providers should be present
        assert set(config.models.providers.keys()) == set(DEFAULT_PROVIDERS.keys())

    def test_load_with_runtime_section(self, tmp_path: Path) -> None:
        """Parses [runtime] python_path and uv_path."""
        _write_config(
            tmp_path,
            '[runtime]\npython_path = "/usr/bin/python3.12"\nuv_path = "/opt/uv/bin/uv"\n',
        )
        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()

        assert config.runtime.python_path == "/usr/bin/python3.12"
        assert config.runtime.uv_path == "/opt/uv/bin/uv"

    def test_load_with_model_default(self, tmp_path: Path) -> None:
        """Parses [models] default."""
        _write_config(tmp_path, '[models]\ndefault = "anthropic:claude-sonnet-4-20250514"\n')
        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()

        assert config.models.default == "anthropic:claude-sonnet-4-20250514"

    def test_load_with_providers(self, tmp_path: Path) -> None:
        """Parses provider configs and merges with built-in defaults."""
        _write_config(
            tmp_path,
            (
                "[models]\n"
                'default = "deepseek:deepseek-chat"\n'
                "\n"
                "[models.providers.deepseek]\n"
                'base_url = "https://custom.deepseek.api/v1"\n'
                'api_key_env = "MY_DEEPSEEK_KEY"\n'
                'api = "openai-compatible"\n'
                "\n"
                "[models.providers.custom_provider]\n"
                'base_url = "http://localhost:8080/v1"\n'
                'api_key_env = "CUSTOM_KEY"\n'
                'api = "openai-compatible"\n'
            ),
        )
        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()

        # deepseek should have overridden base_url and api_key_env
        ds = config.models.providers["deepseek"]
        assert ds.base_url == "https://custom.deepseek.api/v1"
        assert ds.api_key_env == "MY_DEEPSEEK_KEY"

        # custom_provider should be added as a new entry
        custom = config.models.providers["custom_provider"]
        assert custom.base_url == "http://localhost:8080/v1"
        assert custom.api_key_env == "CUSTOM_KEY"

        # built-in providers should still be present
        assert "openai" in config.models.providers
        assert "anthropic" in config.models.providers

    def test_env_var_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """AGENT_MODEL env var overrides config.toml default."""
        _write_config(tmp_path, '[models]\ndefault = "openai:gpt-4o"\n')
        monkeypatch.setenv("AGENT_MODEL", "anthropic:claude-opus-4-20250116")

        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()

        assert config.models.default == "anthropic:claude-opus-4-20250116"

    def test_config_dir_default(self) -> None:
        """Defaults to ~/.agent-nexus/ when AGENT_NEXUS_HOME is not set."""
        loader = ConfigLoader()
        # DEFAULT_CONFIG_DIR is computed at import time, so we check the
        # loader got a non-empty path.
        assert loader.config_dir == DEFAULT_CONFIG_DIR
        assert loader.config_dir.name == ".agent-nexus"

    def test_config_dir_env_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """AGENT_NEXUS_HOME overrides the config directory path.

        DEFAULT_CONFIG_DIR is a module-level constant computed at import time,
        so we patch it in the loader module to simulate the env var being set
        before import.
        """
        custom_dir = tmp_path / "custom-home"
        custom_dir.mkdir()
        monkeypatch.setattr("agent_nexus.platform.config.loader.DEFAULT_CONFIG_DIR", custom_dir)

        loader = ConfigLoader()
        assert loader.config_dir == custom_dir

    def test_ensure_config_dir_creates(self, tmp_path: Path) -> None:
        """Creates the full config directory tree."""
        cfg_dir = tmp_path / "new-nexus-home"
        loader = ConfigLoader(config_dir=cfg_dir)
        result = loader.ensure_config_dir()

        assert result == cfg_dir
        assert cfg_dir.is_dir()
        for subdir in ("agents", "venvs", "cache/repos", "runtimes", "logs"):
            assert (cfg_dir / subdir).is_dir(), f"Missing subdir: {subdir}"

    def test_load_sources_empty(self, tmp_path: Path) -> None:
        """No sources.yaml returns empty list."""
        loader = ConfigLoader(config_dir=tmp_path)
        sources = loader.load_sources()

        assert sources == []

    def test_load_sources_with_entries(self, tmp_path: Path) -> None:
        """Parses sources.yaml correctly."""
        _write_sources(
            tmp_path,
            (
                "sources:\n"
                "  - name: official\n"
                "    type: git\n"
                "    url: https://github.com/user/agent-nexus-packages.git\n"
                "    branch: main\n"
                "  - name: private\n"
                "    type: git\n"
                "    url: https://internal.example.com/agents.git\n"
                "    branch: develop\n"
            ),
        )
        loader = ConfigLoader(config_dir=tmp_path)
        sources = loader.load_sources()

        assert len(sources) == 2
        assert sources[0].name == "official"
        assert sources[0].url == "https://github.com/user/agent-nexus-packages.git"
        assert sources[0].branch == "main"
        assert sources[0].type == "git"
        assert sources[1].name == "private"
        assert sources[1].url == "https://internal.example.com/agents.git"
        assert sources[1].branch == "develop"


class TestConfigLoaderTomlDecodeErrorLogLevel:
    """Iteration 85/122: TomlDecodeError is logged at ERROR level, then re-raised."""

    def test_broken_toml_raises(self, tmp_path: Path) -> None:
        """Broken TOML content triggers logger.error and re-raises TomlDecodeError."""
        import logging

        import toml

        _write_config(tmp_path, "[section\nkey = value\n")
        loader = ConfigLoader(config_dir=tmp_path)

        with patch.object(
            logging.getLogger("agent_nexus.platform.config.loader"),
            "error",
        ) as mock_error, pytest.raises(toml.TomlDecodeError):
            loader.load_config()

        # logger.error should still have been called before re-raising
        mock_error.assert_called_once()
        assert "Failed to parse config file" in mock_error.call_args[0][0]


# ============================================================================
# ModelConfigManager Tests
# ============================================================================


class TestModelConfigManager:
    """Tests for ModelConfigManager: resolve_model, get_provider_config,
    resolve_api_key, parse_model_string."""

    @staticmethod
    def _make_config(**overrides) -> PlatformConfig:
        """Build a PlatformConfig with sensible defaults for testing."""
        from agent_nexus.models.config import ModelConfig, RuntimeConfig

        runtime = RuntimeConfig()
        models = ModelConfig(
            default=overrides.get("default", DEFAULT_MODEL_STRING),
            providers=overrides.get("providers", {}),
        )
        return PlatformConfig(runtime=runtime, models=models)

    def test_resolve_model_default(self) -> None:
        """Returns config default when no env var or tier is specified."""
        config = self._make_config(default="openai:gpt-4o")
        mgr = ModelConfigManager(config)
        result = mgr.resolve_model("test-agent")

        assert result == "openai:gpt-4o"

    def test_resolve_model_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AGENT_MODEL env var wins over config default."""
        config = self._make_config(default="openai:gpt-4o")
        monkeypatch.setenv("AGENT_MODEL", "anthropic:claude-opus-4-20250116")

        mgr = ModelConfigManager(config)
        result = mgr.resolve_model("test-agent")

        assert result == "anthropic:claude-opus-4-20250116"

    def test_resolve_model_tier_mapping(self) -> None:
        """recommended tier maps to provider:model via MODEL_TIER_MAP."""
        config = self._make_config()
        mgr = ModelConfigManager(config)

        lightweight = mgr.resolve_model("agent-a", recommended_tier=ModelTier.LIGHTWEIGHT)
        assert lightweight == MODEL_TIER_MAP[ModelTier.LIGHTWEIGHT]
        assert lightweight == "openai:gpt-4o-mini"

        powerful = mgr.resolve_model("agent-b", recommended_tier=ModelTier.POWERFUL)
        assert powerful == MODEL_TIER_MAP[ModelTier.POWERFUL]
        assert powerful == "anthropic:claude-sonnet-4-20250514"

    def test_resolve_model_fallback(self) -> None:
        """When tier is unknown, falls back to config default."""
        config = self._make_config(default="openai:gpt-4o")
        mgr = ModelConfigManager(config)

        # No tier and no recommended — hits config default
        result = mgr.resolve_model("test-agent")
        assert result == "openai:gpt-4o"

        # Recommended string takes priority over config default
        result = mgr.resolve_model("test-agent", recommended="deepseek:deepseek-chat")
        assert result == "deepseek:deepseek-chat"

    def test_get_provider_config(self) -> None:
        """Looks up a known provider by name."""
        providers = {
            "openai": ProviderConfig(
                base_url="",
                api_key_env="OPENAI_API_KEY",
                api=ProviderApiType.OPENAI_COMPATIBLE,
            ),
        }
        config = self._make_config(providers=providers)
        mgr = ModelConfigManager(config)

        result = mgr.get_provider_config("openai")
        assert result.api_key_env == "OPENAI_API_KEY"
        assert result.api == ProviderApiType.OPENAI_COMPATIBLE

    def test_get_provider_config_unknown(self) -> None:
        """Returns empty ProviderConfig for unknown provider name."""
        config = self._make_config()
        mgr = ModelConfigManager(config)

        result = mgr.get_provider_config("nonexistent")
        assert result.base_url == ""
        assert result.api_key_env == ""
        assert result.api == ProviderApiType.OPENAI_COMPATIBLE

    def test_resolve_api_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Reads API key from the env var configured in ProviderConfig."""
        monkeypatch.setenv("MY_CUSTOM_KEY", "sk-custom-123")
        provider = ProviderConfig(
            base_url="http://localhost:8080/v1",
            api_key_env="MY_CUSTOM_KEY",
        )
        config = self._make_config()
        mgr = ModelConfigManager(config)

        result = mgr.resolve_api_key(provider)
        assert result == "sk-custom-123"

    def test_resolve_api_key_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Falls back to well-known env vars when api_key_env is not set."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-xyz")

        providers = {
            "openai": ProviderConfig(
                api_key_env="OPENAI_API_KEY",
                api=ProviderApiType.OPENAI_COMPATIBLE,
            ),
        }
        config = self._make_config(providers=providers)
        mgr = ModelConfigManager(config)

        # Pass provider name as string — triggers lookup + fallback path
        result = mgr.resolve_api_key("openai")
        assert result == "sk-openai-xyz"

    def test_resolve_api_key_missing(self) -> None:
        """Returns empty string when no env var is set."""
        config = self._make_config()
        mgr = ModelConfigManager(config)

        result = mgr.resolve_api_key("nonexistent")
        assert result == ""

    def test_parse_model_string(self) -> None:
        """Splits 'provider:model' correctly."""
        config = self._make_config()
        mgr = ModelConfigManager(config)

        provider, model = mgr.parse_model_string("openai:gpt-4o")
        assert provider == "openai"
        assert model == "gpt-4o"

        provider, model = mgr.parse_model_string("anthropic:claude-sonnet-4-20250514")
        assert provider == "anthropic"
        assert model == "claude-sonnet-4-20250514"

    def test_parse_model_string_no_colon(self) -> None:
        """Single-part model name defaults to provider 'openai'."""
        config = self._make_config()
        mgr = ModelConfigManager(config)

        provider, model = mgr.parse_model_string("gpt-4o")
        assert provider == "openai"
        assert model == "gpt-4o"

    def test_resolve_model_unknown_tier_string(self) -> None:
        """Unknown tier string logs warning and falls back to config default."""
        config = self._make_config(default="openai:gpt-4o")
        mgr = ModelConfigManager(config)

        result = mgr.resolve_model("test-agent", recommended_tier="super_premium")
        assert result == "openai:gpt-4o"

    def test_resolve_model_recommended_overrides_tier(self) -> None:
        """Recommended model string takes priority over tier mapping."""
        config = self._make_config()
        mgr = ModelConfigManager(config)

        result = mgr.resolve_model(
            "test-agent",
            recommended="deepseek:deepseek-chat",
            recommended_tier=ModelTier.LIGHTWEIGHT,
        )
        assert result == "deepseek:deepseek-chat"

    def test_resolve_api_key_provider_object_identity_lookup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Passing ProviderConfig object finds provider name by identity."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-obj")
        provider = ProviderConfig(api_key_env="NONEXISTENT_KEY")
        config = self._make_config(providers={"deepseek": provider})
        mgr = ModelConfigManager(config)

        # Pass the same object (identity match) — api_key_env is set but empty,
        # so fallback to _PROVIDER_ENV_FALLBACKS["deepseek"] kicks in
        monkeypatch.delenv("NONEXISTENT_KEY", raising=False)
        result = mgr.resolve_api_key(provider)
        assert result == "sk-deepseek-obj"

    def test_resolve_api_key_string_lookup_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """String lookup with no api_key_env falls back to well-known env vars."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anthropic-fallback")
        provider = ProviderConfig()  # no api_key_env set
        config = self._make_config(providers={"anthropic": provider})
        mgr = ModelConfigManager(config)

        result = mgr.resolve_api_key("anthropic")
        assert result == "sk-anthropic-fallback"

    def test_resolve_api_key_object_no_match_returns_empty(self) -> None:
        """ProviderConfig object not in registry returns empty string."""
        orphan = ProviderConfig(api_key_env="NONEXISTENT_VAR")
        config = self._make_config()  # no providers
        mgr = ModelConfigManager(config)

        result = mgr.resolve_api_key(orphan)
        assert result == ""

    def test_resolve_api_key_empty_string_logs_warning(self) -> None:
        """Iteration 85: resolve_api_key logs warning when returning empty string."""
        import logging

        config = self._make_config()
        mgr = ModelConfigManager(config)

        with patch.object(
            logging.getLogger("agent_nexus.platform.config.model_config"),
            "warning",
        ) as mock_warning:
            result = mgr.resolve_api_key("nonexistent")

        assert result == ""
        # Two warnings expected: "Provider not found" + "No API key found"
        assert any("No API key found" in str(call) for call in mock_warning.call_args_list)


# ============================================================================
# Defaults Tests
# ============================================================================


class TestDefaults:
    """Tests for the defaults module constants."""

    def test_default_providers_exist(self) -> None:
        """All 6 built-in providers are defined."""
        expected = {"openai", "anthropic", "deepseek", "minimax", "qwen", "ollama"}
        assert set(DEFAULT_PROVIDERS.keys()) == expected

    def test_model_tier_map_complete(self) -> None:
        """All 4 tiers have mappings."""
        expected_tiers = {
            ModelTier.LIGHTWEIGHT,
            ModelTier.STANDARD,
            ModelTier.POWERFUL,
            ModelTier.PREMIUM,
        }
        assert set(MODEL_TIER_MAP.keys()) == expected_tiers

        # Each value should be a "provider:model" string
        for tier, model_string in MODEL_TIER_MAP.items():
            assert ":" in model_string, f"Tier {tier} value is not provider:model format"

    def test_env_var_overrides_map(self) -> None:
        """All expected env var overrides are defined."""
        assert "AGENT_MODEL" in ENV_VAR_OVERRIDES
        assert "DEFAULT_MODEL" in ENV_VAR_OVERRIDES
        assert "AGENT_NEXUS_HOME" in ENV_VAR_OVERRIDES

        assert ENV_VAR_OVERRIDES["AGENT_MODEL"] == "models.default"
        assert ENV_VAR_OVERRIDES["DEFAULT_MODEL"] == "models.default"
        assert ENV_VAR_OVERRIDES["AGENT_NEXUS_HOME"] == "config_dir"


# ============================================================================
# ConfigLoader ProviderApiType validation (from iter20)
# ============================================================================


class TestConfigLoaderProviderApiTypeValidation:
    def test_invalid_api_type_defaults_to_openai_compatible(self, tmp_path: Path) -> None:
        """An invalid api type string defaults to openai-compatible with a warning."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text(
            '[models.providers.bad]\napi = "invalid_type"\nbase_url = "http://localhost"\n'
        )
        loader = ConfigLoader(config_dir=config_dir)
        config = loader.load_config()
        # Invalid api type should default to OPENAI_COMPATIBLE, not skip the provider
        assert "bad" in config.models.providers
        assert config.models.providers["bad"].api == ProviderApiType.OPENAI_COMPATIBLE

    def test_valid_api_types_accepted(self, tmp_path: Path) -> None:
        """All valid ProviderApiType values should be accepted."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        # Write a config with each valid api type
        lines = []
        for i, pt in enumerate(ProviderApiType):
            lines.append(f"[models.providers.p{i}]")
            lines.append(f'api = "{pt.value}"')
            lines.append(f'base_url = "http://localhost/{i}"')
        (config_dir / "config.toml").write_text("\n".join(lines) + "\n")

        loader = ConfigLoader(config_dir=config_dir)
        config = loader.load_config()
        # The test providers should all be present (merged with built-in defaults)
        for i, pt in enumerate(ProviderApiType):
            assert f"p{i}" in config.models.providers
            assert config.models.providers[f"p{i}"].api is pt

    def test_missing_config_file_still_works(self, tmp_path: Path) -> None:
        """No config file at all should not raise -- just use defaults."""
        config_dir = tmp_path / "empty_config"
        config_dir.mkdir()
        loader = ConfigLoader(config_dir=config_dir)
        config = loader.load_config()
        assert config.models.default is not None

    def test_load_malformed_toml_raises(self, tmp_path: Path) -> None:
        """Malformed TOML raises TomlDecodeError instead of silently falling back."""
        import toml

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text("[section\nkey = value\n", encoding="utf-8")
        loader = ConfigLoader(config_dir=config_dir)
        with pytest.raises(toml.TomlDecodeError):
            loader.load_config()

    def test_load_valid_toml_still_works(self, tmp_path: Path) -> None:
        """Valid TOML file is parsed correctly after adding TomlDecodeError handler."""
        _write_config(
            tmp_path,
            '[models]\ndefault = "deepseek:deepseek-chat"\n'
            "[models.providers.custom]\n"
            'base_url = "http://localhost:9999/v1"\n'
            'api_key_env = "CUSTOM_KEY"\n'
            'api = "openai-compatible"\n',
        )
        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()

        assert config.models.default == "deepseek:deepseek-chat"
        assert "custom" in config.models.providers
        assert config.models.providers["custom"].base_url == "http://localhost:9999/v1"


# ============================================================================
# Regression tests for iteration 22 defects
# ============================================================================


class TestConfigLoaderSourcesValidation:
    """Regression tests for ConfigLoader.load_sources edge cases."""

    def test_load_sources_malformed_yaml_returns_empty(self, tmp_path: Path) -> None:
        """Malformed YAML in sources.yaml returns empty list, not exception."""
        _write_sources(tmp_path, "{{{{invalid yaml")
        loader = ConfigLoader(config_dir=tmp_path)
        sources = loader.load_sources()
        assert sources == []

    def test_load_sources_non_dict_items_skipped(self, tmp_path: Path) -> None:
        """Non-dict items in sources list are skipped with warning."""
        _write_sources(
            tmp_path,
            "sources:\n  - just_a_string\n  - 42\n  - name: valid\n    type: git\n    url: http://x.com\n",
        )
        loader = ConfigLoader(config_dir=tmp_path)
        sources = loader.load_sources()
        assert len(sources) == 1
        assert sources[0].name == "valid"

    def test_load_sources_string_sources_key(self, tmp_path: Path) -> None:
        """When 'sources' key maps to a string, returns empty list."""
        _write_sources(tmp_path, "sources: not_a_list\n")
        loader = ConfigLoader(config_dir=tmp_path)
        sources = loader.load_sources()
        assert sources == []

    def test_load_sources_empty_file(self, tmp_path: Path) -> None:
        """Empty sources.yaml file returns empty list."""
        _write_sources(tmp_path, "")
        loader = ConfigLoader(config_dir=tmp_path)
        sources = loader.load_sources()
        assert sources == []


# ============================================================================
# Coverage gap tests: loader.py lines 94-95, 143-144, 153-154
# ============================================================================


class TestConfigLoaderProvidersValidation:
    """Tests for config.toml [models].providers non-mapping guard."""

    def test_providers_not_a_mapping_uses_defaults(self, tmp_path: Path) -> None:
        """When [models].providers is a list instead of dict, it is ignored."""
        _write_config(
            tmp_path,
            '[models]\ndefault = "openai:gpt-4o"\nproviders = ["not", "a", "dict"]\n',
        )
        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()
        # providers should fall back to built-in defaults, not crash
        assert set(config.models.providers.keys()) == set(DEFAULT_PROVIDERS.keys())


class TestConfigLoaderSourcesNameValidation:
    """Tests for load_sources name validation (lines 143-144)."""

    def test_source_with_non_string_name_skipped(self, tmp_path: Path) -> None:
        """Source entry where 'name' is a number is skipped."""
        _write_sources(
            tmp_path,
            "sources:\n  - name: 42\n    type: git\n    url: http://x.com\n",
        )
        loader = ConfigLoader(config_dir=tmp_path)
        sources = loader.load_sources()
        assert sources == []

    def test_source_with_empty_name_skipped(self, tmp_path: Path) -> None:
        """Source entry where 'name' is an empty string is skipped."""
        _write_sources(
            tmp_path,
            "sources:\n  - name: ''\n    type: git\n    url: http://x.com\n",
        )
        loader = ConfigLoader(config_dir=tmp_path)
        sources = loader.load_sources()
        assert sources == []

    def test_source_with_whitespace_name_skipped(self, tmp_path: Path) -> None:
        """Source entry where 'name' is only whitespace is skipped."""
        _write_sources(
            tmp_path,
            "sources:\n  - name: '   '\n    type: git\n    url: http://x.com\n",
        )
        loader = ConfigLoader(config_dir=tmp_path)
        sources = loader.load_sources()
        assert sources == []


class TestConfigLoaderSourcesInvalidEntry:
    """Tests for load_sources SourceEntry construction failure (lines 153-154)."""

    def test_git_source_without_url_skipped(self, tmp_path: Path) -> None:
        """A git-type source with empty URL fails SourceEntry validation and is skipped."""
        _write_sources(
            tmp_path,
            "sources:\n"
            "  - name: bad-git\n"
            "    type: git\n"
            "    url: ''\n"
            "  - name: good-git\n"
            "    type: git\n"
            "    url: https://github.com/example/repo.git\n",
        )
        loader = ConfigLoader(config_dir=tmp_path)
        sources = loader.load_sources()
        # Only the valid entry should be returned
        assert len(sources) == 1
        assert sources[0].name == "good-git"
