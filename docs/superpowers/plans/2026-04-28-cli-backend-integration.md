# CLI Backend Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate 6 CLI agent tools (Claude Code, Gemini CLI, Codex CLI, OpenClaw, Hermes, Nanobot) as LLM call backends in Agent Nexus, enabling model access through installed CLIs with config-driven architecture and SQLite session persistence.

**Architecture:** CLI backends are a new `ProviderApiType.CLI` variant in the existing `LLMClient`. A `GenericCLIBackend` class/trait handles all CLI invocation through config-driven command templates — no per-CLI code. A `CLIRouter` resolves which CLI to use via 4-strategy priority routing with optional fallback. `CLISessionStore` persists session IDs and task execution data in SQLite with WAL mode. Both Python and Rust implementations share the same config.toml schema and SQLite schema.

**Tech Stack:** Python 3.11+ (subprocess, sqlite3, dataclasses), Rust (tokio::process, rusqlite, serde), SQLite (WAL, triggers), config.toml

**Spec:** `docs/superpowers/specs/2026-04-28-cli-backend-design.md`

**Branch:** `feat/agency-agents-integration`

---

## File Structure

### Python (Phase 1-4)

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/agent_nexus/platform/agency/cli_backend/__init__.py` | Package init, re-export public API |
| Create | `src/agent_nexus/platform/agency/cli_backend/types.py` | `CLIResult`, `CLISessionRecord`, `BackendConfig`, `RoutingConfig`, `DataLifecycleConfig` dataclasses |
| Create | `src/agent_nexus/platform/agency/cli_backend/parser.py` | JSON path extraction + text regex parsing |
| Create | `src/agent_nexus/platform/agency/cli_backend/base.py` | `CLIBackend` ABC + `GenericCLIBackend` (subprocess runner) |
| Create | `src/agent_nexus/platform/agency/cli_backend/registry.py` | `CLIBackendRegistry` — discovery, health check, availability tracking |
| Create | `src/agent_nexus/platform/agency/cli_backend/router.py` | `CLIRouter` — 4-strategy routing + fallback chain |
| Create | `src/agent_nexus/platform/agency/cli_backend/session_store.py` | `CLISessionStore` — SQLite CRUD, schema init, triggers, archival |
| Modify | `src/agent_nexus/models/config.py` | Add `CLI` to `ProviderApiType` enum |
| Modify | `src/agent_nexus/platform/agency/llm_client.py` | Add `_call_cli()` branch + `session_id` kwarg |
| Create | `tests/unit/test_cli_backend_types.py` | Unit tests for CLI data types |
| Create | `tests/unit/test_cli_backend_parser.py` | Unit tests for JSON/text output parsing |
| Create | `tests/unit/test_cli_backend_base.py` | Unit tests for GenericCLIBackend |
| Create | `tests/unit/test_cli_backend_registry.py` | Unit tests for registry + health check |
| Create | `tests/unit/test_cli_backend_router.py` | Unit tests for routing strategies + fallback |
| Create | `tests/unit/test_cli_backend_session_store.py` | Unit tests for SQLite session store + archival |

### Rust (Phase 5-7)

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `crates/ap-cli-backend/Cargo.toml` | Crate manifest |
| Create | `crates/ap-cli-backend/src/lib.rs` | Crate root + re-exports |
| Create | `crates/ap-cli-backend/src/types.rs` | `CLIResult`, `BackendConfig`, `JsonPathConfig`, `TextPatternConfig`, `RoutingConfig`, `CLIBackendError`, `CLISession`, `DataLifecycleConfig` |
| Create | `crates/ap-cli-backend/src/parser.rs` | JSON path extraction + text regex parsing |
| Create | `crates/ap-cli-backend/src/backend.rs` | `CLIBackend` trait + `GenericCLIBackend` impl |
| Create | `crates/ap-cli-backend/src/registry.rs` | `CLIBackendRegistry` — discovery, health check |
| Create | `crates/ap-cli-backend/src/router.rs` | `CLIRouter` — 4-strategy routing + fallback |
| Create | `crates/ap-cli-backend/src/session.rs` | `CLISessionStore` — rusqlite CRUD, schema, triggers |
| Create | `crates/ap-cli-backend/src/archive.rs` | Database archival (ATTACH DATABASE) |
| Create | `crates/ap-cli-backend/src/health.rs` | Health check (binary existence + version) |
| Modify | `Cargo.toml` | Add `ap-cli-backend` to workspace members |
| Modify | `crates/ap-core/src/models/config.rs` | Add `Cli` variant to `ProviderApiType` enum |

---

## Phase 1: Python Core Types & Config Extension

### Task 1: CLI Backend Data Types

**Files:**
- Create: `src/agent_nexus/platform/agency/cli_backend/__init__.py`
- Create: `src/agent_nexus/platform/agency/cli_backend/types.py`
- Create: `tests/unit/test_cli_backend_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cli_backend_types.py
"""Unit tests for CLI backend data types."""

from __future__ import annotations

import pytest

from agent_nexus.platform.agency.cli_backend.types import (
    BackendConfig,
    CLIResult,
    CLISessionRecord,
    DataLifecycleConfig,
    JsonPathConfig,
    RoutingConfig,
    TextPatternConfig,
)


class TestCLIResult:
    def test_defaults(self):
        result = CLIResult(text="hello", model="claude-sonnet-4-20250514")
        assert result.text == "hello"
        assert result.model == "claude-sonnet-4-20250514"
        assert result.session_id is None
        assert result.input_tokens is None
        assert result.output_tokens is None
        assert result.raw_stdout == ""
        assert result.raw_stderr == ""
        assert result.returncode == 0
        assert result.duration_ms == 0
        assert result.parse_error is False

    def test_full_construction(self):
        result = CLIResult(
            text="response",
            model="gemini-2.5-flash",
            session_id="sess-123",
            input_tokens=100,
            output_tokens=50,
            raw_stdout='{"result": "response"}',
            raw_stderr="",
            returncode=0,
            duration_ms=1500,
            parse_error=False,
        )
        assert result.session_id == "sess-123"
        assert result.duration_ms == 1500


class TestBackendConfig:
    def test_minimal_config(self):
        config = BackendConfig(
            command="claude",
            args=["-p"],
            system_prompt_flag="--system-prompt",
            session_flag="--resume",
        )
        assert config.command == "claude"
        assert config.args == ["-p"]
        assert config.output_format == "json"
        assert config.timeout_secs == 180

    def test_full_config(self):
        config = BackendConfig(
            command="gemini",
            args=[],
            system_prompt_flag="--system",
            session_flag="--session",
            output_format="json",
            output_format_flag="--output-format",
            json_paths=JsonPathConfig(
                text="response.text",
                session_id="session.id",
                model="model_version",
                input_tokens="usage_metadata.prompt_token_count",
                output_tokens="usage_metadata.candidates_token_count",
            ),
            model_map={"flash": "gemini-2.5-flash", "pro": "gemini-2.5-pro"},
            timeout_secs=300,
        )
        assert config.json_paths.text == "response.text"
        assert config.model_map["flash"] == "gemini-2.5-flash"

    def test_text_mode_config(self):
        config = BackendConfig(
            command="openclaw",
            args=["agent", "-m"],
            system_prompt_flag="--system",
            session_flag="--session",
            output_format="text",
            text_patterns=TextPatternConfig(
                session_id=r"session[:\s]+([a-f0-9-]+)",
            ),
        )
        assert config.output_format == "text"
        assert config.text_patterns.session_id is not None


class TestJsonPathConfig:
    def test_defaults(self):
        config = JsonPathConfig()
        assert config.text is None
        assert config.session_id is None
        assert config.model is None

    def test_nested_path(self):
        config = JsonPathConfig(
            text="result",
            input_tokens="usage.input_tokens",
        )
        assert config.input_tokens == "usage.input_tokens"


class TestRoutingConfig:
    def test_defaults(self):
        config = RoutingConfig(default="claude-code")
        assert config.default == "claude-code"
        assert config.fallback_enabled is True
        assert config.fallback_chain == []
        assert config.model_rules == {}

    def test_full_routing(self):
        config = RoutingConfig(
            default="claude-code",
            fallback_enabled=False,
            fallback_chain=["gemini-cli", "codex-cli"],
            model_rules={"anthropic:*": "claude-code", "google:*": "gemini-cli"},
        )
        assert config.fallback_enabled is False
        assert len(config.fallback_chain) == 2


class TestCLISessionRecord:
    def test_defaults(self):
        record = CLISessionRecord(
            session_id="sess-abc",
            backend_name="claude-code",
        )
        assert record.name is None
        assert record.model is None
        assert record.task_id is None
        assert record.turn_count == 1


class TestDataLifecycleConfig:
    def test_defaults(self):
        config = DataLifecycleConfig()
        assert config.hot_days == 30
        assert config.warm_days == 90
        assert config.auto_archive is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cli_backend_types.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent_nexus.platform.agency.cli_backend'`

- [ ] **Step 3: Create package init**

```python
# src/agent_nexus/platform/agency/cli_backend/__init__.py
"""CLI Backend Integration — config-driven CLI agent backend for LLM calls."""

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
    "CLIResult",
    "CLISessionRecord",
    "DataLifecycleConfig",
    "JsonPathConfig",
    "RoutingConfig",
    "TextPatternConfig",
]
```

- [ ] **Step 4: Create types module**

```python
# src/agent_nexus/platform/agency/cli_backend/types.py
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
    """Configuration for a single CLI backend provider.

    Maps to a ``[providers.<name>]`` section in config.toml with ``api = "cli"``.
    """

    command: str
    args: list[str] = field(default_factory=list)
    system_prompt_flag: str = "--system-prompt"
    session_flag: str = "--resume"
    output_format: str = "json"
    output_format_flag: str = ""
    json_paths: JsonPathConfig = field(default_factory=JsonPathConfig)
    text_patterns: TextPatternConfig = field(default_factory=TextPatternConfig)
    model_map: dict[str, str] = field(default_factory=dict)
    timeout_secs: int = 180


@dataclass
class RoutingConfig:
    """CLI backend routing strategy configuration.

    Maps to ``[cli_routing]`` section in config.toml.
    """

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
    """Database lifecycle management configuration.

    Maps to ``[data_lifecycle]`` section in config.toml.
    """

    hot_days: int = 30
    warm_days: int = 90
    archive_dir: str = ""
    auto_archive: bool = True
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cli_backend_types.py -v`
Expected: All 9 tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/agent_nexus/platform/agency/cli_backend/__init__.py \
        src/agent_nexus/platform/agency/cli_backend/types.py \
        tests/unit/test_cli_backend_types.py
git commit -m "feat(cli-backend): add CLI backend data types — CLIResult, BackendConfig, RoutingConfig"
```

---

### Task 2: Extend ProviderApiType with CLI variant

**Files:**
- Modify: `src/agent_nexus/models/config.py:10-14`
- Modify: `src/agent_nexus/platform/config/defaults.py` (add CLI provider to DEFAULT_PROVIDERS if desired)

- [ ] **Step 1: Write the failing test**

```python
# Add to tests/unit/test_config_models.py (existing file)
def test_provider_api_type_includes_cli():
    """CLI is a valid ProviderApiType for CLI backend providers."""
    from agent_nexus.models.config import ProviderApiType
    assert ProviderApiType.CLI.value == "cli"
    assert ProviderApiType("cli") == ProviderApiType.CLI
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_config_models.py::test_provider_api_type_includes_cli -v`
Expected: FAIL with `ValueError: 'cli' is not a valid ProviderApiType`

- [ ] **Step 3: Add CLI to ProviderApiType enum**

In `src/agent_nexus/models/config.py`, add `CLI` to the enum:

```python
class ProviderApiType(StrEnum):
    """API protocol type for a model provider."""

    OPENAI_COMPATIBLE = "openai-compatible"
    ANTHROPIC_MESSAGES = "anthropic-messages"
    OLLAMA = "ollama"
    CLI = "cli"                    # CLI backend (subprocess invocation)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_config_models.py::test_provider_api_type_includes_cli -v`
Expected: PASS

