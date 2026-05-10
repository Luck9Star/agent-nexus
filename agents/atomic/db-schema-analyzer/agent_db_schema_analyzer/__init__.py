"""agent-db-schema-analyzer — Database schema design reviewer and anti-pattern detector.

Parses SQL DDL statements, detects design anti-patterns, checks naming
conventions, evaluates indexing strategy, and assesses normalization.
"""

from agent_db_schema_analyzer.agent import DbSchemaAnalyzerAgent
from agent_db_schema_analyzer.models import SchemaIssue, SchemaReview

__all__ = [
    "DbSchemaAnalyzerAgent",
    "SchemaIssue",
    "SchemaReview",
]
