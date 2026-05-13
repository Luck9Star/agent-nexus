"""MCP adapter -- expose code-reviewer as an MCP Server using FastMCP.

Provides three MCP tools:
- analyze_code: Static analysis of a code file.
- check_patterns: Anti-pattern detection in code.
- generate_review: Compile findings into a review report.
"""

from __future__ import annotations

import json

from agent_code_reviewer.models import CodeAnalysis
from agent_code_reviewer.tools.analyze_code import analyze_code as _analyze
from agent_code_reviewer.tools.check_patterns import check_patterns as _check
from agent_code_reviewer.tools.generate_review import generate_review as _review


def create_mcp_server() -> object:
    """Create and return a FastMCP server for code-reviewer.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-code-reviewer[full]

    Returns:
        A FastMCP server instance with analyze_code, check_patterns,
        and generate_review tools.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. Install with: pip install agent-code-reviewer[full]"
        ) from None

    mcp = FastMCP("code-reviewer")

    @mcp.tool()
    def analyze_code(file_path: str, language: str = "") -> dict:
        """Analyze a code file for quality issues and metrics.

        Performs static analysis using language-specific rules to detect
        bugs, security issues, style violations, and calculate code metrics.
        """
        result = _analyze(file_path, language)
        return result.model_dump()

    @mcp.tool()
    def check_patterns(code: str, language: str = "") -> dict:
        """Detect anti-patterns in code including security and performance issues.

        Scans for SQL injection, hardcoded secrets, N+1 queries, deep nesting,
        empty catch blocks, and other common anti-patterns.
        """
        patterns = _check(code, language)
        return {
            "patterns": [p.model_dump() for p in patterns],
        }

    @mcp.tool()
    def generate_review(analysis: str, patterns: str | None = None) -> dict:
        """Generate a structured review report from analysis results.

        Compiles findings into a report with severity counts, suggestions,
        and an overall quality score (0-100).
        """
        # MCP inputSchema: str avoids ambiguous anyOf.
        # Accept JSON strings, parse internally.
        parsed_analysis = CodeAnalysis.model_validate(json.loads(analysis))
        parsed_patterns = None
        if patterns:
            from agent_code_reviewer.models import PatternMatch

            parsed_patterns = [PatternMatch.model_validate(p) for p in json.loads(patterns)]
        result = _review(parsed_analysis, parsed_patterns)
        return result.model_dump()

    return mcp
