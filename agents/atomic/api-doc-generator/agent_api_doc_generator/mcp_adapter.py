"""MCP adapter -- expose api-doc-generator as an MCP Server using FastMCP.

Provides three MCP tools:
- extract_endpoints: Parse code file for API route definitions.
- infer_schema: Convert type annotations to JSON Schema.
- generate_openapi: Assemble OpenAPI 3.1 specification.
"""

from __future__ import annotations

from agent_api_doc_generator.models import EndpointInfo, SchemaInfo
from agent_api_doc_generator.tools.extract_endpoints import (
    extract_endpoints as _extract,
)
from agent_api_doc_generator.tools.generate_openapi import (
    generate_openapi as _generate,
)
from agent_api_doc_generator.tools.infer_schema import (
    infer_schema as _infer,
)


def create_mcp_server() -> object:
    """Create and return a FastMCP server for api-doc-generator.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-api-doc-generator[full]

    Returns:
        A FastMCP server instance with extract_endpoints, infer_schema,
        and generate_openapi tools.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-api-doc-generator[full]"
        )

    mcp = FastMCP("api-doc-generator")

    @mcp.tool()
    def extract_endpoints(file_path: str) -> dict:
        """Extract API endpoint definitions from a source code file.

        Detects the web framework and scans for route definitions.
        Supports FastAPI, Flask, Express, and Spring Boot.
        """
        endpoints = _extract(file_path)
        return {
            "endpoints": [e.model_dump() for e in endpoints],
        }

    @mcp.tool()
    def infer_schema(type_info: str) -> dict:
        """Infer JSON Schema from type annotations.

        Supports Python class definitions and TypeScript interfaces.
        """
        result = _infer(type_info)
        return result.model_dump()

    @mcp.tool()
    def generate_openapi(
        endpoints: list[dict],
        info: dict | None = None,
        schemas: list[dict] | None = None,
    ) -> dict:
        """Generate an OpenAPI 3.1 specification document.

        Assembles endpoints and schemas into a complete OpenAPI spec.
        """
        parsed_endpoints = [EndpointInfo.model_validate(e) for e in endpoints]
        parsed_schemas = None
        if schemas:
            parsed_schemas = [SchemaInfo.model_validate(s) for s in schemas]
        result = _generate(parsed_endpoints, info, parsed_schemas)
        return result.model_dump()

    return mcp
