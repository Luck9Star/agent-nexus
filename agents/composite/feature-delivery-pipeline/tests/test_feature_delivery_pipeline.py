"""Comprehensive tests for feature-delivery-pipeline Composite Agent.

Covers:
- Models: construction, validation, serialization, immutability
- Composition parsing: TOML loading, task extraction, validation
- DAG execution order: root tasks, dependents, parallel groups
- Coordinator: full pipeline execution, error handling
- MCP adapter: server creation
- Local adapter: message dispatch
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from agent_nexus.models.composition import (
    Composition,
    CompositionError,
    CompositionTask,
    _detect_cycles,
)
from pydantic import ValidationError

from agent_feature_delivery_pipeline.coordinator import (
    FeatureDeliveryCoordinator,
    _simulate_agent_execution,
)
from agent_feature_delivery_pipeline.models import (
    PipelineResult,
    PipelineStage,
    StageStatus,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


VALID_TOML = """\
[composition]
name = "test-pipeline"
description = "Test pipeline"

[tasks.task1]
name = "analysis"
agent = "requirements-analyzer"
blocked_by = []

[tasks.task2]
name = "generation"
agent = "api-doc-generator"
blocked_by = ["task1"]
"""

COMPLEX_TOML = """\
[composition]
name = "complex-pipeline"
description = "Complex test pipeline"

[tasks.task1]
name = "root"
agent = "requirements-analyzer"
blocked_by = []

[tasks.task2]
name = "parallel-a"
agent = "api-doc-generator"
blocked_by = ["task1"]

[tasks.task3]
name = "parallel-b"
agent = "test-suite-generator"
blocked_by = ["task1"]

[tasks.task4]
name = "parallel-c"
agent = "code-reviewer"
blocked_by = ["task1"]
"""

PARALLEL_ONLY_TOML = """\
[composition]
name = "parallel-only"
description = "All tasks are root tasks"

[tasks.task1]
name = "alpha"
agent = "security-scanner"
blocked_by = []

[tasks.task2]
name = "beta"
agent = "code-reviewer"
blocked_by = []
"""

CHAIN_TOML = """\
[composition]
name = "chain"
description = "Sequential chain"

[tasks.task1]
name = "step-1"
agent = "requirements-analyzer"
blocked_by = []

[tasks.task2]
name = "step-2"
agent = "api-doc-generator"
blocked_by = ["task1"]

[tasks.task3]
name = "step-3"
agent = "code-reviewer"
blocked_by = ["task2"]
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
def valid_composition_path(tmp_dir: str) -> Path:
    """Write a valid composition.toml and return its path."""
    path = Path(tmp_dir) / "composition.toml"
    path.write_text(VALID_TOML)
    return path


@pytest.fixture
def complex_composition_path(tmp_dir: str) -> Path:
    """Write a complex composition.toml and return its path."""
    path = Path(tmp_dir) / "composition.toml"
    path.write_text(COMPLEX_TOML)
    return path


@pytest.fixture
def coordinator() -> FeatureDeliveryCoordinator:
    """Provide a FeatureDeliveryCoordinator using the bundled composition.toml."""
    return FeatureDeliveryCoordinator()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestStageStatus:
    """Tests for StageStatus enum."""

    def test_values(self) -> None:
        assert StageStatus.PENDING == "pending"
        assert StageStatus.IN_PROGRESS == "in_progress"
        assert StageStatus.COMPLETED == "completed"
        assert StageStatus.FAILED == "failed"
        assert StageStatus.SKIPPED == "skipped"


