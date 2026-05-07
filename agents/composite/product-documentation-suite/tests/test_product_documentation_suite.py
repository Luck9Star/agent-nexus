"""Comprehensive tests for product-documentation-suite composite agent.

Covers:
- Models: construction, validation, serialization, immutability
- composition.toml: parsing and DAG validation
- Coordinator: pipeline execution, parallel ordering, error propagation,
  result aggregation, drift detection
- MCP adapter: server creation
- Local adapter: message dispatch
"""

from __future__ import annotations

import json
import os

import pytest

from agent_product_documentation_suite.coordinator import (
    DocumentationSuiteCoordinator,
    _content_hash,
    _simulate_api_doc_generator,
    _simulate_code_reviewer,
    _simulate_localization,
)
from agent_product_documentation_suite.models import (
    DocArtifact,
    DocumentationResult,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

COMPOSITION_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPOSITION_TOML = os.path.join(COMPOSITION_DIR, "composition.toml")


@pytest.fixture
def coordinator() -> DocumentationSuiteCoordinator:
    """Provide a DocumentationSuiteCoordinator instance."""
    return DocumentationSuiteCoordinator()


@pytest.fixture
def composition_data() -> dict:
    """Parse the actual composition.toml file."""
    return DocumentationSuiteCoordinator.parse_composition(COMPOSITION_TOML)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestDocArtifact:
    """Tests for DocArtifact model."""

    def test_basic_construction(self) -> None:
        artifact = DocArtifact(type="openapi_spec")
        assert artifact.type == "openapi_spec"
        assert artifact.path == ""
        assert artifact.language == "en"
        assert artifact.content_hash == ""

    def test_full_construction(self) -> None:
        artifact = DocArtifact(
            type="review_report",
            path="/tmp/review.json",
            language="zh",
            content_hash="abc123",
        )
        assert artifact.type == "review_report"
        assert artifact.path == "/tmp/review.json"
        assert artifact.language == "zh"
        assert artifact.content_hash == "abc123"

    def test_frozen(self) -> None:
        artifact = DocArtifact(type="spec")
        with pytest.raises(Exception):
            artifact.type = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        artifact = DocArtifact(type="spec", path="/a.json", content_hash="abc")
        data = artifact.model_dump()
        artifact2 = DocArtifact.model_validate(data)
        assert artifact == artifact2

    def test_json_serialization(self) -> None:
        artifact = DocArtifact(type="localization", language="ja")
        json_str = artifact.model_dump_json()
        data = json.loads(json_str)
        assert data["type"] == "localization"
        assert data["language"] == "ja"

    def test_artifact_types(self) -> None:
        for t in ("openapi_spec", "review_report", "localization"):
            artifact = DocArtifact(type=t)
            assert artifact.type == t


class TestDocumentationResult:
    """Tests for DocumentationResult model."""

    def test_minimal_construction(self) -> None:
        result = DocumentationResult()
        assert result.artifacts == []
        assert result.coverage_score == 0.0
        assert result.drift_report == ""
        assert result.success is True

    def test_full_construction(self) -> None:
        artifacts = [
            DocArtifact(type="openapi_spec", path="/a.json"),
            DocArtifact(type="review_report", path="/b.json"),
        ]
        result = DocumentationResult(
            artifacts=artifacts,
            coverage_score=0.85,
            drift_report="No drift detected",
            success=True,
        )
        assert len(result.artifacts) == 2
        assert result.coverage_score == 0.85
        assert result.drift_report == "No drift detected"

    def test_frozen(self) -> None:
        result = DocumentationResult()
        with pytest.raises(Exception):
            result.success = False  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        result = DocumentationResult(
            artifacts=[DocArtifact(type="spec")],
            coverage_score=0.5,
            drift_report="drift found",
        )
        data = result.model_dump()
        result2 = DocumentationResult.model_validate(data)
        assert result == result2

    def test_json_serialization(self) -> None:
        result = DocumentationResult(success=False)
        json_str = result.model_dump_json()
        data = json.loads(json_str)
        assert data["success"] is False

    def test_failure_result(self) -> None:
        result = DocumentationResult(success=False)
        assert result.success is False
        assert result.artifacts == []


# ---------------------------------------------------------------------------
# composition.toml parsing and validation
# ---------------------------------------------------------------------------


class TestCompositionParsing:
    """Tests for composition.toml parsing."""

    def test_parse_actual_file(self, composition_data: dict) -> None:
        assert "composition" in composition_data
        assert (
            composition_data["composition"]["name"]
            == "product-documentation-suite"
        )

    def test_has_three_tasks(self, composition_data: dict) -> None:
        tasks = composition_data["tasks"]
        assert len(tasks) == 3

    def test_task_ids(self, composition_data: dict) -> None:
        assert "task1" in composition_data["tasks"]
        assert "task2" in composition_data["tasks"]
        assert "task3" in composition_data["tasks"]

    def test_task_agents(self, composition_data: dict) -> None:
        tasks = composition_data["tasks"]
        assert tasks["task1"]["agent"] == "api-doc-generator"
        assert tasks["task2"]["agent"] == "code-reviewer"
        assert tasks["task3"]["agent"] == "localization-specialist"

    def test_parallel_then_sequential(self, composition_data: dict) -> None:
        """Verify parallel phase (task1, task2 unblocked) then sequential (task3 blocked)."""
        tasks = composition_data["tasks"]
        assert tasks["task1"]["blocked_by"] == []
        assert tasks["task2"]["blocked_by"] == []
        assert set(tasks["task3"]["blocked_by"]) == {"task1", "task2"}

    def test_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            DocumentationSuiteCoordinator.parse_composition("/nonexistent.toml")


class TestCompositionValidation:
    """Tests for DAG validation."""

    def test_valid_composition(self, composition_data: dict) -> None:
        errors = DocumentationSuiteCoordinator.validate_composition(composition_data)
        assert errors == []

    def test_missing_composition_section(self) -> None:
        errors = DocumentationSuiteCoordinator.validate_composition({})
        assert any("composition" in e for e in errors)

    def test_missing_name(self) -> None:
        data = {
            "composition": {"description": "test"},
            "tasks": {"t1": {"name": "n", "agent": "a", "blocked_by": []}},
        }
        errors = DocumentationSuiteCoordinator.validate_composition(data)
        assert any("name" in e for e in errors)

    def test_no_tasks(self) -> None:
        data = {"composition": {"name": "test", "description": "d"}}
        errors = DocumentationSuiteCoordinator.validate_composition(data)
        assert any("No tasks" in e for e in errors)

    def test_task_missing_agent(self) -> None:
        data = {
            "composition": {"name": "test"},
            "tasks": {"t1": {"name": "task1", "blocked_by": []}},
        }
        errors = DocumentationSuiteCoordinator.validate_composition(data)
        assert any("agent" in e for e in errors)

    def test_unknown_dependency(self) -> None:
        data = {
            "composition": {"name": "test"},
            "tasks": {
                "t1": {"name": "n", "agent": "a", "blocked_by": ["nonexistent"]},
            },
        }
        errors = DocumentationSuiteCoordinator.validate_composition(data)
        assert any("nonexistent" in e for e in errors)

    def test_self_dependency(self) -> None:
        data = {
            "composition": {"name": "test"},
            "tasks": {
                "t1": {"name": "n", "agent": "a", "blocked_by": ["t1"]},
            },
        }
        errors = DocumentationSuiteCoordinator.validate_composition(data)
        assert any("itself" in e for e in errors)

    def test_circular_dependency(self) -> None:
        data = {
            "composition": {"name": "test"},
            "tasks": {
                "t1": {"name": "n", "agent": "a", "blocked_by": ["t2"]},
                "t2": {"name": "n", "agent": "a", "blocked_by": ["t1"]},
            },
        }
        errors = DocumentationSuiteCoordinator.validate_composition(data)
        assert any("Circular" in e for e in errors)


# ---------------------------------------------------------------------------
# Simulated Atomic Agents
# ---------------------------------------------------------------------------


class TestSimulatedAPIDocGenerator:
    """Tests for _simulate_api_doc_generator."""

    def test_returns_openapi_structure(self) -> None:
        result = _simulate_api_doc_generator("/path/to/api.py")
        assert "openapi_version" in result
        assert "info" in result
        assert "paths" in result
        assert "components" in result

    def test_info_contains_title(self) -> None:
        result = _simulate_api_doc_generator("/path/to/api.py")
        assert "title" in result["info"]

    def test_has_paths(self) -> None:
        result = _simulate_api_doc_generator("/path/to/api.py")
        assert len(result["paths"]) > 0

    def test_has_schemas(self) -> None:
        result = _simulate_api_doc_generator("/path/to/api.py")
        assert "schemas" in result["components"]


class TestSimulatedCodeReviewer:
    """Tests for _simulate_code_reviewer."""

    def test_returns_review_structure(self) -> None:
        result = _simulate_code_reviewer("/path/to/api.py")
        assert "summary" in result
        assert "findings" in result
        assert "suggestions" in result
        assert "overall_score" in result

    def test_has_severity_counts(self) -> None:
        result = _simulate_code_reviewer("/path/to/api.py")
        assert "severity_counts" in result
        assert "critical" in result["severity_counts"]
        assert "warning" in result["severity_counts"]

    def test_score_in_range(self) -> None:
        result = _simulate_code_reviewer("/path/to/api.py")
        assert 0 <= result["overall_score"] <= 100


class TestSimulatedLocalization:
    """Tests for _simulate_localization."""

    def test_english(self) -> None:
        result = _simulate_localization("Test text", "en")
        assert "[English]" in result["translated_text"]

    def test_chinese(self) -> None:
        result = _simulate_localization("Test text", "zh")
        assert "[Chinese]" in result["translated_text"]

    def test_japanese(self) -> None:
        result = _simulate_localization("Test text", "ja")
        assert "[Japanese]" in result["translated_text"]

    def test_unknown_lang(self) -> None:
        result = _simulate_localization("Test", "xx")
        assert "[xx]" in result["translated_text"]


class TestContentHash:
    """Tests for _content_hash helper."""

    def test_deterministic(self) -> None:
        h1 = _content_hash("test content")
        h2 = _content_hash("test content")
        assert h1 == h2

    def test_different_content(self) -> None:
        h1 = _content_hash("content a")
        h2 = _content_hash("content b")
        assert h1 != h2

    def test_hash_length(self) -> None:
        h = _content_hash("test")
        assert len(h) == 16


# ---------------------------------------------------------------------------
# Coordinator -- pipeline execution
# ---------------------------------------------------------------------------


class TestDocumentationSuiteCoordinator:
    """Tests for DocumentationSuiteCoordinator."""

    def test_generate_docs_basic(
        self, coordinator: DocumentationSuiteCoordinator
    ) -> None:
        result = coordinator.generate_docs("/path/to/api.py")
        assert result.success is True
        assert len(result.artifacts) > 0

    def test_generate_docs_with_localization(
        self, coordinator: DocumentationSuiteCoordinator
    ) -> None:
        result = coordinator.generate_docs(
            "/path/to/api.py", target_langs=["zh", "en", "ja"]
        )
        assert result.success is True
        lang_set = {a.language for a in result.artifacts if a.type == "localization"}
        assert "zh" in lang_set
        assert "en" in lang_set
        assert "ja" in lang_set

    def test_artifacts_contain_openapi_spec(
        self, coordinator: DocumentationSuiteCoordinator
    ) -> None:
        result = coordinator.generate_docs("/path/to/api.py")
        types = {a.type for a in result.artifacts}
        assert "openapi_spec" in types

    def test_artifacts_contain_review_report(
        self, coordinator: DocumentationSuiteCoordinator
    ) -> None:
        result = coordinator.generate_docs("/path/to/api.py")
        types = {a.type for a in result.artifacts}
        assert "review_report" in types

    def test_artifacts_contain_localization(
        self, coordinator: DocumentationSuiteCoordinator
    ) -> None:
        result = coordinator.generate_docs(
            "/path/to/api.py", target_langs=["zh"]
        )
        types = {a.type for a in result.artifacts}
        assert "localization" in types

    def test_coverage_score(self, coordinator: DocumentationSuiteCoordinator) -> None:
        result = coordinator.generate_docs("/path/to/api.py")
        assert 0.0 <= result.coverage_score <= 1.0

    def test_drift_report_not_empty(
        self, coordinator: DocumentationSuiteCoordinator
    ) -> None:
        result = coordinator.generate_docs("/path/to/api.py")
        assert result.drift_report != ""

    def test_no_drift_when_both_phases_succeed(
        self, coordinator: DocumentationSuiteCoordinator
    ) -> None:
        result = coordinator.generate_docs("/path/to/api.py")
        assert "No drift detected" in result.drift_report

    def test_default_langs(self, coordinator: DocumentationSuiteCoordinator) -> None:
        result = coordinator.generate_docs("/path/to/api.py")
        lang_set = {a.language for a in result.artifacts if a.type == "localization"}
        assert "en" in lang_set

    def test_artifact_hashes(
        self, coordinator: DocumentationSuiteCoordinator
    ) -> None:
        result = coordinator.generate_docs("/path/to/api.py")
        for artifact in result.artifacts:
            assert artifact.content_hash != ""
            assert len(artifact.content_hash) == 16

    def test_total_artifact_count(
        self, coordinator: DocumentationSuiteCoordinator
    ) -> None:
        """2 source artifacts + N localization artifacts."""
        result = coordinator.generate_docs(
            "/path/to/api.py", target_langs=["en", "zh"]
        )
        # 1 openapi_spec + 1 review_report + 2 localizations = 4
        assert len(result.artifacts) == 4


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        try:
            from agent_product_documentation_suite.mcp_adapter import (
                create_mcp_server,
            )

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        import agent_product_documentation_suite.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")


# ---------------------------------------------------------------------------
# Local adapter -- message dispatch
# ---------------------------------------------------------------------------


class TestLocalAdapter:
    """Tests for local adapter message handling."""

    def test_handle_generate_docs(
        self, coordinator: DocumentationSuiteCoordinator
    ) -> None:
        from agent_product_documentation_suite.main import _handle_message

        response = _handle_message(
            coordinator,
            {"method": "generate_docs", "params": {"code_path": "/path/to/api.py"}},
        )
        assert response["status"] == "ok"
        assert response["result"]["success"] is True

    def test_handle_with_target_langs(
        self, coordinator: DocumentationSuiteCoordinator
    ) -> None:
        from agent_product_documentation_suite.main import _handle_message

        response = _handle_message(
            coordinator,
            {
                "method": "generate_docs",
                "params": {
                    "code_path": "/path/to/api.py",
                    "target_langs": ["zh", "ja"],
                },
            },
        )
        assert response["status"] == "ok"

    def test_handle_unknown_method(
        self, coordinator: DocumentationSuiteCoordinator
    ) -> None:
        from agent_product_documentation_suite.main import _handle_message

        response = _handle_message(
            coordinator, {"method": "unknown", "params": {}}
        )
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]

    def test_handle_missing_code_path(
        self, coordinator: DocumentationSuiteCoordinator
    ) -> None:
        from agent_product_documentation_suite.main import _handle_message

        response = _handle_message(
            coordinator, {"method": "generate_docs", "params": {}}
        )
        assert response["status"] == "error"
        assert "Missing" in response["error"]