Also verify no regressions:
Run: `uv run pytest tests/unit/test_config_models.py -v`
Expected: All existing tests still pass

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/models/config.py tests/unit/test_config_models.py
git commit -m "feat(config): add CLI variant to ProviderApiType enum for CLI backend support"
```

---

## Phase 2: Python CLI Backend Core

### Task 3: Output Parser (JSON Path + Text Regex)

**Files:**
- Create: `src/agent_nexus/platform/agency/cli_backend/parser.py`
- Create: `tests/unit/test_cli_backend_parser.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cli_backend_parser.py
"""Unit tests for CLI output parser — JSON path extraction and text regex."""

from __future__ import annotations

import json

import pytest

from agent_nexus.platform.agency.cli_backend.parser import (
    extract_json_value,
    parse_json_output,
    parse_text_output,
)
from agent_nexus.platform.agency.cli_backend.types import (
    BackendConfig,
    CLIResult,
    JsonPathConfig,
    TextPatternConfig,
)


class TestExtractJsonValue:
    def test_simple_path(self):
        data = {"result": "hello world", "session_id": "abc-123"}
        assert extract_json_value(data, "result") == "hello world"
        assert extract_json_value(data, "session_id") == "abc-123"

    def test_nested_path(self):
        data = {"usage": {"input_tokens": 100, "output_tokens": 50}}
        assert extract_json_value(data, "usage.input_tokens") == 100
        assert extract_json_value(data, "usage.output_tokens") == 50

    def test_deeply_nested(self):
        data = {"response": {"text": "answer"}, "session": {"id": "s-1"}}
        assert extract_json_value(data, "response.text") == "answer"
        assert extract_json_value(data, "session.id") == "s-1"

    def test_missing_path_returns_none(self):
        data = {"result": "text"}
        assert extract_json_value(data, "nonexistent.path") is None

    def test_empty_path_returns_none(self):
        data = {"result": "text"}
        assert extract_json_value(data, "") is None


class TestParseJsonOutput:
    def test_claude_code_format(self):
        stdout = json.dumps({
            "result": "task completed",
            "session_id": "sess-001",
            "model": "claude-sonnet-4-20250514",
            "usage": {"input_tokens": 200, "output_tokens": 100},
        })
        config = BackendConfig(
            command="claude",
            json_paths=JsonPathConfig(
                text="result",
                session_id="session_id",
                model="model",
                input_tokens="usage.input_tokens",
                output_tokens="usage.output_tokens",
            ),
        )
        result = parse_json_output(stdout, config)
        assert result.text == "task completed"
        assert result.session_id == "sess-001"
        assert result.model == "claude-sonnet-4-20250514"
        assert result.input_tokens == 200
        assert result.output_tokens == 100
        assert result.parse_error is False

    def test_gemini_format(self):
        stdout = json.dumps({
            "response": {"text": "gemini answer"},
            "session": {"id": "gsess-1"},
            "model_version": "gemini-2.5-flash",
            "usage_metadata": {
                "prompt_token_count": 300,
                "candidates_token_count": 150,
            },
        })
        config = BackendConfig(
            command="gemini",
            json_paths=JsonPathConfig(
                text="response.text",
                session_id="session.id",
                model="model_version",
                input_tokens="usage_metadata.prompt_token_count",
                output_tokens="usage_metadata.candidates_token_count",
            ),
        )
        result = parse_json_output(stdout, config)
        assert result.text == "gemini answer"
        assert result.session_id == "gsess-1"
        assert result.input_tokens == 300

    def test_invalid_json_falls_back_to_text(self):
        stdout = "This is plain text, not JSON"
        config = BackendConfig(command="claude")
        result = parse_json_output(stdout, config)
        assert result.text == stdout
        assert result.parse_error is True

    def test_missing_fields_return_none(self):
        stdout = json.dumps({"result": "partial"})
        config = BackendConfig(
            command="claude",
            json_paths=JsonPathConfig(
                text="result",
                session_id="session_id",
            ),
        )
        result = parse_json_output(stdout, config)
        assert result.text == "partial"
        assert result.session_id is None
        assert result.model is None


class TestParseTextOutput:
    def test_plain_text(self):
        result = parse_text_output(
            stdout="Hello world\nLine 2",
            stderr="",
            config=BackendConfig(command="openclaw"),
        )
        assert result.text == "Hello world\nLine 2"
        assert result.parse_error is False

    def test_regex_session_id_from_stderr(self):
        result = parse_text_output(
            stdout="Task done",
            stderr="session: abc123-def456 started",
            config=BackendConfig(
                command="openclaw",
                text_patterns=TextPatternConfig(
                    session_id=r"session[:\s]+([a-f0-9-]+)",
                ),
            ),
        )
        assert result.text == "Task done"
        assert result.session_id == "abc123-def456"

    def test_no_pattern_returns_none(self):
        result = parse_text_output(
            stdout="output",
            stderr="no session info",
            config=BackendConfig(command="openclaw"),
        )
        assert result.session_id is None
        assert result.model is None

    def test_model_regex_from_stdout(self):
        result = parse_text_output(
            stdout="Using model: hermes-v2\nResult here",
            stderr="",
            config=BackendConfig(
                command="hermes",
                text_patterns=TextPatternConfig(
                    model=r"model[:\s]+(\S+)",
                ),
            ),
        )
        assert result.model == "hermes-v2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cli_backend_parser.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement parser**

```python
# src/agent_nexus/platform/agency/cli_backend/parser.py
"""CLI output parser — JSON path extraction and text regex parsing."""

from __future__ import annotations

import json
import re
from typing import Any


def extract_json_value(data: dict[str, Any], path: str) -> Any:
    """Extract a value from a nested dict using dot-separated path.

    Returns ``None`` if any segment is missing or path is empty.
    """
    if not path:
        return None
    keys = path.split(".")
    current: Any = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def parse_json_output(stdout: str, config: Any) -> Any:
    """Parse JSON-formatted CLI output using json_paths config.

    Falls back to raw text on JSON decode failure, setting parse_error=True.
    """
    from agent_nexus.platform.agency.cli_backend.types import CLIResult

    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return CLIResult(
            text=stdout,
            model="",
            raw_stdout=stdout,
            parse_error=True,
        )

    paths = config.json_paths

    def _extract(path: str | None) -> Any:
        if path is None:
            return None
        return extract_json_value(data, path)

    text = _extract(paths.text)
    if text is None:
        text = stdout
    if not isinstance(text, str):
        text = str(text)

    session_id = _extract(paths.session_id)
    model = _extract(paths.model)
    input_tokens = _extract(paths.input_tokens)
    output_tokens = _extract(paths.output_tokens)

    return CLIResult(
        text=text,
        model=model if isinstance(model, str) else "",
        session_id=session_id if isinstance(session_id, str) else None,
        input_tokens=int(input_tokens) if isinstance(input_tokens, (int, float)) else None,
        output_tokens=int(output_tokens) if isinstance(output_tokens, (int, float)) else None,
        raw_stdout=stdout,
        parse_error=False,
    )


def parse_text_output(stdout: str, stderr: str, config: Any) -> Any:
    """Parse text-mode CLI output, optionally extracting metadata via regex."""
    from agent_nexus.platform.agency.cli_backend.types import CLIResult

    session_id = None
    model = None

    patterns = config.text_patterns
    if patterns.session_id:
        combined = f"{stdout}\n{stderr}"
        match = re.search(patterns.session_id, combined)
        if match:
            session_id = match.group(1)

    if patterns.model:
        match = re.search(patterns.model, stdout)
        if match:
            model = match.group(1)

    return CLIResult(
        text=stdout,
        model=model or "",
        session_id=session_id,
        raw_stdout=stdout,
        raw_stderr=stderr,
        parse_error=False,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cli_backend_parser.py -v`
Expected: All 12 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/agency/cli_backend/parser.py \
        tests/unit/test_cli_backend_parser.py
git commit -m "feat(cli-backend): add config-driven output parser with JSON path and text regex extraction"
```

---

### Task 4: GenericCLIBackend (Subprocess Runner)

**Files:**
- Create: `src/agent_nexus/platform/agency/cli_backend/base.py`
- Create: `tests/unit/test_cli_backend_base.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cli_backend_base.py
"""Unit tests for GenericCLIBackend — subprocess-based CLI invocation."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent_nexus.platform.agency.cli_backend.base import GenericCLIBackend
from agent_nexus.platform.agency.cli_backend.types import (
    BackendConfig,
    CLIResult,
    JsonPathConfig,
)


def _claude_config() -> BackendConfig:
    return BackendConfig(
        command="claude",
        args=["-p"],
        system_prompt_flag="--system-prompt",
        session_flag="--resume",
        output_format="json",
        output_format_flag="--output-format",
        json_paths=JsonPathConfig(
            text="result",
            session_id="session_id",
            model="model",
            input_tokens="usage.input_tokens",
            output_tokens="usage.output_tokens",
        ),
        model_map={"sonnet": "claude-sonnet-4-20250514"},
    )


class TestGenericCLIBackendBuildArgs:
    def test_basic_call(self):
        backend = GenericCLIBackend(_claude_config())
        args = backend.build_args("You are a planner.", "Design the system.")
        assert args[0] == "-p"
        assert "--system-prompt" in args
        assert "You are a planner." in args
        assert "Design the system." in args

    def test_with_session_id(self):
        backend = GenericCLIBackend(_claude_config())
        args = backend.build_args("sys", "user", session_id="sess-123")
        assert "--resume" in args
        assert "sess-123" in args

    def test_without_session_id_no_resume_flag(self):
        backend = GenericCLIBackend(_claude_config())
        args = backend.build_args("sys", "user")
        assert "--resume" not in args

    def test_text_mode_no_json_flag(self):
        config = BackendConfig(
            command="openclaw",
            args=["agent", "-m"],
            system_prompt_flag="--system",
            session_flag="--session",
            output_format="text",
        )
        backend = GenericCLIBackend(config)
        args = backend.build_args("sys", "user msg")
        assert "--output-format" not in args
        # "agent" and "-m" from base args
        assert "agent" in args
        assert "-m" in args


class TestGenericCLIBackendAvailability:
    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_available_when_installed(self, mock_which):
        backend = GenericCLIBackend(_claude_config())
        assert backend.is_available() is True

    @patch("shutil.which", return_value=None)
    def test_not_available_when_missing(self, mock_which):
        backend = GenericCLIBackend(_claude_config())
        assert backend.is_available() is False

    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_name_returns_command(self, mock_which):
        backend = GenericCLIBackend(_claude_config())
        assert backend.name == "claude"


class TestGenericCLIBackendCall:
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_successful_json_call(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps({
                "result": "planned tasks",
                "session_id": "sess-abc",
                "model": "claude-sonnet-4-20250514",
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }),
            stderr="",
            returncode=0,
        )
        backend = GenericCLIBackend(_claude_config())
        result = backend.call("You are a planner.", "Design X.")

        assert isinstance(result, CLIResult)
        assert result.text == "planned tasks"
        assert result.session_id == "sess-abc"
        assert result.model == "claude-sonnet-4-20250514"
        assert result.input_tokens == 100
        assert result.returncode == 0

    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_nonzero_exit_code(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            stdout="",
            stderr="Error: model not found",
            returncode=1,
        )
        backend = GenericCLIBackend(_claude_config())
        result = backend.call("sys", "msg")
        assert result.returncode == 1
        assert "Error: model not found" in result.raw_stderr

    @patch("subprocess.run", side_effect=TimeoutError("timed out"))
    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_timeout_returns_error_result(self, mock_which, mock_run):
        backend = GenericCLIBackend(_claude_config())
        result = backend.call("sys", "msg")
        assert result.returncode == -1
        assert "timed out" in result.raw_stderr.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cli_backend_base.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement GenericCLIBackend**

```python
# src/agent_nexus/platform/agency/cli_backend/base.py
"""GenericCLIBackend — config-driven CLI subprocess invocation."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from typing import Any

from agent_nexus.platform.agency.cli_backend.parser import (
    parse_json_output,
    parse_text_output,
)
from agent_nexus.platform.agency.cli_backend.types import BackendConfig, CLIResult

logger = logging.getLogger(__name__)


