"""Tests for db-schema-analyzer agent.

Covers:
- Models: construction, validation, immutability
- DDL parsing: CREATE TABLE, CREATE INDEX
- Schema checks: missing PK, naming, missing index, type issues
- Agent: full pipeline
- MCP adapter: server creation
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_db_schema_analyzer.agent import DbSchemaAnalyzerAgent
from agent_db_schema_analyzer.models import SchemaIssue, SchemaReview
from agent_db_schema_analyzer.tools.review_schema import (
    _parse_indexes,
    _parse_tables,
    review_schema,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SIMPLE_TABLE = """
CREATE TABLE users (
    id INT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(255) UNIQUE
);
"""

TABLE_WITH_FK = """
CREATE TABLE orders (
    id INT PRIMARY KEY,
    user_id INT REFERENCES users(id),
    total DECIMAL(10, 2) NOT NULL
);
"""

MISSING_PK_TABLE = """
CREATE TABLE logs (
    message TEXT,
    level VARCHAR(20)
);
"""

WIDE_TABLE = """
CREATE TABLE massive (
    id INT PRIMARY KEY,
    col1 VARCHAR(50), col2 VARCHAR(50), col3 VARCHAR(50),
    col4 VARCHAR(50), col5 VARCHAR(50), col6 VARCHAR(50),
    col7 VARCHAR(50), col8 VARCHAR(50), col9 VARCHAR(50),
    col10 VARCHAR(50), col11 VARCHAR(50), col12 VARCHAR(50),
    col13 VARCHAR(50), col14 VARCHAR(50), col15 VARCHAR(50),
    col16 VARCHAR(50)
);
"""

CAMEL_CASE_TABLE = """
CREATE TABLE UserProfiles (
    Id INT PRIMARY KEY,
    UserName VARCHAR(100),
    is_active INT
);
"""


@pytest.fixture
def agent() -> DbSchemaAnalyzerAgent:
    """Provide a DbSchemaAnalyzerAgent instance."""
    return DbSchemaAnalyzerAgent()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestSchemaIssue:
    """Tests for SchemaIssue model."""

    def test_basic(self) -> None:
        issue = SchemaIssue(severity="error", category="missing_pk")
        assert issue.severity == "error"
        assert issue.table == ""
        assert issue.column == ""

    def test_full(self) -> None:
        issue = SchemaIssue(
            severity="warning",
            category="missing_index",
            table="orders",
            column="user_id",
            message="FK column without index",
            suggestion="CREATE INDEX idx_orders_user_id ON orders(user_id)",
        )
        assert issue.table == "orders"
        assert issue.suggestion != ""

    def test_frozen(self) -> None:
        issue = SchemaIssue(severity="info", category="naming")
        with pytest.raises(ValidationError):
            issue.severity = "error"  # type: ignore[misc]


class TestSchemaReview:
    """Tests for SchemaReview model."""

    def test_empty(self) -> None:
        review = SchemaReview()
        assert review.tables_parsed == 0
        assert review.issues == []
        assert review.summary == {}

    def test_with_data(self) -> None:
        issues = [SchemaIssue(severity="error", category="test")]
        review = SchemaReview(tables_parsed=2, issues=issues, summary={"error": 1})
        assert review.tables_parsed == 2


# ---------------------------------------------------------------------------
# DDL parsing
# ---------------------------------------------------------------------------


class TestParseTables:
    """Tests for _parse_tables."""

    def test_simple_table(self) -> None:
        tables = _parse_tables(SIMPLE_TABLE)
        assert len(tables) == 1
        assert tables[0]["name"] == "users"
        assert len(tables[0]["columns"]) == 3
        assert "id" in tables[0]["primary_key"]

    def test_table_with_fk(self) -> None:
        tables = _parse_tables(TABLE_WITH_FK)
        assert len(tables) == 1
        assert len(tables[0]["foreign_keys"]) >= 1
        assert tables[0]["foreign_keys"][0]["column"] == "user_id"

    def test_missing_pk(self) -> None:
        tables = _parse_tables(MISSING_PK_TABLE)
        assert len(tables) == 1
        assert tables[0]["primary_key"] == []

    def test_no_tables(self) -> None:
        tables = _parse_tables("SELECT * FROM users;")
        assert tables == []


class TestParseIndexes:
    """Tests for _parse_indexes."""

    def test_simple_index(self) -> None:
        ddl = "CREATE INDEX idx_users_email ON users(email);"
        indexes = _parse_indexes(ddl)
        assert "users" in indexes
        assert "email" in indexes["users"]

    def test_composite_index(self) -> None:
        ddl = "CREATE INDEX idx_orders_user_date ON orders(user_id, created_at);"
        indexes = _parse_indexes(ddl)
        assert "orders" in indexes
        assert len(indexes["orders"]) == 2

    def test_no_indexes(self) -> None:
        indexes = _parse_indexes(SIMPLE_TABLE)
        assert indexes == {}


# ---------------------------------------------------------------------------
# review_schema integration
# ---------------------------------------------------------------------------


class TestReviewSchema:
    """Tests for review_schema tool."""

    def test_simple_valid_table(self) -> None:
        report = review_schema(SIMPLE_TABLE)
        assert report.tables_parsed == 1
        pk_issues = [i for i in report.issues if i.category == "missing_pk"]
        assert len(pk_issues) == 0

    def test_missing_pk(self) -> None:
        report = review_schema(MISSING_PK_TABLE)
        assert report.tables_parsed == 1
        categories = {i.category for i in report.issues}
        assert "missing_pk" in categories

    def test_missing_fk_index(self) -> None:
        report = review_schema(TABLE_WITH_FK)
        categories = {i.category for i in report.issues}
        assert "missing_index" in categories

    def test_wide_table_warning(self) -> None:
        report = review_schema(WIDE_TABLE)
        categories = {i.category for i in report.issues}
        assert "wide_table" in categories

    def test_naming_convention(self) -> None:
        report = review_schema(CAMEL_CASE_TABLE)
        naming_issues = [i for i in report.issues if i.category == "naming"]
        assert len(naming_issues) >= 1

    def test_summary_counts(self) -> None:
        report = review_schema(MISSING_PK_TABLE)
        assert "total" in report.summary
        assert report.summary["total"] == len(report.issues)

    def test_multiple_tables(self) -> None:
        ddl = SIMPLE_TABLE + TABLE_WITH_FK + MISSING_PK_TABLE
        report = review_schema(ddl)
        assert report.tables_parsed == 3

    def test_empty_ddl(self) -> None:
        report = review_schema("")
        assert report.tables_parsed == 0
        assert report.issues == []

    def test_with_index_no_fk_issue(self) -> None:
        ddl = """
        CREATE TABLE orders (
            id INT PRIMARY KEY,
            user_id INT REFERENCES users(id)
        );
        CREATE INDEX idx_orders_user_id ON orders(user_id);
        """
        report = review_schema(ddl)
        idx_issues = [i for i in report.issues if i.category == "missing_index"]
        assert len(idx_issues) == 0


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class TestDbSchemaAnalyzerAgent:
    """Tests for DbSchemaAnalyzerAgent class."""

    def test_review(self, agent: DbSchemaAnalyzerAgent) -> None:
        report = agent.review_schema(SIMPLE_TABLE)
        assert isinstance(report, SchemaReview)
        assert report.tables_parsed == 1

    def test_review_with_issues(self, agent: DbSchemaAnalyzerAgent) -> None:
        report = agent.review_schema(MISSING_PK_TABLE)
        assert len(report.issues) >= 1


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        try:
            from agent_db_schema_analyzer.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        import agent_db_schema_analyzer.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")
