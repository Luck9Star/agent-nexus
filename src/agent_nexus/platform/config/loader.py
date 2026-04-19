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

        try:
            raw = toml.loads(config_path.read_text(encoding="utf-8"))
            logger.debug("Loading config from %s", config_path)
        except FileNotFoundError:
            logger.debug("Config file not found at %s, using defaults", config_path)
        except toml.TomlDecodeError as exc:
            logger.warning("Failed to parse config file %s: %s", config_path, exc)

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
        providers_raw = models_raw.get("providers", {})
        if not isinstance(providers_raw, dict):
            logger.warning("config.toml [models].providers is not a mapping, ignoring it")
            providers_raw = {}
        providers = self._build_providers(providers_raw)

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
        try:
            raw = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", sources_path, exc)
            return []

        if not isinstance(raw, dict) or "sources" not in raw:
            logger.warning("sources.yaml is empty or missing 'sources' key")
            return []

        sources_list = raw["sources"]
        if not isinstance(sources_list, list):
            logger.warning("sources.yaml 'sources' key is not a list")
            return []

        entries: list[SourceEntry] = []
        for item in sources_list:
            if not isinstance(item, dict):
                logger.warning("Skipping non-mapping source entry: %r", item)
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                logger.error("Skipping source entry with missing/invalid 'name': %r", item)
                continue
            try:
                entry = SourceEntry(
                    name=name,
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
                base_url=str(preset.get("base_url", "")),
                api_key_env=str(preset.get("api_key_env", "")),
                api=ProviderApiType(preset.get("api", "openai-compatible")),
            )

        # Merge / override with user definitions from config.toml
        for name, raw in user_providers.items():
            existing = merged.get(name)
            api_str = raw.get("api", "openai-compatible")

            try:
                api_type = ProviderApiType(api_str)
            except ValueError:
                valid = [e.value for e in ProviderApiType]
                logger.warning(
                    "Invalid api type '%s' in provider '%s'. Valid: %s. "
                    "Defaulting to 'openai-compatible'.",
                    api_str, name, valid,
                )
                api_type = ProviderApiType.OPENAI_COMPATIBLE

            if existing:
                # Override only fields that the user explicitly provides
                merged[name] = ProviderConfig(
                    base_url=raw.get("base_url", existing.base_url),
                    api_key_env=raw.get("api_key_env", existing.api_key_env),
                    api=api_type,
                )
            else:
                merged[name] = ProviderConfig(
                    base_url=raw.get("base_url", ""),
                    api_key_env=raw.get("api_key_env", ""),
                    api=api_type,
                )

        return merged