class TestPipelineStage:
    """Tests for PipelineStage model."""

    def test_basic_construction(self) -> None:
        s = PipelineStage(name="analysis", agent="requirements-analyzer")
        assert s.name == "analysis"
        assert s.agent == "requirements-analyzer"
        assert s.status == StageStatus.PENDING
        assert s.result is None
        assert s.error is None

    def test_full_construction(self) -> None:
        s = PipelineStage(
            name="review",
            agent="code-reviewer",
            status=StageStatus.COMPLETED,
            result={"score": 90},
        )
        assert s.status == StageStatus.COMPLETED
        assert s.result == {"score": 90}

    def test_failed_stage(self) -> None:
        s = PipelineStage(
            name="bad",
            agent="test-agent",
            status=StageStatus.FAILED,
            error="Agent crashed",
        )
        assert s.status == StageStatus.FAILED
        assert s.error == "Agent crashed"

    def test_frozen(self) -> None:
        s = PipelineStage(name="x", agent="y")
        with pytest.raises(ValidationError):
            s.name = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        s = PipelineStage(name="a", agent="b", status=StageStatus.COMPLETED, result={"k": 1})
        data = s.model_dump()
        s2 = PipelineStage.model_validate(data)
        assert s == s2

    def test_json_serialization(self) -> None:
        s = PipelineStage(name="test", agent="agent", status=StageStatus.IN_PROGRESS)
        json_str = s.model_dump_json()
        data = json.loads(json_str)
        assert data["status"] == "in_progress"


class TestPipelineResult:
    """Tests for PipelineResult model."""

    def test_minimal(self) -> None:
        r = PipelineResult(spec="test spec")
        assert r.spec == "test spec"
        assert r.stages == []
        assert r.artifacts == {}
        assert r.success is False

    def test_success_result(self) -> None:
        stages = [
            PipelineStage(name="a", agent="x", status=StageStatus.COMPLETED),
            PipelineStage(name="b", agent="y", status=StageStatus.COMPLETED),
        ]
        r = PipelineResult(
            spec="spec",
            stages=stages,
            artifacts={"a": {"data": 1}},
            success=True,
        )
        assert r.success is True
        assert len(r.stages) == 2

    def test_frozen(self) -> None:
        r = PipelineResult(spec="test")
        with pytest.raises(ValidationError):
            r.success = True  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        r = PipelineResult(
            spec="spec",
            stages=[PipelineStage(name="a", agent="x", status=StageStatus.COMPLETED)],
            artifacts={"a": {"data": 1}},
            success=True,
        )
        data = r.model_dump()
        r2 = PipelineResult.model_validate(data)
        assert r == r2


# ---------------------------------------------------------------------------
# CompositionTask
# ---------------------------------------------------------------------------


class TestCompositionTask:
    """Tests for CompositionTask."""

    def test_basic(self) -> None:
        t = CompositionTask(task_id="t1", name="task", agent="agent-a", blocked_by=[])
        assert t.id == "t1"
        assert t.blocked_by == []

    def test_repr(self) -> None:
        t = CompositionTask(task_id="t1", name="task", agent="agent-a", blocked_by=[])
        assert "t1" in repr(t)
        assert "agent-a" in repr(t)


# ---------------------------------------------------------------------------
# Composition parsing
# ---------------------------------------------------------------------------


class TestCompositionParsing:
    """Tests for Composition.from_toml()."""

    def test_valid_composition(self, valid_composition_path: Path) -> None:
        comp = Composition.from_toml(valid_composition_path)
        assert comp.name == "test-pipeline"
        assert comp.description == "Test pipeline"
        assert len(comp.tasks) == 2

    def test_task_fields(self, valid_composition_path: Path) -> None:
        comp = Composition.from_toml(valid_composition_path)
        task1 = comp.tasks["task1"]
        assert task1.name == "analysis"
        assert task1.agent == "requirements-analyzer"
        assert task1.blocked_by == []

    def test_blocked_by(self, valid_composition_path: Path) -> None:
        comp = Composition.from_toml(valid_composition_path)
        task2 = comp.tasks["task2"]
        assert task2.blocked_by == ["task1"]

    def test_file_not_found(self) -> None:
        with pytest.raises(CompositionError, match="not found"):
            Composition.from_toml(Path("/nonexistent/composition.toml"))

    def test_invalid_toml(self, tmp_dir: str) -> None:
        path = Path(tmp_dir) / "bad.toml"
        path.write_text("this is [not valid toml {{{")
        with pytest.raises(CompositionError, match="Invalid TOML"):
            Composition.from_toml(path)

    def test_unknown_blocked_by(self, tmp_dir: str) -> None:
        toml_content = """\
[composition]
name = "bad"
description = "bad"

[tasks.task1]
name = "a"
agent = "agent-a"
blocked_by = ["nonexistent"]
"""
        path = Path(tmp_dir) / "composition.toml"
        path.write_text(toml_content)
        with pytest.raises(CompositionError, match="unknown task"):
            Composition.from_toml(path)

    def test_cycle_detection(self, tmp_dir: str) -> None:
        path = Path(tmp_dir) / "composition.toml"
        path.write_text(CYCLE_TOML)
        with pytest.raises(CompositionError, match="cycle"):
            Composition.from_toml(path)

    def test_get_root_tasks(self, complex_composition_path: Path) -> None:
        comp = Composition.from_toml(complex_composition_path)
        roots = comp.get_root_tasks()
        assert len(roots) == 1
        assert roots[0].id == "task1"

    def test_get_dependents(self, complex_composition_path: Path) -> None:
        comp = Composition.from_toml(complex_composition_path)
        deps = comp.get_dependents("task1")
        dep_ids = {d.id for d in deps}
        assert dep_ids == {"task2", "task3", "task4"}


