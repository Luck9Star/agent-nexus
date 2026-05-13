"""Unit tests for CLI backend data types."""

from __future__ import annotations

from agent_nexus.platform.agency.cli_backend.types import (
    CLIResult,
)


class TestCLIResult:
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
