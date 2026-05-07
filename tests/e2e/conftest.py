"""End-to-end test configuration.

E2E tests exercise internal subsystems end-to-end (real SQLite, mock LLM,
in-process runtime). They run by default in normal CI.

Tests that genuinely require external services (real API keys, network)
should be marked ``@pytest.mark.requires_api`` — those are gated behind
``--run-e2e``.
"""

import pytest


def pytest_collection_modifyitems(items):
    """Add the 'e2e' marker to every collected test in this directory tree."""
    from pathlib import Path

    e2e_dir = Path(__file__).resolve().parent
    for item in items:
        try:
            item_path = Path(item.fspath).resolve()
        except AttributeError:
            item_path = Path(item.nodeid.split("::")[0]).resolve()
        if str(item_path).startswith(str(e2e_dir)):
            item.add_marker(pytest.mark.e2e)


def pytest_addoption(parser):
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="Run e2e tests that require external services (requires_api marker)",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: end-to-end subsystem tests (runs by default)")
    config.addinivalue_line(
        "markers", "requires_api: requires real API/network access (needs --run-e2e)"
    )


def pytest_runtest_setup(item):
    """Only skip tests marked ``requires_api`` when ``--run-e2e`` is absent."""
    if "requires_api" in [m.name for m in item.iter_markers()] and not item.config.getoption(
        "--run-e2e"
    ):
        pytest.skip("requires --run-e2e flag (external service needed)")
