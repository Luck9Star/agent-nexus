"""MCP adapter — expose data-pipeline-validator as an MCP Server using FastMCP.

Provides two MCP tools:
- validate_pipeline: Validate an ETL pipeline configuration.
- generate_report: Compile findings into a structured report.
"""

from __future__ import annotations

from agent_data_pipeline_validator.tools.generate_report import (
    generate_report as _gen_report,
)
from agent_data_pipeline_validator.tools.validate_pipeline import (
    validate_pipeline as _validate,
)


def create_mcp_server() -> object:
    """Create and return a FastMCP server for data-pipeline-validator.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-data-pipeline-validator[full]

    Returns:
        A FastMCP server instance with validate_pipeline and generate_report tools.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-data-pipeline-validator[full]"
        ) from None

    mcp = FastMCP("data-pipeline-validator")

    @mcp.tool()
    def validate_pipeline(config: str) -> dict:
        """Validate an ETL pipeline configuration for completeness.

        Checks structure, source/target, step definitions, and error handling.
        """
        result = _validate(config)
        return result.model_dump()

    @mcp.tool()
    def generate_report(findings: list) -> dict:
        """Compile pipeline findings into a structured report.

        Aggregates by severity and generates remediation recommendations.
        """
        result = _gen_report(findings)
        return result.model_dump()

    return mcp