# ---------------------------------------------------------------------------
# Execution order
# ---------------------------------------------------------------------------


class TestExecutionOrder:
    """Tests for Composition.get_execution_order()."""

    def test_sequential_parallel(self, complex_composition_path: Path) -> None:
        comp = Composition.from_toml(complex_composition_path)
        order = comp.get_execution_order()
        assert len(order) == 2
        # Group 0: root task
        assert order[0] == ["task1"]
        # Group 1: all parallel dependents
        assert sorted(order[1]) == ["task2", "task3", "task4"]

    def test_simple_chain(self, tmp_dir: str) -> None:
        path = Path(tmp_dir) / "composition.toml"
        path.write_text(CHAIN_TOML)
        comp = Composition.from_toml(path)
        order = comp.get_execution_order()
        assert len(order) == 3
        assert order[0] == ["task1"]
        assert order[1] == ["task2"]
        assert order[2] == ["task3"]

    def test_all_parallel(self, tmp_dir: str) -> None:
        path = Path(tmp_dir) / "composition.toml"
        path.write_text(PARALLEL_ONLY_TOML)
        comp = Composition.from_toml(path)
        order = comp.get_execution_order()
        assert len(order) == 1
        assert sorted(order[0]) == ["task1", "task2"]

    def test_two_tasks(self, valid_composition_path: Path) -> None:
        comp = Composition.from_toml(valid_composition_path)
        order = comp.get_execution_order()
        assert len(order) == 2
        assert order[0] == ["task1"]
        assert order[1] == ["task2"]


# ---------------------------------------------------------------------------
# Cycle detection
# ---------------------------------------------------------------------------


class TestCycleDetection:
    """Tests for _detect_cycles helper."""

    def test_no_cycle(self) -> None:
        tasks = {
            "t1": CompositionTask("t1", "a", "agent", []),
            "t2": CompositionTask("t2", "b", "agent", ["t1"]),
        }
        # Should not raise
        _detect_cycles(tasks)

    def test_simple_cycle(self) -> None:
        tasks = {
            "t1": CompositionTask("t1", "a", "agent", ["t2"]),
            "t2": CompositionTask("t2", "b", "agent", ["t1"]),
        }
        with pytest.raises(CompositionError, match="cycle"):
            _detect_cycles(tasks)

    def test_self_cycle(self) -> None:
        tasks = {
            "t1": CompositionTask("t1", "a", "agent", ["t1"]),
        }
        with pytest.raises(CompositionError, match="cycle"):
            _detect_cycles(tasks)

    def test_three_node_cycle(self) -> None:
        tasks = {
            "t1": CompositionTask("t1", "a", "agent", ["t3"]),
            "t2": CompositionTask("t2", "b", "agent", ["t1"]),
            "t3": CompositionTask("t3", "c", "agent", ["t2"]),
        }
        with pytest.raises(CompositionError, match="cycle"):
            _detect_cycles(tasks)


# ---------------------------------------------------------------------------
# Simulated agent execution
# ---------------------------------------------------------------------------


