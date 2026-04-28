"""Conftest for cicd-quality-gate tests -- adds package to sys.path."""

import sys
from pathlib import Path

# Add this agent's directory to sys.path so the package is importable
_agent_dir = Path(__file__).parent.parent
if str(_agent_dir) not in sys.path:
    sys.path.insert(0, str(_agent_dir))
