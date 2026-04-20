"""Shared test fixtures for agent-nexus tests."""

import gc
import warnings
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


# ---------------------------------------------------------------------------
# IPython / Runtime fixtures — session-scoped to avoid creating 40+ shells
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def _shared_executor():
    """Session-scoped IPythonExecutor — ONE shell shared across all tests."""
    from agent_nexus.platform.runtime.executor import IPythonExecutor

    executor = IPythonExecutor()
    yield executor
    executor.close()


@pytest.fixture(scope="session")
def _shared_runtime():
    """Session-scoped PythonRuntime — ONE shell shared across all tests."""
    from agent_nexus.platform.runtime.runtime import PythonRuntime

    rt = PythonRuntime()
    yield rt
    rt.close()


@pytest.fixture
def shared_executor(_shared_executor):
    """Per-test executor with namespace reset (no new shell created)."""
    _shared_executor.reset()
    return _shared_executor


@pytest.fixture
def shared_runtime(_shared_runtime):
    """Per-test runtime with namespace reset (no new shell created)."""
    _shared_runtime.reset()
    return _shared_runtime


@pytest.fixture(autouse=True)
def _gc_force_collect():
    """Force garbage collection after every test."""
    yield
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        gc.collect()
