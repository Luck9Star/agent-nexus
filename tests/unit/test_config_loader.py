"""Unit tests for ConfigLoader: config.toml loading, provider merging, sources parsing.

Focuses on loader-specific contracts: TOML parsing, env-var priority,
_build_providers merge logic, sources.yaml validation, and ensure_config_dir.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_nexus.models.config import ProviderApiType
from agent_nexus.platform.config import (
    CONFIG_FILE,
    DEFAULT_MODEL_STRING,
    DEFAULT_PROVIDERS,
    SOURCES_FILE,
    ConfigLoader,
)

# Env vars to clean between tests
_ENV_VARS = (
    "AGENT_MODEL",
    "DEFAULT_MODEL",
    "AGENT_NEXUS_HOME",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


# ============================================================================
# load_config — TOML parsing
# ============================================================================


class TestConfigLoaderTomlParsing:
    """ConfigLoader.load_config TOML parsing edge cases."""

    def test_runtime_defaults_when_section_missing(self, tmp_path: Path) -> None:
        """When [runtime] section is absent, defaults are 'python3' and 'uv'."""
        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()
        assert config.runtime.python_path == "python3"
        assert config.runtime.uv_path == "uv"

    def test_runtime_partial_override(self, tmp_path: Path) -> None:
        """Setting only python_path keeps uv_path at default."""
        cfg = tmp_path / CONFIG_FILE
        cfg.write_text('[runtime]\npython_path = "/usr/local/bin/python3.11"\n')
        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()
        assert config.runtime.python_path == "/usr/local/bin/python3.11"
        assert config.runtime.uv_path == "uv"

    def test_default_model_env_chain_priority(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """AGENT_MODEL takes precedence over DEFAULT_MODEL."""
        cfg = tmp_path / CONFIG_FILE
        cfg.write_text('[models]\ndefault = "from:toml"\n')
        monkeypatch.setenv("AGENT_MODEL", "from:agent_model_env")
        monkeypatch.setenv("DEFAULT_MODEL", "from:default_model_env")

        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()
        assert config.models.default == "from:agent_model_env"

    def test_default_model_fallback_to_default_model_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """DEFAULT_MODEL is used when AGENT_MODEL is not set."""
        cfg = tmp_path / CONFIG_FILE
        cfg.write_text('[models]\ndefault = "from:toml"\n')
        monkeypatch.setenv("DEFAULT_MODEL", "from:default_model_env")

        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()
        assert config.models.default == "from:default_model_env"

    def test_default_model_fallback_to_toml(self, tmp_path: Path) -> None:
        """When no env vars are set, config.toml default is used."""
        cfg = tmp_path / CONFIG_FILE
        cfg.write_text('[models]\ndefault = "deepseek:deepseek-chat"\n')
        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()
        assert config.models.default == "deepseek:deepseek-chat"

    def test_default_model_hardcoded_fallback(self, tmp_path: Path) -> None:
        """When env vars and config.toml are absent, hardcoded default is used."""
        loader = ConfigLoader(config_dir=tmp_path)
        config = loader.load_config()
        assert config.models.default == DEFAULT_MODEL_STRING


# ============================================================================
# _build_providers — merge logic
# ============================================================================


class TestConfigLoaderProviderMerge:
    """ConfigLoader._build_providers merge and validation logic."""

    def test_empty_user_providers_returns_all_builtins(self) -> None:
        """Passing empty dict returns all 6 built-in providers."""
        result = ConfigLoader._build_providers({})
        assert set(result.keys()) == set(DEFAULT_PROVIDERS.keys())

    def test_builtin_provider_fields_preserved(self) -> None:
        """Built-in provider preset fields are correctly transferred."""
        result = ConfigLoader._build_providers({})
        anthropic = result["anthropic"]
        assert anthropic.api_key_env == "ANTHROPIC_API_KEY"
        assert anthropic.api == ProviderApiType.ANTHROPIC_MESSAGES

    def test_user_override_of_builtin_provider(self) -> None:
        """User config overrides specific fields of a built-in provider."""
        result = ConfigLoader._build_providers({
            "ollama": {"base_url": "http://custom-host:1234/v1"},
        })
        ollama = result["ollama"]
        assert ollama.base_url == "http://custom-host:1234/v1"
        # api_key_env should keep its built-in default ("")
        assert ollama.api_key_env == ""

    def test_new_custom_provider_added(self) -> None:
        """A completely new provider is added alongside built-ins."""
        result = ConfigLoader._build_providers({
            "my_provider": {
                "base_url": "http://my.api/v1",
                "api_key_env": "MY_API_KEY",
            },
        })
        assert "my_provider" in result
        assert result["my_provider"].base_url == "http://my.api/v1"
        # Built-in providers remain
        assert "openai" in result

    def test_invalid_api_type_gets_default(self) -> None:
        """Invalid api string defaults to OPENAI_COMPATIBLE."""
        result = ConfigLoader._build_providers({
            "bad": {"api": "totally_invalid"},
        })
        assert result["bad"].api == ProviderApiType.OPENAI_COMPATIBLE

    def test_override_retains_existing_base_url(self) -> None:
        """When user provides only api_key_env, base_url is kept from builtin."""
        result = ConfigLoader._build_providers({
            "deepseek": {"api_key_env": "CUSTOM_DEEPSEEK_KEY"},
        })
        assert result["deepseek"].api_key_env == "CUSTOM_DEEPSEEK_KEY"
        # base_url should remain the built-in default
        assert result["deepseek"].base_url == "https://api.deepseek.com/v1"


# ============================================================================
# load_sources — YAML parsing
# ============================================================================


class TestConfigLoaderSources:
    """ConfigLoader.load_sources YAML parsing edge cases."""

    def test_sources_missing_dict_root(self, tmp_path: Path) -> None:
        """YAML that parses to a non-dict returns empty list."""
        src = tmp_path / SOURCES_FILE
        src.write_text("just a string\n")
        loader = ConfigLoader(config_dir=tmp_path)
        assert loader.load_sources() == []

    def test_sources_dict_without_sources_key(self, tmp_path: Path) -> None:
        """YAML dict that lacks 'sources' key returns empty list."""
        src = tmp_path / SOURCES_FILE
        src.write_text(yaml.dump({"other_key": []}))
        loader = ConfigLoader(config_dir=tmp_path)
        assert loader.load_sources() == []

    def test_sources_with_missing_name_key(self, tmp_path: Path) -> None:
        """Source entry without 'name' is skipped."""
        src = tmp_path / SOURCES_FILE
        src.write_text(yaml.dump({
            "sources": [{"type": "git", "url": "https://x.com/repo.git"}],
        }))
        loader = ConfigLoader(config_dir=tmp_path)
        assert loader.load_sources() == []

    def test_sources_partial_entry_fills_defaults(self, tmp_path: Path) -> None:
        """Valid minimal source entry gets default type and branch."""
        src = tmp_path / SOURCES_FILE
        src.write_text(yaml.dump({
            "sources": [{"name": "minimal", "url": "https://x.com/repo.git"}],
        }))
        loader = ConfigLoader(config_dir=tmp_path)
        sources = loader.load_sources()
        assert len(sources) == 1
        assert sources[0].name == "minimal"
        assert sources[0].type == "git"
        assert sources[0].branch == "main"


# ============================================================================
# ensure_config_dir
# ============================================================================


class TestConfigLoaderEnsureDir:
    """ConfigLoader.ensure_config_dir directory creation."""

    def test_idempotent_creation(self, tmp_path: Path) -> None:
        """Calling ensure_config_dir twice does not raise."""
        cfg_dir = tmp_path / "nexus-home"
        loader = ConfigLoader(config_dir=cfg_dir)
        loader.ensure_config_dir()
        result = loader.ensure_config_dir()
        assert result == cfg_dir

    def test_returns_config_dir_path(self, tmp_path: Path) -> None:
        """Returns the same path passed to constructor."""
        cfg_dir = tmp_path / "return-test"
        loader = ConfigLoader(config_dir=cfg_dir)
        assert loader.ensure_config_dir() == cfg_dir
