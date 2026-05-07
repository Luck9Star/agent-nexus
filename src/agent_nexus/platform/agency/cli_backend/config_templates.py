# Backward compatibility — moved to config/config_templates.py
# to resolve the config -> agency dependency direction violation.
#
# Lazy imports to avoid circular dependency during module initialization:
#   agency.cli_backend.__init__ -> config_templates (this file) -> config.config_templates
#   config/__init__ -> loader -> config_templates -> agency.cli_backend.types
#   -> __init__ -> this file
from __future__ import annotations

import importlib as _importlib


def __getattr__(name: str):
    if name in (
        "_parse_backend_config",
        "load_backend_configs_from_cli_backends",
        "load_backend_configs_from_providers",
        "load_routing_config",
    ):
        mod = _importlib.import_module("agent_nexus.platform.config.config_templates")
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
