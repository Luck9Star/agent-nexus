"""ConfigLoader: load config.toml and sources.yaml from the platform config directory.

Priority chain for every setting::

    environment variables  >  config.toml values  >  built-in defaults
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import toml
import yaml

from agent_nexus.models.config import (
    ModelConfig,
    PlatformConfig,
    ProviderApiType,
    ProviderConfig,
    RuntimeConfig,
)
from agent_nexus.models.distribution import SourceEntry

from .defaults import (
    CONFIG_FILE,
    DEFAULT_CONFIG_DIR,
    DEFAULT_MODEL_STRING,
    DEFAULT_PROVIDERS,
    SOURCES_FILE,
)

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Load and merge platform configuration from multiple sources.

    Parameters
    ----------
    config_dir:
        Path to the platform config directory.  Defaults to
        ``~/.agent-nexus/`` (overridable via ``AGENT_NEXUS_HOME``).
    """

    def __init__(self, config_dir: Path | None = None) -> None:
        self.config_dir = config_dir or DEFAULT_CONFIG_DIR

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_config(self) -> PlatformConfig:
        """Load ``config.toml``, merge with env vars and built-in defaults.

        Merge order (highest priority first):
        1. Environment variable overrides (``AGENT_MODEL``, ``DEFAULT_MODEL``)
        2. Values from ``config.toml``
        3. Built-in defaults from :data:`DEFAULT_PROVIDERS`
        """
        config_path = self.config_dir / CONFIG_FILE
        raw: dict[str, Any] = {}

        if config_path.exists():
            logger.debug("Loading config from %s", config_path)
            raw = toml.loads(config_path.read_text(encoding="utf-8"))
        else:
            logger.debug("Config file not found at %s, using defaults", config_path)

        # --- Runtime section ---
        runtime_raw = raw.get("runtime", {})
        runtime = RuntimeConfig(
            python_path=runtime_raw.get("python_path", "python3"),
            uv_path=runtime_raw.get("uv_path", "uv"),
        )

        # --- Models section ---
        models_raw = raw.get("models", {})

        # Determine default model: env vars > config > hardcoded default
        default_model = (
            os.environ.get("AGENT_MODEL")
            or os.environ.get("DEFAULT_MODEL")
            or models_raw.get("default", DEFAULT_MODEL_STRING)
        )

        # Build provider registry: built-in defaults merged with config.toml
        providers = self._build_providers(models_raw.get("providers", {}))

        models = ModelConfig(default=default_model, providers=providers)

        config = PlatformConfig(runtime=runtime, models=models)

        logger.info(
            "Config loaded: default_model=%s, providers=%s",
            config.models.default,
            list(config.models.providers.keys()),
        )
        return config

    def load_sources(self) -> list[SourceEntry]:
        """Load ``sources.yaml`` and return validated source entries.

        Returns an empty list when the file does not exist.
        """
        sources_path = self.config_dir / SOURCES_FILE

        if not sources_path.exists():
            logger.debug("Sources file not found at %s", sources_path)
            return []

        logger.debug("Loading sources from %s", sources_path)
        raw = yaml.safe_load(sources_path.read_text(encoding="utf-8"))

        if not raw or "sources" not in raw:
            logger.warning("sources.yaml is empty or missing 'sources' key")
            return []

        entries: list[SourceEntry] = []
        for item in raw["sources"]:
            try:
                entry = SourceEntry(
                    name=item["name"],
                    type=item.get("type", "git"),
                    url=item.get("url", ""),
                    branch=item.get("branch", "main"),
                )
                entries.append(entry)
            except Exception as exc:
                logger.warning("Skipping invalid source entry %s: %s", item, exc)

        logger.info("Loaded %d source(s)", len(entries))
        return entries

    def ensure_config_dir(self) -> Path:
        """Create the config directory tree if it does not exist.

        Returns the created (or existing) config directory path.
        """
        self.config_dir.mkdir(parents=True, exist_ok=True)

        # Ensure standard subdirectories exist
        for subdir in ("agents", "venvs", "cache/repos", "runtimes", "logs"):
            (self.config_dir / subdir).mkdir(parents=True, exist_ok=True)

        logger.debug("Config dir ensured: %s", self.config_dir)
        return self.config_dir

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_providers(
        user_providers: dict[str, dict[str, Any]],
    ) -> dict[str, ProviderConfig]:
        """Merge built-in provider defaults with user-defined providers.

        User entries override built-in defaults field-by-field.  Providers
        not present in ``DEFAULT_PROVIDERS`` are added as-is.
        """
        merged: dict[str, ProviderConfig] = {}

        # Start with built-in defaults
        for name, preset in DEFAULT_PROVIDERS.items():
            merged[name] = ProviderConfig(
                base_url=preset.get("base_url", ""),
                api_key_env=preset.get("api_key_env", ""),
                api=ProviderApiType(preset.get("api", "openai-compatible")),
            )

        # Merge / override with user definitions from config.toml
        for name, raw in user_providers.items():
            existing = merged.get(name)
            api_str = raw.get("api", "openai-compatible")

            if existing:
                # Override only fields that the user explicitly provides
                merged[name] = ProviderConfig(
                    base_url=raw.get("base_url", existing.base_url),
                    api_key_env=raw.get("api_key_env", existing.api_key_env),
                    api=ProviderApiType(api_str),
                )
            else:
                merged[name] = ProviderConfig(
                    base_url=raw.get("base_url", ""),
                    api_key_env=raw.get("api_key_env", ""),
                    api=ProviderApiType(api_str),
                )

        return merged
