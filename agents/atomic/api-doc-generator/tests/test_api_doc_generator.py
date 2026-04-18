"""Comprehensive tests for api-doc-generator agent.

Covers:
- Models: construction, validation, serialization, immutability
- extract_endpoints: FastAPI, Flask, Express, Spring route detection
- infer_schema: Python and TypeScript type inference
- generate_openapi: path assembly, components, spec structure
- Agent: three-phase pipeline
- MCP adapter: server creation
- Local adapter: message dispatch
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from agent_api_doc_generator.agent import APIDocGeneratorAgent
from agent_api_doc_generator.local_adapter import handle_message
from agent_api_doc_generator.models import EndpointInfo, OpenAPISpec, SchemaInfo
from agent_api_doc_generator.tools.extract_endpoints import (
    _detect_framework,
    _extract_path_params,
    extract_endpoints,
)
from agent_api_doc_generator.tools.generate_openapi import generate_openapi
from agent_api_doc_generator.tools.infer_schema import infer_schema


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    """Provide a temporary directory."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def agent() -> APIDocGeneratorAgent:
    """Provide an APIDocGeneratorAgent instance."""
    return APIDocGeneratorAgent()


FASTAPI_CODE = '''from fastapi import FastAPI

app = FastAPI()

@app.get("/users")
def list_users():
    pass

@app.post("/users")
def create_user():
    pass

@app.get("/users/{user_id}")
def get_user(user_id: int):
    pass

@router.put("/users/{user_id}")
def update_user(user_id: int):
    pass
'''

FLASK_CODE = '''from flask import Flask

app = Flask(__name__)

@app.route("/api/items", methods=["GET"])
def list_items():
    pass

@app.route("/api/items", methods=["POST"])
def create_item():
    pass

@app.route("/api/items/<item_id>", methods=["GET"])
def get_item(item_id):
    pass
'''

EXPRESS_CODE = '''const express = require("express");
const router = express.Router();

router.get("/products", (req, res) => {});
router.post("/products", (req, res) => {});
router.delete("/products/{id}", (req, res) => {});
'''

SPRING_CODE = '''@RestController
@RequestMapping("/api")
class UserController {
    @GetMapping("/users")
    List<User> listUsers() {}

    @PostMapping("/users")
    User createUser() {}

    @GetMapping("/users/{id}")
    User getUser(@PathVariable Long id) {}
}
'''

PYTHON_TYPES = '''class User:
    name: str
    age: int
    email: Optional[str]
    is_active: bool
    tags: list
'''

TYPESCRIPT_TYPES = '''interface Product {
    name: string;
    price: number;
    inStock: boolean;
    description?: string;
}
'''


# ---------------------------------------------------------------------------
# Models -- construction, validation, serialization
# ---------------------------------------------------------------------------


class TestEndpointInfo:
    """Tests for EndpointInfo model."""

    def test_basic_construction(self) -> None:
        e = EndpointInfo(path="/users")
        assert e.path == "/users"
        assert e.method == "GET"
        assert e.parameters == []
        assert e.responses == {"200": "Successful response"}

    def test_full_construction(self) -> None:
        e = EndpointInfo(
            path="/users/{id}",
            method="POST",
            summary="Create user",
            parameters=[{"name": "id", "in": "path"}],
            request_body="UserCreate",
            responses={"201": "Created", "400": "Bad request"},
        )
        assert e.method == "POST"
        assert e.request_body == "UserCreate"

    def test_frozen(self) -> None:
        e = EndpointInfo(path="/test")
        with pytest.raises(Exception):
            e.path = "/changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        e = EndpointInfo(path="/x", method="DELETE")
        data = e.model_dump()
        e2 = EndpointInfo.model_validate(data)
        assert e == e2

    def test_json_serialization(self) -> None:
        e = EndpointInfo(path="/test", method="PUT")
        json_str = e.model_dump_json()
        data = json.loads(json_str)
        assert data["method"] == "PUT"


class TestSchemaInfo:
    """Tests for SchemaInfo model."""

    def test_basic_construction(self) -> None:
        s = SchemaInfo(name="User")
        assert s.name == "User"
        assert s.schema == {}
        assert s.required_fields == []

    def test_with_schema(self) -> None:
        s = SchemaInfo(
            name="User",
            schema={"type": "object", "properties": {"name": {"type": "string"}}},
            required_fields=["name"],
        )
        assert s.schema["type"] == "object"
        assert "name" in s.required_fields

    def test_frozen(self) -> None:
        s = SchemaInfo(name="test")
        with pytest.raises(Exception):
            s.name = "changed"  # type: ignore[misc]


