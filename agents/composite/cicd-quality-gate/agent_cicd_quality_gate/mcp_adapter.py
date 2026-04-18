"""MCP adapter -- expose cicd-quality-gate as an MCP Server using FastMCP.

Provides one MCP tool:
- run_gate: Execute the quality gate pipeline for a code path.
"""

from __future__ import annotations

from typing import Any

from agent_cicd_quality_gate.coordinator import QualityGateCoordinator


def create_mcp_server() -> object:
    """Create and return a FastMCP server for cicd-quality-gate.

    Requires the ``fastmcp`` package to be installed.

    Returns:
        A FastMCP server instance with run_gate tool.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-cicd-quality-gate[full]"
        )

    mcp = FastMCP("cicd-quality-gate")
    coordinator = QualityGateCoordinator()

    @mcp.tool()
    def run_gate(code_path: str, config: dict[str, Any] | None = None) -> dict:
        """Run the quality gate pipeline for a code path.

        Executes security scanning, code review, and test generation in parallel,
        then makes a pass/fail decision based on configurable thresholds.
        """
        result = coordinator.run_gate(code_path, config)
        return result.model_dump()

    return mcp
