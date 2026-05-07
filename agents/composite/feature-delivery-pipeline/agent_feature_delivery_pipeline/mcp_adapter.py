"""MCP adapter -- expose feature-delivery-pipeline as an MCP Server using FastMCP.

Provides one MCP tool:
- run_pipeline: Execute the full feature delivery pipeline for a specification.
"""

from __future__ import annotations

from agent_feature_delivery_pipeline.coordinator import FeatureDeliveryCoordinator


def create_mcp_server() -> object:
    """Create and return a FastMCP server for feature-delivery-pipeline.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-feature-delivery-pipeline[full]

    Returns:
        A FastMCP server instance with run_pipeline tool.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-feature-delivery-pipeline[full]"
        ) from None

    mcp = FastMCP("feature-delivery-pipeline")
    coordinator = FeatureDeliveryCoordinator()

    @mcp.tool()
    async def run_pipeline(spec: str) -> dict:
        """Execute the feature delivery pipeline for a requirement specification.

        Runs requirements analysis, then parallel API doc generation,
        test suite generation, and code review. Returns aggregated results.
        """
        result = await coordinator.run_pipeline_async(spec)
        return result.model_dump()

    return mcp
