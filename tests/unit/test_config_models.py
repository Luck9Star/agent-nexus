"""Unit tests for agent_nexus.models.config module."""

import json

from agent_nexus.models.config import (
    ModelConfig,
    PlatformConfig,
    ProviderApiType,
    ProviderConfig,
    RuntimeConfig,
)

# ---------------------------------------------------------------------------
# ProviderConfig
# ---------------------------------------------------------------------------


class TestProviderConfig:
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


class TestPlatformConfig:
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
