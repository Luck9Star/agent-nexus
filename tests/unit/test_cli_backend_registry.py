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

    def test_all_backends(self):
        registry = CLIBackendRegistry()
        registry.register("a", GenericCLIBackend(_make_config("a", "cmd-a")))
        registry.register("b", GenericCLIBackend(_make_config("b", "cmd-b")))
        all_b = registry.all_backends()
        assert len(all_b) == 2
        assert "a" in all_b
        assert "b" in all_b

    def test_len(self):
        registry = CLIBackendRegistry()
        assert len(registry) == 0
        registry.register("a", GenericCLIBackend(_make_config("a", "cmd")))
        assert len(registry) == 1
