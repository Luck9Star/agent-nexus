"""MCP adapter — expose security-scanner as an MCP Server using FastMCP.

Provides three MCP tools:
- scan_code: Scan a file or directory for security vulnerabilities.
- check_dependencies: Check project dependencies for known CVEs.
- generate_report: Compile findings into a structured security report.
"""

from __future__ import annotations

from agent_security_scanner.tools.check_dependencies import (
    check_dependencies as _check_deps,
)
from agent_security_scanner.tools.generate_report import (
    generate_report as _gen_report,
)
from agent_security_scanner.tools.scan_code import scan_code as _scan_code


def create_mcp_server() -> object:
    """Create and return a FastMCP server for security-scanner.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-security-scanner[full]

    Returns:
        A FastMCP server instance with scan_code, check_dependencies,
        and generate_report tools.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-security-scanner[full]"
        )

    mcp = FastMCP("security-scanner")

    @mcp.tool()
    def scan_code(file_path: str) -> dict:
        """Scan a file or directory for security vulnerabilities.

        Detects OWASP Top 10 vulnerability patterns including SQL injection,
        XSS, path traversal, command injection, and hardcoded credentials.
        """
        result = _scan_code(file_path)
        return result.model_dump()

    @mcp.tool()
    def check_dependencies(deps: dict) -> dict:
        """Check project dependencies for known CVEs.

        Compares declared versions against a built-in vulnerability database.
        """
        result = _check_deps(deps)
        return result.model_dump()

    @mcp.tool()
    def generate_report(findings: list) -> dict:
        """Compile security findings into a structured report.

        Aggregates by severity and generates prioritized remediation recommendations.
        """
        result = _gen_report(findings)
        return result.model_dump()

    return mcp
