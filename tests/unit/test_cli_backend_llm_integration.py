"""Unit tests for LLMClient CLI backend integration."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_nexus.models.config import ProviderApiType


def _write_cli_config(tmp_path: Path) -> Path:
    (tmp_path / "config.toml").write_text("""
[models]
default = "claude-code:sonnet"

[cli_backends.claude-code]
command = "claude"
args = ["-p"]
output_format = "json"
output_format_flag = "--output-format"

[cli_backends.claude-code.json_paths]
text = "result"
session_id = "session_id"
model = "model"
input_tokens = "usage.input_tokens"
output_tokens = "usage.output_tokens"
""")
    return tmp_path


class TestLLMClientCLIInit:
    def test_cli_provider_skips_api_key_check(self, tmp_path: Path):
        _write_cli_config(tmp_path)
        from agent_nexus.platform.agency.llm_client import LLMClient

        try:
            client = LLMClient(model_string="claude-code:sonnet", config_dir=tmp_path)
            assert client._provider_config.api == ProviderApiType.CLI
            assert client._api_key == ""
            client.close()
        except ValueError as e:
            if "API key" in str(e):
                pytest.fail("CLI provider should not require API key")


class TestLLMClientCLICall:
    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("subprocess.Popen")
    def test_cli_call_returns_llm_response(self, mock_popen_cls, mock_which, tmp_path: Path):
        _write_cli_config(tmp_path)
        proc = MagicMock()
        proc.communicate.return_value = (
            json.dumps({
                "result": "planned tasks",
                "session_id": "sess-001",
                "model": "claude-sonnet-4-20250514",
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }),
            "",
        )
        proc.returncode = 0
        proc.pid = 12345
        mock_popen_cls.return_value = proc
        from agent_nexus.platform.agency.llm_client import LLMClient

        client = LLMClient(model_string="claude-code:sonnet", config_dir=tmp_path)
        response = client.call(
            system_prompt="You are a planner.",
            user_message="Design X.",
            session_id="sess-001",
        )
        assert response.text == "planned tasks"
        assert response.model == "claude-sonnet-4-20250514"
        assert response.metadata.get("session_id") == "sess-001"
        assert response.metadata.get("input_tokens") == 100
        client.close()
