"""DbSchemaAnalyzerAgent — Database schema design reviewer.

Single-phase pipeline:
  review_schema() — parse SQL DDL, check for design anti-patterns, return report

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_db_schema_analyzer.models import SchemaReview
from agent_db_schema_analyzer.tools.review_schema import review_schema as _review


class DbSchemaAnalyzerAgent:
    """Database schema design reviewer and anti-pattern detector.

    This agent parses SQL DDL statements, detects design anti-patterns,
    checks naming conventions, evaluates indexing strategy, and assesses
    normalization.

    Usage:
        agent = DbSchemaAnalyzerAgent()
        report = agent.review_schema("CREATE TABLE users (id INT PRIMARY KEY, ...);")
        print(report.tables_parsed, report.summary)
    """

    def review_schema(self, ddl_text: str, dialect: str = "generic") -> SchemaReview:
        """Review database schema DDL for design issues.

        Args:
            ddl_text: SQL DDL statements to review.
            dialect: SQL dialect hint (currently unused, reserved for future).

        Returns:
            SchemaReview with parsed tables, issues, and severity summary.
        """
        return _review(ddl_text, dialect)