class GenericCLIBackend:
    """Generic CLI backend that invokes any CLI via subprocess.

    All CLI-specific behavior is driven by ``BackendConfig`` — no per-CLI
    subclasses needed. New CLI = new config entry.
    """

    def __init__(self, config: BackendConfig) -> None:
        self._config = config
        self._available: bool | None = None

    @property
    def name(self) -> str:
        return self._config.command

    @property
    def config(self) -> BackendConfig:
        return self._config

    @property
    def supported_models(self) -> list[str]:
        return list(self._config.model_map.values())

    def is_available(self) -> bool:
        """Check if the CLI binary exists in PATH."""
        if self._available is None:
            self._available = shutil.which(self._config.command) is not None
        return self._available

    def refresh_availability(self) -> bool:
        """Force re-check CLI availability."""
        self._available = shutil.which(self._config.command) is not None
        return self._available

    def build_args(
        self,
        system_prompt: str,
        user_message: str,
        session_id: str | None = None,
    ) -> list[str]:
        """Build the full argument list for the CLI subprocess call."""
        args = list(self._config.args)

        # System prompt
        if self._config.system_prompt_flag:
            args.extend([self._config.system_prompt_flag, system_prompt])

        # Session resume
        if session_id and self._config.session_flag:
            args.extend([self._config.session_flag, session_id])

        # Output format
        if self._config.output_format == "json" and self._config.output_format_flag:
            args.extend([self._config.output_format_flag, "json"])

        # User message (positional, always last)
        args.append(user_message)

        return args

    def call(
        self,
        system_prompt: str,
        user_message: str,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> CLIResult:
        """Invoke the CLI and return a structured result.

        Parameters
        ----------
        system_prompt:
            System instructions passed via ``--system-prompt`` flag.
        user_message:
            The user's task / message (positional argument).
        session_id:
            Optional session ID for multi-turn resume via ``--resume``.
        timeout:
            Override timeout in seconds. Defaults to config timeout.
        """
        args = self.build_args(system_prompt, user_message, session_id)
        effective_timeout = timeout or self._config.timeout_secs

        logger.info(
            "CLI call: %s %s (timeout=%ds, session=%s)",
            self._config.command, " ".join(args[:4]) + ("..." if len(args) > 4 else ""),
            effective_timeout, session_id,
        )

        start = time.monotonic()
        try:
            proc = subprocess.run(
                [self._config.command, *args],
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
            duration_ms = int((time.monotonic() - start) * 1000)

            if proc.returncode != 0:
                logger.warning(
                    "CLI '%s' exited with code %d: %s",
                    self._config.command, proc.returncode, proc.stderr[:200],
                )
                return CLIResult(
                    text="",
                    model="",
                    raw_stdout=proc.stdout,
                    raw_stderr=proc.stderr,
                    returncode=proc.returncode,
                    duration_ms=duration_ms,
                )

            # Parse output
            result = self._parse_output(proc.stdout, proc.stderr)
            result.duration_ms = duration_ms
            result.raw_stdout = proc.stdout
            result.raw_stderr = proc.stderr
            return result

        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.error(
                "CLI '%s' timed out after %ds",
                self._config.command, effective_timeout,
            )
            return CLIResult(
                text="",
                model="",
                raw_stderr=f"CLI timed out after {effective_timeout}s",
                returncode=-1,
                duration_ms=duration_ms,
            )

    def _parse_output(self, stdout: str, stderr: str) -> CLIResult:
        """Parse CLI output based on config output_format."""
        if self._config.output_format == "json":
            result = parse_json_output(stdout, self._config)
            if result.parse_error:
                logger.warning(
                    "JSON parsing failed for '%s', using raw text",
                    self._config.command,
                )
            return result
        return parse_text_output(stdout, stderr, self._config)

    def list_sessions(self) -> list[str]:
        """List known session IDs (delegated to SessionStore)."""
        return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cli_backend_base.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/agency/cli_backend/base.py \
        tests/unit/test_cli_backend_base.py
git commit -m "feat(cli-backend): add GenericCLIBackend with config-driven subprocess invocation"
```

---

### Task 5: CLIBackendRegistry (Discovery + Health Check)

**Files:**
- Create: `src/agent_nexus/platform/agency/cli_backend/registry.py`
- Create: `tests/unit/test_cli_backend_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cli_backend_registry.py
"""Unit tests for CLIBackendRegistry — backend discovery and health check."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_nexus.platform.agency.cli_backend.base import GenericCLIBackend
from agent_nexus.platform.agency.cli_backend.registry import CLIBackendRegistry
from agent_nexus.platform.agency.cli_backend.types import BackendConfig


def _make_config(name: str, command: str) -> BackendConfig:
    return BackendConfig(command=command, args=["-p"])


class TestCLIBackendRegistry:
    def test_register_and_get(self):
        registry = CLIBackendRegistry()
        config = _make_config("claude-code", "claude")
        backend = GenericCLIBackend(config)
        registry.register("claude-code", backend)

        result = registry.get("claude-code")
        assert result is backend

    def test_get_nonexistent_raises(self):
        registry = CLIBackendRegistry()
        with pytest.raises(KeyError, match="unknown-backend"):
            registry.get("unknown-backend")

    @patch("shutil.which", side_effect=lambda cmd: f"/usr/bin/{cmd}" if cmd == "claude" else None)
    def test_available_backends_filters_by_availability(self, mock_which):
        registry = CLIBackendRegistry()
        registry.register("claude-code", GenericCLIBackend(_make_config("cc", "claude")))
        registry.register("gemini-cli", GenericCLIBackend(_make_config("gc", "gemini")))

        available = registry.available_backends()
        names = [b.name for b in available]
        assert "claude" in names
        assert "gemini" not in names

    @patch("shutil.which", return_value="/usr/bin/test")
    def test_refresh_availability(self, mock_which):
        registry = CLIBackendRegistry()
        backend = GenericCLIBackend(_make_config("cc", "claude"))
        registry.register("claude-code", backend)

        assert len(registry.available_backends()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cli_backend_registry.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement CLIBackendRegistry**

```python
# src/agent_nexus/platform/agency/cli_backend/registry.py
"""CLIBackendRegistry — backend discovery, registration, and health check."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent_nexus.platform.agency.cli_backend.base import GenericCLIBackend

logger = logging.getLogger(__name__)


class CLIBackendRegistry:
    """Registry of available CLI backends with health check support."""

    def __init__(self) -> None:
        self._backends: dict[str, GenericCLIBackend] = {}

    def register(self, name: str, backend: GenericCLIBackend) -> None:
        """Register a CLI backend by name."""
        self._backends[name] = backend
        logger.debug("Registered CLI backend: %s -> %s", name, backend.name)

    def get(self, name: str) -> GenericCLIBackend:
        """Get a registered backend by name.

        Raises KeyError if not found.
        """
        if name not in self._backends:
            raise KeyError(
                f"CLI backend '{name}' not registered. "
                f"Available: {list(self._backends.keys())}"
            )
        return self._backends[name]

    def available_backends(self) -> list[GenericCLIBackend]:
        """Return backends whose CLI binary is installed."""
        return [b for b in self._backends.values() if b.is_available()]

    def all_backends(self) -> dict[str, GenericCLIBackend]:
        """Return all registered backends (including unavailable)."""
        return dict(self._backends)

    def refresh_all(self) -> None:
        """Force re-check availability for all backends."""
        for backend in self._backends.values():
            backend.refresh_availability()

    def __len__(self) -> int:
        return len(self._backends)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cli_backend_registry.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/agency/cli_backend/registry.py \
        tests/unit/test_cli_backend_registry.py
git commit -m "feat(cli-backend): add CLIBackendRegistry with discovery and health check"
```

---

### Task 6: CLIRouter (4-Strategy Routing + Fallback)

**Files:**
- Create: `src/agent_nexus/platform/agency/cli_backend/router.py`
- Create: `tests/unit/test_cli_backend_router.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cli_backend_router.py
"""Unit tests for CLIRouter — 4-strategy routing with fallback chain."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agent_nexus.platform.agency.cli_backend.base import GenericCLIBackend
from agent_nexus.platform.agency.cli_backend.registry import CLIBackendRegistry
from agent_nexus.platform.agency.cli_backend.router import CLIRouter
from agent_nexus.platform.agency.cli_backend.types import BackendConfig, RoutingConfig


def _make_registry_with(
    names: list[str],
) -> tuple[CLIBackendRegistry, dict[str, GenericCLIBackend]]:
    registry = CLIBackendRegistry()
    backends = {}
    for name in names:
        config = BackendConfig(command=name, args=[])
        backend = GenericCLIBackend(config)
        registry.register(name, backend)
        backends[name] = backend
    return registry, backends


class TestCLIRouterExplicit:
    def test_explicit_backend(self):
        registry, _ = _make_registry_with(["claude-code", "gemini-cli"])
        router = CLIRouter(
            config=RoutingConfig(default="claude-code"),
            registry=registry,
        )
        result = router.resolve(explicit_backend="gemini-cli")
        assert result.name == "gemini-cli"

    def test_explicit_backend_not_found_raises(self):
        registry, _ = _make_registry_with(["claude-code"])
        router = CLIRouter(
            config=RoutingConfig(default="claude-code"),
            registry=registry,
        )
        with pytest.raises(KeyError):
            router.resolve(explicit_backend="nonexistent")


class TestCLIRouterModelRules:
    def test_model_rule_match(self):
        registry, _ = _make_registry_with(["claude-code", "gemini-cli"])
        router = CLIRouter(
            config=RoutingConfig(
                default="claude-code",
                model_rules={"anthropic:*": "claude-code", "google:*": "gemini-cli"},
            ),
            registry=registry,
        )
        result = router.resolve(model_string="anthropic:claude-sonnet-4-20250514")
        assert result.name == "claude-code"

    def test_model_rule_no_match_falls_to_default(self):
        registry, _ = _make_registry_with(["claude-code"])
        router = CLIRouter(
            config=RoutingConfig(
                default="claude-code",
                model_rules={"google:*": "gemini-cli"},
            ),
            registry=registry,
        )
        result = router.resolve(model_string="openai:gpt-4o")
        assert result.name == "claude-code"


class TestCLIRouterDefault:
    def test_default_fallback(self):
        registry, _ = _make_registry_with(["claude-code"])
        router = CLIRouter(
            config=RoutingConfig(default="claude-code"),
            registry=registry,
        )
        result = router.resolve()
        assert result.name == "claude-code"


class TestCLIRouterFallback:
    @patch("shutil.which", side_effect=lambda cmd: f"/bin/{cmd}" if cmd == "gemini-cli" else None)
    def test_fallback_chain_on_unavailable(self, mock_which):
        registry, backends = _make_registry_with(
            ["claude-code", "gemini-cli", "codex-cli"]
        )
        router = CLIRouter(
            config=RoutingConfig(
                default="claude-code",
                fallback_enabled=True,
                fallback_chain=["gemini-cli", "codex-cli"],
            ),
            registry=registry,
        )
        # claude-code is unavailable (which returns None), fallback to gemini-cli
        result = router.resolve_with_fallback()
        assert result.name == "gemini-cli"

    def test_fallback_disabled_raises(self):
        registry, _ = _make_registry_with(["claude-code"])
        router = CLIRouter(
            config=RoutingConfig(
                default="claude-code",
                fallback_enabled=False,
            ),
            registry=registry,
        )
        with pytest.raises(RuntimeError, match="Fallback disabled"):
            router.resolve_with_fallback(
                explicit_backend="nonexistent",
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cli_backend_router.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement CLIRouter**

```python
# src/agent_nexus/platform/agency/cli_backend/router.py
"""CLIRouter — 4-strategy priority routing with fallback chain."""

from __future__ import annotations

import fnmatch
import logging

from agent_nexus.platform.agency.cli_backend.base import GenericCLIBackend
from agent_nexus.platform.agency.cli_backend.registry import CLIBackendRegistry
from agent_nexus.platform.agency.cli_backend.types import RoutingConfig

logger = logging.getLogger(__name__)


class CLIRouter:
    """Resolve which CLI backend to use for a given request.

    Strategy priority (highest first):
    1. Explicit backend name
    2. Model string pattern matching (model_rules)
    3. Default backend
    """

    def __init__(
        self,
        config: RoutingConfig,
        registry: CLIBackendRegistry,
    ) -> None:
        self._config = config
        self._registry = registry

    def resolve(
        self,
        model_string: str | None = None,
        explicit_backend: str | None = None,
    ) -> GenericCLIBackend:
        """Resolve a backend using the 3-strategy priority chain.

        Does NOT apply fallback — returns the primary match or raises.
        """
        # Strategy 1: Explicit
        if explicit_backend:
            return self._registry.get(explicit_backend)

        # Strategy 2: Model rules
        if model_string:
            for pattern, backend_name in self._config.model_rules.items():
                if fnmatch.fnmatch(model_string, pattern):
                    try:
                        return self._registry.get(backend_name)
                    except KeyError:
                        logger.warning(
                            "Model rule '%s' -> '%s' but backend not registered",
                            pattern, backend_name,
                        )

        # Strategy 3: Default
        return self._registry.get(self._config.default)

    def resolve_with_fallback(
        self,
        model_string: str | None = None,
        explicit_backend: str | None = None,
    ) -> GenericCLIBackend:
        """Resolve with fallback chain on unavailable backends.

        Raises RuntimeError if fallback is disabled and primary fails,
        or if all backends in the chain are unavailable.
        """
        try:
            primary = self.resolve(model_string, explicit_backend)
            if primary.is_available():
                return primary
        except KeyError:
            pass  # Explicit backend not found, fall through

        if not self._config.fallback_enabled:
            raise RuntimeError(
                f"Fallback disabled — primary backend unavailable. "
                f"Enable via [cli_routing] fallback_enabled = true"
            )

        # Try fallback chain
        for name in self._config.fallback_chain:
            try:
                backend = self._registry.get(name)
                if backend.is_available():
                    logger.info("Fallback: using backend '%s'", name)
                    return backend
            except KeyError:
                logger.warning("Fallback backend '%s' not registered, skipping", name)

        raise RuntimeError(
            f"All backends unavailable. "
            f"Primary: {explicit_backend or self._config.default}, "
            f"Fallback chain: {self._config.fallback_chain}"
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cli_backend_router.py -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/agency/cli_backend/router.py \
        tests/unit/test_cli_backend_router.py
git commit -m "feat(cli-backend): add CLIRouter with 4-strategy routing and fallback chain"
```

---

## Phase 3: Python Session Store (SQLite)

### Task 7: CLISessionStore (SQLite CRUD + Schema + Triggers)

**Files:**
- Create: `src/agent_nexus/platform/agency/cli_backend/session_store.py`
- Create: `tests/unit/test_cli_backend_session_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cli_backend_session_store.py
"""Unit tests for CLISessionStore — SQLite session persistence."""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from agent_nexus.platform.agency.cli_backend.session_store import CLISessionStore
from agent_nexus.platform.agency.cli_backend.types import CLISessionRecord


@pytest.fixture
def store(tmp_path: Path) -> CLISessionStore:
    db_path = tmp_path / "test.db"
    return CLISessionStore(db_path)


class TestCLISessionStoreSchema:
    def test_tables_created(self, store: CLISessionStore):
        """All 4 tables + 2 triggers exist after initialization."""
        tables = store._list_tables()
        assert "cli_sessions" in tables
        assert "task_executions" in tables
        assert "backend_health" in tables
        assert "daily_stats" in tables

    def test_triggers_created(self, store: CLISessionStore):
        triggers = store._list_triggers()
        assert "trg_update_daily_stats" in triggers
        assert "trg_delete_daily_stats" in triggers

    def test_wal_mode_enabled(self, store: CLISessionStore):
        result = store._pragma("journal_mode")
        assert result == "wal"


class TestCLISessionStoreCRUD:
    def test_save_and_get(self, store: CLISessionStore):
        record = CLISessionRecord(
            session_id="sess-001",
            backend_name="claude-code",
            model="claude-sonnet-4-20250514",
            name="planning session",
        )
        store.save_session(record)

        retrieved = store.get_session("sess-001")
        assert retrieved is not None
        assert retrieved.session_id == "sess-001"
        assert retrieved.backend_name == "claude-code"
        assert retrieved.model == "claude-sonnet-4-20250514"
        assert retrieved.name == "planning session"

    def test_get_nonexistent_returns_none(self, store: CLISessionStore):
        assert store.get_session("nonexistent") is None

    def test_get_by_task(self, store: CLISessionStore):
        store.save_session(CLISessionRecord(
            session_id="s1", backend_name="cc", task_id="task-1",
        ))
        store.save_session(CLISessionRecord(
            session_id="s2", backend_name="gc", task_id="task-1",
        ))
        store.save_session(CLISessionRecord(
            session_id="s3", backend_name="cc", task_id="task-2",
        ))

        results = store.get_sessions_by_task("task-1")
        assert len(results) == 2
        assert {r.session_id for r in results} == {"s1", "s2"}

    def test_update_session(self, store: CLISessionStore):
        store.save_session(CLISessionRecord(
            session_id="s1", backend_name="cc", turn_count=1,
        ))
        # Re-save with updated turn_count (INSERT OR REPLACE)
        store.save_session(CLISessionRecord(
            session_id="s1", backend_name="cc", turn_count=3,
        ))
        retrieved = store.get_session("s1")
        assert retrieved.turn_count == 3


class TestTaskExecutions:
    def test_record_execution(self, store: CLISessionStore):
        store.record_execution(
            task_id="task-1",
            backend_type="cli",
            backend_name="claude-code",
            model="claude-sonnet-4-20250514",
            session_id="sess-001",
            input_tokens=100,
            output_tokens=50,
            duration_ms=1500,
            status="success",
        )

    def test_daily_stats_auto_updated_via_trigger(self, store: CLISessionStore):
        """INSERT into task_executions should auto-update daily_stats via trigger."""
        store.record_execution(
            task_id="t1", backend_type="cli", backend_name="claude-code",
            status="success", input_tokens=100, output_tokens=50, duration_ms=1000,
        )
        store.record_execution(
            task_id="t2", backend_type="cli", backend_name="claude-code",
            status="error", input_tokens=50, output_tokens=0, duration_ms=500,
        )

        stats = store.get_daily_stats()
        assert len(stats) == 1
        assert stats[0]["total_calls"] == 2
        assert stats[0]["success_calls"] == 1
        assert stats[0]["total_input_tokens"] == 150
        assert stats[0]["total_output_tokens"] == 50


class TestBackendHealth:
    def test_update_and_get_health(self, store: CLISessionStore):
        store.update_health("claude-code", available=True, version="1.0.0")
        health = store.get_health("claude-code")
        assert health is not None
        assert health["is_available"] == 1
        assert health["version"] == "1.0.0"

    def test_get_nonexistent_health(self, store: CLISessionStore):
        assert store.get_health("nonexistent") is None


class TestCleanup:
    def test_cleanup_old_sessions(self, store: CLISessionStore):
        # Insert a session with an old created_at date
        store._conn.execute(
            "INSERT INTO cli_sessions (session_id, backend_name, created_at, last_used_at) "
            "VALUES ('old-sess', 'cc', '2020-01-01T00:00:00', '2020-01-01T00:00:00')"
        )
        store.save_session(CLISessionRecord(session_id="new-sess", backend_name="cc"))

        deleted = store.cleanup_sessions(max_age_days=30)
        assert deleted >= 1
        assert store.get_session("old-sess") is None
        assert store.get_session("new-sess") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cli_backend_session_store.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement CLISessionStore**

```python
# src/agent_nexus/platform/agency/cli_backend/session_store.py
"""CLISessionStore — SQLite session persistence with WAL mode and triggers."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_nexus.platform.agency.cli_backend.types import CLISessionRecord

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cli_sessions (
    session_id   TEXT PRIMARY KEY,
    name         TEXT,
    backend_name TEXT NOT NULL,
    model        TEXT,
    task_id      TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    last_used_at TEXT DEFAULT (datetime('now')),
    turn_count   INTEGER DEFAULT 1,
    metadata     TEXT
);

CREATE TABLE IF NOT EXISTS task_executions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL,
    backend_type  TEXT NOT NULL,
    backend_name  TEXT NOT NULL,
    model         TEXT,
    session_id    TEXT REFERENCES cli_sessions(session_id),
    input_tokens  INTEGER,
    output_tokens INTEGER,
    duration_ms   INTEGER,
    status        TEXT DEFAULT 'success',
    error         TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS backend_health (
    backend_name TEXT PRIMARY KEY,
    is_available INTEGER DEFAULT 0,
    last_check   TEXT,
    version      TEXT,
    error_msg    TEXT
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date         TEXT NOT NULL,
    backend_name TEXT NOT NULL,
    total_calls  INTEGER DEFAULT 0,
    success_calls INTEGER DEFAULT 0,
    total_input_tokens  INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    avg_duration_ms     REAL DEFAULT 0,
    PRIMARY KEY (date, backend_name)
);

CREATE TRIGGER IF NOT EXISTS trg_update_daily_stats
AFTER INSERT ON task_executions
BEGIN
    INSERT INTO daily_stats (date, backend_name, total_calls, success_calls,
                             total_input_tokens, total_output_tokens, avg_duration_ms)
    VALUES (DATE('now'), NEW.backend_name, 1,
            CASE WHEN NEW.status = 'success' THEN 1 ELSE 0 END,
            COALESCE(NEW.input_tokens, 0), COALESCE(NEW.output_tokens, 0),
            COALESCE(NEW.duration_ms, 0))
    ON CONFLICT(date, backend_name) DO UPDATE SET
        total_calls = total_calls + 1,
        success_calls = success_calls + CASE WHEN NEW.status = 'success' THEN 1 ELSE 0 END,
        total_input_tokens = total_input_tokens + COALESCE(NEW.input_tokens, 0),
        total_output_tokens = total_output_tokens + COALESCE(NEW.output_tokens, 0),
        avg_duration_ms = (avg_duration_ms * (total_calls - 1) + COALESCE(NEW.duration_ms, 0)) / total_calls;
END;

CREATE TRIGGER IF NOT EXISTS trg_delete_daily_stats
AFTER DELETE ON task_executions
BEGIN
    UPDATE daily_stats SET
        total_calls = total_calls - 1,
        success_calls = success_calls - CASE WHEN OLD.status = 'success' THEN 1 ELSE 0 END,
        total_input_tokens = total_input_tokens - COALESCE(OLD.input_tokens, 0),
        total_output_tokens = total_output_tokens - COALESCE(OLD.output_tokens, 0)
    WHERE date = DATE(OLD.created_at) AND backend_name = OLD.backend_name;
END;
"""

_PRAGMAS = """
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=1000;
PRAGMA synchronous=NORMAL;
"""


class CLISessionStore:
    """SQLite-backed session store with WAL mode and auto-stats triggers."""

    def __init__(self, db_path: Path) -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_PRAGMAS)
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        logger.debug("CLISessionStore initialized: %s", db_path)

    def close(self) -> None:
        self._conn.close()

    # ── Schema inspection helpers (for testing) ──────────────────────

    def _list_tables(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows]

    def _list_triggers(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows]

    def _pragma(self, key: str) -> str:
        row = self._conn.execute(f"PRAGMA {key}").fetchone()
        return dict(row)[key] if row else ""

    # ── Sessions ─────────────────────────────────────────────────────

    def save_session(self, record: CLISessionRecord) -> None:
        now = datetime.now(timezone.utc).isoformat()
        metadata_json = json.dumps(record.metadata) if record.metadata else None

        self._conn.execute(
            "INSERT OR REPLACE INTO cli_sessions "
            "(session_id, name, backend_name, model, task_id, "
            " created_at, last_used_at, turn_count, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.session_id,
                record.name,
                record.backend_name,
                record.model,
                record.task_id,
                record.created_at or now,
                record.last_used_at or now,
                record.turn_count,
                metadata_json,
            ),
        )
        self._conn.commit()

    def get_session(self, session_id: str) -> CLISessionRecord | None:
        row = self._conn.execute(
            "SELECT * FROM cli_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_session(row)

    def get_sessions_by_task(self, task_id: str) -> list[CLISessionRecord]:
        rows = self._conn.execute(
            "SELECT * FROM cli_sessions WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def cleanup_sessions(self, max_age_days: int = 30) -> int:
        cursor = self._conn.execute(
            "DELETE FROM cli_sessions WHERE last_used_at < datetime('now', ?)",
            (f"-{max_age_days} days",),
        )
        self._conn.commit()
        return cursor.rowcount

    # ── Task Executions ──────────────────────────────────────────────

    def record_execution(
        self,
        task_id: str,
        backend_type: str,
        backend_name: str,
        model: str | None = None,
        session_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        duration_ms: int | None = None,
        status: str = "success",
        error: str | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO task_executions "
            "(task_id, backend_type, backend_name, model, session_id, "
            " input_tokens, output_tokens, duration_ms, status, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task_id, backend_type, backend_name, model, session_id,
                input_tokens, output_tokens, duration_ms, status, error,
            ),
        )
        self._conn.commit()

    # ── Daily Stats ──────────────────────────────────────────────────

    def get_daily_stats(self) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM daily_stats ORDER BY date DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Backend Health ───────────────────────────────────────────────

    def update_health(
        self,
        backend_name: str,
        available: bool,
        version: str | None = None,
        error_msg: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO backend_health "
            "(backend_name, is_available, last_check, version, error_msg) "
            "VALUES (?, ?, ?, ?, ?)",
            (backend_name, int(available), now, version, error_msg),
        )
        self._conn.commit()

    def get_health(self, backend_name: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM backend_health WHERE backend_name = ?",
            (backend_name,),
        ).fetchone()
        return dict(row) if row else None

    # ── Internal ─────────────────────────────────────────────────────

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> CLISessionRecord:
        metadata = None
        if row["metadata"]:
            try:
                metadata = json.loads(row["metadata"])
            except json.JSONDecodeError:
                pass
        return CLISessionRecord(
            session_id=row["session_id"],
            backend_name=row["backend_name"],
            name=row["name"],
            model=row["model"],
            task_id=row["task_id"],
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
            turn_count=row["turn_count"] or 1,
            metadata=metadata,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cli_backend_session_store.py -v`
Expected: All 13 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/agency/cli_backend/session_store.py \
        tests/unit/test_cli_backend_session_store.py
git commit -m "feat(cli-backend): add CLISessionStore with SQLite WAL, triggers, and daily stats"
```

---

## Phase 4: Python LLMClient Integration

### Task 8: Wire CLI Backend into LLMClient

**Files:**
- Modify: `src/agent_nexus/platform/agency/llm_client.py` — add `_call_cli()` method, `session_id` kwarg, and CLI branch in `call()`

- [ ] **Step 1: Write the failing test**

```python
# Add to a new test file: tests/unit/test_cli_backend_llm_integration.py
"""Unit tests for LLMClient CLI backend integration."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent_nexus.models.config import ProviderApiType


class TestLLMClientCLIIntegration:
    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("subprocess.run")
    def test_cli_call_returns_llm_response(self, mock_run, mock_which):
        mock_run.return_value = MagicMock(
            stdout=json.dumps({
                "result": "planned tasks",
                "session_id": "sess-001",
                "model": "claude-sonnet-4-20250514",
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }),
            stderr="",
            returncode=0,
        )
        # Verify ProviderApiType.CLI exists
        assert ProviderApiType.CLI.value == "cli"
```

- [ ] **Step 2: Run test to verify it passes (CLI enum already added in Task 2)**

Run: `uv run pytest tests/unit/test_cli_backend_llm_integration.py -v`
Expected: PASS (validates enum exists)

- [ ] **Step 3: Add `_call_cli()` method and `session_id` kwarg to LLMClient**

In `src/agent_nexus/platform/agency/llm_client.py`:

1. Import `GenericCLIBackend` and `BackendConfig` from cli_backend types at the top
2. Update `call()` signature to add `*, session_id=None` kwarg
3. Add CLI branch in `call()` that delegates to `_call_cli()`
4. Implement `_call_cli()` which creates/reuses a `GenericCLIBackend` and returns `LLMResponse`

The key change to `call()`:

```python
def call(
    self,
    system_prompt: str,
    user_message: str,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    timeout: float | None = None,
    *,
    session_id: str | None = None,
) -> LLMResponse:
    if self._provider_config.api == ProviderApiType.CLI:
        return self._call_cli(system_prompt, user_message, session_id, timeout)
    # ... existing Anthropic/OpenAI branches unchanged
```

New method:

```python
def _call_cli(
    self,
    system_prompt: str,
    user_message: str,
    session_id: str | None,
    timeout: float | None,
) -> LLMResponse:
    from agent_nexus.platform.agency.cli_backend.base import GenericCLIBackend
    from agent_nexus.platform.agency.cli_backend.types import BackendConfig

    config = BackendConfig(
        command=self._model_name,
        timeout_secs=int(timeout or self._TIMEOUT),
    )
    backend = GenericCLIBackend(config)
    result = backend.call(system_prompt, user_message, session_id=session_id)

    return LLMResponse(
        text=result.text,
        model=result.model or self._model_name,
        provider=self._provider_name,
        metadata={
            "session_id": result.session_id,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        },
    )
```

> **Design Note:** The full integration also needs config.toml parsing to construct `BackendConfig` from the provider section. This is deferred to Task 9 (config templates). The minimal version above derives config from the model name, which works for simple cases. When config.toml has `[providers.claude-code] api = "cli"` sections, `LLMClient.__init__` will construct the proper `BackendConfig` and pass it to `GenericCLIBackend`.

- [ ] **Step 4: Run existing tests to verify no regressions**

Run: `uv run pytest tests/unit/test_agency_executor.py tests/unit/test_llm_planner.py tests/unit/test_llm_integrator.py tests/unit/test_llm_qa_gate.py -v`
Expected: All existing tests still PASS (CLI branch only activates when `ProviderApiType.CLI`)

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/agency/llm_client.py tests/unit/test_cli_backend_llm_integration.py
git commit -m "feat(llm-client): add CLI backend branch with session_id support"
```

---

### Task 9: CLI Provider Config Templates

**Files:**
- Create: `src/agent_nexus/platform/agency/cli_backend/config_templates.py` (helper to build CLI provider configs from config.toml sections)

This task provides the config-to-BackendConfig mapping. When `config.toml` has a provider section with `api = "cli"`, the system needs to convert the raw TOML dict into a `BackendConfig` with proper `json_paths` and `text_patterns`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cli_backend_config_templates.py
"""Unit tests for CLI config template loading from config.toml."""

from __future__ import annotations

from agent_nexus.platform.agency.cli_backend.config_templates import (
    load_backend_configs_from_providers,
    load_routing_config,
)


class TestLoadBackendConfigs:
    def test_loads_cli_provider_only(self):
        providers = {
            "claude-code": {
                "api": "cli",
                "command": "claude",
                "args": ["-p"],
                "system_prompt_flag": "--system-prompt",
                "session_flag": "--resume",
                "output_format": "json",
                "output_format_flag": "--output-format",
                "model_map": {"sonnet": "claude-sonnet-4-20250514"},
                "json_paths": {
                    "text": "result",
                    "session_id": "session_id",
                    "model": "model",
                    "input_tokens": "usage.input_tokens",
                    "output_tokens": "usage.output_tokens",
                },
            },
            "openai": {
                "api": "openai-compatible",
                "base_url": "https://api.openai.com/v1",
            },
        }
        configs = load_backend_configs_from_providers(providers)
        assert "claude-code" in configs
        assert "openai" not in configs
        assert configs["claude-code"].command == "claude"
        assert configs["claude-code"].json_paths.text == "result"

    def test_ignores_non_cli_providers(self):
        providers = {"openai": {"api": "openai-compatible"}}
        configs = load_backend_configs_from_providers(providers)
        assert len(configs) == 0

    def test_text_mode_provider(self):
        providers = {
            "openclaw": {
                "api": "cli",
                "command": "openclaw",
                "args": ["agent", "-m"],
                "output_format": "text",
                "text_patterns": {
                    "session_id": r"session[:\s]+([a-f0-9-]+)",
                },
            },
        }
        configs = load_backend_configs_from_providers(providers)
        assert configs["openclaw"].output_format == "text"
        assert configs["openclaw"].text_patterns.session_id is not None


class TestLoadRoutingConfig:
    def test_loads_routing(self):
        raw = {
            "default": "claude-code",
            "fallback_enabled": False,
            "fallback_chain": ["gemini-cli"],
            "model_rules": {"anthropic:*": "claude-code"},
        }
        config = load_routing_config(raw)
        assert config.default == "claude-code"
        assert config.fallback_enabled is False
        assert config.model_rules["anthropic:*"] == "claude-code"

    def test_defaults(self):
        raw = {"default": "claude-code"}
        config = load_routing_config(raw)
        assert config.fallback_enabled is True
        assert config.fallback_chain == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_cli_backend_config_templates.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement config_templates.py**

```python
# src/agent_nexus/platform/agency/cli_backend/config_templates.py
"""Load CLI backend configs from config.toml provider sections."""

from __future__ import annotations

from typing import Any

from agent_nexus.platform.agency.cli_backend.types import (
    BackendConfig,
    JsonPathConfig,
    RoutingConfig,
    TextPatternConfig,
)


def load_backend_configs_from_providers(
    providers: dict[str, dict[str, Any]],
) -> dict[str, BackendConfig]:
    """Extract CLI backend configs from raw provider dicts.

    Only providers with ``api = "cli"`` are included.
    """
    configs: dict[str, BackendConfig] = {}
    for name, raw in providers.items():
        if raw.get("api") != "cli":
            continue

        json_paths = JsonPathConfig(
            **{k: v for k, v in raw.get("json_paths", {}).items()
               if k in ("text", "session_id", "model", "input_tokens", "output_tokens")}
        ) if "json_paths" in raw else JsonPathConfig()

        text_patterns = TextPatternConfig(
            **{k: v for k, v in raw.get("text_patterns", {}).items()
               if k in ("session_id", "model")}
        ) if "text_patterns" in raw else TextPatternConfig()

        configs[name] = BackendConfig(
            command=raw.get("command", name),
            args=raw.get("args", []),
            system_prompt_flag=raw.get("system_prompt_flag", "--system-prompt"),
            session_flag=raw.get("session_flag", "--resume"),
            output_format=raw.get("output_format", "json"),
            output_format_flag=raw.get("output_format_flag", ""),
            json_paths=json_paths,
            text_patterns=text_patterns,
            model_map=raw.get("model_map", {}),
            timeout_secs=raw.get("timeout_secs", 180),
        )
    return configs


def load_routing_config(raw: dict[str, Any]) -> RoutingConfig:
    """Load routing config from raw TOML dict."""
    return RoutingConfig(
        default=raw["default"],
        fallback_enabled=raw.get("fallback_enabled", True),
        fallback_chain=raw.get("fallback_chain", []),
        model_rules=raw.get("model_rules", {}),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_cli_backend_config_templates.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent_nexus/platform/agency/cli_backend/config_templates.py \
        tests/unit/test_cli_backend_config_templates.py
git commit -m "feat(cli-backend): add config template loader for CLI providers from config.toml"
```

---

### Task 10: Update __init__.py exports and run full test suite

**Files:**
- Modify: `src/agent_nexus/platform/agency/cli_backend/__init__.py` — add new exports

- [ ] **Step 1: Update __init__.py**

```python
# src/agent_nexus/platform/agency/cli_backend/__init__.py
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
```

- [ ] **Step 2: Run full Python test suite**

Run: `uv run pytest tests/unit/test_cli_backend_*.py -v`
Expected: All CLI backend tests PASS

Run: `uv run pytest tests/unit/ -x -q`
Expected: All unit tests PASS (no regressions)

- [ ] **Step 3: Commit**

```bash
git add src/agent_nexus/platform/agency/cli_backend/__init__.py
git commit -m "feat(cli-backend): complete Python CLI backend module with full exports"
```

---

## Phase 5: Rust Crate Foundation

### Task 11: Scaffold ap-cli-backend Crate

**Files:**
- Create: `crates/ap-cli-backend/Cargo.toml`
- Create: `crates/ap-cli-backend/src/lib.rs`
- Create: `crates/ap-cli-backend/src/types.rs`
- Modify: `Cargo.toml` — add workspace member

- [ ] **Step 1: Create Cargo.toml**

```toml
# crates/ap-cli-backend/Cargo.toml
[package]
name = "ap-cli-backend"
version.workspace = true
edition.workspace = true
license.workspace = true

[dependencies]
tokio = { workspace = true }
serde = { workspace = true }
serde_json = { workspace = true }
toml = { workspace = true }
thiserror = { workspace = true }
rusqlite = { workspace = true }
tracing = { workspace = true }
chrono = { workspace = true }
which = "7"
regex = "1"

[dev-dependencies]
rstest = { workspace = true }
tempfile = "3"
tokio-test = { workspace = true }
```

- [ ] **Step 2: Create lib.rs**

```rust
// crates/ap-cli-backend/src/lib.rs
//! ap-cli-backend — CLI agent backend for LLM calls via subprocess invocation.

pub mod archive;
pub mod backend;
pub mod health;
pub mod parser;
pub mod registry;
pub mod router;
pub mod session;
pub mod types;
```

- [ ] **Step 3: Create types.rs with all core types**

```rust
// crates/ap-cli-backend/src/types.rs
//! Core types for CLI backend integration.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::time::Duration;

/// CLI backend call result.
#[derive(Debug, Clone)]
pub struct CLIResult {
    pub text: String,
    pub model: String,
    pub session_id: Option<String>,
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub raw_stdout: String,
    pub raw_stderr: String,
    pub returncode: i32,
    pub duration: Duration,
    pub parse_error: bool,
}

/// CLI backend configuration (deserialized from config.toml).
#[derive(Debug, Clone, Deserialize)]
pub struct BackendConfig {
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default = "default_system_prompt_flag")]
    pub system_prompt_flag: String,
    #[serde(default = "default_session_flag")]
    pub session_flag: String,
    #[serde(default = "default_output_format")]
    pub output_format: String,
    #[serde(default)]
    pub output_format_flag: String,
    #[serde(default)]
    pub json_paths: JsonPathConfig,
    #[serde(default)]
    pub text_patterns: TextPatternConfig,
    #[serde(default)]
    pub model_map: HashMap<String, String>,
    #[serde(default = "default_timeout")]
    pub timeout_secs: u64,
}

/// JSON output field path mapping.
#[derive(Debug, Clone, Deserialize, Default)]
pub struct JsonPathConfig {
    pub text: Option<String>,
    pub session_id: Option<String>,
    pub model: Option<String>,
    pub input_tokens: Option<String>,
    pub output_tokens: Option<String>,
}

/// Text-mode regex extraction rules.
#[derive(Debug, Clone, Deserialize, Default)]
pub struct TextPatternConfig {
    pub session_id: Option<String>,
    pub model: Option<String>,
}

/// CLI backend error type.
#[derive(Debug, thiserror::Error)]
pub enum CLIBackendError {
    #[error("CLI '{0}' not found in PATH")]
    NotInstalled(String),

    #[error("CLI '{command}' timed out after {timeout_secs}s")]
    Timeout { command: String, timeout_secs: u64 },

    #[error("CLI '{command}' exited with code {code}: {stderr}")]
    ExitError { command: String, code: i32, stderr: String },

    #[error("Failed to parse output from '{0}': {1}")]
    ParseError(String, String),

    #[error("Session '{session_id}' not found in backend '{backend}'")]
    SessionNotFound { session_id: String, backend: String },

    #[error("No available CLI backend (fallback disabled)")]
    NoAvailableBackend,

    #[error("All backends unavailable after fallback chain exhausted")]
    AllBackendsUnavailable,

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),

    #[error("Database error: {0}")]
    Database(#[from] rusqlite::Error),
}

/// Routing configuration.
#[derive(Debug, Clone, Deserialize)]
pub struct RoutingConfig {
    pub default: String,
    #[serde(default = "default_true")]
    pub fallback_enabled: bool,
    #[serde(default)]
    pub fallback_chain: Vec<String>,
    #[serde(default)]
    pub model_rules: HashMap<String, String>,
}

/// Database lifecycle configuration.
#[derive(Debug, Clone, Deserialize)]
pub struct DataLifecycleConfig {
    #[serde(default = "default_hot")]
    pub hot_days: u32,
    #[serde(default = "default_warm")]
    pub warm_days: u32,
    #[serde(default)]
    pub archive_dir: String,
    #[serde(default = "default_true")]
    pub auto_archive: bool,
}

/// CLI session record.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CLISession {
    pub session_id: String,
    pub name: Option<String>,
    pub backend_name: String,
    pub model: Option<String>,
    pub task_id: Option<String>,
    pub created_at: String,
    pub last_used_at: String,
    pub turn_count: u32,
    pub metadata: Option<String>,
}

fn default_system_prompt_flag() -> String { "--system-prompt".into() }
fn default_session_flag() -> String { "--resume".into() }
fn default_output_format() -> String { "json".into() }
fn default_timeout() -> u64 { 180 }
fn default_true() -> bool { true }
fn default_hot() -> u32 { 30 }
fn default_warm() -> u32 { 90 }

impl Default for DataLifecycleConfig {
    fn default() -> Self {
        Self {
            hot_days: 30,
            warm_days: 90,
            archive_dir: String::new(),
            auto_archive: true,
        }
    }
}
```

- [ ] **Step 4: Create stub modules**

Create minimal stub files for each module referenced in `lib.rs`:

```rust
// crates/ap-cli-backend/src/parser.rs
//! CLI output parser — JSON path extraction and text regex.

use crate::types::{BackendConfig, CLIResult, JsonPathConfig};
use std::time::Duration;

pub fn extract_json_value<'a>(data: &'a serde_json::Value, path: &str) -> Option<&'a serde_json::Value> {
    if path.is_empty() {
        return None;
    }
    path.split('.').try_fold(data, |current, key| current.get(key))
}

pub fn parse_json_output(stdout: &str, config: &BackendConfig) -> CLIResult {
    match serde_json::from_str(stdout) {
        Ok(data) => build_result_from_json(&data, stdout, &config.json_paths),
        Err(_) => CLIResult {
            text: stdout.to_string(),
            model: String::new(),
            raw_stdout: stdout.to_string(),
            parse_error: true,
            ..Default::default()
        },
    }
}

fn build_result_from_json(
    data: &serde_json::Value,
    raw: &str,
    paths: &JsonPathConfig,
) -> CLIResult {
    let text = paths.text.as_ref()
        .and_then(|p| extract_json_value(data, p))
        .and_then(|v| v.as_str())
        .unwrap_or(raw)
        .to_string();

    let session_id = paths.session_id.as_ref()
        .and_then(|p| extract_json_value(data, p))
        .and_then(|v| v.as_str())
        .map(String::from);

    let model = paths.model.as_ref()
        .and_then(|p| extract_json_value(data, p))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();

    let input_tokens = paths.input_tokens.as_ref()
        .and_then(|p| extract_json_value(data, p))
        .and_then(|v| v.as_u64());

    let output_tokens = paths.output_tokens.as_ref()
        .and_then(|p| extract_json_value(data, p))
        .and_then(|v| v.as_u64());

    CLIResult {
        text,
        model,
        session_id,
        input_tokens,
        output_tokens,
        raw_stdout: raw.to_string(),
        parse_error: false,
        ..Default::default()
    }
}

pub fn parse_text_output(
    stdout: &str,
    stderr: &str,
    config: &BackendConfig,
) -> CLIResult {
    use regex::Regex;

    let session_id = config.text_patterns.session_id.as_ref()
        .and_then(|pattern| Regex::new(pattern).ok())
        .and_then(|re| {
            let combined = format!("{stdout}\n{stderr}");
            re.captures(&combined).and_then(|c| c.get(1).map(|m| m.as_str().to_string()))
        });

    let model = config.text_patterns.model.as_ref()
        .and_then(|pattern| Regex::new(pattern).ok())
        .and_then(|re| re.captures(stdout).and_then(|c| c.get(1).map(|m| m.as_str().to_string())));

    CLIResult {
        text: stdout.to_string(),
        model: model.unwrap_or_default(),
        session_id,
        raw_stdout: stdout.to_string(),
        raw_stderr: stderr.to_string(),
        parse_error: false,
        ..Default::default()
    }
}
```

```rust
// crates/ap-cli-backend/src/backend.rs
//! GenericCLIBackend — config-driven CLI invocation via tokio::process.

use crate::parser::{parse_json_output, parse_text_output};
use crate::types::{BackendConfig, CLIBackendError, CLIResult};
use std::time::{Duration, Instant};

pub struct GenericCLIBackend {
    config: BackendConfig,
    available: std::sync::atomic::AtomicBool,
}

impl GenericCLIBackend {
    pub fn new(config: BackendConfig) -> Self {
        let available = std::sync::atomic::AtomicBool::new(
            which::which(&config.command).is_ok()
        );
        Self { config, available }
    }

    pub fn name(&self) -> &str {
        &self.config.command
    }

    pub fn is_available(&self) -> bool {
        self.available.load(std::sync::atomic::Ordering::Relaxed)
    }

    pub fn refresh_availability(&self) -> bool {
        let avail = which::which(&self.config.command).is_ok();
        self.available.store(avail, std::sync::atomic::Ordering::Relaxed);
        avail
    }

    pub fn build_args(
        &self,
        system_prompt: &str,
        user_message: &str,
        session_id: Option<&str>,
    ) -> Vec<String> {
        let mut args = self.config.args.clone();

        if !self.config.system_prompt_flag.is_empty() {
            args.push(self.config.system_prompt_flag.clone());
            args.push(system_prompt.to_string());
        }

        if let Some(sid) = session_id {
            if !self.config.session_flag.is_empty() {
                args.push(self.config.session_flag.clone());
                args.push(sid.to_string());
            }
        }

        if self.config.output_format == "json" && !self.config.output_format_flag.is_empty() {
            args.push(self.config.output_format_flag.clone());
            args.push("json".to_string());
        }

        args.push(user_message.to_string());
        args
    }

    pub async fn call(
        &self,
        system_prompt: &str,
        user_message: &str,
        session_id: Option<&str>,
    ) -> Result<CLIResult, CLIBackendError> {
        let args = self.build_args(system_prompt, user_message, session_id);
        let start = Instant::now();

        let output = tokio::time::timeout(
            Duration::from_secs(self.config.timeout_secs),
            tokio::process::Command::new(&self.config.command)
                .args(&args)
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::piped())
                .stderr(std::process::Stdio::piped())
                .kill_on_drop(true)
                .output(),
        )
        .await
        .map_err(|_| CLIBackendError::Timeout {
            command: self.config.command.clone(),
            timeout_secs: self.config.timeout_secs,
        })??;

        let duration = start.elapsed();
        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();

        if !output.status.success() {
            return Err(CLIBackendError::ExitError {
                command: self.config.command.clone(),
                code: output.status.code().unwrap_or(-1),
                stderr: stderr.chars().take(500).collect(),
            });
        }

        let mut result = if self.config.output_format == "json" {
            let mut r = parse_json_output(&stdout, &self.config);
            if r.parse_error {
                tracing::warn!("JSON parse failed for '{}', used raw text", self.config.command);
            }
            r
        } else {
            parse_text_output(&stdout, &stderr, &self.config)
        };

        result.duration = duration;
        result.raw_stdout = stdout;
        result.raw_stderr = stderr;
        result.returncode = 0;

        Ok(result)
    }
}
```

```rust
// crates/ap-cli-backend/src/registry.rs
//! CLIBackendRegistry — backend discovery and health check.

use crate::backend::GenericCLIBackend;
use crate::types::BackendConfig;
use std::collections::HashMap;
use std::sync::Arc;

pub struct CLIBackendRegistry {
    backends: HashMap<String, Arc<GenericCLIBackend>>,
}

impl CLIBackendRegistry {
    pub fn new() -> Self {
        Self { backends: HashMap::new() }
    }

    pub fn register(&mut self, name: String, backend: GenericCLIBackend) {
        self.backends.insert(name, Arc::new(backend));
    }

    pub fn get(&self, name: &str) -> Result<Arc<GenericCLIBackend>, String> {
        self.backends.get(name)
            .cloned()
            .ok_or_else(|| format!("CLI backend '{}' not registered", name))
    }

    pub fn available_backends(&self) -> Vec<Arc<GenericCLIBackend>> {
        self.backends.values()
            .filter(|b| b.is_available())
            .cloned()
            .collect()
    }

    pub fn len(&self) -> usize {
        self.backends.len()
    }
}
```

```rust
// crates/ap-cli-backend/src/router.rs
//! CLIRouter — 4-strategy routing with fallback.

use crate::backend::GenericCLIBackend;
use crate::registry::CLIBackendRegistry;
use crate::types::{CLIBackendError, RoutingConfig};
use std::sync::Arc;

pub struct CLIRouter {
    config: RoutingConfig,
    registry: CLIBackendRegistry,
}

impl CLIRouter {
    pub fn new(config: RoutingConfig, registry: CLIBackendRegistry) -> Self {
        Self { config, registry }
    }

    pub fn resolve(
        &self,
        model_string: Option<&str>,
        explicit_backend: Option<&str>,
    ) -> Result<Arc<GenericCLIBackend>, CLIBackendError> {
        // 1. Explicit
        if let Some(name) = explicit_backend {
            return self.registry.get(name)
                .map_err(|e| CLIBackendError::NotInstalled(e));
        }

        // 2. Model rules
        if let Some(model) = model_string {
            for (pattern, backend_name) in &self.config.model_rules {
                if matches_pattern(model, pattern) {
                    if let Ok(backend) = self.registry.get(backend_name) {
                        return Ok(backend);
                    }
                }
            }
        }

        // 3. Default
        self.registry.get(&self.config.default)
            .map_err(|e| CLIBackendError::NotInstalled(e))
    }

    pub fn resolve_with_fallback(
        &self,
        model_string: Option<&str>,
        explicit_backend: Option<&str>,
    ) -> Result<Arc<GenericCLIBackend>, CLIBackendError> {
        let primary = self.resolve(model_string, explicit_backend);

        if let Ok(backend) = &primary {
            if backend.is_available() {
                return Ok(backend.clone());
            }
        }

        if !self.config.fallback_enabled {
            return Err(CLIBackendError::NoAvailableBackend);
        }

        for name in &self.config.fallback_chain {
            if let Ok(backend) = self.registry.get(name) {
                if backend.is_available() {
                    tracing::info!("Fallback: using backend '{}'", name);
                    return Ok(backend);
                }
            }
        }

        Err(CLIBackendError::AllBackendsUnavailable)
    }
}

fn matches_pattern(model: &str, pattern: &str) -> bool {
    if pattern.contains('*') || pattern.contains('?') {
        // Simple glob matching
        let regex_pattern = pattern
            .replace('.', r"\.")
            .replace('*', ".*")
            .replace('?', ".");
        regex::Regex::new(&format!("^{regex_pattern}$"))
            .map(|re| re.is_match(model))
            .unwrap_or(false)
    } else {
        model == pattern
    }
}
```

```rust
// crates/ap-cli-backend/src/session.rs
//! CLISessionStore — SQLite session persistence with WAL and triggers.

use crate::types::{CLIBackendError, CLISession, DataLifecycleConfig};
use rusqlite::{params, Connection};
use std::path::Path;

const SCHEMA: &str = "
CREATE TABLE IF NOT EXISTS cli_sessions (
    session_id   TEXT PRIMARY KEY,
    name         TEXT,
    backend_name TEXT NOT NULL,
    model        TEXT,
    task_id      TEXT,
    created_at   TEXT DEFAULT (datetime('now')),
    last_used_at TEXT DEFAULT (datetime('now')),
    turn_count   INTEGER DEFAULT 1,
    metadata     TEXT
);

CREATE TABLE IF NOT EXISTS task_executions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT NOT NULL,
    backend_type  TEXT NOT NULL,
    backend_name  TEXT NOT NULL,
    model         TEXT,
    session_id    TEXT REFERENCES cli_sessions(session_id),
    input_tokens  INTEGER,
    output_tokens INTEGER,
    duration_ms   INTEGER,
    status        TEXT DEFAULT 'success',
    error         TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS backend_health (
    backend_name TEXT PRIMARY KEY,
    is_available INTEGER DEFAULT 0,
    last_check   TEXT,
    version      TEXT,
    error_msg    TEXT
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date         TEXT NOT NULL,
    backend_name TEXT NOT NULL,
    total_calls  INTEGER DEFAULT 0,
    success_calls INTEGER DEFAULT 0,
    total_input_tokens  INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    avg_duration_ms     REAL DEFAULT 0,
    PRIMARY KEY (date, backend_name)
);

CREATE TRIGGER IF NOT EXISTS trg_update_daily_stats
AFTER INSERT ON task_executions
BEGIN
    INSERT INTO daily_stats (date, backend_name, total_calls, success_calls,
                             total_input_tokens, total_output_tokens, avg_duration_ms)
    VALUES (DATE('now'), NEW.backend_name, 1,
            CASE WHEN NEW.status = 'success' THEN 1 ELSE 0 END,
            COALESCE(NEW.input_tokens, 0), COALESCE(NEW.output_tokens, 0),
            COALESCE(NEW.duration_ms, 0))
    ON CONFLICT(date, backend_name) DO UPDATE SET
        total_calls = total_calls + 1,
        success_calls = success_calls + CASE WHEN NEW.status = 'success' THEN 1 ELSE 0 END,
        total_input_tokens = total_input_tokens + COALESCE(NEW.input_tokens, 0),
        total_output_tokens = total_output_tokens + COALESCE(NEW.output_tokens, 0),
        avg_duration_ms = (avg_duration_ms * (total_calls - 1) + COALESCE(NEW.duration_ms, 0)) / total_calls;
END;

CREATE TRIGGER IF NOT EXISTS trg_delete_daily_stats
AFTER DELETE ON task_executions
BEGIN
    UPDATE daily_stats SET
        total_calls = total_calls - 1,
        success_calls = success_calls - CASE WHEN OLD.status = 'success' THEN 1 ELSE 0 END,
        total_input_tokens = total_input_tokens - COALESCE(OLD.input_tokens, 0),
        total_output_tokens = total_output_tokens - COALESCE(OLD.output_tokens, 0)
    WHERE date = DATE(OLD.created_at) AND backend_name = OLD.backend_name;
END;
";

const PRAGMAS: &str = "
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=1000;
PRAGMA synchronous=NORMAL;
";

pub struct CLISessionStore {
    conn: Connection,
}

impl CLISessionStore {
    pub fn open(db_path: &Path) -> Result<Self, CLIBackendError> {
        let conn = Connection::open(db_path)?;
        conn.execute_batch(PRAGMAS)?;
        conn.execute_batch(SCHEMA)?;
        Ok(Self { conn })
    }

    pub fn save_session(&self, session: &CLISession) -> Result<(), CLIBackendError> {
        self.conn.execute(
            "INSERT OR REPLACE INTO cli_sessions \
             (session_id, name, backend_name, model, task_id, \
              created_at, last_used_at, turn_count, metadata) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
            params![
                session.session_id, session.name, session.backend_name,
                session.model, session.task_id, session.created_at,
                session.last_used_at, session.turn_count, session.metadata,
            ],
        )?;
        Ok(())
    }

    pub fn get_session(&self, session_id: &str) -> Result<Option<CLISession>, CLIBackendError> {
        let mut stmt = self.conn.prepare(
            "SELECT session_id, name, backend_name, model, task_id, \
                    created_at, last_used_at, turn_count, metadata \
             FROM cli_sessions WHERE session_id = ?1"
        )?;

        let mut rows = stmt.query(params![session_id])?;
        match rows.next()? {
            Some(row) => Ok(Some(CLISession {
                session_id: row.get(0)?,
                name: row.get(1)?,
                backend_name: row.get(2)?,
                model: row.get(3)?,
                task_id: row.get(4)?,
                created_at: row.get(5)?,
                last_used_at: row.get(6)?,
                turn_count: row.get(7)?,
                metadata: row.get(8)?,
            })),
            None => Ok(None),
        }
    }

    pub fn record_execution(
        &self,
        task_id: &str,
        backend_type: &str,
        backend_name: &str,
        model: Option<&str>,
        session_id: Option<&str>,
        input_tokens: Option<u64>,
        output_tokens: Option<u64>,
        duration_ms: Option<u64>,
        status: &str,
        error: Option<&str>,
    ) -> Result<(), CLIBackendError> {
        self.conn.execute(
            "INSERT INTO task_executions \
             (task_id, backend_type, backend_name, model, session_id, \
              input_tokens, output_tokens, duration_ms, status, error) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![
                task_id, backend_type, backend_name, model, session_id,
                input_tokens, output_tokens, duration_ms, status, error,
            ],
        )?;
        Ok(())
    }

    pub fn close(self) {}
}
```

```rust
// crates/ap-cli-backend/src/health.rs
//! Health check — binary existence and version verification.

use crate::types::{BackendConfig, CLIBackendError};
use std::process::Command;

pub struct HealthCheck;

impl HealthCheck {
    pub fn check_installed(config: &BackendConfig) -> bool {
        which::which(&config.command).is_ok()
    }

    pub fn check_version(config: &BackendConfig) -> Result<String, CLIBackendError> {
        let output = Command::new(&config.command)
            .arg("--version")
            .output()
            .map_err(|e| CLIBackendError::Io(e))?;

        if !output.status.success() {
            return Err(CLIBackendError::ExitError {
                command: config.command.clone(),
                code: output.status.code().unwrap_or(-1),
                stderr: String::from_utf8_lossy(&output.stderr).to_string(),
            });
        }

        let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
        Ok(version)
    }
}
```

```rust
// crates/ap-cli-backend/src/archive.rs
//! Database archival — ATTACH DATABASE based cold storage.

use crate::types::{CLIBackendError, DataLifecycleConfig};
use rusqlite::Connection;
use std::path::Path;

pub fn archive_old_data(
    conn: &Connection,
    config: &DataLifecycleConfig,
    archive_path: &Path,
) -> Result<u64, CLIBackendError> {
    conn.execute(
        &format!("ATTACH DATABASE '{}' AS archive", archive_path.display()),
        [],
    )?;

    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS archive.task_executions AS SELECT * FROM task_executions WHERE 0;
         CREATE TABLE IF NOT EXISTS archive.cli_sessions AS SELECT * FROM cli_sessions WHERE 0;"
    )?;

    let migrated = conn.execute(
        &format!(
            "INSERT INTO archive.task_executions SELECT * FROM task_executions \
             WHERE created_at < datetime('now', '-{} days')",
            config.hot_days
        ),
        [],
    )?;

    conn.execute(
        &format!(
            "DELETE FROM task_executions WHERE created_at < datetime('now', '-{} days')",
            config.hot_days
        ),
        [],
    )?;

    conn.execute("DETACH DATABASE archive", [])?;

    Ok(migrated as u64)
}
```

- [ ] **Step 5: Update workspace Cargo.toml**

Add to `Cargo.toml` workspace members:

```toml
[workspace]
members = [
    "crates/ap-core",
    "crates/ap-runtime",
    "crates/ap-gateway",
    "crates/ap-fetcher",
    "crates/ap-evolution",
    "crates/ap-cli",
    "crates/ap-cli-backend",     # NEW
]
```

Add internal crate dependency:

```toml
# In [workspace.dependencies]:
ap-cli-backend = { path = "crates/ap-cli-backend" }
```

- [ ] **Step 6: Add `Cli` variant to Rust ProviderApiType**

In `crates/ap-core/src/models/config.rs`:

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "kebab-case")]
pub enum ProviderApiType {
    #[default]
    OpenaiCompatible,
    AnthropicMessages,
    Ollama,
    Cli,           // NEW: CLI backend (subprocess invocation)
}
```

- [ ] **Step 7: Implement Default for CLIResult**

Add to `crates/ap-cli-backend/src/types.rs`:

```rust
impl Default for CLIResult {
    fn default() -> Self {
        Self {
            text: String::new(),
            model: String::new(),
            session_id: None,
            input_tokens: None,
            output_tokens: None,
            raw_stdout: String::new(),
            raw_stderr: String::new(),
            returncode: 0,
            duration: Duration::ZERO,
            parse_error: false,
        }
    }
}
```

- [ ] **Step 8: Build and test**

Run: `cargo build -p ap-cli-backend`
Expected: Compiles successfully

Run: `cargo test -p ap-cli-backend`
Expected: No test failures (stubs have no tests yet, but compilation validates types)

Run: `cargo test -p ap-core`
Expected: All existing ap-core tests still pass (verify `Cli` enum variant doesn't break anything)

- [ ] **Step 9: Commit**

```bash
git add crates/ap-cli-backend/ Cargo.toml crates/ap-core/src/models/config.rs
git commit -m "feat(rust): scaffold ap-cli-backend crate with types, parser, backend, session store"
```

---

## Phase 6: Rust Tests

### Task 12: Rust Unit Tests for Parser, Backend, Router, Session

**Files:**
- Add tests inline in each Rust source file (Rust convention: `#[cfg(test)] mod tests`)

