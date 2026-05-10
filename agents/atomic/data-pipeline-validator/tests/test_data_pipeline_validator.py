"""Tests for data-pipeline-validator agent.

Covers:
- Models: construction, validation, serialization, immutability
- validate_pipeline: structure, source, target, steps, error handling
- generate_report: severity counting, recommendations
- Agent: full pipeline
- MCP adapter: server creation
- Local adapter: message dispatch
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from agent_data_pipeline_validator.agent import DataPipelineValidatorAgent
from agent_data_pipeline_validator.local_adapter import handle_message
from agent_data_pipeline_validator.models import (
    PipelineFinding,
    PipelineReport,
    PipelineValidationResult,
)
from agent_data_pipeline_validator.tools.generate_report import generate_report
from agent_data_pipeline_validator.tools.validate_pipeline import (
    _KNOWN_STEP_TYPES,
    validate_pipeline,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent() -> DataPipelineValidatorAgent:
    """Provide a DataPipelineValidatorAgent instance."""
    return DataPipelineValidatorAgent()


def _make_config(**overrides) -> str:
    """Build a minimal valid pipeline config as JSON string."""
    config = {
        "name": "test-pipeline",
        "source": {"type": "database", "connection": "postgresql://localhost/db"},
        "target": {"type": "file", "path": "/output/data.csv"},
        "steps": [
            {
                "name": "extract",
                "type": "extract",
                "config": {"table": "users"},
                "on_error": "skip",
            },
            {
                "name": "transform",
                "type": "transform",
                "config": {"mapping": {"id": "user_id"}},
                "on_error": "abort",
            },
            {
                "name": "load",
                "type": "load",
                "config": {"format": "csv"},
                "retry": 3,
            },
        ],
    }
    config.update(overrides)
    return json.dumps(config)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestPipelineFinding:
    """Tests for PipelineFinding model."""

    def test_basic_construction(self) -> None:
        f = PipelineFinding(severity="error", category="structure", location="<root>")
        assert f.severity == "error"
        assert f.category == "structure"
        assert f.location == "<root>"

    def test_full_construction(self) -> None:
        f = PipelineFinding(
            severity="warning",
            category="source",
            location="source",
            description="No connection",
            remediation="Add connection",
        )
        assert f.description == "No connection"

    def test_frozen(self) -> None:
        f = PipelineFinding(severity="info", category="x", location="y")
        with pytest.raises(ValidationError):
            f.severity = "error"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        f = PipelineFinding(severity="error", category="structure", location="<root>")
        data = f.model_dump()
        f2 = PipelineFinding.model_validate(data)
        assert f == f2


class TestPipelineValidationResult:
    """Tests for PipelineValidationResult model."""

    def test_empty(self) -> None:
        r = PipelineValidationResult()
        assert r.findings == []
        assert r.is_valid is True
        assert r.step_count == 0

    def test_with_findings(self) -> None:
        f = PipelineFinding(severity="error", category="structure", location="<root>")
        r = PipelineValidationResult(findings=[f], is_valid=False, step_count=3)
        assert r.step_count == 3


class TestPipelineReport:
    """Tests for PipelineReport model."""

    def test_empty(self) -> None:
        r = PipelineReport()
        assert r.error_count == 0
        assert r.findings == []

    def test_with_counts(self) -> None:
        r = PipelineReport(error_count=2, warning_count=1, step_count=5)
        assert r.error_count == 2


# ---------------------------------------------------------------------------
# validate_pipeline
# ---------------------------------------------------------------------------


class TestValidatePipeline:
    """Tests for validate_pipeline tool."""

    def test_invalid_json(self) -> None:
        result = validate_pipeline("not json")
        assert result.is_valid is False
        assert any("Invalid JSON" in f.description for f in result.findings)

    def test_non_object_json(self) -> None:
        result = validate_pipeline("[1, 2, 3]")
        assert result.is_valid is False

    def test_valid_config(self) -> None:
        result = validate_pipeline(_make_config())
        assert result.is_valid is True
        assert result.step_count == 3
        assert result.pipeline_name == "test-pipeline"

    def test_missing_name(self) -> None:
        result = validate_pipeline(_make_config(name=""))
        assert any("'name'" in f.description for f in result.findings)

    def test_missing_source(self) -> None:
        config = {
            "name": "test",
            "target": {"type": "file", "path": "/out"},
            "steps": [{"name": "load", "type": "load"}],
        }
        result = validate_pipeline(json.dumps(config))
        assert any("'source'" in f.description for f in result.findings)

    def test_missing_target(self) -> None:
        config = {
            "name": "test",
            "source": {"type": "db"},
            "steps": [{"name": "load", "type": "load"}],
        }
        result = validate_pipeline(json.dumps(config))
        assert any("'target'" in f.description for f in result.findings)

    def test_missing_steps(self) -> None:
        config = {
            "name": "test",
            "source": {"type": "db"},
            "target": {"type": "file"},
        }
        result = validate_pipeline(json.dumps(config))
        assert any("'steps'" in f.description for f in result.findings)

    def test_empty_steps(self) -> None:
        result = validate_pipeline(_make_config(steps=[]))
        assert any("empty" in f.description.lower() for f in result.findings)

    def test_source_no_type(self) -> None:
        config = {
            "name": "test",
            "source": {"connection": "db://localhost"},
            "target": {"type": "file", "path": "/out"},
            "steps": [{"name": "load", "type": "load"}],
        }
        result = validate_pipeline(json.dumps(config))
        assert any(f.category == "source" and "type" in f.description for f in result.findings)

    def test_source_no_connection(self) -> None:
        config = {
            "name": "test",
            "source": {"type": "database"},
            "target": {"type": "file", "path": "/out"},
            "steps": [{"name": "load", "type": "load"}],
        }
        result = validate_pipeline(json.dumps(config))
        assert any("connection" in f.description.lower() for f in result.findings)

    def test_step_missing_type(self) -> None:
        config = {
            "name": "test",
            "source": {"type": "db"},
            "target": {"type": "file"},
            "steps": [{"name": "mystery"}],
        }
        result = validate_pipeline(json.dumps(config))
        assert any("'type'" in f.description and f.category == "step" for f in result.findings)

    def test_step_unknown_type(self) -> None:
        config = {
            "name": "test",
            "source": {"type": "db"},
            "target": {"type": "file"},
            "steps": [{"name": "custom", "type": "teleport"}],
        }
        result = validate_pipeline(json.dumps(config))
        assert any("unknown" in f.description.lower() for f in result.findings)

    def test_step_no_error_handling(self) -> None:
        config = {
            "name": "test",
            "source": {"type": "db"},
            "target": {"type": "file"},
            "steps": [{"name": "load", "type": "load", "config": {}}],
        }
        result = validate_pipeline(json.dumps(config))
        assert any(f.category == "error_handling" for f in result.findings)

    def test_step_with_error_handling(self) -> None:
        config = {
            "name": "test",
            "source": {"type": "db"},
            "target": {"type": "file"},
            "steps": [{"name": "load", "type": "load", "on_error": "retry", "retry": 3}],
        }
        result = validate_pipeline(json.dumps(config))
        assert not any(f.category == "error_handling" for f in result.findings)

    def test_step_missing_name(self) -> None:
        config = {
            "name": "test",
            "source": {"type": "db"},
            "target": {"type": "file"},
            "steps": [{"type": "load", "on_error": "skip"}],
        }
        result = validate_pipeline(json.dumps(config))
        assert any("'name'" in f.description and f.category == "step" for f in result.findings)


# ---------------------------------------------------------------------------
# generate_report
# ---------------------------------------------------------------------------


class TestGenerateReport:
    """Tests for generate_report tool."""

    def test_empty_findings(self) -> None:
        report = generate_report([])
        assert report.error_count == 0

    def test_severity_counts(self) -> None:
        findings = [
            PipelineFinding(severity="error", category="a", location="x"),
            PipelineFinding(severity="warning", category="b", location="y"),
            PipelineFinding(severity="info", category="c", location="z"),
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
            PipelineFinding(
                severity="error",
                category="source",
                location="source",
                remediation="Add type field",
            ),
        ]
        report = generate_report(findings)
        assert len(report.recommendations) >= 1
        assert "ERROR" in report.recommendations[0]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    """Tests for helper constants."""

    def test_known_step_types(self) -> None:
        assert "extract" in _KNOWN_STEP_TYPES
        assert "transform" in _KNOWN_STEP_TYPES
        assert "load" in _KNOWN_STEP_TYPES


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class TestDataPipelineValidatorAgent:
    """Tests for DataPipelineValidatorAgent class."""

    def test_validate_pipeline(self, agent: DataPipelineValidatorAgent) -> None:
        result = agent.validate_pipeline(_make_config())
        assert isinstance(result, PipelineValidationResult)
        assert result.is_valid is True

    def test_validate_pipeline_invalid(self, agent: DataPipelineValidatorAgent) -> None:
        result = agent.validate_pipeline("bad json")
        assert result.is_valid is False

    def test_generate_report(self, agent: DataPipelineValidatorAgent) -> None:
        findings = [PipelineFinding(severity="warning", category="source", location="source")]
        result = agent.generate_report(findings)
        assert isinstance(result, PipelineReport)
        assert result.warning_count == 1

    def test_full_pipeline(self, agent: DataPipelineValidatorAgent) -> None:
        validation = agent.validate_pipeline(_make_config())
        report = agent.generate_report(validation.findings)
        assert isinstance(report, PipelineReport)


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        try:
            from agent_data_pipeline_validator.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        import agent_data_pipeline_validator.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")


# ---------------------------------------------------------------------------
# Local adapter
# ---------------------------------------------------------------------------


class TestLocalAdapter:
    """Tests for local adapter message handling."""

    def test_handle_validate(self, agent: DataPipelineValidatorAgent) -> None:
        response = handle_message(
            agent,
            {"method": "validate_pipeline", "params": {"config": _make_config()}},
        )
        assert response["status"] == "ok"
        assert "result" in response

    def test_handle_validate_missing_config(self, agent: DataPipelineValidatorAgent) -> None:
        response = handle_message(agent, {"method": "validate_pipeline", "params": {}})
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_generate_report(self, agent: DataPipelineValidatorAgent) -> None:
        response = handle_message(
            agent,
            {
                "method": "generate_report",
                "params": {"findings": [{"severity": "error", "category": "x", "location": "y"}]},
            },
        )
        assert response["status"] == "ok"
        assert response["result"]["error_count"] == 1

    def test_handle_unknown_method(self, agent: DataPipelineValidatorAgent) -> None:
        response = handle_message(agent, {"method": "unknown", "params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]
