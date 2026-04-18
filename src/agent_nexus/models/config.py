"""Configuration models: RuntimeConfig, ModelConfig, ProviderConfig, ModelTier (re-export)."""

from __future__ import annotations

from enum import StrEnum
from pydantic import BaseModel, ConfigDict, Field


class ProviderApiType(StrEnum):
    """API protocol type for a model provider."""

    OPENAI_COMPATIBLE = "openai-compatible"
    ANTHROPIC_MESSAGES = "anthropic-messages"
    OLLAMA = "ollama"


class ProviderConfig(BaseModel):
    """A single model provider configuration.

    Maps to a [models.providers.<name>] section in config.toml.
    API keys are read from the environment variable named in api_key_env,
    never stored directly in the config file.
    """

    model_config = ConfigDict(frozen=True)

    base_url: str = ""
    api_key_env: str = ""
    api: ProviderApiType = ProviderApiType.OPENAI_COMPATIBLE


class ModelConfig(BaseModel):
    """Top-level model configuration.

    Maps to the [models] section in config.toml.

    Example config.toml:
        [models]
        default = "openai:gpt-4o"

        [models.providers.deepseek]
        base_url = "https://api.deepseek.com/v1"
        api_key_env = "DEEPSEEK_API_KEY"
        api = "openai-compatible"
    """

    model_config = ConfigDict(frozen=True)

    default: str = "openai:gpt-4o"
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)


class RuntimeConfig(BaseModel):
    """Runtime environment configuration.

    Maps to the [runtime] section in config.toml.

    Example config.toml:
        [runtime]
        python_path = "python3"
        uv_path = "uv"
    """

    model_config = ConfigDict(frozen=True)

    python_path: str = "python3"
    uv_path: str = "uv"


class PlatformConfig(BaseModel):
    """Root configuration model for the entire platform.

    This is the top-level object parsed from config.toml.
    """

    model_config = ConfigDict(frozen=True)

    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    models: ModelConfig = Field(default_factory=ModelConfig)
