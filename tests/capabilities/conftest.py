"""Capability test configuration — markers and CLI options."""

from __future__ import annotations

import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        item.add_marker(pytest.mark.capability)


def pytest_addoption(parser):
    parser.addoption(
        "--run-release",
        action="store_true",
        default=False,
        help="Run release acceptance tests",
    )
    parser.addoption(
        "--run-api",
        action="store_true",
        default=False,
        help="Run real API call tests",
    )


def pytest_runtest_setup(item):
    markers = [m.name for m in item.iter_markers()]
    if "capability_release" in markers and not item.config.getoption("--run-release"):
        pytest.skip("release tests require --run-release")
    if "requires_api" in markers and not item.config.getoption("--run-api"):
        pytest.skip("API tests require --run-api")
