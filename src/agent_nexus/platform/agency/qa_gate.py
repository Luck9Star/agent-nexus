"""QA Gate — output contract validation + GitNexus gate hook."""

from __future__ import annotations

from dataclasses import dataclass, field

# Task types that require GitNexus checks per doc §11
_CODE_CHANGE_TASK_TYPES = {"code_change", "refactor", "symbol_edit"}


@dataclass
class ContractValidationResult:
    """Result of output contract validation."""

    passed: bool
    missing_sections: list[str] = field(default_factory=list)


@dataclass
class GitNexusCheckResult:
    """Result of GitNexus gate check."""

    passed: bool
    skipped: bool = False
    failed_checks: list[str] = field(default_factory=list)


@dataclass
class QAGateInput:
    """Input for a full QA gate run."""

    output: dict
    required_sections: list[str]
    task_type: str
    impact_analysis_completed: bool = False
    detect_changes_completed: bool = False


@dataclass
class QAGateResult:
    """Combined result from contract validation and GitNexus gate."""

    passed: bool
    contract_result: ContractValidationResult
    gitnexus_result: GitNexusCheckResult
    failures: list[str] = field(default_factory=list)


class QAGate:
    """QA Gate with output contract validation and GitNexus hook.

    Per doc §11, code-changing workflows must run impact_analysis and
    detect_changes before proceeding. Per doc §9.2, the QA Gate checks
    output contracts for missing sections.
    """

    @staticmethod
    def validate_contract(
        output: dict,
        required_sections: list[str],
    ) -> ContractValidationResult:
        """Validate that the output contains all required sections.

        Parameters
        ----------
        output:
            Dict with at least ``sections`` key mapping to a dict.
        required_sections:
            List of section names that must be present.
        """
        sections = output.get("sections", {})
        missing = [s for s in required_sections if s not in sections]

        # Validate that present sections have non-empty, non-whitespace values
        empty_sections: list[str] = []
        for s in required_sections:
            if s in sections:
                value = sections[s]
                if value is None:
                    empty_sections.append(s)
                elif isinstance(value, str) and value.strip() == "":
                    empty_sections.append(s)

        all_missing = missing + empty_sections

        return ContractValidationResult(
            passed=len(all_missing) == 0,
            missing_sections=all_missing,
        )

    @staticmethod
    def check_gitnexus_gate(
        task_type: str,
        impact_analysis_completed: bool = False,
        detect_changes_completed: bool = False,
    ) -> GitNexusCheckResult:
        """Check GitNexus gate requirements for code-changing tasks.

        Per doc §11, code_change/refactor/symbol_edit tasks require:
        1. impact_analysis_completed
        2. detect_changes_completed

        Non-code tasks skip GitNexus checks entirely.
        """
        if task_type not in _CODE_CHANGE_TASK_TYPES:
            return GitNexusCheckResult(passed=True, skipped=True)

        failed: list[str] = []
        if not impact_analysis_completed:
            failed.append("impact_analysis_completed")
        if not detect_changes_completed:
            failed.append("detect_changes_completed")

        return GitNexusCheckResult(
            passed=len(failed) == 0,
            skipped=False,
            failed_checks=failed,
        )

    @staticmethod
    def run(gate_input: QAGateInput) -> QAGateResult:
        """Run full QA gate: contract validation + GitNexus check."""
        contract = QAGate.validate_contract(
            gate_input.output,
            gate_input.required_sections,
        )

        gitnexus = QAGate.check_gitnexus_gate(
            task_type=gate_input.task_type,
            impact_analysis_completed=gate_input.impact_analysis_completed,
            detect_changes_completed=gate_input.detect_changes_completed,
        )

        overall = contract.passed and gitnexus.passed

        failures: list[str] = []
        if not contract.passed:
            failures.append(f"Missing sections: {contract.missing_sections}")
        if not gitnexus.passed:
            failures.append(f"GitNexus gate failed: {gitnexus.failed_checks}")

        return QAGateResult(
            passed=overall,
            contract_result=contract,
            gitnexus_result=gitnexus,
            failures=failures,
        )