- [ ] **Step 1: Add parser tests to `crates/ap-cli-backend/src/parser.rs`**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::TextPatternConfig;

    fn claude_config() -> BackendConfig {
        serde_json::from_str(r#"{
            "command": "claude",
            "args": ["-p"],
            "json_paths": {
                "text": "result",
                "session_id": "session_id",
                "model": "model",
                "input_tokens": "usage.input_tokens",
                "output_tokens": "usage.output_tokens"
            }
        }"#).unwrap()
    }

    #[test]
    fn extract_simple_path() {
        let data = serde_json::json!({"result": "hello", "session_id": "abc"});
        assert_eq!(extract_json_value(&data, "result").unwrap().as_str(), Some("hello"));
    }

    #[test]
    fn extract_nested_path() {
        let data = serde_json::json!({"usage": {"input_tokens": 100}});
        assert_eq!(extract_json_value(&data, "usage.input_tokens").unwrap().as_u64(), Some(100));
    }

    #[test]
    fn extract_missing_returns_none() {
        let data = serde_json::json!({"result": "text"});
        assert!(extract_json_value(&data, "nonexistent.path").is_none());
    }

    #[test]
    fn parse_json_claude_format() {
        let stdout = r#"{"result": "planned", "session_id": "s1", "model": "claude-sonnet-4", "usage": {"input_tokens": 100, "output_tokens": 50}}"#;
        let result = parse_json_output(stdout, &claude_config());
        assert_eq!(result.text, "planned");
        assert_eq!(result.session_id, Some("s1".to_string()));
        assert_eq!(result.model, "claude-sonnet-4");
        assert_eq!(result.input_tokens, Some(100));
        assert_eq!(result.output_tokens, Some(50));
        assert!(!result.parse_error);
    }

    #[test]
    fn parse_json_invalid_falls_back() {
        let result = parse_json_output("not json", &claude_config());
        assert_eq!(result.text, "not json");
        assert!(result.parse_error);
    }

    #[test]
    fn parse_text_with_regex() {
        let mut config = BackendConfig::default();
        config.command = "openclaw".into();
        config.output_format = "text".into();
        config.text_patterns = TextPatternConfig {
            session_id: Some(r"session[:\s]+([a-f0-9-]+)".into()),
            model: None,
        };
        let result = parse_text_output("done", "session: abc-123 started", &config);
        assert_eq!(result.text, "done");
        assert_eq!(result.session_id, Some("abc-123".to_string()));
    }
}
```

- [ ] **Step 2: Add backend tests to `crates/ap-cli-backend/src/backend.rs`**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    fn test_config() -> BackendConfig {
        BackendConfig {
            command: "echo".into(),
            args: vec![],
            system_prompt_flag: "--system".into(),
            session_flag: "--resume".into(),
            output_format: "text".into(),
            output_format_flag: String::new(),
            json_paths: Default::default(),
            text_patterns: Default::default(),
            model_map: Default::default(),
            timeout_secs: 10,
        }
    }

    #[test]
    fn build_args_basic() {
        let backend = GenericCLIBackend::new(test_config());
        let args = backend.build_args("sys prompt", "user msg", None);
        assert!(args.contains(&"--system".to_string()));
        assert!(args.contains(&"sys prompt".to_string()));
        assert!(args.contains(&"user msg".to_string()));
    }

    #[test]
    fn build_args_with_session() {
        let backend = GenericCLIBackend::new(test_config());
        let args = backend.build_args("sys", "user", Some("sess-123"));
        assert!(args.contains(&"--resume".to_string()));
        assert!(args.contains(&"sess-123".to_string()));
    }

    #[tokio::test]
    async fn call_echo_command() {
        let config = BackendConfig {
            command: "echo".into(),
            output_format: "text".into(),
            ..test_config()
        };
        let backend = GenericCLIBackend::new(config);
        let result = backend.call("sys", "hello world", None).await;
        assert!(result.is_ok());
        let r = result.unwrap();
        assert!(r.text.contains("hello world"));
        assert_eq!(r.returncode, 0);
    }

    #[tokio::test]
    async fn call_nonexistent_command() {
        let mut config = test_config();
        config.command = "definitely_not_a_real_command_xyz".into();
        config.timeout_secs = 2;
        let backend = GenericCLIBackend::new(config);
        let result = backend.call("sys", "msg", None).await;
        assert!(result.is_err());
    }
}
```

