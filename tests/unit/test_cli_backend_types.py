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
        assert config.timeout_secs == 300

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
                session_id=r"session[:\s]+([a-f0-9-]+)"
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
        config = JsonPathConfig(text="result", input_tokens="usage.input_tokens")
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
        record = CLISessionRecord(session_id="sess-abc", backend_name="claude-code")
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
