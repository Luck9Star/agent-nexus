"""Comprehensive tests for cicd-quality-gate Composite Agent.

Covers:
- Models: construction, validation, serialization, immutability
- Composition parsing: TOML loading, task extraction, validation
- DAG execution order: parallel roots, merge step
- Coordinator: full gate execution, pass/fail decisions, scoring
- MCP adapter: server creation
- Local adapter: message dispatch
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_cicd_quality_gate.coordinator import (
    Composition,
    CompositionError,
    CompositionTask,
    QualityGateCoordinator,
    _detect_cycles,
    _make_gate_decision,
    _simulate_agent_check,
)
from agent_cicd_quality_gate.models import (
    GateCheck,
    GateResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


VALID_TOML = """\
[composition]
name = "test-gate"
description = "Test quality gate"

[tasks.task1]
name = "security"
agent = "security-scanner"
blocked_by = []

[tasks.task2]
name = "decision"
agent = "quality-gate-decider"
blocked_by = ["task1"]
"""

FULL_TOML = """\
[composition]
name = "full-gate"
description = "Full quality gate"

[tasks.task1]
name = "security-scan"
agent = "security-scanner"
blocked_by = []

[tasks.task2]
name = "code-review"
agent = "code-reviewer"
blocked_by = []

[tasks.task3]
name = "test-generation"
agent = "test-suite-generator"
blocked_by = []

[tasks.task4]
name = "quality-gate-decision"
agent = "quality-gate-decider"
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
def coordinator() -> QualityGateCoordinator:
    """Provide a QualityGateCoordinator using the bundled composition.toml."""
    return QualityGateCoordinator()


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestGateCheck:
    """Tests for GateCheck model."""

    def test_basic_construction(self) -> None:
        gc = GateCheck(agent="security-scanner")
        assert gc.agent == "security-scanner"
        assert gc.passed is True
        assert gc.findings == []
        assert gc.score == 100.0

    def test_failed_check(self) -> None:
        gc = GateCheck(
            agent="code-reviewer",
            passed=False,
            findings=["Function too long", "Missing docs"],
            score=45.0,
        )
        assert gc.passed is False
        assert len(gc.findings) == 2
        assert gc.score == 45.0

    def test_frozen(self) -> None:
        gc = GateCheck(agent="x")
        with pytest.raises(Exception):
            gc.agent = "y"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        gc = GateCheck(agent="test", passed=True, findings=["a"], score=88.5)
        data = gc.model_dump()
        gc2 = GateCheck.model_validate(data)
        assert gc == gc2

    def test_json_serialization(self) -> None:
        gc = GateCheck(agent="scanner", score=95.0)
        json_str = gc.model_dump_json()
        data = json.loads(json_str)
        assert data["score"] == 95.0


class TestGateResult:
    """Tests for GateResult model."""

    def test_empty(self) -> None:
        r = GateResult()
        assert r.checks == []
        assert r.overall_passed is False
        assert r.gate_score == 0.0
        assert r.blockers == []
        assert r.warnings == []

    def test_passed_result(self) -> None:
        checks = [
            GateCheck(agent="security-scanner", passed=True, score=95.0),
            GateCheck(agent="code-reviewer", passed=True, score=90.0),
        ]
        r = GateResult(checks=checks, overall_passed=True, gate_score=92.5)
        assert r.overall_passed is True
        assert r.gate_score == 92.5

    def test_failed_result(self) -> None:
        r = GateResult(
            checks=[GateCheck(agent="scanner", passed=False, score=30.0)],
            overall_passed=False,
            gate_score=30.0,
            blockers=["Critical vulnerability found"],
            warnings=["Consider improving coverage"],
        )
        assert r.overall_passed is False
        assert len(r.blockers) == 1
        assert len(r.warnings) == 1

    def test_frozen(self) -> None:
        r = GateResult()
        with pytest.raises(Exception):
            r.overall_passed = True  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        r = GateResult(
            checks=[GateCheck(agent="x", passed=True, score=80.0)],
            overall_passed=True,
            gate_score=80.0,
            blockers=[],
            warnings=["Minor issue"],
        )
        data = r.model_dump()
        r2 = GateResult.model_validate(data)
        assert r == r2


# ---------------------------------------------------------------------------
# Composition parsing
# ---------------------------------------------------------------------------