class TestOpenAPISpec:
    """Tests for OpenAPISpec model."""

    def test_default_construction(self) -> None:
        spec = OpenAPISpec()
        assert spec.openapi_version == "3.1.0"
        assert spec.info == {"title": "API Documentation", "version": "1.0.0"}
        assert spec.paths == {}
        assert spec.components == {"schemas": {}}

    def test_frozen(self) -> None:
        spec = OpenAPISpec()
        with pytest.raises(Exception):
            spec.openapi_version = "2.0"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        spec = OpenAPISpec(
            info={"title": "Test API", "version": "2.0.0"},
            paths={"/users": {"get": {"summary": "List users"}}},
        )
        data = spec.model_dump()
        spec2 = OpenAPISpec.model_validate(data)
        assert spec == spec2


# ---------------------------------------------------------------------------
# extract_endpoints -- route detection
# ---------------------------------------------------------------------------


class TestExtractPathParams:
    """Tests for _extract_path_params helper."""

    def test_no_params(self) -> None:
        assert _extract_path_params("/users") == []

    def test_single_param(self) -> None:
        params = _extract_path_params("/users/{id}")
        assert len(params) == 1
        assert params[0]["name"] == "id"

    def test_multiple_params(self) -> None:
        params = _extract_path_params("/orgs/{org_id}/users/{user_id}")
        assert len(params) == 2
        assert params[0]["name"] == "org_id"
        assert params[1]["name"] == "user_id"


class TestDetectFramework:
    """Tests for _detect_framework helper."""

    def test_fastapi(self) -> None:
        assert _detect_framework("from fastapi import FastAPI") == "fastapi"

    def test_flask(self) -> None:
        assert _detect_framework("@app.route('/test')") == "flask"

    def test_spring(self) -> None:
        assert _detect_framework("@GetMapping('/test')") == "spring"

    def test_express(self) -> None:
        assert _detect_framework("router.get('/test')") == "express"

    def test_unknown(self) -> None:
        assert _detect_framework("hello world") == "unknown"


class TestExtractEndpoints:
    """Tests for extract_endpoints tool."""

    def test_file_not_found(self) -> None:
        result = extract_endpoints("/nonexistent/file.py")
        assert result == []

    def test_empty_file(self, tmp_dir: str) -> None:
        path = os.path.join(tmp_dir, "empty.py")
        Path(path).write_text("")
        result = extract_endpoints(path)
        assert result == []

    def test_fastapi_endpoints(self, tmp_dir: str) -> None:
        path = os.path.join(tmp_dir, "api.py")
        Path(path).write_text(FASTAPI_CODE)
        endpoints = extract_endpoints(path)
        assert len(endpoints) >= 4

        paths = {e.path for e in endpoints}
        assert "/users" in paths
        assert "/users/{user_id}" in paths

        methods = {e.method for e in endpoints}
        assert "GET" in methods
        assert "POST" in methods
        assert "PUT" in methods

    def test_fastapi_path_params(self, tmp_dir: str) -> None:
        path = os.path.join(tmp_dir, "api.py")
        Path(path).write_text(FASTAPI_CODE)
        endpoints = extract_endpoints(path)
        detail = [e for e in endpoints if "{user_id}" in e.path]
        assert len(detail) > 0
        assert any(p["name"] == "user_id" for p in detail[0].parameters)

    def test_flask_endpoints(self, tmp_dir: str) -> None:
        path = os.path.join(tmp_dir, "app.py")
        Path(path).write_text(FLASK_CODE)
        endpoints = extract_endpoints(path)
        assert len(endpoints) >= 3

        paths = {e.path for e in endpoints}
        assert "/api/items" in paths

    def test_express_endpoints(self, tmp_dir: str) -> None:
        path = os.path.join(tmp_dir, "routes.js")
        Path(path).write_text(EXPRESS_CODE)
        endpoints = extract_endpoints(path)
        assert len(endpoints) >= 3

    def test_spring_endpoints(self, tmp_dir: str) -> None:
        path = os.path.join(tmp_dir, "Controller.java")
        Path(path).write_text(SPRING_CODE)
        endpoints = extract_endpoints(path)
        assert len(endpoints) >= 3

    def test_no_routes_in_plain_code(self, tmp_dir: str) -> None:
        path = os.path.join(tmp_dir, "plain.py")
        Path(path).write_text("def add(a, b):\n    return a + b\n")
        endpoints = extract_endpoints(path)
        assert endpoints == []


