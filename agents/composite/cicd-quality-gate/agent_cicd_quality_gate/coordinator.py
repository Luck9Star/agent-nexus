"""QualityGateCoordinator -- DAG-based CI/CD quality gate coordinator.

Parses composition.toml to build the task DAG, then executes all quality
checks in parallel, followed by an aggregate quality gate decision.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agent_nexus.models.composition import Composition, CompositionError
from agent_nexus.platform.utils import resolve_composition_path

from agent_cicd_quality_gate.models import GateCheck, GateResult

# Simulated agent outputs for POC
_AGENT_RESULTS: dict[str, dict[str, Any]] = {
    "security-scanner": {
        "vulnerabilities": [],
        "risk_score": 95.0,
        "scan_summary": "No critical vulnerabilities found",
    },
    "code-reviewer": {
        "issues": [],
        "quality_score": 88.0,
        "review_summary": "Code quality acceptable with minor improvements",
    },
    "test-suite-generator": {
        "test_count": 24,
        "coverage": 0.87,
        "failing_tests": 0,
    },
}

# Default quality gate thresholds
_DEFAULT_THRESHOLDS = {
    "security_threshold": 80.0,
    "review_threshold": 70.0,
    "coverage_threshold": 0.75,
}


def _simulate_agent_check(agent_name: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Simulate quality gate agent execution for POC."""
    result = dict(
        _AGENT_RESULTS.get(agent_name, {"output": f"{agent_name} completed", "score": 100.0})
    )
    if context:
        result["input_context"] = bool(context)
    return result