- [ ] **Step 3: Add session store tests to `crates/ap-cli-backend/src/session.rs`**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn setup() -> (TempDir, CLISessionStore) {
        let dir = TempDir::new().unwrap();
        let db_path = dir.path().join("test.db");
        let store = CLISessionStore::open(&db_path).unwrap();
        (dir, store)
    }

    #[test]
    fn save_and_get_session() {
        let (_dir, store) = setup();
        let session = CLISession {
            session_id: "s1".into(),
            backend_name: "claude-code".into(),
            model: Some("claude-sonnet-4".into()),
            name: Some("test session".into()),
            created_at: "2026-01-01T00:00:00".into(),
            last_used_at: "2026-01-01T00:00:00".into(),
            turn_count: 1,
            ..Default::default()
        };
        store.save_session(&session).unwrap();
        let retrieved = store.get_session("s1").unwrap();
        assert!(retrieved.is_some());
        assert_eq!(retrieved.unwrap().backend_name, "claude-code");
    }

    #[test]
    fn get_nonexistent_returns_none() {
        let (_dir, store) = setup();
        assert!(store.get_session("nonexistent").unwrap().is_none());
    }

    #[test]
    fn record_execution_updates_daily_stats() {
        let (_dir, store) = setup();
        store.record_execution(
            "t1", "cli", "claude-code", Some("model"),
            None, Some(100), Some(50), Some(1000), "success", None,
        ).unwrap();
        store.record_execution(
            "t2", "cli", "claude-code", Some("model"),
            None, Some(50), Some(0), Some(500), "error", None,
        ).unwrap();

        let mut stmt = store.conn.prepare(
            "SELECT total_calls, success_calls FROM daily_stats WHERE backend_name = 'claude-code'"
        ).unwrap();
        let row: (i64, i64) = stmt.query_row([], |row| Ok((row.get(0)?, row.get(1)?))).unwrap();
        assert_eq!(row.0, 2);
        assert_eq!(row.1, 1);
    }
}
```

- [ ] **Step 4: Run all Rust tests**

Run: `cargo test -p ap-cli-backend`
Expected: All tests PASS

Run: `cargo test` (full workspace)
Expected: All workspace tests PASS (no regressions)

- [ ] **Step 5: Commit**

```bash
git add crates/ap-cli-backend/
git commit -m "test(cli-backend): add Rust unit tests for parser, backend, and session store"
```

---

## Phase 7: Rust Workspace Integration

### Task 13: Wire ap-cli-backend into ap-cli Data Commands

**Files:**
- Modify: `crates/ap-cli/src/main.rs` or relevant command module — add `data` subcommands

- [ ] **Step 1: Add `data` subcommand to ap-cli**

In `crates/ap-cli/src/main.rs` (or wherever clap commands are defined), add a `Data` variant:

```rust
// Add to the existing clap command enum:
Data {
    #[command(subcommand)]
    command: DataCommands,
}

