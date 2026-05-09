"""Unit tests for CLI output parser — JSON path extraction and text regex."""

from __future__ import annotations

import json

from agent_nexus.platform.agency.cli_backend.parser import (
    extract_json_value,
    parse_json_output,
    parse_text_output,
)
from agent_nexus.platform.agency.cli_backend.types import (
    BackendConfig,
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
        stdout = json.dumps(
            {
                "result": "task completed",
                "session_id": "sess-001",
                "model": "claude-sonnet-4-20250514",
                "usage": {"input_tokens": 200, "output_tokens": 100},
            }
        )
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
        stdout = json.dumps(
            {
                "response": {"text": "gemini answer"},
                "session": {"id": "gsess-1"},
                "model_version": "gemini-2.5-flash",
                "usage_metadata": {"prompt_token_count": 300, "candidates_token_count": 150},
            }
        )
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
        assert result.model == ""


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
        assert result.model == ""

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