async def _simulate_agent_check_async(
    agent_name: str, context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Async version of _simulate_agent_check for use with asyncio.gather."""
    return _simulate_agent_check(agent_name, context)


def _make_gate_decision(
    checks: list[GateCheck],
    config: dict[str, Any],
) -> tuple[bool, float, list[str], list[str]]:
    """Aggregate check results into a pass/fail gate decision.

    Returns:
        Tuple of (overall_passed, gate_score, blockers, warnings).
    """
    if not checks:
        return False, 0.0, ["No checks completed"], []

    thresholds = {**_DEFAULT_THRESHOLDS, **config}
    blockers: list[str] = []
    warnings: list[str] = []
    total_score = 0.0

    for check in checks:
        total_score += check.score

        if check.agent == "security-scanner" and check.score < thresholds["security_threshold"]:
            blockers.append(
                f"Security score {check.score} below threshold {thresholds['security_threshold']}"
            )
        elif check.agent == "code-reviewer" and check.score < thresholds["review_threshold"]:
            blockers.append(
                f"Review score {check.score} below threshold {thresholds['review_threshold']}"
            )
        elif not check.passed:
            blockers.append(f"{check.agent}: {', '.join(check.findings)}")
        elif check.findings:
            warnings.append(f"{check.agent}: {', '.join(check.findings)}")

    gate_score = total_score / len(checks) if checks else 0.0
    overall_passed = len(blockers) == 0
    return overall_passed, gate_score, blockers, warnings


def _convert_security(r: dict[str, Any]) -> GateCheck:
    score = r.get("risk_score", 100.0)
    findings = r.get("vulnerabilities", [])
    passed = score >= 70.0 and len(findings) == 0
    return GateCheck(
        agent="security-scanner",
        passed=passed,
        findings=findings if isinstance(findings, list) else [str(findings)],
        score=score,
    )


def _convert_review(r: dict[str, Any]) -> GateCheck:
    score = r.get("quality_score", 100.0)
    findings = r.get("issues", [])
    passed = score >= 70.0 and len(findings) == 0
    return GateCheck(
        agent="code-reviewer",
        passed=passed,
        findings=findings if isinstance(findings, list) else [str(findings)],
        score=score,
    )


def _convert_test(r: dict[str, Any]) -> GateCheck:
    coverage = r.get("coverage", 1.0)
    failing = r.get("failing_tests", 0)
    score = coverage * 100
    findings = [f"{failing} failing tests"] if failing > 0 else []
    passed = score >= 70.0 and len(findings) == 0
    return GateCheck(
        agent="test-suite-generator",
        passed=passed,
        findings=findings if isinstance(findings, list) else [str(findings)],
        score=score,
    )


def _convert_default(r: dict[str, Any]) -> GateCheck:
    score = r.get("score", 100.0)
    findings = r.get("findings", [])
    passed = score >= 70.0 and len(findings) == 0
    return GateCheck(
        agent=r.get("agent", "unknown"),
        passed=passed,
        findings=findings if isinstance(findings, list) else [str(findings)],
        score=score,
    )


_AGENT_CONVERTERS: dict[str, Any] = {
    "security-scanner": _convert_security,
    "code-reviewer": _convert_review,
    "test-suite-generator": _convert_test,
}


class QualityGateCoordinator:
    """Coordinates the CI/CD quality gate pipeline.

    Parses composition.toml to build the DAG, then executes all quality
    checks in parallel, followed by aggregate pass/fail decision.

    Usage:
        coordinator = QualityGateCoordinator()
        result = coordinator.run_gate("/path/to/code", {"security_threshold": 90})
        print(result.overall_passed, result.gate_score)
    """

    def __init__(self, composition_path: Path | None = None) -> None:
        if composition_path is None:
            composition_path = resolve_composition_path(__file__)
        self._composition_path = composition_path
        self._composition_cache: Composition | None = None

    def load_composition(self) -> Composition:
        """Load and validate composition.toml (cached)."""
        if self._composition_cache is None:
            self._composition_cache = Composition.from_toml(self._composition_path)
        return self._composition_cache

    def run_gate(self, code_path: str, config: dict[str, Any] | None = None) -> GateResult:
        """Execute the full quality gate pipeline.

        Pipeline flow:
        1. Parse composition.toml and build DAG
        2. Execute all root tasks (parallel in production)
        3. Execute quality-gate-decision merge step
        4. Return GateResult with pass/fail decision

        Args:
            code_path: Path to the code being checked.
            config: Quality gate configuration (thresholds, rules).

        Returns:
            GateResult with pass/fail decision, scores, blockers, and warnings.
        """
        if config is None:
            config = {}
        return asyncio.run(self._run_gate_async(code_path, config))

    async def run_gate_async(
        self, code_path: str, config: dict[str, Any] | None = None
    ) -> GateResult:
        """Async version of run_gate for use inside an existing event loop."""
        if config is None:
            config = {}
        return await self._run_gate_async(code_path, config)

    async def _run_gate_async(self, code_path: str, config: dict[str, Any]) -> GateResult:
        try:
            composition = self.load_composition()
        except CompositionError:
            return GateResult()

        execution_order = composition.get_execution_order()
        completed_results: dict[str, dict[str, Any]] = {}
        checks: list[GateCheck] = []

        for group in execution_order:
            context_base: dict[str, Any] = {"code_path": code_path, "config": config}
            merge_task = None
            non_merge_tasks = []
            for task_id in group:
                task = composition.tasks[task_id]
                if task.agent == "quality-gate-decider":
                    merge_task = task
                else:
                    non_merge_tasks.append(task)

            if non_merge_tasks:
                coros = []
                for task in non_merge_tasks:
                    context = dict(context_base)
                    for dep_id in task.blocked_by:
                        if dep_id in completed_results:
                            context[dep_id] = completed_results[dep_id]
                    coros.append((task, _simulate_agent_check_async(task.agent, context)))
                results = await asyncio.gather(
                    *[c for _, c in coros],
                    return_exceptions=True,
                )
                for (task, _), result in zip(coros, results, strict=True):
                    if isinstance(result, Exception):
                        checks.append(
                            GateCheck(
                                agent=task.agent,
                                passed=False,
                                findings=[str(result)],
                                score=0.0,
                            )
                        )
                    else:
                        completed_results[task.id] = result  # type: ignore[assignment]
                        converter = _AGENT_CONVERTERS.get(task.agent, _convert_default)
                        checks.append(converter(result))

            if merge_task is not None:
                overall_passed, gate_score, blockers, warnings = _make_gate_decision(checks, config)
                return GateResult(
                    checks=checks,
                    overall_passed=overall_passed,
                    gate_score=gate_score,
                    blockers=blockers,
                    warnings=warnings,
                )

        overall_passed, gate_score, blockers, warnings = _make_gate_decision(checks, config)
        return GateResult(
            checks=checks,
            overall_passed=overall_passed,
            gate_score=gate_score,
            blockers=blockers,
            warnings=warnings,
        )
