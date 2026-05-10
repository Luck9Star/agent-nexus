"""MCP adapter — expose api-contract-tester as an MCP Server using FastMCP.

Provides two MCP tools:
- validate_contract: Validate an OpenAPI specification.
- generate_report: Compile findings into a structured report.
"""

from __future__ import annotations

from agent_api_contract_tester.tools.generate_report import (
    generate_report as _gen_report,
)
from agent_api_contract_tester.tools.validate_contract import (
    validate_contract as _validate,
)


def create_mcp_server() -> object:
    """Create and return a FastMCP server for api-contract-tester.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-api-contract-tester[full]

    Returns:
        A FastMCP server instance with validate_contract and generate_report tools.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-api-contract-tester[full]"
        ) from None

    mcp = FastMCP("api-contract-tester")

    @mcp.tool()
    def validate_contract(spec_content: str) -> dict:
        """Validate an OpenAPI specification for structural completeness.

        Checks required fields, schema references, and endpoint consistency.
        """
        result = _validate(spec_content)
        return result.model_dump()

    @mcp.tool()
    def generate_report(findings: list) -> dict:
        """Compile contract findings into a structured report.

        Aggregates by severity and generates remediation recommendations.
        """
        result = _gen_report(findings)
        return result.model_dump()

    return mcp
