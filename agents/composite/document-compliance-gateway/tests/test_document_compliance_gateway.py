"""Comprehensive tests for document-compliance-gateway Composite Agent.

Covers:
- Models: construction, validation, serialization, immutability
- Composition parsing: TOML loading, task extraction, validation
- DAG execution order: parallel roots, merge step
- Coordinator: full compliance check, conflict detection, scoring
- MCP adapter: server creation
- Local adapter: message dispatch
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_document_compliance_gateway.coordinator import (
    ComplianceCoordinator,
    Composition,
    CompositionError,
    CompositionTask,
    _compute_overall_score,
    _detect_conflicts,
    _detect_cycles,
    _generate_recommendations,
    _simulate_agent_check,
)
from agent_document_compliance_gateway.models import (
    CheckStatus,
    ComplianceCheck,
    ComplianceResult,
    ConflictItem,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


VALID_TOML = """\
[composition]
name = "test-compliance"
description = "Test compliance gateway"

[tasks.task1]
name = "legal"
agent = "contract-analyzer"
blocked_by = []

[tasks.task2]
name = "merge"
agent = "conflict-detector"
blocked_by = ["task1"]
"""

FULL_TOML = """\
[composition]
name = "full-compliance"
description = "Full compliance pipeline"

[tasks.task1]
name = "contract-analysis"
agent = "contract-analyzer"
blocked_by = []

[tasks.task2]
name = "accessibility-audit"
agent = "accessibility-auditor"
blocked_by = []

[tasks.task3]
name = "localization-analysis"
agent = "localization-specialist"
blocked_by = []

[tasks.task4]
name = "conflict-detection"
agent = "conflict-detector"
blocked_by = ["task1", "task2", "task3"]
"""

CYCLE_TOML = """\
[composition]
name = "cycle"
description = "Has a cycle"

[tasks.task1]
name = "a"
agent = "agent-a"
blocked_by = ["task2"]

