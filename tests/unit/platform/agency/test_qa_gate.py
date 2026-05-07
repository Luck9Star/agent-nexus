"""Phase F tests: QA Gate — output contract validation, GitNexus gate hook."""

import pytest

from agent_nexus.platform.agency.qa_gate import (
    ContractValidationResult,
    QAGate,
    QAGateInput,
    QAGateResult,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def complete_output() -> dict:
    """Output satisfying architecture_plan contract."""
    return {
        "artifact_type": "architecture_plan",
        "sections": {
            "context": "...",
            "assumptions": [],
            "proposed_design": "...",
            "tradeoffs": [],
            "risks": [],
            "next_steps": [],
        },
    }


@pytest.fixture
def incomplete_output() -> dict:
    """Output missing 'risks' and 'next_steps' sections."""
    return {
        "artifact_type": "architecture_plan",
        "sections": {
            "context": "...",
            "assumptions": [],
            "proposed_design": "...",
            "tradeoffs": [],
        },
    }


# ---------------------------------------------------------------------------
# Contract validation
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestContractValidation:
    """QA Gate validates output contracts — missing sections should fail."""

    def test_complete_output_passes(self, complete_output: dict) -> None:
        result = QAGate.validate_contract(
            complete_output,
            required_sections=[
                "context",
                "assumptions",
                "proposed_design",
                "tradeoffs",
                "risks",
                "next_steps",
            ],
        )

        assert isinstance(result, ContractValidationResult)
        assert result.passed is True
        assert result.missing_sections == []

    def test_incomplete_output_fails(self, incomplete_output: dict) -> None:
        result = QAGate.validate_contract(
            incomplete_output,
            required_sections=[
                "context",
                "assumptions",
                "proposed_design",
                "tradeoffs",
                "risks",
                "next_steps",
            ],
        )

        assert result.passed is False
        assert "risks" in result.missing_sections
        assert "next_steps" in result.missing_sections

    def test_empty_output_fails(self) -> None:
        result = QAGate.validate_contract(
            {"artifact_type": "test", "sections": {}},
            required_sections=["summary"],
        )

        assert result.passed is False
        assert "summary" in result.missing_sections

    def test_no_required_sections_always_passes(self) -> None:
        result = QAGate.validate_contract(
            {"artifact_type": "test", "sections": {}},
            required_sections=[],
        )

        assert result.passed is True


# ---------------------------------------------------------------------------
# GitNexus gate
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestGitNexusGate:
    """Code-change workflows must trigger GitNexus impact_analysis."""

    def test_code_change_triggers_gitnexus(self) -> None:
        result = QAGate.check_gitnexus_gate(
            task_type="code_change",
            impact_analysis_completed=True,
            detect_changes_completed=True,
        )

        assert result.passed is True

    def test_code_change_without_impact_analysis_fails(self) -> None:
        result = QAGate.check_gitnexus_gate(
            task_type="code_change",
            impact_analysis_completed=False,
            detect_changes_completed=True,
        )

        assert result.passed is False
        assert any("impact_analysis" in c for c in result.failed_checks)

    def test_code_change_without_detect_changes_fails(self) -> None:
        result = QAGate.check_gitnexus_gate(
            task_type="code_change",
            impact_analysis_completed=True,
            detect_changes_completed=False,
        )

        assert result.passed is False
        assert any("detect_changes" in c for c in result.failed_checks)

    def test_plan_task_skips_gitnexus(self) -> None:
        """Non-code tasks don't need GitNexus checks."""
        result = QAGate.check_gitnexus_gate(
            task_type="architecture_review",
            impact_analysis_completed=False,
            detect_changes_completed=False,
        )

        assert result.passed is True
        assert result.skipped is True

    def test_refactor_triggers_gitnexus(self) -> None:
        result = QAGate.check_gitnexus_gate(
            task_type="refactor",
            impact_analysis_completed=True,
            detect_changes_completed=True,
        )

        assert result.passed is True

    def test_symbol_edit_triggers_gitnexus(self) -> None:
        result = QAGate.check_gitnexus_gate(
            task_type="symbol_edit",
            impact_analysis_completed=True,
            detect_changes_completed=True,
        )

        assert result.passed is True


# ---------------------------------------------------------------------------
# Full QA gate (contract + gitnexus)
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestFullQAGate:
    """Full QA gate runs both contract and GitNexus checks."""

    def test_full_pass_plan_task(self, complete_output: dict) -> None:
        gate_input = QAGateInput(
            output=complete_output,
            required_sections=[
                "context",
                "assumptions",
                "proposed_design",
                "tradeoffs",
                "risks",
                "next_steps",
            ],
            task_type="architecture_review",
        )
        result = QAGate.run(gate_input)

        assert isinstance(result, QAGateResult)
        assert result.passed is True
        assert result.contract_result.passed is True
        assert result.gitnexus_result.skipped is True

    def test_full_fail_missing_section_and_gitnexus(self, incomplete_output: dict) -> None:
        gate_input = QAGateInput(
            output=incomplete_output,
            required_sections=["context", "risks", "next_steps"],
            task_type="code_change",
        )
        result = QAGate.run(gate_input)

        assert result.passed is False
        assert result.contract_result.passed is False
        assert result.gitnexus_result.passed is False
