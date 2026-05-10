"""Data models for db-schema-analyzer Agent.

Pydantic v2 frozen models for database schema review and issue reporting.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SchemaIssue(BaseModel):
    """A single issue found in a database schema.

    Attributes:
        severity: Issue severity — error, warning, or info.
        category: Issue category (e.g. "missing_pk", "naming", "missing_index").
        table: The table name where the issue was found.
        column: The column name (if applicable).
        message: Human-readable description of the issue.
        suggestion: Suggested fix or improvement.
    """

    model_config = ConfigDict(frozen=True)

    severity: str
    category: str
    table: str = ""
    column: str = ""
    message: str = ""
    suggestion: str = ""


class SchemaReview(BaseModel):
    """Result of reviewing a database schema.

    Attributes:
        tables_parsed: Number of CREATE TABLE statements parsed.
        issues: All schema issues discovered.
        summary: Counts by severity level.
    """

    model_config = ConfigDict(frozen=True)

    tables_parsed: int = 0
    issues: list[SchemaIssue] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