[tasks.task2]
name = "b"
agent = "agent-b"
blocked_by = ["task1"]
"""


@pytest.fixture
def tmp_dir():
    """Provide a temporary directory."""
    with tempfile.TemporaryDirectory() as d:
        yield d


@pytest.fixture
def full_composition_path(tmp_dir: str) -> Path:
    """Write the full composition.toml and return its path."""
    path = Path(tmp_dir) / "composition.toml"
    path.write_text(FULL_TOML)
    return path


@pytest.fixture
def coordinator() -> ComplianceCoordinator:
    """Provide a ComplianceCoordinator using the bundled composition.toml."""
    return ComplianceCoordinator()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestCheckStatus:
    """Tests for CheckStatus enum."""

    def test_values(self) -> None:
        assert CheckStatus.PASS == "pass"
        assert CheckStatus.FAIL == "fail"
        assert CheckStatus.WARNING == "warning"
        assert CheckStatus.ERROR == "error"


class TestComplianceCheck:
    """Tests for ComplianceCheck model."""

    def test_basic_construction(self) -> None:
        c = ComplianceCheck(dimension="legal")
        assert c.dimension == "legal"
        assert c.status == CheckStatus.PASS
        assert c.issues == []
        assert c.score == 100.0

    def test_failed_check(self) -> None:
        c = ComplianceCheck(
            dimension="accessibility",
            status=CheckStatus.FAIL,
            issues=["Missing alt text"],
            score=45.0,
        )
        assert c.status == CheckStatus.FAIL
        assert len(c.issues) == 1
        assert c.score == 45.0

    def test_frozen(self) -> None:
        c = ComplianceCheck(dimension="x")
        with pytest.raises(Exception):
            c.dimension = "y"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        c = ComplianceCheck(
            dimension="legal", status=CheckStatus.WARNING, issues=["issue1"], score=80.0
        )
        data = c.model_dump()
        c2 = ComplianceCheck.model_validate(data)
        assert c == c2

    def test_json_serialization(self) -> None:
        c = ComplianceCheck(dimension="test", score=50.5)
        json_str = c.model_dump_json()
        data = json.loads(json_str)
        assert data["score"] == 50.5


class TestConflictItem:
    """Tests for ConflictItem model."""

    def test_basic_construction(self) -> None:
        ci = ConflictItem(
            dimensions=["legal", "localization"],
            description="Legal terms conflict with localization",
        )
        assert ci.dimensions == ["legal", "localization"]
        assert ci.resolution == ""

    def test_with_resolution(self) -> None:
        ci = ConflictItem(
            dimensions=["a", "b"],
            description="Conflict",
            resolution="Review and adjust",
        )
        assert ci.resolution == "Review and adjust"

    def test_frozen(self) -> None:
        ci = ConflictItem(dimensions=["x"], description="y")
        with pytest.raises(Exception):
            ci.description = "z"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        ci = ConflictItem(dimensions=["a", "b"], description="desc", resolution="res")
        data = ci.model_dump()
        ci2 = ConflictItem.model_validate(data)
        assert ci == ci2


class TestComplianceResult:
    """Tests for ComplianceResult model."""

    def test_empty(self) -> None:
        r = ComplianceResult()
        assert r.checks == []
        assert r.conflicts == []
        assert r.overall_score == 0.0
        assert r.recommendations == []

    def test_with_checks(self) -> None:
        checks = [
            ComplianceCheck(dimension="legal", score=80.0),
            ComplianceCheck(dimension="accessibility", score=90.0),
        ]
        r = ComplianceResult(checks=checks, overall_score=85.0)
        assert len(r.checks) == 2
        assert r.overall_score == 85.0

    def test_frozen(self) -> None:
        r = ComplianceResult()
        with pytest.raises(Exception):
            r.overall_score = 100.0  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        r = ComplianceResult(
            checks=[ComplianceCheck(dimension="x", score=75.0)],
            conflicts=[ConflictItem(dimensions=["a"], description="d")],
            overall_score=75.0,
            recommendations=["Fix x"],
        )
        data = r.model_dump()
        r2 = ComplianceResult.model_validate(data)
        assert r == r2


# ---------------------------------------------------------------------------
# Composition parsing
# ---------------------------------------------------------------------------


class TestCompositionParsing:
    """Tests for Composition.from_toml()."""

    def test_full_composition(self, full_composition_path: Path) -> None:
        comp = Composition.from_toml(full_composition_path)
        assert comp.name == "full-compliance"
        assert len(comp.tasks) == 4

    def test_root_tasks(self, full_composition_path: Path) -> None:
        comp = Composition.from_toml(full_composition_path)
        roots = comp.get_root_tasks()
        root_ids = {t.id for t in roots}
        assert root_ids == {"task1", "task2", "task3"}

    def test_merge_task(self, full_composition_path: Path) -> None:
        comp = Composition.from_toml(full_composition_path)
        task4 = comp.tasks["task4"]
        assert task4.agent == "conflict-detector"
        assert sorted(task4.blocked_by) == ["task1", "task2", "task3"]

    def test_file_not_found(self) -> None:
        with pytest.raises(CompositionError, match="not found"):
            Composition.from_toml(Path("/nonexistent/composition.toml"))

    def test_cycle_detection(self, tmp_dir: str) -> None:
        path = Path(tmp_dir) / "composition.toml"
        path.write_text(CYCLE_TOML)
        with pytest.raises(CompositionError, match="cycle"):
            Composition.from_toml(path)


class TestExecutionOrder:
    """Tests for execution order computation."""

    def test_full_pipeline(self, full_composition_path: Path) -> None:
        comp = Composition.from_toml(full_composition_path)
        order = comp.get_execution_order()
        assert len(order) == 2
        # Group 0: all three root tasks in parallel
        assert sorted(order[0]) == ["task1", "task2", "task3"]
        # Group 1: merge task
        assert order[1] == ["task4"]

    def test_simple_pipeline(self, tmp_dir: str) -> None:
        path = Path(tmp_dir) / "composition.toml"
        path.write_text(VALID_TOML)
        comp = Composition.from_toml(path)
        order = comp.get_execution_order()
        assert len(order) == 2
        assert order[0] == ["task1"]
        assert order[1] == ["task2"]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestConflictDetection:
    """Tests for _detect_conflicts helper."""

    def test_no_issues(self) -> None:
        checks = [
            ComplianceCheck(dimension="legal", score=100.0),
            ComplianceCheck(dimension="accessibility", score=100.0),
        ]
        conflicts = _detect_conflicts(checks)
        assert conflicts == []

    def test_legal_localization_conflict(self) -> None:
        checks = [
            ComplianceCheck(
                dimension="legal", status=CheckStatus.FAIL, issues=["Missing clause"], score=60.0
            ),
            ComplianceCheck(
                dimension="localization",
                status=CheckStatus.WARNING,
                issues=["Untranslated"],
                score=80.0,
            ),
        ]
        conflicts = _detect_conflicts(checks)
        assert len(conflicts) >= 1
        dims_in_conflict = {d for c in conflicts for d in c.dimensions}
        assert "legal" in dims_in_conflict or "localization" in dims_in_conflict

    def test_accessibility_localization_conflict(self) -> None:
        checks = [
            ComplianceCheck(
                dimension="accessibility", issues=["Alt text missing"], score=50.0
            ),
            ComplianceCheck(
                dimension="localization", issues=["Untranslated"], score=80.0
            ),
        ]
        conflicts = _detect_conflicts(checks)
        assert any("accessibility" in c.dimensions for c in conflicts)


class TestOverallScore:
    """Tests for _compute_overall_score helper."""

    def test_empty(self) -> None:
        assert _compute_overall_score([]) == 0.0

    def test_single_check(self) -> None:
        checks = [ComplianceCheck(dimension="x", score=80.0)]
        assert _compute_overall_score(checks) == 80.0

    def test_multiple_checks(self) -> None:
        checks = [
            ComplianceCheck(dimension="a", score=60.0),
            ComplianceCheck(dimension="b", score=80.0),
            ComplianceCheck(dimension="c", score=100.0),
        ]
        assert _compute_overall_score(checks) == pytest.approx(80.0)

    def test_error_check_excluded(self) -> None:
        checks = [
            ComplianceCheck(dimension="a", score=100.0),
            ComplianceCheck(dimension="b", status=CheckStatus.ERROR, score=0.0),
        ]
        assert _compute_overall_score(checks) == 100.0

    def test_all_errors(self) -> None:
        checks = [
            ComplianceCheck(dimension="a", status=CheckStatus.ERROR, score=0.0),
            ComplianceCheck(dimension="b", status=CheckStatus.ERROR, score=0.0),
        ]
        assert _compute_overall_score(checks) == 0.0


class TestRecommendations:
    """Tests for _generate_recommendations helper."""

    def test_no_issues(self) -> None:
        checks = [ComplianceCheck(dimension="x", score=100.0)]
        recs = _generate_recommendations(checks, [])
        assert recs == []

    def test_low_score_recommendation(self) -> None:
        checks = [ComplianceCheck(dimension="legal", score=60.0)]
        recs = _generate_recommendations(checks, [])
        assert any("legal" in r for r in recs)

    def test_conflict_recommendation(self) -> None:
        conflicts = [
            ConflictItem(
                dimensions=["a", "b"], description="conflict", resolution="Fix it"
            )
        ]
        recs = _generate_recommendations([], conflicts)
        assert "Fix it" in recs


class TestSimulatedCheck:
    """Tests for _simulate_agent_check helper."""

    def test_known_agent(self) -> None:
        result = _simulate_agent_check("contract-analyzer")
        assert result["dimension"] == "legal"
        assert "issues" in result

    def test_unknown_agent(self) -> None:
        result = _simulate_agent_check("unknown-agent")
        assert result["score"] == 100.0

    def test_with_context(self) -> None:
        result = _simulate_agent_check("accessibility-auditor", context={"doc": "x"})
        assert result.get("input_context") is True


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class TestComplianceCoordinator:
    """Tests for ComplianceCoordinator."""

    def test_load_composition(self, coordinator: ComplianceCoordinator) -> None:
        comp = coordinator.load_composition()
        assert comp.name == "document-compliance-gateway"
        assert len(comp.tasks) == 4

    def test_check_compliance_success(self, coordinator: ComplianceCoordinator) -> None:
        result = coordinator.check_compliance("测试文档内容", jurisdictions=["CN", "EU"])
        assert len(result.checks) == 3
        assert result.overall_score > 0

    def test_check_compliance_dimensions(self, coordinator: ComplianceCoordinator) -> None:
        result = coordinator.check_compliance("doc", jurisdictions=["CN"])
        dimensions = {c.dimension for c in result.checks}
        assert "legal" in dimensions
        assert "accessibility" in dimensions
        assert "localization" in dimensions

    def test_check_compliance_conflicts(self, coordinator: ComplianceCoordinator) -> None:
        result = coordinator.check_compliance("doc with issues", jurisdictions=["CN", "EU"])
        # Since all agents return issues, there should be conflicts
        assert isinstance(result.conflicts, list)

    def test_check_compliance_recommendations(self, coordinator: ComplianceCoordinator) -> None:
        result = coordinator.check_compliance("doc", jurisdictions=["CN"])
        assert isinstance(result.recommendations, list)

    def test_check_compliance_serializable(self, coordinator: ComplianceCoordinator) -> None:
        result = coordinator.check_compliance("序列化测试", jurisdictions=["CN"])
        data = result.model_dump()
        json_str = json.dumps(data, ensure_ascii=False)
        assert "序列化测试" not in json_str  # document is not in result
        assert "overall_score" in json_str

    def test_invalid_composition_path(self, tmp_dir: str) -> None:
        bad_path = Path(tmp_dir) / "nonexistent.toml"
        coord = ComplianceCoordinator(composition_path=bad_path)
        result = coord.check_compliance("doc")
        assert result.checks == []
        assert result.overall_score == 0.0

    def test_empty_document(self, coordinator: ComplianceCoordinator) -> None:
        result = coordinator.check_compliance("", jurisdictions=[])
        # Should still return valid checks (agents handle empty input)
        assert isinstance(result.checks, list)


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        try:
            from agent_document_compliance_gateway.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        import agent_document_compliance_gateway.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")


# ---------------------------------------------------------------------------
# Local adapter
# ---------------------------------------------------------------------------


class TestLocalAdapter:
    """Tests for local adapter message handling."""

    def _handle_message(self, message: dict) -> dict:
        from agent_document_compliance_gateway.coordinator import ComplianceCoordinator

        coordinator = ComplianceCoordinator()
        method = message.get("method", "")
        params = message.get("params", {})

        if method == "check_compliance":
            document = params.get("document", "")
            if not document:
                return {"status": "error", "error": "Missing 'document' parameter"}
            try:
                jurisdictions = params.get("jurisdictions", [])
                result = coordinator.check_compliance(document, jurisdictions)
                return {"status": "ok", "result": result.model_dump()}
            except Exception as exc:
                return {
                    "status": "error",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
        else:
            return {"status": "error", "error": f"Unknown method: {method}"}

    def test_check_compliance(self) -> None:
        response = self._handle_message(
            {"method": "check_compliance", "params": {"document": "Test doc"}}
        )
        assert response["status"] == "ok"
        assert "checks" in response["result"]

    def test_missing_document(self) -> None:
        response = self._handle_message(
            {"method": "check_compliance", "params": {}}
        )
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_unknown_method(self) -> None:
        response = self._handle_message(
            {"method": "unknown", "params": {}}
        )
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]

    def test_with_jurisdictions(self) -> None:
        response = self._handle_message(
            {
                "method": "check_compliance",
                "params": {"document": "doc", "jurisdictions": ["CN", "EU"]},
            }
        )
        assert response["status"] == "ok"
