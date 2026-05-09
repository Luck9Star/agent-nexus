"""Conftest for composite agent tests -- adds each agent package to sys.path.

When running tests from the project root (e.g. ``pytest agents/``),
each composite agent's package directory must be on sys.path for
imports to resolve. This conftest dynamically discovers and adds them.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add each composite agent's package directory to sys.path
_composite_dir = Path(__file__).parent
for _agent_dir in _composite_dir.iterdir():
    if _agent_dir.is_dir() and not _agent_dir.name.startswith(("_", ".")):
        if _agent_dir in sys.path:
            continue
        sys.path.insert(0, str(_agent_dir))
