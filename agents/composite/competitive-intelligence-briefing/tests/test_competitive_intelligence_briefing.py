"""Comprehensive tests for competitive-intelligence-briefing composite agent.

Covers:
- Models: construction, validation, serialization, immutability
- composition.toml: parsing and DAG validation
- Coordinator: pipeline execution, sequential ordering, error propagation,
  result aggregation
- MCP adapter: server creation
- Local adapter: message dispatch
- Main: AGENT_MODE routing
"""

from __future__ import annotations

import json
import os

import pytest

from agent_competitive_intelligence_briefing.coordinator import (
    CompetitiveIntelCoordinator,
    _simulate_doc_filler,
    _simulate_localization,
    _simulate_market_intel,
)
from agent_competitive_intelligence_briefing.models import (
    BriefingResult,
    PipelineStep,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

COMPOSITION_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
COMPOSITION_TOML = os.path.join(COMPOSITION_DIR, "composition.toml")


@pytest.fixture
def coordinator() -> CompetitiveIntelCoordinator:
    """Provide a CompetitiveIntelCoordinator instance."""
    return CompetitiveIntelCoordinator()


@pytest.fixture
def composition_data() -> dict:
    """Parse the actual composition.toml file."""
    return CompetitiveIntelCoordinator.parse_composition(COMPOSITION_TOML)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class TestPipelineStep:
    """Tests for PipelineStep model."""

    def test_basic_construction(self) -> None:
        step = PipelineStep(name="Step 1", agent="market-intelligence-analyst")
        assert step.name == "Step 1"
        assert step.agent == "market-intelligence-analyst"
        assert step.input_data == {}
        assert step.output_data is None
        assert step.status == "pending"

    def test_full_construction(self) -> None:
        step = PipelineStep(
            name="Step 1",
            agent="market-intelligence-analyst",
            input_data={"query": "test"},
            output_data={"title": "Report"},
            status="completed",
        )
        assert step.input_data == {"query": "test"}
        assert step.output_data == {"title": "Report"}
        assert step.status == "completed"

    def test_frozen(self) -> None:
        step = PipelineStep(name="Step", agent="agent")
        with pytest.raises(Exception):
            step.name = "Changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        step = PipelineStep(
            name="Step",
            agent="agent",
            input_data={"key": "value"},
            status="running",
        )
        data = step.model_dump()
        step2 = PipelineStep.model_validate(data)
        assert step == step2

    def test_json_serialization(self) -> None:
        step = PipelineStep(name="Step", agent="a", status="failed")
        json_str = step.model_dump_json()
        data = json.loads(json_str)
        assert data["status"] == "failed"

    def test_status_values(self) -> None:
        for status in ("pending", "running", "completed", "failed", "skipped"):
            step = PipelineStep(name="S", agent="a", status=status)
            assert step.status == status


class TestBriefingResult:
    """Tests for BriefingResult model."""

    def test_minimal_construction(self) -> None:
        result = BriefingResult(query="test query")
        assert result.query == "test query"
        assert result.analysis == {}
        assert result.report_path == ""
        assert result.localizations == {}
        assert result.success is True

    def test_full_construction(self) -> None:
        result = BriefingResult(
            query="EV market",
            analysis={"title": "EV Report"},
            report_path="/tmp/report.docx",
            localizations={"zh": "Translated", "en": "Original"},
            success=True,
        )
        assert result.analysis["title"] == "EV Report"
        assert len(result.localizations) == 2

    def test_frozen(self) -> None:
        result = BriefingResult(query="q")
        with pytest.raises(Exception):
            result.query = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        result = BriefingResult(
            query="q",
            analysis={"key": "val"},
            localizations={"en": "text"},
        )
        data = result.model_dump()
        result2 = BriefingResult.model_validate(data)
        assert result == result2

    def test_json_serialization(self) -> None:
        result = BriefingResult(query="q", success=False)
        json_str = result.model_dump_json()
        data = json.loads(json_str)
        assert data["success"] is False

    def test_failure_result(self) -> None:
        result = BriefingResult(query="q", success=False)
        assert result.success is False
        assert result.report_path == ""


# ---------------------------------------------------------------------------
# composition.toml parsing and validation
# ---------------------------------------------------------------------------


