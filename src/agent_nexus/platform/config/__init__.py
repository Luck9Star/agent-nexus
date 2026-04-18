"""Platform configuration module: config loading, model resolution, defaults.

Usage::

    from agent_nexus.platform.config import ConfigLoader, ModelConfigManager

    loader = ConfigLoader()
    config = loader.load_config()
    mgr = ModelConfigManager(config)
    model = mgr.resolve_model("doc-filler", recommended_tier="lightweight")
"""

from .defaults import (
    CONFIG_FILE,
    DEFAULT_CONFIG_DIR,
    DEFAULT_MODEL_STRING,
    DEFAULT_PROVIDERS,
    ENV_VAR_OVERRIDES,
    LOCKFILE,
    MODEL_TIER_MAP,
    SOURCES_FILE,
)
from .loader import ConfigLoader
from .model_config import ModelConfigManager

__all__ = [
    # Loader
    "ConfigLoader",
    # Model resolution
    "ModelConfigManager",
    # Defaults & constants
    "CONFIG_FILE",
    "DEFAULT_CONFIG_DIR",
    "DEFAULT_MODEL_STRING",
    "DEFAULT_PROVIDERS",
    "ENV_VAR_OVERRIDES",
    "LOCKFILE",
    "MODEL_TIER_MAP",
    "SOURCES_FILE",
]