class TestCompositionParsing:
    """Tests for Composition.from_toml()."""

    def test_full_composition(self, full_composition_path: Path) -> None:
        comp = Composition.from_toml(full_composition_path)
        assert comp.name == "full-gate"
        assert len(comp.tasks) == 4

    def test_root_tasks(self, full_composition_path: Path) -> None:
        comp = Composition.from_toml(full_composition_path)
        roots = comp.get_root_tasks()
        root_ids = {t.id for t in roots}
        assert root_ids == {"task1", "task2", "task3"}

    def test_merge_task(self, full_composition_path: Path) -> None:
        comp = Composition.from_toml(full_composition_path)
        task4 = comp.tasks["task4"]
        assert task4.agent == "quality-gate-decider"
        assert sorted(task4.blocked_by) == ["task1", "task2", "task3"]

    def test_file_not_found(self) -> None:
        with pytest.raises(CompositionError, match="not found"):
            Composition.from_toml(Path("/nonexistent/composition.toml"))

    def test_invalid_toml(self, tmp_dir: str) -> None:
        path = Path(tmp_dir) / "bad.toml"
        path.write_text("invalid [[[")
        with pytest.raises(CompositionError, match="Invalid TOML"):
            Composition.from_toml(path)

    def test_cycle_detection(self, tmp_dir: str) -> None:
        path = Path(tmp_dir) / "composition.toml"
        path.write_text(CYCLE_TOML)
        with pytest.raises(CompositionError, match="cycle"):
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


class TestExecutionOrder:
    """Tests for execution order computation."""

    def test_full_pipeline(self, full_composition_path: Path) -> None:
        comp = Composition.from_toml(full_composition_path)
        order = comp.get_execution_order()
        assert len(order) == 2
        assert sorted(order[0]) == ["task1", "task2", "task3"]
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
# Cycle detection
# ---------------------------------------------------------------------------


class TestCycleDetection:
    """Tests for _detect_cycles helper."""

    def test_no_cycle(self) -> None:
        tasks = {
            "t1": CompositionTask("t1", "a", "agent", []),
            "t2": CompositionTask("t2", "b", "agent", ["t1"]),
        }
        _detect_cycles(tasks)  # Should not raise

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


# ---------------------------------------------------------------------------
# Gate decision helper
# ---------------------------------------------------------------------------


class TestGateDecision:
    """Tests for _make_gate_decision helper."""

    def test_all_pass(self) -> None:
        checks = [
            GateCheck(agent="security-scanner", passed=True, score=95.0),
            GateCheck(agent="code-reviewer", passed=True, score=90.0),
        ]
        passed, score, blockers, warnings = _make_gate_decision(checks, {})
        assert passed is True
        assert score == pytest.approx(92.5)
        assert blockers == []

    def test_one_fails(self) -> None:
        checks = [
            GateCheck(agent="security-scanner", passed=False, findings=["CVE-2024-1234"], score=30.0),
            GateCheck(agent="code-reviewer", passed=True, score=90.0),
        ]
        passed, score, blockers, warnings = _make_gate_decision(checks, {})
        assert passed is False
        assert len(blockers) >= 1

    def test_empty_checks(self) -> None:
        passed, score, blockers, warnings = _make_gate_decision([], {})
        assert passed is False
        assert score == 0.0
        assert len(blockers) >= 1

    def test_below_security_threshold(self) -> None:
        checks = [
            GateCheck(agent="security-scanner", passed=True, score=50.0),
        ]
        passed, score, blockers, warnings = _make_gate_decision(
            checks, {"security_threshold": 80.0}
        )
        assert passed is False
        assert any("Security" in b for b in blockers)

    def test_below_review_threshold(self) -> None:
        checks = [
            GateCheck(agent="code-reviewer", passed=True, score=60.0),
        ]
        passed, score, blockers, warnings = _make_gate_decision(
            checks, {"review_threshold": 70.0}
        )
        assert passed is False
        assert any("Review" in b for b in blockers)

    def test_warnings_with_findings(self) -> None:
        checks = [
            GateCheck(agent="code-reviewer", passed=True, findings=["Minor issue"], score=80.0),
        ]
        passed, score, blockers, warnings = _make_gate_decision(checks, {})
        assert passed is True
        assert len(warnings) >= 1

    def test_custom_thresholds(self) -> None:
        checks = [
            GateCheck(agent="security-scanner", passed=True, score=85.0),
        ]
        # Default threshold is 80, so 85 should pass
        passed, _, _, _ = _make_gate_decision(checks, {})
        assert passed is True

        # With higher threshold, should fail
        passed, _, blockers, _ = _make_gate_decision(
            checks, {"security_threshold": 90.0}
        )
        assert passed is False


