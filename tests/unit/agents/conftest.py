"""Ensure agent packages under agents/atomic/ are importable in tests."""

import sys
from pathlib import Path

# Add each atomic agent's root directory to sys.path so that
# agent-specific packages (e.g. agent_generic_expert_agent) can be imported.
_ATOMIC_DIR = Path(__file__).resolve().parents[3] / "agents" / "atomic"

for _agent_dir in _ATOMIC_DIR.iterdir():
    if _agent_dir.is_dir():
        _str = str(_agent_dir)
        if _str not in sys.path:
            sys.path.insert(0, _str)
