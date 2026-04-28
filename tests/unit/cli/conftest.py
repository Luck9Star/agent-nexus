"""Shared fixtures for CLI tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def tmp_config_dir(tmp_path: Path) -> Path:
    """Create a temporary config directory tree."""
    config_dir = tmp_path / ".agent-nexus"
    config_dir.mkdir()
    for subdir in ("agents", "venvs", "cache/repos", "runtimes", "logs"):
        (config_dir / subdir).mkdir(parents=True)
    return config_dir


@pytest.fixture(autouse=True)
def _isolate_config_env(monkeypatch: Any, tmp_path: Path) -> None:
    """Ensure tests don't read/write the real ~/.agent-nexus."""
    config_dir = tmp_path / ".agent-nexus"
    monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))
