"""Data types for CLI backend integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CLIResult:
    """Structured result from a CLI backend call."""

    text: str
    model: str
    session_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw_stdout: str = ""
    raw_stderr: str = ""
    returncode: int = 0
    duration_ms: int = 0
    parse_error: bool = False


@dataclass
class JsonPathConfig:
    """JSON output field path mapping for extracting CLI output."""

    text: str | None = None
    session_id: str | None = None
    model: str | None = None
    input_tokens: str | None = None
    output_tokens: str | None = None


@dataclass
class TextPatternConfig:
    """Regex patterns for extracting metadata from text-mode CLI output."""

    session_id: str | None = None
    model: str | None = None


@dataclass
class BackendConfig:
    """Configuration for a single CLI backend provider."""

    command: str
    args: list[str] = field(default_factory=list)
    system_prompt_flag: str = "--system-prompt"
    session_flag: str = "--resume"
    output_format: str = "json"
    output_format_flag: str = ""
    json_paths: JsonPathConfig = field(default_factory=JsonPathConfig)
    text_patterns: TextPatternConfig = field(default_factory=TextPatternConfig)
    model_map: dict[str, str] = field(default_factory=dict)
    timeout_secs: int = 300


@dataclass
class RoutingConfig:
    """CLI backend routing strategy configuration."""

    default: str
    fallback_enabled: bool = True
    fallback_chain: list[str] = field(default_factory=list)
    model_rules: dict[str, str] = field(default_factory=dict)


@dataclass
class CLISessionRecord:
    """A CLI session record persisted in SQLite."""

    session_id: str
    backend_name: str
    name: str | None = None
    model: str | None = None
    task_id: str | None = None
    created_at: str | None = None
    last_used_at: str | None = None
    turn_count: int = 1
    metadata: dict[str, Any] | None = None


@dataclass
class DataLifecycleConfig:
    """Database lifecycle management configuration."""

    hot_days: int = 30
    warm_days: int = 90
    archive_dir: str = ""
    auto_archive: bool = True
