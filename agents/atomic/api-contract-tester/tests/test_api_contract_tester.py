"""Tests for api-contract-tester agent.

Covers:
- Models: construction, validation, serialization, immutability
- validate_contract: structure checks, schema refs, endpoint consistency
- generate_report: severity counting, coverage score, recommendations
- Agent: full pipeline
- MCP adapter: server creation
- Local adapter: message dispatch
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agent_api_contract_tester.agent import ApiContractTesterAgent
from agent_api_contract_tester.local_adapter import handle_message
from agent_api_contract_tester.models import (
    ContractFinding,
    ContractReport,
    ContractValidationResult,
)
from agent_api_contract_tester.tools.generate_report import (
    _compute_coverage_score,
    generate_report,
)
from agent_api_contract_tester.tools.validate_contract import (
    _count_endpoints,
    _get_schemas,
    validate_contract,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent() -> ApiContractTesterAgent:
    """Provide an ApiContractTesterAgent instance."""
    return ApiContractTesterAgent()


def _make_spec(**overrides) -> str:
    """Build a minimal valid OpenAPI spec as JSON string."""
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/users": {
                "get": {
                    "responses": {
                        "200": {"description": "OK"},
                        "400": {"description": "Bad Request"},
                    }
                },
                "post": {
                    "responses": {
                        "201": {"description": "Created"},
                        "400": {"description": "Bad Request"},
                    }
                },
            }
        },
    }
    spec.update(overrides)
    return json.dumps(spec)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestContractFinding:
    """Tests for ContractFinding model."""

    def test_basic_construction(self) -> None:
        f = ContractFinding(severity="error", category="structure", location="<root>")
        assert f.severity == "error"
        assert f.category == "structure"
        assert f.location == "<root>"
        assert f.description == ""
        assert f.remediation == ""

    def test_full_construction(self) -> None:
        f = ContractFinding(
            severity="warning",
            category="schema_ref",
            location="paths./users",
            description="Missing ref",
            remediation="Add schema",
        )
        assert f.description == "Missing ref"
        assert f.remediation == "Add schema"

    def test_frozen(self) -> None:
        f = ContractFinding(severity="info", category="x", location="y")
        with pytest.raises(ValidationError):
            f.severity = "error"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        f = ContractFinding(severity="error", category="structure", location="<root>")
        data = f.model_dump()
        f2 = ContractFinding.model_validate(data)
        assert f == f2


class TestContractValidationResult:
    """Tests for ContractValidationResult model."""

    def test_empty(self) -> None:
        r = ContractValidationResult()
        assert r.findings == []
        assert r.is_valid is True
        assert r.endpoint_count == 0

    def test_with_findings(self) -> None:
        f = ContractFinding(severity="error", category="structure", location="<root>")
        r = ContractValidationResult(findings=[f], is_valid=False, endpoint_count=3)
        assert len(r.findings) == 1
        assert r.is_valid is False
        assert r.endpoint_count == 3


class TestContractReport:
    """Tests for ContractReport model."""

    def test_empty(self) -> None:
        r = ContractReport()
        assert r.error_count == 0
        assert r.coverage_score == 0.0

    def test_with_counts(self) -> None:
        r = ContractReport(error_count=2, warning_count=3, coverage_score=74.0)
        assert r.error_count == 2
        assert r.coverage_score == 74.0


# ---------------------------------------------------------------------------
# validate_contract
# ---------------------------------------------------------------------------


class TestValidateContract:
    """Tests for validate_contract tool."""

    def test_invalid_json(self) -> None:
        result = validate_contract("not json at all")
        assert result.is_valid is False
        assert any("Invalid JSON" in f.description for f in result.findings)

    def test_non_object_json(self) -> None:
        result = validate_contract("[1, 2, 3]")
        assert result.is_valid is False
        assert any("JSON object" in f.description for f in result.findings)

    def test_valid_spec(self) -> None:
        result = validate_contract(_make_spec())
        assert result.is_valid is True
        assert result.spec_version == "3.0.0"
        assert result.endpoint_count == 2

    def test_missing_openapi(self) -> None:
        result = validate_contract(_make_spec(openapi=None))
        assert result.is_valid is False
        assert any("'openapi'" in f.description for f in result.findings)

    def test_missing_info(self) -> None:
        spec = {"openapi": "3.0.0", "paths": {}}
        result = validate_contract(json.dumps(spec))
        assert result.is_valid is False

    def test_missing_paths(self) -> None:
        spec = {"openapi": "3.0.0", "info": {"title": "T", "version": "1.0"}}
        result = validate_contract(json.dumps(spec))
        assert result.is_valid is False

    def test_empty_paths(self) -> None:
        result = validate_contract(_make_spec(paths={}))
        assert any("empty" in f.description.lower() for f in result.findings)

    def test_broken_schema_ref(self) -> None:
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1.0"},
            "paths": {
                "/users": {
                    "get": {
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/NonExistent"}
                                    }
                                },
                            }
                        }
                    }
                }
            },
        }
        result = validate_contract(json.dumps(spec))
        assert any(f.category == "schema_ref" for f in result.findings)

    def test_endpoint_no_responses(self) -> None:
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1.0"},
            "paths": {"/items": {"get": {}}},
        }
        result = validate_contract(json.dumps(spec))
        assert any("No responses" in f.description for f in result.findings)

    def test_endpoint_no_success_response(self) -> None:
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1.0"},
            "paths": {"/items": {"get": {"responses": {"400": {"description": "Bad Request"}}}}},
        }
        result = validate_contract(json.dumps(spec))
        assert any("2xx" in f.description for f in result.findings)

    def test_missing_delete_204(self) -> None:
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "T", "version": "1.0"},
            "paths": {
                "/items/{id}": {
                    "delete": {
                        "responses": {"200": {"description": "OK"}, "404": {"description": "NF"}}
                    }
                }
            },
        }
        result = validate_contract(json.dumps(spec))
        assert any("204" in f.description for f in result.findings)


# ---------------------------------------------------------------------------
# generate_report
# ---------------------------------------------------------------------------


class TestGenerateReport:
    """Tests for generate_report tool."""

    def test_empty_findings(self) -> None:
        report = generate_report([])
        assert report.error_count == 0
        assert report.coverage_score == 100.0

    def test_severity_counts(self) -> None:
        findings = [
            ContractFinding(severity="error", category="a", location="x"),
            ContractFinding(severity="warning", category="b", location="y"),
            ContractFinding(severity="info", category="c", location="z"),
        ]
        report = generate_report(findings)
        assert report.error_count == 1
        assert report.warning_count == 1
        assert report.info_count == 1

    def test_dict_findings(self) -> None:
        findings = [{"severity": "error", "category": "structure", "location": "<root>"}]
        report = generate_report(findings)
        assert report.error_count == 1

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(TypeError):
            generate_report(["not a finding"])

    def test_recommendations_populated(self) -> None:
        findings = [
            ContractFinding(
                severity="error",
                category="structure",
                location="<root>",
                remediation="Add openapi field",
            ),
        ]
        report = generate_report(findings)
        assert len(report.recommendations) >= 1
        assert "ERROR" in report.recommendations[0]


class TestCoverageScore:
    """Tests for coverage score computation."""

    def test_perfect_score(self) -> None:
        score = _compute_coverage_score([])
        assert score == 100.0

    def test_error_penalty(self) -> None:
        findings = [ContractFinding(severity="error", category="a", location="x")]
        score = _compute_coverage_score(findings)
        assert score == 90.0

    def test_warning_penalty(self) -> None:
        findings = [ContractFinding(severity="warning", category="a", location="x")]
        score = _compute_coverage_score(findings)
        assert score == 97.0

    def test_clamp_to_zero(self) -> None:
        findings = [
            ContractFinding(severity="error", category="a", location=f"l{i}") for i in range(15)
        ]
        score = _compute_coverage_score(findings)
        assert score == 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for helper functions."""

    def test_count_endpoints(self) -> None:
        paths = {
            "/users": {"get": {}, "post": {}},
            "/items": {"get": {}},
        }
        assert _count_endpoints(paths) == 3

    def test_get_schemas(self) -> None:
        spec = {"components": {"schemas": {"User": {}, "Error": {}}}}
        assert _get_schemas(spec) == {"User", "Error"}

    def test_get_schemas_empty(self) -> None:
        assert _get_schemas({}) == set()


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class TestApiContractTesterAgent:
    """Tests for ApiContractTesterAgent class."""

    def test_validate_contract(self, agent: ApiContractTesterAgent) -> None:
        result = agent.validate_contract(_make_spec())
        assert isinstance(result, ContractValidationResult)
        assert result.is_valid is True

    def test_validate_contract_invalid(self, agent: ApiContractTesterAgent) -> None:
        result = agent.validate_contract("bad json")
        assert isinstance(result, ContractValidationResult)
        assert result.is_valid is False

    def test_generate_report(self, agent: ApiContractTesterAgent) -> None:
        findings = [ContractFinding(severity="warning", category="structure", location="x")]
        result = agent.generate_report(findings)
        assert isinstance(result, ContractReport)
        assert result.warning_count == 1

    def test_full_pipeline(self, agent: ApiContractTesterAgent) -> None:
        validation = agent.validate_contract(_make_spec())
        report = agent.generate_report(validation.findings)
        assert isinstance(report, ContractReport)
        assert report.coverage_score > 0


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        try:
            from agent_api_contract_tester.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        import agent_api_contract_tester.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")


# ---------------------------------------------------------------------------
# Local adapter
# ---------------------------------------------------------------------------


class TestLocalAdapter:
    """Tests for local adapter message handling."""

    def test_handle_validate(self, agent: ApiContractTesterAgent) -> None:
        response = handle_message(
            agent,
            {"method": "validate_contract", "params": {"spec_content": _make_spec()}},
        )
        assert response["status"] == "ok"
        assert "result" in response

    def test_handle_validate_missing_content(self, agent: ApiContractTesterAgent) -> None:
        response = handle_message(agent, {"method": "validate_contract", "params": {}})
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_generate_report(self, agent: ApiContractTesterAgent) -> None:
        response = handle_message(
            agent,
            {
                "method": "generate_report",
                "params": {"findings": [{"severity": "error", "category": "x", "location": "y"}]},
            },
        )
        assert response["status"] == "ok"
        assert response["result"]["error_count"] == 1

    def test_handle_unknown_method(self, agent: ApiContractTesterAgent) -> None:
        response = handle_message(agent, {"method": "unknown", "params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]
