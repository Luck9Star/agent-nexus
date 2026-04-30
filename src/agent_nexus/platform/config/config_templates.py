"""Config template parsing for CLI backend providers.

Moved from agency.cli_backend.config_templates to resolve the
config -> agency dependency direction violation (config must not
import from agency).
"""

from __future__ import annotations

from typing import Any

from agent_nexus.platform.agency.cli_backend.types import (
    BackendConfig as _BackendConfig,
)
from agent_nexus.platform.agency.cli_backend.types import (
    JsonPathConfig as _JsonPathConfig,
)
from agent_nexus.platform.agency.cli_backend.types import (
    RoutingConfig as _RoutingConfig,
)
from agent_nexus.platform.agency.cli_backend.types import (
    TextPatternConfig as _TextPatternConfig,
)
from agent_nexus.platform.config.defaults import DEFAULT_PIPELINE_TIMEOUT


def _parse_backend_config(name: str, raw: dict[str, Any]) -> _BackendConfig:
    json_paths_raw = raw.get("json_paths", {})
    json_paths = (
        _JsonPathConfig(
            text=json_paths_raw.get("text"),
            session_id=json_paths_raw.get("session_id"),
            model=json_paths_raw.get("model"),
            input_tokens=json_paths_raw.get("input_tokens"),
            output_tokens=json_paths_raw.get("output_tokens"),
        )
        if isinstance(json_paths_raw, dict)
        else _JsonPathConfig()
    )

    text_patterns_raw = raw.get("text_patterns", {})
    text_patterns = (
        _TextPatternConfig(
            session_id=text_patterns_raw.get("session_id"),
            model=text_patterns_raw.get("model"),
        )
        if isinstance(text_patterns_raw, dict)
        else _TextPatternConfig()
    )

    return _BackendConfig(
        command=raw.get("command", name),
        args=raw.get("args", []),
        system_prompt_flag=raw.get("system_prompt_flag", "--system-prompt"),
        session_flag=raw.get("session_flag", "--resume"),
        output_format=raw.get("output_format", "json"),
        output_format_flag=raw.get("output_format_flag", ""),
        json_paths=json_paths,
        text_patterns=text_patterns,
        model_map=raw.get("model_map", {}),
        timeout_secs=raw.get("timeout_secs", DEFAULT_PIPELINE_TIMEOUT),
    )


def load_backend_configs_from_providers(
    providers: dict[str, dict[str, Any]],
) -> dict[str, _BackendConfig]:
    """Extract BackendConfig for providers with api = "cli"."""
    result: dict[str, _BackendConfig] = {}
    for name, raw in providers.items():
        if not isinstance(raw, dict):
            continue
        if raw.get("api") != "cli":
            continue
        result[name] = _parse_backend_config(name, raw)
    return result


def load_backend_configs_from_cli_backends(
    cli_backends: dict[str, dict[str, Any]],
) -> dict[str, _BackendConfig]:
    """Parse [cli_backends.<name>] sections into BackendConfig instances."""
    result: dict[str, _BackendConfig] = {}
    for name, raw in cli_backends.items():
        if not isinstance(raw, dict):
            continue
        result[name] = _parse_backend_config(name, raw)
    return result


def load_routing_config(raw: dict[str, Any]) -> _RoutingConfig:
    """Parse [cli_routing] section into RoutingConfig."""
    return _RoutingConfig(
        default=raw.get("default", ""),
        fallback_enabled=raw.get("fallback_enabled", True),
        fallback_chain=raw.get("fallback_chain", []),
        model_rules=raw.get("model_rules", {}),
    )
