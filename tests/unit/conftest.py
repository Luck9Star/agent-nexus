"""Auto-apply 'unit' marker to all tests in this directory."""

from pathlib import Path

import pytest

_UNIT_DIR = Path(__file__).resolve().parent


def pytest_collection_modifyitems(items):
    """Add the 'unit' marker to every collected test in this directory tree."""
    for item in items:
        try:
            item_path = Path(item.fspath).resolve()
        except AttributeError:
            item_path = Path(item.nodeid.split("::")[0]).resolve()
        # Check that item is under tests/unit/
        if str(item_path).startswith(str(_UNIT_DIR)):
            item.add_marker(pytest.mark.unit)
