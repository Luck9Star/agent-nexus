"""OpenAPI spec generation tool -- assemble OpenAPI 3.1 document.

Takes extracted endpoints and inferred schemas, and assembles a
complete OpenAPI 3.1.0 specification.
"""

from __future__ import annotations

from agent_api_doc_generator.models import EndpointInfo, OpenAPISpec, SchemaInfo


def _endpoint_to_path_item(endpoint: EndpointInfo) -> dict:
    """Convert an EndpointInfo to an OpenAPI path item operation."""
    operation: dict = {
        "summary": endpoint.summary,
        "responses": {},
    }

    # Add parameters
    if endpoint.parameters:
        operation["parameters"] = endpoint.parameters

    # Add request body
    if endpoint.request_body:
        operation["requestBody"] = {
            "content": {
                "application/json": {
                    "schema": {"$ref": f"#/components/schemas/{endpoint.request_body}"},
                }
            }
        }

    # Add responses
    for status_code, description in endpoint.responses.items():
        operation["responses"][status_code] = {
            "description": description,
        }

    return operation


def _build_paths(endpoints: list[EndpointInfo]) -> dict:
    """Build the OpenAPI paths object from endpoint list."""
    paths: dict = {}

    for endpoint in endpoints:
        path = endpoint.path
        if path not in paths:
            paths[path] = {}

        method = endpoint.method.lower()
        if method == "all":
            # RequestMapping without specific method -- add all common methods
            for m in ("get", "post", "put", "delete"):
                paths[path][m] = _endpoint_to_path_item(endpoint)
        else:
            paths[path][method] = _endpoint_to_path_item(endpoint)

    return paths


def _build_components(schemas: list[SchemaInfo]) -> dict:
    """Build the OpenAPI components/schemas object."""
    components: dict = {"schemas": {}}

    for schema in schemas:
        if schema.name and schema.json_schema:
            components["schemas"][schema.name] = schema.json_schema

    return components


def generate_openapi(
    endpoints: list[EndpointInfo],
    info: dict | None = None,
    schemas: list[SchemaInfo] | None = None,
) -> OpenAPISpec:
    """Generate an OpenAPI 3.1 specification document.

    Assembles endpoints, schemas, and metadata into a complete
    OpenAPI 3.1.0 specification.

    Args:
        endpoints: List of extracted API endpoints.
        info: API metadata dict with keys like title, version, description.
            Defaults to a generic API documentation info block.
        schemas: Optional list of SchemaInfo objects for component schemas.

    Returns:
        OpenAPISpec with the complete OpenAPI 3.1.0 document.
    """
    if info is None:
        info = {
            "title": "API Documentation",
            "version": "1.0.0",
        }

    paths = _build_paths(endpoints)

    components = _build_components(schemas) if schemas else {"schemas": {}}

    return OpenAPISpec(
        openapi_version="3.1.0",
        info=info,
        paths=paths,
        components=components,
    )
