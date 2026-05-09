"""Unit tests for agent_nexus.models.config module."""

import json

import pytest

from agent_nexus.models.config import (
    ModelConfig,
    PlatformConfig,
    ProviderApiType,
    ProviderConfig,
    RuntimeConfig,
)

# ---------------------------------------------------------------------------
# ProviderApiType enum
# ---------------------------------------------------------------------------


class TestProviderApiType:
    def test_members(self):
        assert set(ProviderApiType) == {
            ProviderApiType.OPENAI_COMPATIBLE,
            ProviderApiType.ANTHROPIC_MESSAGES,
            ProviderApiType.OLLAMA,
            ProviderApiType.CLI,
        }

    def test_values(self):
        assert ProviderApiType.OPENAI_COMPATIBLE == "openai-compatible"

    def test_cli_variant(self):
        assert ProviderApiType.CLI.value == "cli"
        assert ProviderApiType("cli") == ProviderApiType.CLI
        assert ProviderApiType.ANTHROPIC_MESSAGES == "anthropic-messages"
        assert ProviderApiType.OLLAMA == "ollama"

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            ProviderApiType("unknown")


# ---------------------------------------------------------------------------
# ProviderConfig
# ---------------------------------------------------------------------------


class TestProviderConfig:
    def test_defaults(self):
        cfg = ProviderConfig()
        assert cfg.base_url == ""
        assert cfg.api_key_env == ""
        assert cfg.api is ProviderApiType.OPENAI_COMPATIBLE

    def test_with_openai_compatible(self):
        cfg = ProviderConfig(
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
            api=ProviderApiType.OPENAI_COMPATIBLE,
        )
        assert cfg.base_url == "https://api.openai.com/v1"
        assert cfg.api_key_env == "OPENAI_API_KEY"

    def test_with_anthropic(self):
        cfg = ProviderConfig(
            base_url="https://api.anthropic.com",
            api_key_env="ANTHROPIC_API_KEY",
            api=ProviderApiType.ANTHROPIC_MESSAGES,
        )
        assert cfg.api is ProviderApiType.ANTHROPIC_MESSAGES

    def test_with_ollama(self):
        cfg = ProviderConfig(
            base_url="http://localhost:11434",
            api=ProviderApiType.OLLAMA,
        )
        assert cfg.api is ProviderApiType.OLLAMA

    def test_serialization_round_trip(self):
        cfg = ProviderConfig(
            base_url="https://api.deepseek.com/v1",
            api_key_env="DEEPSEEK_API_KEY",
        )
        data = cfg.model_dump()
        cfg2 = ProviderConfig(**data)
        assert cfg2 == cfg


# ---------------------------------------------------------------------------
# ModelConfig
# ---------------------------------------------------------------------------


class TestModelConfig:
    def test_defaults(self):
        cfg = ModelConfig()
        assert cfg.default == "openai:gpt-4o"
        assert cfg.providers == {}

    def test_with_providers(self):
        cfg = ModelConfig(
            default="deepseek:deepseek-chat",
            providers={
                "deepseek": ProviderConfig(
                    base_url="https://api.deepseek.com/v1",
                    api_key_env="DEEPSEEK_API_KEY",
                ),
                "ollama": ProviderConfig(
                    base_url="http://localhost:11434",
                    api=ProviderApiType.OLLAMA,
                ),
            },
        )
        assert cfg.default == "deepseek:deepseek-chat"
        assert len(cfg.providers) == 2
        assert "deepseek" in cfg.providers

    def test_serialization_round_trip(self):
        cfg = ModelConfig(
            default="anthropic:claude-sonnet-4-20250514",
            providers={
                "anthropic": ProviderConfig(
                    api_key_env="ANTHROPIC_API_KEY",
                    api=ProviderApiType.ANTHROPIC_MESSAGES,
                ),
            },
        )
        data = cfg.model_dump()
        cfg2 = ModelConfig(**data)
        assert cfg2 == cfg

    def test_json_serialization(self):
        cfg = ModelConfig()
        json_str = cfg.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["default"] == "openai:gpt-4o"
        cfg2 = ModelConfig.model_validate_json(json_str)
        assert cfg2 == cfg


# ---------------------------------------------------------------------------
# RuntimeConfig
# ---------------------------------------------------------------------------


class TestRuntimeConfig:
    def test_defaults(self):
        cfg = RuntimeConfig()
        assert cfg.python_path == "python3"
        assert cfg.uv_path == "uv"

    def test_custom_paths(self):
        cfg = RuntimeConfig(python_path="/usr/bin/python3.12", uv_path="/opt/uv/bin/uv")
        assert cfg.python_path == "/usr/bin/python3.12"
        assert cfg.uv_path == "/opt/uv/bin/uv"


# ---------------------------------------------------------------------------
# PlatformConfig
# ---------------------------------------------------------------------------


class TestPlatformConfig:
    def test_defaults(self):
        cfg = PlatformConfig()
        assert isinstance(cfg.runtime, RuntimeConfig)
        assert isinstance(cfg.models, ModelConfig)
        assert cfg.runtime.python_path == "python3"
        assert cfg.models.default == "openai:gpt-4o"

    def test_with_custom_configs(self):
        cfg = PlatformConfig(
            runtime=RuntimeConfig(python_path="python3.12"),
            models=ModelConfig(default="anthropic:claude-sonnet-4-20250514"),
        )
        assert cfg.runtime.python_path == "python3.12"
        assert cfg.models.default == "anthropic:claude-sonnet-4-20250514"

    def test_serialization_round_trip(self):
        cfg = PlatformConfig(
            runtime=RuntimeConfig(uv_path="custom-uv"),
            models=ModelConfig(default="ollama:llama3"),
        )
        data = cfg.model_dump()
        cfg2 = PlatformConfig(**data)
        assert cfg2 == cfg

    def test_json_serialization(self):
        cfg = PlatformConfig()
        json_str = cfg.model_dump_json()
        cfg2 = PlatformConfig.model_validate_json(json_str)
        assert cfg2 == cfg
