"""Data models for config-linter Agent.

Pydantic v2 frozen models for configuration linting and issue reporting.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LintIssue(BaseModel):
    """A single lint issue found in a config file.

    Attributes:
        severity: Issue severity — error, warning, or info.
        category: Issue category (e.g. "missing_key", "type_mismatch", "deprecated").
        location: Location in the file (line number or section path).
        message: Human-readable description of the issue.
        suggestion: Suggested fix for the issue.
    """

    model_config = ConfigDict(frozen=True)

    severity: str
    category: str
    location: str = ""
    message: str = ""
    suggestion: str = ""


class LintReport(BaseModel):
    """Result of linting a configuration file.

    Attributes:
        issues: All lint issues discovered.
        total_issues: Total number of issues.
        error_count: Number of error-severity issues.
        warning_count: Number of warning-severity issues.
        info_count: Number of info-severity issues.
        format_detected: The detected file format (toml, yaml, json, unknown).
    """

    model_config = ConfigDict(frozen=True)

    issues: list[LintIssue] = Field(default_factory=list)
    total_issues: int = 0
    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    format_detected: str = "unknown"
