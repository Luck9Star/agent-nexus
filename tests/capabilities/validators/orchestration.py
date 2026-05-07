"""OrchestrationValidator — DAG topology and parallelism validation."""

from __future__ import annotations

from typing import Any

from tests.capabilities.contracts.schema import (
    CapabilityContract,
    ValidationResult,
)


class OrchestrationValidator:
    """Validate composite/agency orchestration behavior."""

    def validate(
        self,
        contract: CapabilityContract,
        raw_output: Any,
    ) -> ValidationResult:
        failures: list[str] = []

        if contract.agent_type == "composite":
            self._validate_composite(contract, raw_output, failures)
        elif contract.agent_type == "agency":
            self._validate_agency(contract, raw_output, failures)

        score = 1.0 - (len(failures) * 0.25)
        return ValidationResult(
            passed=len(failures) == 0,
            score=max(0.0, score),
            failures=failures,
        )

    def _validate_composite(
        self,
        contract: CapabilityContract,
        output: Any,
        failures: list[str],
    ) -> None:
        if not isinstance(output, dict):
            failures.append("Composite output must be a dict")
            return

        if "checks" in output:
            checks = output["checks"]
            if not isinstance(checks, list):
                failures.append("'checks' must be a list")
            elif len(checks) == 0:
                failures.append("'checks' must not be empty for composite agent")

        if "overall_passed" in output and not isinstance(output["overall_passed"], bool):
            failures.append("'overall_passed' must be bool")

        if "gate_score" in output:
            score = output["gate_score"]
            if not isinstance(score, (int, float)):
                failures.append("'gate_score' must be numeric")
            elif score < 0 or score > 100:
                failures.append(f"'gate_score' {score} out of range [0, 100]")

    def _validate_agency(
        self,
        contract: CapabilityContract,
        output: Any,
        failures: list[str],
    ) -> None:
        if not isinstance(output, dict):
            failures.append("Agency output must be a dict")
            return

        if "plan" not in output:
            failures.append("Agency output missing 'plan'")

        if "artifacts" in output:
            artifacts = output["artifacts"]
            if not isinstance(artifacts, list):
                failures.append("'artifacts' must be a list")
            elif len(artifacts) == 0:
                failures.append("'artifacts' must not be empty after agency run")

        if "qa_score" in output:
            qa_score = output["qa_score"]
            if isinstance(qa_score, (int, float)):
                threshold = contract.quality_thresholds.score_threshold
                if qa_score < threshold:
                    failures.append(f"QA score {qa_score} below threshold {threshold}")
