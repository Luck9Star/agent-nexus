"""Tests for ConfigLoader: config.toml and sources.yaml loading."""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import patch

import pytest
import toml
import yaml

from agent_nexus.models.config import ProviderApiType
from agent_nexus.platform.config.loader import ConfigLoader

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_config(config_dir: Any, content: dict[str, Any]) -> None:
    """Write a config.toml dict to config_dir."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.toml").write_text(toml.dumps(content), encoding="utf-8")


def _write_sources_yaml(config_dir: Any, sources: list[dict[str, str]]) -> None:
    """Write a sources.yaml to config_dir."""
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "sources.yaml").write_text(yaml.dump({"sources": sources}), encoding="utf-8")


def _make_loader(config_dir: Any) -> ConfigLoader:
    return ConfigLoader(config_dir=config_dir)


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------


class TestConfigLoaderInit:
    def test_default_config_dir(self):
        loader = ConfigLoader()
        from agent_nexus.platform.config.defaults import DEFAULT_CONFIG_DIR

        assert loader.config_dir == DEFAULT_CONFIG_DIR


# ---------------------------------------------------------------------------
# load_config()
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_missing_config_returns_defaults(self, tmp_path):
        loader = _make_loader(tmp_path)
        config = loader.load_config()
        assert config.models.default == "openai:gpt-4o"
        assert config.runtime.python_path == "python3"
        assert config.sources == []

    def test_loads_basic_config(self, tmp_path):
        _write_config(
            tmp_path,
            {
                "schema_version": "2.0",
                "runtime": {"python_path": "/usr/bin/python3.12", "log_level": "DEBUG"},
                "models": {"default": "anthropic:claude-sonnet-4-20250514"},
            },
        )
        loader = _make_loader(tmp_path)
        config = loader.load_config()

        assert config.schema_version == "2.0"
        assert config.runtime.python_path == "/usr/bin/python3.12"
        assert config.runtime.log_level == "DEBUG"
        assert config.models.default == "anthropic:claude-sonnet-4-20250514"

    def test_env_var_overrides_config(self, tmp_path):
        _write_config(tmp_path, {"models": {"default": "openai:gpt-4o"}})
        loader = _make_loader(tmp_path)

        with patch.dict(os.environ, {"AGENT_MODEL": "anthropic:claude-opus-4-7"}):
            config = loader.load_config()
            assert config.models.default == "anthropic:claude-opus-4-7"

    def test_default_model_env_overrides(self, tmp_path):
        _write_config(tmp_path, {"models": {"default": "openai:gpt-4o"}})
        loader = _make_loader(tmp_path)

        with patch.dict(os.environ, {"DEFAULT_MODEL": "deepseek:deepseek-chat"}, clear=False):
            config = loader.load_config()
            assert config.models.default == "deepseek:deepseek-chat"

    def test_agent_model_takes_precedence_over_default_model(self, tmp_path):
        loader = _make_loader(tmp_path)
        with patch.dict(
            os.environ,
            {
                "AGENT_MODEL": "agent-model",
                "DEFAULT_MODEL": "default-model",
            },
        ):
            config = loader.load_config()
            assert config.models.default == "agent-model"

    def test_caches_config_when_mtime_unchanged(self, tmp_path):
        _write_config(tmp_path, {"models": {"default": "openai:gpt-4o"}})
        loader = _make_loader(tmp_path)

        config1 = loader.load_config()
        config2 = loader.load_config()
        assert config1 is config2  # same object from cache

    def test_invalid_toml_raises(self, tmp_path):
        (tmp_path / "config.toml").write_text("not valid [toml {{{", encoding="utf-8")
        loader = _make_loader(tmp_path)

        with pytest.raises(toml.TomlDecodeError):
            loader.load_config()

    def test_loads_sources_from_config(self, tmp_path):
        _write_config(
            tmp_path,
            {
                "sources": [
                    {"name": "official", "type": "git", "url": "https://example.com/repo"},
                ],
            },
        )
        loader = _make_loader(tmp_path)
        config = loader.load_config()
        assert len(config.sources) == 1
        assert config.sources[0].name == "official"

    def test_invalid_providers_section_falls_back(self, tmp_path):
        _write_config(tmp_path, {"models": {"providers": "not-a-dict"}})
        loader = _make_loader(tmp_path)
        config = loader.load_config()
        # Should use built-in defaults
        assert "openai" in config.models.providers

    def test_invalid_stages_section_falls_back(self, tmp_path):
        _write_config(tmp_path, {"models": {"stages": "not-a-dict"}})
        loader = _make_loader(tmp_path)
        config = loader.load_config()
        assert config.models.stages == {}

    def test_stages_parsed(self, tmp_path):
        _write_config(
            tmp_path,
            {
                "models": {"stages": {"planning": "anthropic:claude-sonnet-4-20250514"}},
            },
        )
        loader = _make_loader(tmp_path)
        config = loader.load_config()
        assert config.models.stages["planning"] == "anthropic:claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# load_sources()
# ---------------------------------------------------------------------------


class TestLoadSources:
    def test_sources_falls_back_to_yaml(self, tmp_path):
        _write_config(tmp_path, {})
        _write_sources_yaml(
            tmp_path,
            [
                {"name": "yaml-src", "url": "https://git.example.com/yaml-repo"},
            ],
        )
        loader = _make_loader(tmp_path)
        sources = loader.load_sources()
        assert len(sources) == 1
        assert sources[0].name == "yaml-src"

    def test_empty_sources_returns_empty_list(self, tmp_path):
        _write_config(tmp_path, {})
        loader = _make_loader(tmp_path)
        assert loader.load_sources() == []


# ---------------------------------------------------------------------------
# _parse_sources_from_raw()
# ---------------------------------------------------------------------------


class TestParseSourcesFromRaw:
    def test_valid_sources(self):
        raw = {
            "sources": [
                {"name": "s1", "type": "git", "url": "https://a.com"},
                {"name": "s2", "url": "https://b.com"},
            ],
        }
        entries = ConfigLoader._parse_sources_from_raw(raw)
        assert len(entries) == 2
        assert entries[0].name == "s1"
        assert entries[1].type == "git"  # default

    def test_skips_non_dict_items(self):
        raw = {"sources": ["string", 42, {"name": "ok", "url": "u"}]}
        entries = ConfigLoader._parse_sources_from_raw(raw)
        assert len(entries) == 1

    def test_skips_items_with_missing_name(self):
        raw = {"sources": [{"url": "u"}, {"name": "", "url": "u"}, {"name": "valid", "url": "u"}]}
        entries = ConfigLoader._parse_sources_from_raw(raw)
        assert len(entries) == 1
        assert entries[0].name == "valid"


# ---------------------------------------------------------------------------
# _load_sources_from_yaml()
# ---------------------------------------------------------------------------


class TestLoadSourcesFromYaml:
    def test_valid_yaml(self, tmp_path):
        _write_sources_yaml(
            tmp_path,
            [
                {"name": "s1", "type": "git", "url": "https://a.com"},
            ],
        )
        loader = _make_loader(tmp_path)
        entries = loader._load_sources_from_yaml()
        assert len(entries) == 1

    def test_missing_yaml_returns_empty(self, tmp_path):
        loader = _make_loader(tmp_path)
        assert loader._load_sources_from_yaml() == []

    def test_invalid_yaml_returns_empty(self, tmp_path):
        (tmp_path / "sources.yaml").write_text("{{{{invalid yaml", encoding="utf-8")
        loader = _make_loader(tmp_path)
        assert loader._load_sources_from_yaml() == []

    def test_yaml_skips_non_dict_entries(self, tmp_path):
        (tmp_path / "sources.yaml").write_text(
            yaml.dump({"sources": ["bad", {"name": "ok", "url": "u"}]}),
            encoding="utf-8",
        )
        loader = _make_loader(tmp_path)
        entries = loader._load_sources_from_yaml()
        assert len(entries) == 1

    def test_yaml_caches_result(self, tmp_path):
        _write_sources_yaml(tmp_path, [{"name": "s1", "url": "u"}])
        loader = _make_loader(tmp_path)
        e1 = loader._load_sources_from_yaml()
        e2 = loader._load_sources_from_yaml()
        assert e1 is e2


# ---------------------------------------------------------------------------
# _build_providers()
# ---------------------------------------------------------------------------


class TestBuildProviders:
    def test_user_override_merges(self):
        providers = ConfigLoader._build_providers(
            {
                "openai": {"base_url": "https://custom.api.com/v1"},
            }
        )
        assert providers["openai"].base_url == "https://custom.api.com/v1"
        # Should retain non-overridden fields
        assert providers["openai"].api_key_env == "OPENAI_API_KEY"

    def test_new_provider_added(self):
        providers = ConfigLoader._build_providers(
            {
                "custom": {"base_url": "https://custom.com", "api_key_env": "CUSTOM_KEY"},
            }
        )
        assert "custom" in providers
        assert providers["custom"].base_url == "https://custom.com"

    def test_invalid_api_type_defaults_to_openai_compatible(self):
        providers = ConfigLoader._build_providers(
            {
                "bad": {"api": "nonexistent_api_type"},
            }
        )
        assert providers["bad"].api == ProviderApiType.OPENAI_COMPATIBLE

    def test_streaming_override(self):
        providers = ConfigLoader._build_providers(
            {
                "openai": {"streaming": False},
            }
        )
        assert providers["openai"].streaming is False


# ---------------------------------------------------------------------------
# _parse_external_server()
# ---------------------------------------------------------------------------


class TestParseExternalServer:
    def test_valid_server(self):
        result = ConfigLoader._parse_external_server(
            {
                "name": "my-server",
                "command": "npx",
                "args": ["-y", "some-mcp"],
                "transport": "stdio",
            }
        )
        assert result is not None
        assert result.name == "my-server"
        assert result.command == "npx"

    def test_missing_name_returns_none(self):
        assert ConfigLoader._parse_external_server({"command": "x"}) is None

    def test_empty_name_returns_none(self):
        assert ConfigLoader._parse_external_server({"name": ""}) is None

    def test_disabled_returns_none(self):
        assert (
            ConfigLoader._parse_external_server(
                {
                    "name": "s",
                    "enabled": False,
                }
            )
            is None
        )

    def test_enabled_as_string(self):
        result = ConfigLoader._parse_external_server(
            {
                "name": "s",
                "enabled": "true",
            }
        )
        assert result is not None
        assert result.name == "s"

    def test_invalid_transport_defaults_to_stdio(self):
        result = ConfigLoader._parse_external_server(
            {
                "name": "s",
                "transport": "invalid",
            }
        )
        assert result is not None
        assert result.transport.value == "stdio"

    def test_non_list_args_uses_empty(self):
        result = ConfigLoader._parse_external_server(
            {
                "name": "s",
                "args": "not a list",
            }
        )
        assert result is not None
        assert result.args == []

    def test_non_dict_headers_uses_empty(self):
        result = ConfigLoader._parse_external_server(
            {
                "name": "s",
                "headers": "not a dict",
            }
        )
        assert result is not None
        assert result.headers == {}

    def test_sse_server_with_url(self):
        result = ConfigLoader._parse_external_server(
            {
                "name": "remote",
                "transport": "sse",
                "url": "http://localhost:8080/sse",
            }
        )
        assert result is not None
        assert result.url == "http://localhost:8080/sse"


# ---------------------------------------------------------------------------
# load_external_servers()
# ---------------------------------------------------------------------------


class TestLoadExternalServers:
    def test_loads_servers(self, tmp_path):
        _write_config(
            tmp_path,
            {
                "mcp": {
                    "external_servers": [
                        {"name": "s1", "command": "npx", "args": ["-y", "tool"]},
                    ],
                },
            },
        )
        loader = _make_loader(tmp_path)
        servers = loader.load_external_servers()
        assert len(servers) == 1
        assert servers[0].name == "s1"

    def test_skips_non_dict_entries(self, tmp_path):
        loader = _make_loader(tmp_path)
        # Mock _load_raw to return data with non-dict entries
        loader._load_raw = lambda: {
            "mcp": {"external_servers": ["bad", {"name": "ok", "command": "c"}]},
        }
        servers = loader.load_external_servers()
        assert len(servers) == 1


# ---------------------------------------------------------------------------
# load_project_config()
# ---------------------------------------------------------------------------


class TestLoadProjectConfig:
    def test_missing_returns_none(self, tmp_path):
        loader = _make_loader(tmp_path)
        assert loader.load_project_config(tmp_path) is None

    def test_loads_project_config(self, tmp_path):
        config_path = tmp_path / "agent-nexus.toml"
        config_path.write_text(
            toml.dumps(
                {
                    "models": {"default": "ollama:llama3"},
                }
            ),
            encoding="utf-8",
        )
        loader = _make_loader(tmp_path)
        result = loader.load_project_config(tmp_path)
        assert result is not None
        assert result.models.default == "ollama:llama3"

    def test_invalid_toml_returns_none(self, tmp_path):
        config_path = tmp_path / "agent-nexus.toml"
        config_path.write_text("{{bad", encoding="utf-8")
        loader = _make_loader(tmp_path)
        assert loader.load_project_config(tmp_path) is None


# ---------------------------------------------------------------------------
# load_merged_config()
# ---------------------------------------------------------------------------


class TestLoadMergedConfig:
    def test_no_project_config_returns_global(self, tmp_path):
        _write_config(tmp_path, {"models": {"default": "openai:gpt-4o"}})
        loader = _make_loader(tmp_path)
        result = loader.load_merged_config(tmp_path)
        assert result.models.default == "openai:gpt-4o"

    def test_project_overrides_global(self, tmp_path):
        _write_config(tmp_path, {"models": {"default": "openai:gpt-4o"}})
        (tmp_path / "agent-nexus.toml").write_text(
            toml.dumps(
                {
                    "models": {"default": "anthropic:claude-sonnet-4-20250514"},
                }
            ),
            encoding="utf-8",
        )
        loader = _make_loader(tmp_path)
        result = loader.load_merged_config(tmp_path)
        assert result.models.default == "anthropic:claude-sonnet-4-20250514"

    def test_project_preserves_global_when_empty(self, tmp_path):
        _write_config(tmp_path, {"models": {"default": "openai:gpt-4o"}})
        (tmp_path / "agent-nexus.toml").write_text(
            toml.dumps(
                {
                    "models": {"default": ""},
                }
            ),
            encoding="utf-8",
        )
        loader = _make_loader(tmp_path)
        result = loader.load_merged_config(tmp_path)
        # empty string is falsy, so global wins
        assert result.models.default == "openai:gpt-4o"


# ---------------------------------------------------------------------------
# ensure_config_dir()
# ---------------------------------------------------------------------------


class TestEnsureConfigDir:
    def test_creates_config_dir(self, tmp_path):
        config_dir = tmp_path / "new-config"
        loader = ConfigLoader(config_dir=config_dir)
        result = loader.ensure_config_dir()
        assert result == config_dir
        assert config_dir.exists()

    def test_creates_subdirs(self, tmp_path):
        config_dir = tmp_path / "with-subs"
        loader = ConfigLoader(config_dir=config_dir)
        loader.ensure_config_dir()
        assert (config_dir / "agents").exists()
        assert (config_dir / "venvs").exists()
        assert (config_dir / "cache" / "repos").exists()
        assert (config_dir / "runtimes").exists()
        assert (config_dir / "logs").exists()


# ---------------------------------------------------------------------------
# invalidate_cache()
# ---------------------------------------------------------------------------


class TestInvalidateCache:
    def test_invalidate_clears_cache(self, tmp_path):
        _write_config(tmp_path, {"models": {"default": "openai:gpt-4o"}})
        loader = _make_loader(tmp_path)
        config1 = loader.load_config()
        loader.invalidate_cache()
        config2 = loader.load_config()
        assert config1 is not config2  # different object after invalidation


# ---------------------------------------------------------------------------
# load_cli_routing()
# ---------------------------------------------------------------------------


class TestLoadCliRouting:
    def test_missing_section_returns_none(self, tmp_path):
        _write_config(tmp_path, {})
        loader = _make_loader(tmp_path)
        assert loader.load_cli_routing() is None

    def test_present_section_returns_config(self, tmp_path):
        _write_config(tmp_path, {"cli_routing": {"default": "anthropic"}})
        loader = _make_loader(tmp_path)
        result = loader.load_cli_routing()
        assert result is not None
        assert result.default == "anthropic"