# ---------------------------------------------------------------------------
# infer_schema -- type annotation inference
# ---------------------------------------------------------------------------


class TestInferSchema:
    """Tests for infer_schema tool."""

    def test_empty_input(self) -> None:
        result = infer_schema("")
        assert result.name == "Empty"

    def test_python_class(self) -> None:
        result = infer_schema(PYTHON_TYPES)
        assert result.name == "User"
        props = result.schema.get("properties", {})
        assert "name" in props
        assert props["name"]["type"] == "string"
        assert props["age"]["type"] == "integer"
        assert props["is_active"]["type"] == "boolean"

    def test_python_required_fields(self) -> None:
        result = infer_schema(PYTHON_TYPES)
        assert "name" in result.required_fields
        assert "age" in result.required_fields
        assert "email" not in result.required_fields

    def test_python_optional_nullable(self) -> None:
        result = infer_schema(PYTHON_TYPES)
        props = result.schema.get("properties", {})
        assert props["email"].get("nullable") is True

    def test_typescript_interface(self) -> None:
        result = infer_schema(TYPESCRIPT_TYPES)
        assert result.name == "Product"
        props = result.schema.get("properties", {})
        assert props["name"]["type"] == "string"
        assert props["price"]["type"] == "number"

    def test_typescript_required(self) -> None:
        result = infer_schema(TYPESCRIPT_TYPES)
        assert "name" in result.required_fields
        assert "price" in result.required_fields
        assert "description" not in result.required_fields

    def test_simple_type(self) -> None:
        result = infer_schema("str")
        assert result.schema["type"] == "string"

    def test_unknown_class(self) -> None:
        code = "class Foo:\n    x: custom_type"
        result = infer_schema(code)
        assert result.name == "Foo"


# ---------------------------------------------------------------------------
# generate_openapi -- spec assembly
# ---------------------------------------------------------------------------


class TestGenerateOpenAPI:
    """Tests for generate_openapi tool."""

    def test_empty_endpoints(self) -> None:
        spec = generate_openapi([])
        assert isinstance(spec, OpenAPISpec)
        assert spec.paths == {}
        assert spec.openapi_version == "3.1.0"

    def test_single_endpoint(self) -> None:
        endpoints = [EndpointInfo(path="/users", method="GET", summary="List users")]
        spec = generate_openapi(endpoints)
        assert "/users" in spec.paths
        assert "get" in spec.paths["/users"]

    def test_multiple_endpoints_same_path(self) -> None:
        endpoints = [
            EndpointInfo(path="/users", method="GET"),
            EndpointInfo(path="/users", method="POST"),
        ]
        spec = generate_openapi(endpoints)
        assert "get" in spec.paths["/users"]
        assert "post" in spec.paths["/users"]

    def test_custom_info(self) -> None:
        spec = generate_openapi(
            [],
            info={"title": "My API", "version": "2.0.0", "description": "Test"},
        )
        assert spec.info["title"] == "My API"
        assert spec.info["version"] == "2.0.0"

    def test_with_schemas(self) -> None:
        schemas = [
            SchemaInfo(
                name="User",
                schema={"type": "object", "properties": {"name": {"type": "string"}}},
            )
        ]
        spec = generate_openapi([], schemas=schemas)
        assert "User" in spec.components["schemas"]

    def test_endpoint_with_path_params(self) -> None:
        endpoints = [
            EndpointInfo(
                path="/users/{id}",
                method="GET",
                parameters=[{"name": "id", "in": "path", "required": "true"}],
            )
        ]
        spec = generate_openapi(endpoints)
        op = spec.paths["/users/{id}"]["get"]
        assert "parameters" in op

    def test_endpoint_with_request_body(self) -> None:
        endpoints = [
            EndpointInfo(
                path="/users",
                method="POST",
                request_body="CreateUser",
            )
        ]
        spec = generate_openapi(endpoints)
        op = spec.paths["/users"]["post"]
        assert "requestBody" in op

    def test_custom_responses(self) -> None:
        endpoints = [
            EndpointInfo(
                path="/users",
                method="POST",
                responses={"201": "Created", "400": "Bad Request"},
            )
        ]
        spec = generate_openapi(endpoints)
        op = spec.paths["/users"]["post"]
        assert "201" in op["responses"]
        assert "400" in op["responses"]


