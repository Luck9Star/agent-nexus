"""Shared test fixtures for agent-nexus tests."""

from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Provide a temporary database path."""
    return tmp_path / "test.db"


@pytest.fixture
def task_graph(tmp_db: Path):
    """Provide a fresh TaskGraph instance."""
    from agent_nexus.platform.orchestration.task_graph import TaskGraph

    return TaskGraph(tmp_db)
