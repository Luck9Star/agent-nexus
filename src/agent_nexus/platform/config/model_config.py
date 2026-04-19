"""ModelConfigManager: resolve model strings and provider API keys.

Model resolution priority (highest first):
1. ``AGENT_MODEL`` env var (global override)
2. Agent-specific recommended model (from manifest)
3. Tier-to-model mapping (from ``MODEL_TIER_MAP``)
4. ``config.toml`` ``[models].default``
"""

from __future__ import annotations

import logging
import os

from agent_nexus.models.agent import ModelTier
from agent_nexus.models.config import PlatformConfig, ProviderConfig

from .defaults import DEFAULT_MODEL_STRING, MODEL_TIER_MAP

logger = logging.getLogger(__name__)

# Fallback env var names for well-known providers.
_PROVIDER_ENV_FALLBACKS: dict[str, list[str]] = {
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "minimax": ["MINIMAX_API_KEY"],
    "qwen": ["DASHSCOPE_API_KEY"],
}


class ModelConfigManager:
    """Resolve model string for a given agent based on tier and config.

    Parameters
    ----------
    config:
        The loaded :class:`PlatformConfig` (typically from :class:`ConfigLoader`).
    """

    def __init__(self, config: PlatformConfig) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_model(
        self,
        agent_name: str,
        recommended: str | None = None,
        recommended_tier: str | ModelTier | None = None,
    ) -> str:
        """Resolve the final model string for an agent.

        Parameters
        ----------
        agent_name:
            Agent name (used for logging; future: agent-specific overrides).
        recommended:
            Explicit model string from the agent manifest's
            ``model_config.recommended`` field.  Takes priority over tier.
        recommended_tier:
            Model tier (``lightweight``, ``standard``, ``powerful``,
            ``premium``) from the agent manifest.

        Returns
        -------
        str
            A ``provider:model`` string, e.g. ``"openai:gpt-4o"``.
        """
        # 1. Global env-var override
        env_model = os.environ.get("AGENT_MODEL")
        if env_model:
            logger.debug(
                "Model for '%s' resolved from AGENT_MODEL env: %s",
                agent_name,
                env_model,
            )
            return env_model

        # 2. Agent-specific recommended model from manifest
        if recommended:
            logger.debug(
                "Model for '%s' resolved from manifest recommended: %s",
                agent_name,
                recommended,
            )
            return recommended

        # 3. Tier mapping
        if recommended_tier:
            try:
                tier_key = (
                    ModelTier(recommended_tier)
                    if isinstance(recommended_tier, str)
                    else recommended_tier
                )
            except ValueError:
                logger.warning(
                    "Unknown model tier '%s' for agent '%s', ignoring",
                    recommended_tier,
                    agent_name,
                )
                tier_key = None
            if tier_key is not None:
                tier_model = MODEL_TIER_MAP.get(tier_key)
                if tier_model:
                    logger.debug(
                        "Model for '%s' resolved from tier %s: %s",
                        agent_name,
                        tier_key,
                        tier_model,
                    )
                    return tier_model

        # 4. Config default
        default = self._config.models.default or DEFAULT_MODEL_STRING
        logger.debug(
            "Model for '%s' resolved from config default: %s",
            agent_name,
            default,
        )
        return default

    def get_provider_config(self, provider_name: str) -> ProviderConfig:
        """Get provider configuration by name.

        Looks up the provider in the config's provider registry.  Returns
        an empty default if the provider is not registered.

        Parameters
        ----------
        provider_name:
            Provider identifier (e.g. ``"openai"``, ``"deepseek"``).

        Returns
        -------
        ProviderConfig
            Provider configuration with base URL and API key env var name.
        """
        provider = self._config.models.providers.get(provider_name)
        if provider:
            return provider

        logger.warning(
            "Provider '%s' not found in config, returning empty default",
            provider_name,
        )
        return ProviderConfig()

    def resolve_api_key(self, provider: ProviderConfig | str) -> str:
        """Read API key from the environment variable named in the provider config.

        Falls back to well-known env vars based on provider name when
        ``api_key_env`` is not set or the variable is empty.

        Parameters
        ----------
        provider:
            Either a :class:`ProviderConfig` instance or a provider name
            string (looked up in the provider registry).

        Returns
        -------
        str
            The API key string, or empty string if not set.
        """
        if isinstance(provider, str):
            # Look up by name so we can also do fallback lookups
            provider_cfg = self.get_provider_config(provider)
            provider_name = provider
        else:
            provider_cfg = provider
            # Find the provider name by identity in the registry
            provider_name = ""
            for pname, pcfg in self._config.models.providers.items():
                if pcfg is provider_cfg:
                    provider_name = pname
                    break

        # Primary: read from the configured env var
        if provider_cfg.api_key_env:
            key = os.environ.get(provider_cfg.api_key_env, "")
            if key:
                return key

        # Secondary: try well-known fallback env vars for the provider
        # Normalize to lowercase so callers can pass "OpenAI" / "OPENAI" etc.
        provider_name = provider_name.lower()
        for env_var in _PROVIDER_ENV_FALLBACKS.get(provider_name, []):
            key = os.environ.get(env_var, "")
            if key:
                return key

        logger.warning(
            "No API key found for provider '%s' (checked env: %s)",
            provider_name,
            [provider_cfg.api_key_env] + _PROVIDER_ENV_FALLBACKS.get(provider_name.lower(), []),
        )
        return ""

    def parse_model_string(self, model_string: str) -> tuple[str, str]:
        """Split a ``provider:model`` string into ``(provider, model_name)``.

        Parameters
        ----------
        model_string:
            A string like ``"openai:gpt-4o"`` or
            ``"anthropic:claude-sonnet-4-20250514"``.

        Returns
        -------
        tuple[str, str]
            ``(provider_name, model_name)``.  If no colon is present,
            provider defaults to ``"openai"``.
        """
        if ":" in model_string:
            provider, model_name = model_string.split(":", 1)
            return provider, model_name
        return "openai", model_string