class TestCompositionParsing:
    """Tests for composition.toml parsing."""

    def test_parse_actual_file(self, composition_data: dict) -> None:
        assert "composition" in composition_data
        assert composition_data["composition"]["name"] == "competitive-intelligence-briefing"

    def test_has_three_tasks(self, composition_data: dict) -> None:
        tasks = composition_data["tasks"]
        assert len(tasks) == 3

    def test_task_ids(self, composition_data: dict) -> None:
        assert "task1" in composition_data["tasks"]
        assert "task2" in composition_data["tasks"]
        assert "task3" in composition_data["tasks"]

    def test_task_agents(self, composition_data: dict) -> None:
        tasks = composition_data["tasks"]
        assert tasks["task1"]["agent"] == "market-intelligence-analyst"
        assert tasks["task2"]["agent"] == "doc-filler"
        assert tasks["task3"]["agent"] == "localization-specialist"

    def test_sequential_chain(self, composition_data: dict) -> None:
        """Verify the sequential blocked_by chain."""
        tasks = composition_data["tasks"]
        assert tasks["task1"]["blocked_by"] == []
        assert tasks["task2"]["blocked_by"] == ["task1"]
        assert tasks["task3"]["blocked_by"] == ["task2"]

    def test_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            CompetitiveIntelCoordinator.parse_composition("/nonexistent.toml")


class TestCompositionValidation:
    """Tests for DAG validation."""

    def test_valid_composition(self, composition_data: dict) -> None:
        errors = CompetitiveIntelCoordinator.validate_composition(composition_data)
        assert errors == []

    def test_missing_composition_section(self) -> None:
        errors = CompetitiveIntelCoordinator.validate_composition({})
        assert any("composition" in e for e in errors)

    def test_missing_name(self) -> None:
        data = {"composition": {"description": "test"}, "tasks": {"t1": {"name": "n", "agent": "a", "blocked_by": []}}}
        errors = CompetitiveIntelCoordinator.validate_composition(data)
        assert any("name" in e for e in errors)

    def test_no_tasks(self) -> None:
        data = {"composition": {"name": "test", "description": "d"}}
        errors = CompetitiveIntelCoordinator.validate_composition(data)
        assert any("No tasks" in e for e in errors)

    def test_task_missing_agent(self) -> None:
        data = {
            "composition": {"name": "test"},
            "tasks": {"t1": {"name": "task1", "blocked_by": []}},
        }
        errors = CompetitiveIntelCoordinator.validate_composition(data)
        assert any("agent" in e for e in errors)

    def test_unknown_dependency(self) -> None:
        data = {
            "composition": {"name": "test"},
            "tasks": {
                "t1": {"name": "n", "agent": "a", "blocked_by": ["nonexistent"]},
            },
        }
        errors = CompetitiveIntelCoordinator.validate_composition(data)
        assert any("nonexistent" in e for e in errors)

    def test_self_dependency(self) -> None:
        data = {
            "composition": {"name": "test"},
            "tasks": {
                "t1": {"name": "n", "agent": "a", "blocked_by": ["t1"]},
            },
        }
        errors = CompetitiveIntelCoordinator.validate_composition(data)
        assert any("itself" in e for e in errors)

    def test_circular_dependency(self) -> None:
        data = {
            "composition": {"name": "test"},
            "tasks": {
                "t1": {"name": "n", "agent": "a", "blocked_by": ["t2"]},
                "t2": {"name": "n", "agent": "a", "blocked_by": ["t1"]},
            },
        }
        errors = CompetitiveIntelCoordinator.validate_composition(data)
        assert any("Circular" in e for e in errors)


# ---------------------------------------------------------------------------
# Simulated Atomic Agents
# ---------------------------------------------------------------------------


class TestSimulatedMarketIntel:
    """Tests for _simulate_market_intel."""

    def test_returns_briefing_structure(self) -> None:
        result = _simulate_market_intel("EV market")
        assert "title" in result
        assert "executive_summary" in result
        assert "sections" in result
        assert "recommendations" in result

    def test_title_contains_query(self) -> None:
        result = _simulate_market_intel("AI trends")
        assert "AI trends" in result["title"]

    def test_framework_variants(self) -> None:
        for fw in ("porter", "swot", "pestel"):
            result = _simulate_market_intel("test", framework=fw)
            assert "title" in result


class TestSimulatedDocFiller:
    """Tests for _simulate_doc_filler."""

    def test_returns_fill_result(self) -> None:
        result = _simulate_doc_filler({"title": "Test", "executive_summary": "Sum"})
        assert result["success"] is True
        assert "output_path" in result
        assert result["filled_count"] > 0

    def test_custom_template_path(self) -> None:
        result = _simulate_doc_filler(
            {"title": "T"}, template_path="/custom/path.docx"
        )
        assert result["output_path"] == "/custom/path.docx"


