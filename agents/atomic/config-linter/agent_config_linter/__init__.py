"""agent-config-linter — Config file linter for TOML, YAML, and JSON.

Parses and validates configuration files, checking for structural issues,
missing keys, type mismatches, and deprecated options.
"""

from agent_config_linter.agent import ConfigLinterAgent
from agent_config_linter.models import LintIssue, LintReport

__all__ = [
    "ConfigLinterAgent",
    "LintIssue",
    "LintReport",
]
