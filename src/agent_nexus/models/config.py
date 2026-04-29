"""Configuration models: RuntimeConfig, ModelConfig, ProviderConfig, ModelTier (re-export)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from agent_nexus.models._common import FrozenModel
from agent_nexus.models.distribution import SourceEntry


class ProviderApiType(StrEnum):
    """API protocol type for a model provider."""

    OPENAI_COMPATIBLE = "openai-compatible"
    ANTHROPIC_MESSAGES = "anthropic-messages"
    OLLAMA = "ollama"
    CLI = "cli"


class ProviderConfig(FrozenModel):
    """A single model provider configuration.

    Maps to a [models.providers.<name>] section in config.toml.
    API keys are read from the environment variable named in api_key_env,
    never stored directly in the config file.
    """

    base_url: str = ""
    api_key_env: str = ""
    api: ProviderApiType = ProviderApiType.OPENAI_COMPATIBLE
    streaming: bool | None = None


class ModelConfig(FrozenModel):
    """Top-level model configuration.

    Maps to the [models] section in config.toml.

    Example config.toml:
        [models]
        default = "openai:gpt-4o"

        [models.stages]
        planning = "openai:gpt-4o"
        integration = "openai:gpt-4o"
        qa = "openai:gpt-4o"

        [models.providers.deepseek]
        base_url = "https://api.deepseek.com/v1"
        api_key_env = "DEEPSEEK_API_KEY"
        api = "openai-compatible"
    """

    default: str = "openai:gpt-4o"
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    stages: dict[str, str] = Field(default_factory=dict)
    """Per-stage model overrides (e.g. ``{"planning": "openai:gpt-4o"}``).

    Supported stages: ``planning``, ``integration``, ``qa``, ``execution``.
    Falls back to ``default`` if a stage is not specified.
    """
    streaming_default: bool = True


class RuntimeConfig(FrozenModel):
    """Runtime environment configuration.

    Maps to the [runtime] section in config.toml.

    Example config.toml:
        [runtime]
        python_path = "python3"
        uv_path = "uv"
    """

    python_path: str = "python3"
    uv_path: str = "uv"


class PlatformConfig(FrozenModel):
    """Root configuration model for the entire platform.

    This is the top-level object parsed from config.toml.
    """

    schema_version: str = "1.0"
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    models: ModelConfig = Field(default_factory=ModelConfig)
    sources: list[SourceEntry] = Field(default_factory=list)
