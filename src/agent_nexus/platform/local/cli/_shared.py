"""Shared helpers for CLI command modules.

Provides:
- _get_config_dir(): Resolve the platform config directory.
- _init_managers(): Initialise the standard manager stack.
- ConfigMigrator: Schema-based config migration.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import toml

logger = logging.getLogger(__name__)


def _get_config_dir() -> Path:
    """Resolve the platform config directory.

    Priority: ``AGENT_NEXUS_HOME`` env var > built-in default.
    """
    env = os.environ.get("AGENT_NEXUS_HOME")
    if env:
        return Path(env)
    from agent_nexus.platform.config.defaults import DEFAULT_CONFIG_DIR
    return DEFAULT_CONFIG_DIR


def _init_managers(
    config_dir: Path | None = None,
) -> tuple:
    """Initialise the standard manager stack used by most commands.

    Returns (config_loader, lockfile_manager, source_manager, config_dir).
    """
    from agent_nexus.platform.config.loader import ConfigLoader
    from agent_nexus.platform.local.lockfile import LockfileManager
    from agent_nexus.platform.local.sources import SourceManager

    _config_dir = config_dir or _get_config_dir()
    loader = ConfigLoader(_config_dir)
    loader.ensure_config_dir()

    lockfile = LockfileManager(_config_dir / "lockfile.json")
    sources = SourceManager(_config_dir / "sources.yaml")

    return loader, lockfile, sources, _config_dir


class ConfigMigrator:
    """Merge new defaults into user config when the schema version changes.

    Merge strategy:
    - New keys: add with default value
    - Existing keys: never overwrite (user intent preserved)
    - Nested dicts: recursive merge
    - Removed keys: leave in place
    - User-defined sections: never touched
    """

    TARGET_VERSION = "1.0"

    @classmethod
    def merge_if_needed(cls, config_path: Path) -> bool:
        """Merge new defaults into user config if schema is outdated.

        Returns True if migration was performed.
        """
        if not config_path.exists():
            return False

        try:
            raw = toml.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Cannot parse %s for migration", config_path)
            return False

        current_version = raw.get("schema_version", "")
        if current_version == cls.TARGET_VERSION:
            return False

        defaults = cls._default_config_dict()
        merged = cls._deep_merge(defaults, raw)
        merged["schema_version"] = cls.TARGET_VERSION

        config_path.write_text(
            toml.dumps(merged),
            encoding="utf-8",
        )
        logger.info(
            "Config migrated: %s -> %s",
            current_version or "(none)",
            cls.TARGET_VERSION,
        )
        return True

    @classmethod
    def check_version(cls, config_path: Path) -> str | None:
        """Return current schema_version, or None if config doesn't exist."""
        if not config_path.exists():
            return None
        try:
            raw = toml.loads(config_path.read_text(encoding="utf-8"))
            return raw.get("schema_version")
        except Exception:
            return None

    @classmethod
    def _default_config_dict(cls) -> dict[str, Any]:
        """Return the default config as a plain dict (no comments)."""
        return {
            "schema_version": cls.TARGET_VERSION,
            "runtime": {
                "python_path": "python3",
                "uv_path": "uv",
            },
            "models": {
                "default": "openai:gpt-4o",
                "providers": {
                    "openai": {
                        "api_key_env": "OPENAI_API_KEY",
                        "api": "openai-compatible",
                    },
                    "anthropic": {
                        "api_key_env": "ANTHROPIC_API_KEY",
                        "api": "anthropic-messages",
                    },
                },
            },
        }

    @classmethod
    def _deep_merge(
        cls,
        defaults: dict[str, Any],
        user: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge defaults into user config. User values always win."""
        result = dict(defaults)
        for key, user_val in user.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(user_val, dict)
            ):
                result[key] = cls._deep_merge(result[key], user_val)
            else:
                result[key] = user_val
        return result
