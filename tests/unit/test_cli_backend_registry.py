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

    @patch("shutil.which", return_value="/usr/bin/test")
    def test_refresh_availability(self, mock_which):
        registry = CLIBackendRegistry()
        backend = GenericCLIBackend(_make_config("cc", "claude"))
        registry.register("claude-code", backend)
        assert len(registry.available_backends()) == 1