class TestSimulatedExecution:
    """Tests for _simulate_agent_execution helper."""

    def test_known_agent(self) -> None:
        result = _simulate_agent_execution("requirements-analyzer")
        assert "summary" in result

    def test_unknown_agent(self) -> None:
        result = _simulate_agent_execution("unknown-agent")
        assert "output" in result

    def test_with_context(self) -> None:
        result = _simulate_agent_execution("api-doc-generator", context={"data": 1})
        assert result.get("input_context") is True


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class TestFeatureDeliveryCoordinator:
    """Tests for FeatureDeliveryCoordinator."""

    def test_load_composition(self, coordinator: FeatureDeliveryCoordinator) -> None:
        comp = coordinator.load_composition()
        assert comp.name == "feature-delivery-pipeline"
        assert len(comp.tasks) == 4

    def test_run_pipeline_success(self, coordinator: FeatureDeliveryCoordinator) -> None:
        result = coordinator.run_pipeline("实现用户注册 API")
        assert result.success is True
        assert result.spec == "实现用户注册 API"
        assert len(result.stages) == 4
        assert all(s.status == StageStatus.COMPLETED for s in result.stages)

    def test_pipeline_artifacts(self, coordinator: FeatureDeliveryCoordinator) -> None:
        result = coordinator.run_pipeline("测试规格")
        assert "requirements-analysis" in result.artifacts
        assert "api-doc-generation" in result.artifacts
        assert "test-suite-generation" in result.artifacts
        assert "code-review" in result.artifacts

    def test_pipeline_stage_order(self, coordinator: FeatureDeliveryCoordinator) -> None:
        result = coordinator.run_pipeline("测试规格")
        # First stage should be the root task
        assert result.stages[0].agent == "requirements-analyzer"
        assert result.stages[0].status == StageStatus.COMPLETED

    def test_pipeline_serializable(self, coordinator: FeatureDeliveryCoordinator) -> None:
        result = coordinator.run_pipeline("序列化测试")
        data = result.model_dump()
        assert isinstance(data, dict)
        json_str = json.dumps(data, ensure_ascii=False)
        assert "序列化测试" in json_str

    def test_invalid_composition_path(self, tmp_dir: str) -> None:
        bad_path = Path(tmp_dir) / "nonexistent.toml"
        coordinator = FeatureDeliveryCoordinator(composition_path=bad_path)
        result = coordinator.run_pipeline("should fail")
        assert result.success is False
        assert result.stages == []

    def test_pipeline_with_simulated_failure(self, coordinator: FeatureDeliveryCoordinator) -> None:
        """Test that pipeline handles root task failure gracefully."""
        with patch(
            "agent_feature_delivery_pipeline.coordinator._simulate_agent_execution",
            side_effect=RuntimeError("Agent crashed"),
        ):
            result = coordinator.run_pipeline("will fail")
            assert result.success is False
            # Root task failed
            root_stage = result.stages[0]
            assert root_stage.agent == "requirements-analyzer"
            assert root_stage.status == StageStatus.FAILED


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        """Test MCP adapter handles missing fastmcp."""
        try:
            from agent_feature_delivery_pipeline.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        """The mcp_adapter module should always be importable."""
        import agent_feature_delivery_pipeline.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")


# ---------------------------------------------------------------------------
# Local adapter (via main.py)
# ---------------------------------------------------------------------------


class TestLocalAdapter:
    """Tests for local adapter message handling via stdin/stdout simulation."""

    def _handle_local_message(self, message: dict) -> dict:
        """Simulate local adapter message handling."""
        from agent_feature_delivery_pipeline.coordinator import FeatureDeliveryCoordinator

        coordinator = FeatureDeliveryCoordinator()
        method = message.get("method", "")
        params = message.get("params", {})

        if method == "run_pipeline":
            spec = params.get("spec", "")
            if not spec:
                return {"status": "error", "error": "Missing 'spec' parameter"}
            try:
                result = coordinator.run_pipeline(spec)
                return {"status": "ok", "result": result.model_dump()}
            except Exception as exc:
                return {
                    "status": "error",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
        else:
            return {"status": "error", "error": f"Unknown method: {method}"}

    def test_run_pipeline(self) -> None:
        response = self._handle_local_message(
            {"method": "run_pipeline", "params": {"spec": "Test spec"}}
        )
        assert response["status"] == "ok"
        assert response["result"]["success"] is True

    def test_missing_spec(self) -> None:
        response = self._handle_local_message({"method": "run_pipeline", "params": {}})
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_unknown_method(self) -> None:
        response = self._handle_local_message({"method": "unknown", "params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]