# ---------------------------------------------------------------------------
# Agent -- three-phase pipeline
# ---------------------------------------------------------------------------


class TestAPIDocGeneratorAgent:
    """Tests for APIDocGeneratorAgent class."""

    def test_extract_file_not_found(self, agent: APIDocGeneratorAgent) -> None:
        result = agent.extract("/nonexistent.py")
        assert result == []

    def test_extract_fastapi(
        self, agent: APIDocGeneratorAgent, tmp_dir: str
    ) -> None:
        path = os.path.join(tmp_dir, "api.py")
        Path(path).write_text(FASTAPI_CODE)
        endpoints = agent.extract(path)
        assert len(endpoints) >= 4

    def test_infer(self, agent: APIDocGeneratorAgent) -> None:
        result = agent.infer(PYTHON_TYPES)
        assert isinstance(result, SchemaInfo)
        assert result.name == "User"

    def test_generate(self, agent: APIDocGeneratorAgent) -> None:
        endpoints = [EndpointInfo(path="/test", method="GET")]
        spec = agent.generate(endpoints)
        assert isinstance(spec, OpenAPISpec)
        assert "/test" in spec.paths

    def test_full_pipeline(
        self, agent: APIDocGeneratorAgent, tmp_dir: str
    ) -> None:
        # Phase 1: extract
        path = os.path.join(tmp_dir, "api.py")
        Path(path).write_text(FASTAPI_CODE)
        endpoints = agent.extract(path)
        assert len(endpoints) > 0

        # Phase 2: infer schema
        schema = agent.infer(PYTHON_TYPES)
        assert schema.name == "User"

        # Phase 3: generate spec
        spec = agent.generate(
            endpoints,
            info={"title": "Test API", "version": "1.0.0"},
            schemas=[schema],
        )
        assert spec.openapi_version == "3.1.0"
        assert len(spec.paths) > 0
        assert "User" in spec.components["schemas"]


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        try:
            from agent_api_doc_generator.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        import agent_api_doc_generator.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")


# ---------------------------------------------------------------------------
# Local adapter -- message dispatch
# ---------------------------------------------------------------------------


class TestLocalAdapter:
    """Tests for local adapter message handling."""

    def test_handle_extract(
        self, agent: APIDocGeneratorAgent, tmp_dir: str
    ) -> None:
        path = os.path.join(tmp_dir, "api.py")
        Path(path).write_text(FASTAPI_CODE)
        response = handle_message(
            agent,
            {"method": "extract", "params": {"file_path": path}},
        )
        assert response["status"] == "ok"
        assert len(response["result"]["endpoints"]) >= 4

    def test_handle_extract_missing_path(
        self, agent: APIDocGeneratorAgent
    ) -> None:
        response = handle_message(
            agent, {"method": "extract", "params": {}}
        )
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_infer(self, agent: APIDocGeneratorAgent) -> None:
        response = handle_message(
            agent,
            {"method": "infer", "params": {"type_info": PYTHON_TYPES}},
        )
        assert response["status"] == "ok"
        assert response["result"]["name"] == "User"

    def test_handle_infer_missing_type_info(
        self, agent: APIDocGeneratorAgent
    ) -> None:
        response = handle_message(
            agent, {"method": "infer", "params": {}}
        )
        assert response["status"] == "error"

    def test_handle_generate(self, agent: APIDocGeneratorAgent) -> None:
        endpoints = [{"path": "/test", "method": "GET"}]
        response = handle_message(
            agent,
            {
                "method": "generate",
                "params": {"endpoints": endpoints},
            },
        )
        assert response["status"] == "ok"
        assert response["result"]["openapi_version"] == "3.1.0"

    def test_handle_generate_missing_endpoints(
        self, agent: APIDocGeneratorAgent
    ) -> None:
        response = handle_message(
            agent, {"method": "generate", "params": {}}
        )
        assert response["status"] == "error"

    def test_handle_unknown_method(
        self, agent: APIDocGeneratorAgent
    ) -> None:
        response = handle_message(agent, {"method": "unknown", "params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]

    def test_handle_extract_nonexistent_file(
        self, agent: APIDocGeneratorAgent
    ) -> None:
        response = handle_message(
            agent,
            {"method": "extract", "params": {"file_path": "/nonexistent.py"}},
        )
        assert response["status"] == "ok"
        assert response["result"]["endpoints"] == []
