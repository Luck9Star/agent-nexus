"""Schema review tool — parse SQL DDL and check for design anti-patterns.

Parses CREATE TABLE and CREATE INDEX statements using regex. Checks for:
- Missing primary keys
- Naming convention violations
- Missing indexes on foreign keys
- Wide tables
- Type choice issues
- Normalization concerns
"""

from __future__ import annotations

import re

from agent_db_schema_analyzer.models import SchemaIssue, SchemaReview


def _extract_balanced_parens(text: str, start: int) -> tuple[str, int]:
    """Extract content within balanced parentheses starting at position start.

    Args:
        text: Full text.
        start: Index of the opening '('.

    Returns:
        Tuple of (content_between_parens, end_index).
    """
    depth = 0
    i = start
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
        i += 1
    return text[start + 1 :], len(text)


def _parse_tables(ddl: str) -> list[dict]:
    """Parse CREATE TABLE statements into structured data.

    Args:
        ddl: SQL DDL text.

    Returns:
        List of dicts with table_name, columns, primary_key, foreign_keys, indexes.
    """
    tables: list[dict] = []
    # Match CREATE TABLE header (name only, then extract body with balanced parens)
    table_header = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"']?(\w+)[\"']?\s*\(",
        re.IGNORECASE,
    )

    for match in table_header.finditer(ddl):
        table_name = match.group(1)
        paren_start = match.end() - 1  # position of '('
        body, _end = _extract_balanced_parens(ddl, paren_start)

        columns: list[dict] = []
        primary_key: list[str] = []
        foreign_keys: list[dict] = []

        # Split by commas, but handle nested parentheses
        parts = _split_columns(body)

        for part in parts:
            part = part.strip()
            if not part:
                continue

            # PRIMARY KEY constraint
            pk_match = re.match(r"PRIMARY\s+KEY\s*\(([^)]+)\)", part, re.IGNORECASE)
            if pk_match:
                pk_cols = [c.strip().strip('"').strip("'") for c in pk_match.group(1).split(",")]
                primary_key.extend(pk_cols)
                continue

            # FOREIGN KEY constraint
            fk_match = re.match(
                r"(?:CONSTRAINT\s+\w+\s+)?FOREIGN\s+KEY\s*\((\w+)\)\s*"
                r"REFERENCES\s+(\w+)\s*\((\w+)\)",
                part,
                re.IGNORECASE,
            )
            if fk_match:
                foreign_keys.append(
                    {
                        "column": fk_match.group(1),
                        "ref_table": fk_match.group(2),
                        "ref_column": fk_match.group(3),
                    }
                )
                continue

            # Column definition
            col_match = re.match(
                r"[\"']?(\w+)[\"']?\s+([A-Z]+(?:\([^)]*\))?)"
                r"(.*)",
                part,
                re.IGNORECASE,
            )
            if col_match:
                col_name = col_match.group(1)
                col_type = col_match.group(2).upper()
                rest = col_match.group(3).upper()

                is_pk = "PRIMARY KEY" in rest
                is_fk = "REFERENCES" in rest
                is_unique = "UNIQUE" in rest
                is_not_null = "NOT NULL" in rest

                if is_pk:
                    primary_key.append(col_name)

                # Extract inline REFERENCES for foreign_keys list
                if is_fk:
                    fk_inline = re.match(
                        r".*REFERENCES\s+(\w+)\s*\((\w+)\)",
                        rest,
                        re.IGNORECASE,
                    )
                    if fk_inline:
                        foreign_keys.append(
                            {
                                "column": col_name,
                                "ref_table": fk_inline.group(1),
                                "ref_column": fk_inline.group(2),
                            }
                        )

                columns.append(
                    {
                        "name": col_name,
                        "type": col_type,
                        "is_pk": is_pk,
                        "is_fk": is_fk,
                        "is_unique": is_unique,
                        "is_not_null": is_not_null,
                    }
                )

        tables.append(
            {
                "name": table_name,
                "columns": columns,
                "primary_key": primary_key,
                "foreign_keys": foreign_keys,
            }
        )

    return tables


