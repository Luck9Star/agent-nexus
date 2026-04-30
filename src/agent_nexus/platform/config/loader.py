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

from .config_templates import (
    load_backend_configs_from_cli_backends,
    load_routing_config,
)
from .defaults import (
    CONFIG_FILE,
    DEFAULT_CONFIG_DIR,
    DEFAULT_MODEL_STRING,
    DEFAULT_PROVIDERS,
    PROJECT_CONFIG_FILE,
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
        # mtime-based caches to avoid re-reading unchanged files
        self._config_cache: PlatformConfig | None = None
        self._config_cache_mtime: float = 0.0
        self._sources_cache: list[SourceEntry] | None = None
        self._sources_cache_mtime: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_config(self) -> PlatformConfig:
        """Load ``config.toml``, merge with env vars and built-in defaults.

        Merge order (highest priority first):
        1. Environment variable overrides (``AGENT_MODEL``, ``DEFAULT_MODEL``)
        2. Values from ``config.toml``
        3. Built-in defaults from :data:`DEFAULT_PROVIDERS`

        Results are cached based on the file's mtime — repeated calls
        return the same object until the file is modified.
        """
        config_path = self.config_dir / CONFIG_FILE

        # mtime-based cache check
        try:
            mtime = os.path.getmtime(config_path)
        except OSError:
            mtime = 0.0

        if self._config_cache is not None and mtime == self._config_cache_mtime:
            logger.debug("Returning cached config (mtime unchanged)")
            return self._config_cache

        raw: dict[str, Any] = {}

        try:
            raw = toml.loads(config_path.read_text(encoding="utf-8"))
            logger.debug("Loading config from %s", config_path)
        except FileNotFoundError:
            logger.debug("Config file not found at %s, using defaults", config_path)
        except toml.TomlDecodeError as exc:
            logger.error("Failed to parse config file %s: %s", config_path, exc)
            raise

        # --- Schema version ---
        schema_version = raw.get("schema_version", "1.0")

        # --- Runtime section ---
        runtime_raw = raw.get("runtime", {})
        runtime = RuntimeConfig(
            python_path=runtime_raw.get("python_path", "python3"),
            uv_path=runtime_raw.get("uv_path", "uv"),
            log_level=runtime_raw.get("log_level", "INFO"),
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

        # Parse stages
        stages_raw = models_raw.get("stages", {})
        if not isinstance(stages_raw, dict):
            logger.warning("config.toml [models].stages is not a mapping, ignoring it")
            stages_raw = {}
        stages: dict[str, str] = {str(k): str(v) for k, v in stages_raw.items()}

        models = ModelConfig(
            default=default_model,
            providers=providers,
            stages=stages,
            streaming_default=models_raw.get("streaming_default", True),
        )

        # --- Sources section ---
        sources = self._parse_sources_from_raw(raw)

        config = PlatformConfig(
            schema_version=schema_version,
            runtime=runtime,
            models=models,
            sources=sources,
        )

        logger.info(
            "Config loaded: default_model=%s, providers=%s, sources=%d",
            config.models.default,
            list(config.models.providers.keys()),
            len(config.sources),
        )
        self._config_cache = config
        self._config_cache_mtime = mtime
        return config

    def load_sources(self) -> list[SourceEntry]:
        """Load source entries from config.toml ``[sources]``.

        Falls back to ``sources.yaml`` for backward compatibility.

        Returns an empty list when neither has entries.
        """
        config = self.load_config()
        if config.sources:
            return list(config.sources)
        return self._load_sources_from_yaml()

    def load_cli_backends(self) -> dict[str, Any]:
        """Load CLI backend configs from config.toml [cli_backends.*] sections."""
        raw = self._load_raw()
        cli_backends = raw.get("cli_backends", {})
        if not isinstance(cli_backends, dict):
            return {}
        return load_backend_configs_from_cli_backends(cli_backends)

    def load_cli_routing(self) -> Any:
        """Load [cli_routing] section from config.toml.

        Returns None when the section is absent.
        """
        raw = self._load_raw()
        if "cli_routing" not in raw:
            return None
        return load_routing_config(raw["cli_routing"])

    def _load_raw(self) -> dict[str, Any]:
        """Read and parse config.toml, returning the raw dict."""
        config_path = self.config_dir / CONFIG_FILE
        try:
            return toml.loads(config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except toml.TomlDecodeError:
            return {}

    def load_project_config(self, project_dir: Path | None = None) -> PlatformConfig | None:
        """Load optional project-level ``agent-nexus.toml``.

        Searches *project_dir* (defaults to cwd) for ``agent-nexus.toml``.
        Returns ``None`` when the file is missing.
        """
        search_dir = (project_dir or Path.cwd()).resolve()
        project_config_path = search_dir / PROJECT_CONFIG_FILE

        if not project_config_path.exists():
            logger.debug("No project config at %s", project_config_path)
            return None

        try:
            raw = toml.loads(project_config_path.read_text(encoding="utf-8"))
        except (toml.TomlDecodeError, OSError) as exc:
            logger.warning("Failed to load project config: %s", exc)
            return None

        models_raw = raw.get("models", {})
        providers_raw = models_raw.get("providers", {})
        if not isinstance(providers_raw, dict):
            providers_raw = {}

        default_model = models_raw.get("default", "")

        stages_raw = models_raw.get("stages", {})
        if not isinstance(stages_raw, dict):
            stages_raw = {}
        stages: dict[str, str] = {str(k): str(v) for k, v in stages_raw.items()}

        return PlatformConfig(
            schema_version=raw.get("schema_version", "1.0"),
            runtime=RuntimeConfig(),
            models=ModelConfig(
                default=default_model,
                providers=self._build_providers(providers_raw),
                stages=stages,
            ),
        )

    def load_merged_config(self, project_dir: Path | None = None) -> PlatformConfig:
        """Load global config merged with optional project-level overrides.

        Project config values win where non-empty. Priority:
        env vars > project ``agent-nexus.toml`` > global ``config.toml``
        > built-in defaults.
        """
        global_config = self.load_config()
        project_config = self.load_project_config(project_dir)

        if project_config is None:
            return global_config

        merged_default = project_config.models.default or global_config.models.default

        merged_providers = dict(global_config.models.providers)
        merged_providers.update(project_config.models.providers)

        merged_stages = dict(global_config.models.stages)
        merged_stages.update(project_config.models.stages)

        return PlatformConfig(
            schema_version=global_config.schema_version,
            runtime=global_config.runtime,
            models=ModelConfig(
                default=merged_default,
                providers=merged_providers,
                stages=merged_stages,
            ),
            sources=global_config.sources,
        )

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
    def _parse_sources_from_raw(raw: dict[str, Any]) -> list[SourceEntry]:
        """Extract source entries from ``[sources]`` section of config.toml."""
        sources_list = raw.get("sources", [])
        if not isinstance(sources_list, list):
            return []
        entries: list[SourceEntry] = []
        for item in sources_list:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            try:
                entry = SourceEntry(
                    name=name,
                    type=item.get("type", "git"),
                    url=item.get("url", ""),
                    branch=item.get("branch", "main"),
                )
                entries.append(entry)
            except Exception:
                logger.warning("Skipping invalid source entry: %s", item)
        return entries

    def _load_sources_from_yaml(self) -> list[SourceEntry]:
        """Load ``sources.yaml`` for backward compatibility.

        Returns an empty list when the file does not exist.
        """
        sources_path = self.config_dir / SOURCES_FILE

        # mtime-based cache check
        try:
            mtime = os.path.getmtime(sources_path)
        except OSError:
            mtime = 0.0

        if self._sources_cache is not None and mtime == self._sources_cache_mtime:
            logger.debug("Returning cached sources (mtime unchanged)")
            return self._sources_cache

        if not sources_path.exists():
            logger.debug("Sources file not found at %s", sources_path)
            entries: list[SourceEntry] = []
            self._sources_cache = entries
            self._sources_cache_mtime = mtime
            return entries

        logger.debug("Loading sources from %s", sources_path)
        try:
            raw = yaml.safe_load(sources_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            logger.error("Failed to parse %s: %s", sources_path, exc)
            return []
        except OSError as exc:
            logger.error("Cannot read %s: %s", sources_path, exc)
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

        logger.info("Loaded %d source(s) from sources.yaml", len(entries))
        self._sources_cache = entries
        self._sources_cache_mtime = mtime
        return entries

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
                    api_str,
                    name,
                    valid,
                )
                api_type = ProviderApiType.OPENAI_COMPATIBLE

            if existing:
                # Override only fields that the user explicitly provides
                merged[name] = ProviderConfig(
                    base_url=raw.get("base_url", existing.base_url),
                    api_key_env=raw.get("api_key_env", existing.api_key_env),
                    api=api_type,
                    streaming=raw.get("streaming", existing.streaming),
                )
            else:
                merged[name] = ProviderConfig(
                    base_url=raw.get("base_url", ""),
                    api_key_env=raw.get("api_key_env", ""),
                    api=api_type,
                    streaming=raw.get("streaming"),
                )

        return merged