# ---------------------------------------------------------------------------
# Simulated agent check
# ---------------------------------------------------------------------------


class TestSimulatedCheck:
    """Tests for _simulate_agent_check helper."""

    def test_known_agent(self) -> None:
        result = _simulate_agent_check("security-scanner")
        assert "risk_score" in result
        assert "vulnerabilities" in result

    def test_code_reviewer(self) -> None:
        result = _simulate_agent_check("code-reviewer")
        assert "quality_score" in result

    def test_test_generator(self) -> None:
        result = _simulate_agent_check("test-suite-generator")
        assert "coverage" in result
        assert "test_count" in result

    def test_unknown_agent(self) -> None:
        result = _simulate_agent_check("unknown")
        assert "output" in result

    def test_with_context(self) -> None:
        result = _simulate_agent_check("security-scanner", context={"code_path": "/x"})
        assert result.get("input_context") is True


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class TestQualityGateCoordinator:
    """Tests for QualityGateCoordinator."""

    def test_load_composition(self, coordinator: QualityGateCoordinator) -> None:
        comp = coordinator.load_composition()
        assert comp.name == "cicd-quality-gate"
        assert len(comp.tasks) == 4

    def test_run_gate_pass(self, coordinator: QualityGateCoordinator) -> None:
        result = coordinator.run_gate("/path/to/code", {})
        assert isinstance(result, GateResult)
        assert len(result.checks) == 3
        # Default simulated results should all pass
        assert result.overall_passed is True

    def test_run_gate_scores(self, coordinator: QualityGateCoordinator) -> None:
        result = coordinator.run_gate("/path/to/code", {})
        assert result.gate_score > 0
        # All simulated agents return high scores
        assert all(c.score > 70 for c in result.checks)

    def test_run_gate_agents(self, coordinator: QualityGateCoordinator) -> None:
        result = coordinator.run_gate("/path/to/code", {})
        agents = {c.agent for c in result.checks}
        assert agents == {"security-scanner", "code-reviewer", "test-suite-generator"}

    def test_run_gate_serializable(self, coordinator: QualityGateCoordinator) -> None:
        result = coordinator.run_gate("/path/to/code", {})
        data = result.model_dump()
        json_str = json.dumps(data, ensure_ascii=False)
        assert "overall_passed" in json_str
        assert "gate_score" in json_str

    def test_run_gate_with_config(self, coordinator: QualityGateCoordinator) -> None:
        result = coordinator.run_gate(
            "/path/to/code",
            {"security_threshold": 99.0},  # Very high threshold
        )
        # Security scanner returns 95.0, which is below 99
        assert result.overall_passed is False
        assert any("Security" in b for b in result.blockers)

    def test_invalid_composition_path(self, tmp_dir: str) -> None:
        bad_path = Path(tmp_dir) / "nonexistent.toml"
        coord = QualityGateCoordinator(composition_path=bad_path)
        result = coord.run_gate("/path")
        assert result.checks == []
        assert result.overall_passed is False

    def test_run_gate_with_simulated_failure(
        self, coordinator: QualityGateCoordinator
    ) -> None:
        """Test that gate handles agent failures."""
        with patch(
            "agent_cicd_quality_gate.coordinator._simulate_agent_check",
            side_effect=RuntimeError("Agent error"),
        ):
            result = coordinator.run_gate("/path")
            assert result.overall_passed is False
            assert all(not c.passed for c in result.checks)

    def test_run_gate_no_blockers_by_default(self, coordinator: QualityGateCoordinator) -> None:
        result = coordinator.run_gate("/path", {})
        assert result.blockers == []

    def test_run_gate_warnings(self, coordinator: QualityGateCoordinator) -> None:
        """Code reviewer has minor findings that should appear as warnings."""
        result = coordinator.run_gate("/path", {})
        assert isinstance(result.warnings, list)