def _split_columns(body: str) -> list[str]:
    """Split column definitions by commas, respecting parentheses nesting.

    Args:
        body: The body of a CREATE TABLE statement.

    Returns:
        List of column definition strings.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in body:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        parts.append("".join(current))
    return parts


def _parse_indexes(ddl: str) -> dict[str, list[str]]:
    """Parse CREATE INDEX statements.

    Args:
        ddl: SQL DDL text.

    Returns:
        Mapping of table_name -> list of indexed column names.
    """
    indexes: dict[str, list[str]] = {}
    idx_pattern = re.compile(
        r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+"
        r"ON\s+(\w+)\s*\(([^)]+)\)",
        re.IGNORECASE,
    )
    for match in idx_pattern.finditer(ddl):
        table = match.group(2)
        cols = [c.strip().strip('"').strip("'").split()[0] for c in match.group(3).split(",")]
        if table not in indexes:
            indexes[table] = []
        indexes[table].extend(cols)

    return indexes


def _check_schema(tables: list[dict], indexes: dict[str, list[str]]) -> list[SchemaIssue]:
    """Run all schema checks and return issues.

    Args:
        tables: Parsed table structures.
        indexes: Parsed index structures.

    Returns:
        List of SchemaIssue instances.
    """
    issues: list[SchemaIssue] = []

    for table in tables:
        tname = table["name"]
        cols = table["columns"]
        pk = table["primary_key"]
        fks = table["foreign_keys"]
        table_indexes = indexes.get(tname, [])

        # 1. Missing primary key
        if not pk:
            issues.append(
                SchemaIssue(
                    severity="error",
                    category="missing_pk",
                    table=tname,
                    message=f"Table '{tname}' has no primary key",
                    suggestion=f"Add a primary key: ALTER TABLE {tname} ADD PRIMARY KEY (...)",
                )
            )

        # 2. Wide table check (> 15 columns is a smell)
        if len(cols) > 15:
            issues.append(
                SchemaIssue(
                    severity="warning",
                    category="wide_table",
                    table=tname,
                    message=f"Table '{tname}' has {len(cols)} columns — consider splitting",
                    suggestion="Review for normalization opportunities (1NF/2NF/3NF)",
                )
            )

        # 3. Naming convention: check for non-snake_case
        if tname != tname.lower():
            issues.append(
                SchemaIssue(
                    severity="info",
                    category="naming",
                    table=tname,
                    message=f"Table name '{tname}' should use snake_case",
                    suggestion=f"Rename to '{tname.lower()}'",
                )
            )

        for col in cols:
            cname = col["name"]
            ctype = col["type"]

            # Column naming
            if cname != cname.lower():
                issues.append(
                    SchemaIssue(
                        severity="info",
                        category="naming",
                        table=tname,
                        column=cname,
                        message=f"Column '{cname}' in '{tname}' should use snake_case",
                        suggestion=f"Rename to '{cname.lower()}'",
                    )
                )

            # UUID stored as VARCHAR
            if "UUID" in cname.upper() and "VARCHAR" in ctype:
                issues.append(
                    SchemaIssue(
                        severity="info",
                        category="type_choice",
                        table=tname,
                        column=cname,
                        message=f"UUID column '{cname}' uses VARCHAR — consider native UUID type",
                        suggestion="Use UUID type instead of VARCHAR for better performance",
                    )
                )

            # Boolean stored as INT
            if cname.startswith("is_") and "INT" in ctype and "BIGINT" not in ctype:
                issues.append(
                    SchemaIssue(
                        severity="info",
                        category="type_choice",
                        table=tname,
                        column=cname,
                        message=f"Boolean column '{cname}' uses INTEGER — consider BOOLEAN type",
                        suggestion="Use BOOLEAN type for is_* columns",
                    )
                )

        # 4. Missing indexes on foreign key columns
        for fk in fks:
            fk_col = fk["column"]
            if fk_col not in table_indexes and fk_col not in pk:
                issues.append(
                    SchemaIssue(
                        severity="warning",
                        category="missing_index",
                        table=tname,
                        column=fk_col,
                        message=(f"Foreign key column '{fk_col}' in '{tname}' has no index"),
                        suggestion=(f"CREATE INDEX idx_{tname}_{fk_col} ON {tname}({fk_col})"),
                    )
                )

        # 5. Check for TEXT/BLOB overuse
        text_cols = [c for c in cols if any(t in c["type"] for t in ["TEXT", "BLOB", "BYTEA"])]
        if len(text_cols) > 3:
            issues.append(
                SchemaIssue(
                    severity="warning",
                    category="type_overuse",
                    table=tname,
                    message=(f"Table '{tname}' has {len(text_cols)} TEXT/BLOB columns"),
                    suggestion="Consider whether VARCHAR(n) is sufficient for some columns",
                )
            )

        # 6. Missing timestamp columns
        col_names_lower = {c["name"].lower() for c in cols}
        has_created = any("created" in n for n in col_names_lower)
        has_updated = any("updated" in n or "modified" in n for n in col_names_lower)
        if not has_created and len(cols) > 3:
            issues.append(
                SchemaIssue(
                    severity="info",
                    category="missing_timestamp",
                    table=tname,
                    message=f"Table '{tname}' has no created_at column",
                    suggestion="Add created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
                )
            )
        if not has_updated and len(cols) > 3:
            issues.append(
                SchemaIssue(
                    severity="info",
                    category="missing_timestamp",
                    table=tname,
                    message=f"Table '{tname}' has no updated_at column",
                    suggestion="Add updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
                )
            )

    return issues


def _build_summary(issues: list[SchemaIssue]) -> dict[str, int]:
    """Build severity count summary.

    Args:
        issues: List of schema issues.

    Returns:
        Dict with counts per severity level.
    """
    counts: dict[str, int] = {}
    for issue in issues:
        counts[issue.severity] = counts.get(issue.severity, 0) + 1
    counts["total"] = len(issues)
    return counts


def review_schema(ddl_text: str, dialect: str = "generic") -> SchemaReview:
    """Review database schema DDL for design issues.

    Parses CREATE TABLE and CREATE INDEX statements, then checks for
    missing primary keys, naming violations, missing indexes, type issues,
    and normalization concerns.

    Args:
        ddl_text: SQL DDL statements to review.
        dialect: SQL dialect hint (currently unused, reserved for future).

    Returns:
        SchemaReview with parsed tables, issues, and severity summary.
    """
    tables = _parse_tables(ddl_text)
    indexes = _parse_indexes(ddl_text)
    issues = _check_schema(tables, indexes)
    summary = _build_summary(issues)

    return SchemaReview(
        tables_parsed=len(tables),
        issues=issues,
        summary=summary,
    )
