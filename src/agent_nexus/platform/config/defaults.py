"""Default configuration values, path constants, and built-in provider definitions.

All platform-wide defaults are centralized here so every other module can
import a single source of truth.
"""

from __future__ import annotations

import os
from pathlib import Path

from agent_nexus.models.agent import ModelTier
from agent_nexus.models.config import ProviderApiType

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_DIR: Path = Path(
    os.environ.get("AGENT_NEXUS_HOME", str(Path.home() / ".agent-nexus"))
)
"""Root directory for all platform state.

Overridable via the ``AGENT_NEXUS_HOME`` environment variable.
"""

CONFIG_FILE: str = "config.toml"
SOURCES_FILE: str = "sources.yaml"
LOCKFILE: str = "lockfile.json"

# ---------------------------------------------------------------------------
# Built-in provider definitions
# ---------------------------------------------------------------------------

DEFAULT_PROVIDERS: dict[str, dict[str, str]] = {
    "openai": {
        "api_key_env": "OPENAI_API_KEY",
        "api": ProviderApiType.OPENAI_COMPATIBLE,
    },
    "anthropic": {
        "api_key_env": "ANTHROPIC_API_KEY",
        "api": ProviderApiType.ANTHROPIC_MESSAGES,
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "api": ProviderApiType.OPENAI_COMPATIBLE,
    },
    "minimax": {
        "base_url": "https://api.minimax.chat/v1",
        "api_key_env": "MINIMAX_API_KEY",
        "api": ProviderApiType.OPENAI_COMPATIBLE,
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "api": ProviderApiType.OPENAI_COMPATIBLE,
    },
    "ollama": {
        "base_url": "http://localhost:11434/v1",
        "api_key_env": "",
        "api": ProviderApiType.OPENAI_COMPATIBLE,
    },
}
"""Built-in provider presets merged with user-defined providers from config.toml.

Each entry maps to a :class:`ProviderConfig`.  Users can override any field
by declaring a ``[models.providers.<name>]`` section in config.toml.
"""

# ---------------------------------------------------------------------------
# Model tier -> default model string mapping
# ---------------------------------------------------------------------------

MODEL_TIER_MAP: dict[str, str] = {
    ModelTier.LIGHTWEIGHT: "openai:gpt-4o-mini",
    ModelTier.STANDARD: "openai:gpt-4o",
    ModelTier.POWERFUL: "anthropic:claude-sonnet-4-20250514",
    ModelTier.PREMIUM: "anthropic:claude-opus-4-20250116",
}
"""Maps :class:`ModelTier` enum values to default ``provider:model`` strings.

When an agent declares a recommended tier (e.g. ``lightweight``) but does not
specify an exact model string, the tier map supplies the concrete default.
"""

# ---------------------------------------------------------------------------
# Environment variable -> config path overrides
# ---------------------------------------------------------------------------

ENV_VAR_OVERRIDES: dict[str, str] = {
    "AGENT_MODEL": "models.default",
    "DEFAULT_MODEL": "models.default",
    "AGENT_NEXUS_HOME": "config_dir",
}
"""Environment variables that take priority over config.toml values.

Keys are env var names; values are dot-separated config paths that the env
var overrides.
"""

# ---------------------------------------------------------------------------
# Default model string (used when config.toml is absent)
# ---------------------------------------------------------------------------

DEFAULT_MODEL_STRING: str = "openai:gpt-4o"
"""Fallback model string when neither env vars nor config.toml provide one."""