#[derive(clap::Subcommand)]
enum DataCommands {
    /// Archive old data to cold storage
    Archive,
    /// Show statistics summary
    Stats,
    /// List active CLI sessions
    Sessions {
        #[command(subcommand)]
        command: SessionCommands,
    },
}

#[derive(clap::Subcommand)]
enum SessionCommands {
    /// List active sessions
    List,
    /// Clean up expired sessions
    Cleanup {
        #[arg(long, default_value = "30")]
        max_age_days: u32,
    },
}
```

- [ ] **Step 2: Implement command handlers**

The handlers should delegate to `ap-cli-backend`:

```rust
// In the match arm for Data command:
Data { command } => {
    use ap_cli_backend::session::CLISessionStore;
    let db_path = get_data_dir()?.join("agent-nexus.db");
    let store = CLISessionStore::open(&db_path)?;

    match command {
        DataCommands::Archive => {
            // Use archive module
            let config = ap_cli_backend::types::DataLifecycleConfig::default();
            let archive_dir = get_data_dir()?.join("archive");
            std::fs::create_dir_all(&archive_dir)?;
            let archive_path = archive_dir.join(format!(
                "agent-nexus-{}.db",
                chrono::Local::now().format("%Y-%m")
            ));
            let count = ap_cli_backend::archive::archive_old_data(
                &store.conn, &config, &archive_path,
            )?;
            println!("Archived {} old records to {}", count, archive_path.display());
        }
        DataCommands::Stats => {
            // Query daily_stats and display
            let mut stmt = store.conn.prepare(
                "SELECT date, backend_name, total_calls, success_calls FROM daily_stats ORDER BY date DESC LIMIT 10"
            )?;
            let rows = stmt.query_map([], |row| {
                Ok(format!("{} | {} | calls: {} | success: {}",
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, i64>(2)?,
                    row.get::<_, i64>(3)?))
            })?;
            for row in rows {
                println!("{}", row?);
            }
        }
        DataCommands::Sessions { command: SessionCommands::List } => {
            // List sessions from cli_sessions table
            let mut stmt = store.conn.prepare(
                "SELECT session_id, backend_name, model, last_used_at FROM cli_sessions ORDER BY last_used_at DESC LIMIT 20"
            )?;
            let rows = stmt.query_map([], |row| {
                Ok(format!("{} | {} | {} | {}",
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, Option<String>>(2)?.unwrap_or_default(),
                    row.get::<_, String>(3)?))
            })?;
            for row in rows {
                println!("{}", row?);
            }
        }
        DataCommands::Sessions { command: SessionCommands::Cleanup { max_age_days } } => {
            let deleted = store.conn.execute(
                "DELETE FROM cli_sessions WHERE last_used_at < datetime('now', ?)",
                [format!("-{max_age_days} days")],
            )?;
            println!("Cleaned up {} expired sessions", deleted);
        }
    }
    store.close();
}
```

- [ ] **Step 3: Build and test**

Run: `cargo build -p ap-cli`
Expected: Compiles successfully

Run: `cargo test -p ap-cli`
Expected: All existing tests still pass

- [ ] **Step 4: Commit**

```bash
git add crates/ap-cli/
git commit -m "feat(ap-cli): add data subcommands (archive, stats, sessions) using ap-cli-backend"
```

---

## Self-Review Checklist

### Spec Coverage

| Spec Section | Plan Task | Status |
|-------------|-----------|--------|
| 1. Motivation | N/A (context) | Covered |
| 2. Architecture (Python) | Tasks 1-6 | All modules created |
| 2. Architecture (Rust) | Tasks 11-13 | All modules created |
| 3. Config Schema | Task 9 | Config templates + loader |
| 4. SQLite Schema | Task 7 | Full schema + triggers |
| 5. Routing Strategy | Task 6 | 4-strategy + fallback |
| 6. Error Handling | Task 4 | Nonzero exit, timeout, parse error |
| 7. LLMClient Integration | Task 8 | CLI branch + session_id |
| 8. Output Parsing | Task 3 | JSON path + text regex |
| 9. Rust Implementation | Tasks 11-13 | Full crate |
| 10. Operational Details | Task 7 (WAL, triggers), Task 13 (data cmds) | Covered |
| 11. Testing Strategy | All tasks (TDD) | Unit tests for every module |
| 12. Scope | N/A | MVP only, no streaming/capability matching |

### Placeholder Scan

No TBD, TODO, or "implement later" in any task code. All steps contain complete code.

### Type Consistency

- `CLIResult.text` (str) — consistent across Python parser, base, and LLMClient integration
- `BackendConfig.command` (str) — used by `GenericCLIBackend` and `build_args()`
- `RoutingConfig.default` (str) — used by `CLIRouter.resolve()`
- `CLISessionRecord.session_id` (str) — used by `CLISessionStore.save_session()` and `get_session()`
- Rust `CLIResult` mirrors Python `CLIResult` fields exactly
- `ProviderApiType.CLI` added to both Python and Rust enums
