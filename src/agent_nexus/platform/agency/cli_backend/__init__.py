"""CLI Backend Integration — config-driven CLI agent backend for LLM calls."""

from .base import GenericCLIBackend
from .config_templates import load_backend_configs_from_providers, load_routing_config
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
    "load_backend_configs_from_providers",
    "load_routing_config",
    "parse_json_output",
    "parse_text_output",
]
