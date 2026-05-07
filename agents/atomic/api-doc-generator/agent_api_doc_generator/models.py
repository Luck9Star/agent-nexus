"""Data models for api-doc-generator Agent.

Pydantic v2 frozen models for endpoint extraction, schema inference,
and OpenAPI 3.1 specification generation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EndpointInfo(BaseModel):
    """Information about a single API endpoint.

    Attributes:
        path: URL path (e.g. "/users/{id}").
        method: HTTP method (GET, POST, PUT, DELETE, PATCH).
        summary: Short summary of what the endpoint does.
        parameters: List of parameter names with their location.
        request_body: Description of the request body schema reference.
        responses: Mapping of status codes to response descriptions.
    """

    model_config = ConfigDict(frozen=True)

    path: str
    method: str = "GET"
    summary: str = ""
    parameters: list[dict[str, str]] = Field(default_factory=list)
    request_body: str = ""
    responses: dict[str, str] = Field(
        default_factory=lambda: {
            "200": "Successful response",
        }
    )


class SchemaInfo(BaseModel):
    """Inferred JSON Schema from type annotations."""

    model_config = ConfigDict(frozen=True, protected_namespaces=(), populate_by_name=True)

    name: str
    json_schema: dict = Field(default_factory=dict, alias="schema")
    required_fields: list[str] = Field(default_factory=list)


class OpenAPISpec(BaseModel):
    """OpenAPI 3.1 specification document.

    Attributes:
        openapi_version: OpenAPI specification version.
        info: API metadata (title, version, description).
        paths: API path definitions.
        components: Reusable schemas and other components.
    """

    model_config = ConfigDict(frozen=True)

    openapi_version: str = "3.1.0"
    info: dict = Field(
        default_factory=lambda: {
            "title": "API Documentation",
            "version": "1.0.0",
        }
    )
    paths: dict = Field(default_factory=dict)
    components: dict = Field(
        default_factory=lambda: {
            "schemas": {},
        }
    )
