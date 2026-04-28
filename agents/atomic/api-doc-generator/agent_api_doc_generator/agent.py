"""APIDocGeneratorAgent -- API 文档生成专家。

Three-phase pipeline:
  1. extract()   -- parse code for API route endpoints
  2. infer()     -- infer JSON Schema from type annotations
  3. generate()  -- assemble OpenAPI 3.1 specification

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_api_doc_generator.models import EndpointInfo, OpenAPISpec, SchemaInfo
from agent_api_doc_generator.tools.extract_endpoints import extract_endpoints
from agent_api_doc_generator.tools.generate_openapi import generate_openapi
from agent_api_doc_generator.tools.infer_schema import infer_schema


class APIDocGeneratorAgent:
    """API 文档生成专家。

    This agent provides a three-phase pipeline for API documentation:
    Phase 1 (extract) parses source code to identify API endpoints.
    Phase 2 (infer) converts type annotations to JSON Schema.
    Phase 3 (generate) assembles everything into an OpenAPI 3.1 spec.

    Usage:
        agent = APIDocGeneratorAgent()
        endpoints = agent.extract("/path/to/api.py")
        schema = agent.infer("class User:\\n    name: str\\n    age: int")
        spec = agent.generate(endpoints, info={"title": "My API"})
        print(spec.openapi_version, spec.paths)
    """

    def extract(self, file_path: str) -> list[EndpointInfo]:
        """Phase 1: Extract API endpoints from a source code file.

        Args:
            file_path: Path to the source code file.

        Returns:
            List of EndpointInfo for each detected route.
        """
        return extract_endpoints(file_path)

    def infer(self, type_info: str) -> SchemaInfo:
        """Phase 2: Infer JSON Schema from type annotations.

        Args:
            type_info: Type annotation text (Python class or TypeScript interface).

        Returns:
            SchemaInfo with inferred JSON Schema.
        """
        return infer_schema(type_info)

    def generate(
        self,
        endpoints: list[EndpointInfo],
        info: dict | None = None,
        schemas: list[SchemaInfo] | None = None,
    ) -> OpenAPISpec:
        """Phase 3: Generate OpenAPI 3.1 specification.

        Args:
            endpoints: List of API endpoints.
            info: API metadata (title, version, description).
            schemas: Optional schema definitions for components.

        Returns:
            OpenAPISpec with the complete OpenAPI document.
        """
        return generate_openapi(endpoints, info, schemas)