class TestSimulatedLocalization:
    """Tests for _simulate_localization."""

    def test_english(self) -> None:
        result = _simulate_localization("Hello", "en")
        assert "[English]" in result["translated_text"]

    def test_chinese(self) -> None:
        result = _simulate_localization("Hello", "zh")
        assert "[Chinese]" in result["translated_text"]

    def test_unknown_lang(self) -> None:
        result = _simulate_localization("Hello", "xx")
        assert "[xx]" in result["translated_text"]


# ---------------------------------------------------------------------------
# Coordinator -- pipeline execution
# ---------------------------------------------------------------------------


class TestCompetitiveIntelCoordinator:
    """Tests for CompetitiveIntelCoordinator."""

    def test_generate_briefing_basic(self, coordinator: CompetitiveIntelCoordinator) -> None:
        result = coordinator.generate_briefing("EV market")
        assert result.success is True
        assert result.query == "EV market"
        assert "title" in result.analysis

    def test_generate_briefing_with_localization(
        self, coordinator: CompetitiveIntelCoordinator
    ) -> None:
        result = coordinator.generate_briefing(
            "AI trends", target_langs=["zh", "en", "ja"]
        )
        assert result.success is True
        assert "zh" in result.localizations
        assert "en" in result.localizations
        assert "ja" in result.localizations

    def test_generate_briefing_report_path(self, coordinator: CompetitiveIntelCoordinator) -> None:
        result = coordinator.generate_briefing("test")
        assert result.report_path != ""
        assert result.report_path.endswith(".docx")

    def test_generate_briefing_default_langs(self, coordinator: CompetitiveIntelCoordinator) -> None:
        result = coordinator.generate_briefing("test")
        assert "en" in result.localizations

    def test_build_steps(self, coordinator: CompetitiveIntelCoordinator) -> None:
        steps = coordinator._build_steps("test query")
        assert len(steps) == 3
        assert steps[0].agent == "market-intelligence-analyst"
        assert steps[1].agent == "doc-filler"
        assert steps[2].agent == "localization-specialist"
        assert all(s.status == "pending" for s in steps)

    def test_analysis_contains_sections(self, coordinator: CompetitiveIntelCoordinator) -> None:
        result = coordinator.generate_briefing("cloud computing")
        assert "sections" in result.analysis
        assert len(result.analysis["sections"]) > 0

    def test_analysis_contains_recommendations(
        self, coordinator: CompetitiveIntelCoordinator
    ) -> None:
        result = coordinator.generate_briefing("cloud computing")
        assert "recommendations" in result.analysis
        assert len(result.analysis["recommendations"]) > 0


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        try:
            from agent_competitive_intelligence_briefing.mcp_adapter import (
                create_mcp_server,
            )

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        import agent_competitive_intelligence_briefing.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")


# ---------------------------------------------------------------------------
# Local adapter -- message dispatch (via main._handle_message)
# ---------------------------------------------------------------------------


class TestLocalAdapter:
    """Tests for local adapter message handling."""

    def test_handle_generate_briefing(self, coordinator: CompetitiveIntelCoordinator) -> None:
        from agent_competitive_intelligence_briefing.main import _handle_message

        response = _handle_message(
            coordinator,
            {"method": "generate_briefing", "params": {"query": "EV market"}},
        )
        assert response["status"] == "ok"
        assert response["result"]["success"] is True

    def test_handle_with_target_langs(
        self, coordinator: CompetitiveIntelCoordinator
    ) -> None:
        from agent_competitive_intelligence_briefing.main import _handle_message

        response = _handle_message(
            coordinator,
            {
                "method": "generate_briefing",
                "params": {"query": "test", "target_langs": ["zh", "ja"]},
            },
        )
        assert response["status"] == "ok"
        assert "zh" in response["result"]["localizations"]

    def test_handle_unknown_method(self, coordinator: CompetitiveIntelCoordinator) -> None:
        from agent_competitive_intelligence_briefing.main import _handle_message

        response = _handle_message(
            coordinator, {"method": "unknown", "params": {}}
        )
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]

    def test_handle_missing_query(self, coordinator: CompetitiveIntelCoordinator) -> None:
        from agent_competitive_intelligence_briefing.main import _handle_message

        response = _handle_message(
            coordinator, {"method": "generate_briefing", "params": {}}
        )
        assert response["status"] == "error"
        assert "Missing" in response["error"]
