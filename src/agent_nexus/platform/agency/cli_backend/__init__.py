"""CLI Backend Integration — config-driven CLI agent backend for LLM calls."""

from .base import GenericCLIBackend
from .parser import extract_json_value, parse_json_output, parse_text_output
from .registry import CLIBackendRegistry
from .router import CLIRouter
from .session_store import CLISessionStore
from .types import (
    BackendConfig,
    CLIResult,
    CLISessionRecord,
    DataLifecycleConfig,
    JsonPathConfig,
    RoutingConfig,
    TextPatternConfig,
)

__all__ = [
    "BackendConfig",
    "CLIBackendRegistry",
    "CLIResult",
    "CLIRouter",
    "CLISessionRecord",
    "CLISessionStore",
    "DataLifecycleConfig",
    "GenericCLIBackend",
    "JsonPathConfig",
    "RoutingConfig",
    "TextPatternConfig",
    "extract_json_value",
    "load_backend_configs_from_cli_backends",
    "load_backend_configs_from_providers",
    "load_routing_config",
    "parse_json_output",
    "parse_text_output",
]


# Backward-compatible lazy re-exports for functions moved to config.config_templates.
# Using __getattr__ avoids circular imports during package initialization.
def __getattr__(name: str):
    if name in (
        "load_backend_configs_from_cli_backends",
        "load_backend_configs_from_providers",
        "load_routing_config",
    ):
        from agent_nexus.platform.config import config_templates as _ct

        return getattr(_ct, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
