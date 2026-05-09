"""Shared fixtures for CLI tests."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _isolate_config_env(monkeypatch: Any, tmp_path: Any) -> None:
    """Ensure tests don't read/write the real ~/.agent-nexus."""
    config_dir = tmp_path / ".agent-nexus"
    monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))
