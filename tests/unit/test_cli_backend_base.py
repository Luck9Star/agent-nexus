"""Unit tests for GenericCLIBackend — subprocess-based CLI invocation."""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

from agent_nexus.platform.agency.cli_backend.base import GenericCLIBackend
from agent_nexus.platform.agency.cli_backend.types import BackendConfig, CLIResult, JsonPathConfig


def _claude_config() -> BackendConfig:
    return BackendConfig(
        command="claude", args=["-p"],
        system_prompt_flag="--system-prompt", session_flag="--resume",
        output_format="json", output_format_flag="--output-format",
        json_paths=JsonPathConfig(
            text="result", session_id="session_id", model="model",
            input_tokens="usage.input_tokens", output_tokens="usage.output_tokens",
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
            command="openclaw", args=["agent", "-m"],
            system_prompt_flag="--system", session_flag="--session",
            output_format="text",
        )
        backend = GenericCLIBackend(config)
        args = backend.build_args("sys", "user msg")
        assert "--output-format" not in args
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


class TestGenericCLIBackendModelMap:
    def test_resolve_known_model(self):
        backend = GenericCLIBackend(_claude_config())
        assert backend.resolve_model("sonnet") == "claude-sonnet-4-20250514"

    def test_resolve_unknown_returns_input(self):
        backend = GenericCLIBackend(_claude_config())
        assert backend.resolve_model("opus") == "opus"

    def test_empty_model_map_passes_through(self):
        config = BackendConfig(command="test", args=[])
        backend = GenericCLIBackend(config)
        assert backend.resolve_model("anything") == "anything"


class TestGenericCLIBackendCall:
    @patch("subprocess.run")
    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_successful_json_call(self, mock_which, mock_run):
        mock_run.return_value = MagicMock(
            stdout=json.dumps({
                "result": "planned tasks", "session_id": "sess-abc",
                "model": "claude-sonnet-4-20250514",
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }),
            stderr="", returncode=0,
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
            stdout="", stderr="Error: model not found", returncode=1,
        )
        backend = GenericCLIBackend(_claude_config())
        result = backend.call("sys", "msg")
        assert result.returncode == 1
        assert "Error: model not found" in result.raw_stderr

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=180))
    @patch("shutil.which", return_value="/usr/local/bin/claude")
    def test_timeout_returns_error_result(self, mock_which, mock_run):
        backend = GenericCLIBackend(_claude_config())
        result = backend.call("sys", "msg")
        assert result.returncode == -1
        assert "timed out" in result.raw_stderr.lower()
