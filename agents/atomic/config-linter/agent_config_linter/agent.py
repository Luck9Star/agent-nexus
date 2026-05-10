"""ConfigLinterAgent — Config file linter for TOML, YAML, and JSON.

Single-phase pipeline:
  lint_config() — parse config content, check for common issues, return report

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_config_linter.models import LintReport
from agent_config_linter.tools.lint_config import lint_config as _lint


class ConfigLinterAgent:
    """Config file linter for TOML, YAML, and JSON.

    This agent parses configuration files, detects the format automatically,
    and checks for common issues like missing keys, type mismatches,
    deprecated options, and structural problems.

    Usage:
        agent = ConfigLinterAgent()
        report = agent.lint_config('[project]\\nname = "my-pkg"')
        print(report.error_count, report.warning_count)
    """

    def lint_config(self, content: str, fmt: str = "auto") -> LintReport:
        """Lint a configuration file for common issues.

        Args:
            content: Configuration file content string.
            fmt: Format hint — "auto", "toml", "yaml", or "json".

        Returns:
            LintReport with all issues found and severity counts.
        """
        return _lint(content, fmt)
