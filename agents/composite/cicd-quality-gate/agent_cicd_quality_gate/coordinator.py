"""QualityGateCoordinator -- DAG-based CI/CD quality gate coordinator.

Parses composition.toml to build the task DAG, then executes all quality
checks in parallel, followed by an aggregate quality gate decision.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import toml

from agent_cicd_quality_gate.models import GateCheck, GateResult

# Simulated agent outputs for POC
_AGENT_RESULTS: dict[str, dict[str, Any]] = {
    "security-scanner": {
        "vulnerabilities": [],
        "risk_score": 95.0,
        "scan_summary": "No critical vulnerabilities found",
    },
    "code-reviewer": {
        "issues": ["Minor: function too long"],
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


class CompositionError(Exception):
    """Error in composition.toml parsing or validation."""


class CompositionTask:
    """A single task parsed from composition.toml."""

    def __init__(self, task_id: str, name: str, agent: str, blocked_by: list[str]) -> None:
        self.id = task_id
        self.name = name
        self.agent = agent
        self.blocked_by = blocked_by

    def __repr__(self) -> str:
        return f"CompositionTask({self.id!r}, agent={self.agent!r})"


class Composition:
    """Parsed composition.toml definition."""

    def __init__(self, name: str, description: str, tasks: dict[str, CompositionTask]) -> None:
        self.name = name
        self.description = description
        self.tasks = tasks

    def get_root_tasks(self) -> list[CompositionTask]:
        """Get tasks with no dependencies (entry points)."""
        return [t for t in self.tasks.values() if not t.blocked_by]

    def get_dependents(self, task_id: str) -> list[CompositionTask]:
        """Get tasks that depend on the given task."""
        return [t for t in self.tasks.values() if task_id in t.blocked_by]

    def get_execution_order(self) -> list[list[str]]:
        """Compute parallel execution groups (BFS by dependency depth)."""
        groups: list[list[str]] = []
        completed: set[str] = set()
        remaining = set(self.tasks.keys())

        while remaining:
            ready = [
                tid for tid in remaining
                if all(dep in completed for dep in self.tasks[tid].blocked_by)
            ]
            if not ready:
                break
            groups.append(sorted(ready))
            completed.update(ready)
            remaining -= set(ready)

        return groups

    @classmethod
    def from_toml(cls, path: Path) -> Composition:
        """Parse composition.toml file."""
        if not path.exists():
            raise CompositionError(f"composition.toml not found: {path}")

        try:
            raw = toml.load(str(path))
        except Exception as exc:
            raise CompositionError(f"Invalid TOML in {path}: {exc}") from exc

        meta = raw.get("composition", {})
        name = meta.get("name", "unknown")
        description = meta.get("description", "")

        tasks_raw = raw.get("tasks", {})
        if not isinstance(tasks_raw, dict):
            raise CompositionError("[tasks] must be a table of task definitions")

        tasks: dict[str, CompositionTask] = {}
        for task_id, task_def in tasks_raw.items():
            if not isinstance(task_def, dict):
                raise CompositionError(f"tasks.{task_id} must be a table")
            tasks[task_id] = CompositionTask(
                task_id=task_id,
                name=task_def.get("name", task_id),
                agent=task_def.get("agent", ""),
                blocked_by=list(task_def.get("blocked_by", [])),
            )

        # Validate references
        for tid, task in tasks.items():
            for dep in task.blocked_by:
                if dep not in tasks:
                    raise CompositionError(
                        f"Task '{tid}' blocked_by unknown task '{dep}'"
                    )

        # Validate no cycles
        _detect_cycles(tasks)

        return cls(name=name, description=description, tasks=tasks)


def _detect_cycles(tasks: dict[str, CompositionTask]) -> None:
    """Detect dependency cycles. Raises CompositionError if found."""
    visiting: set[str] = set()
    visited: set[str] = set()

    def _dfs(node: str, path: list[str]) -> None:
        if node in visiting:
            cycle_start = path.index(node)
            cycle = path[cycle_start:] + [node]
            raise CompositionError(f"Dependency cycle: {' -> '.join(cycle)}")
        if node in visited:
            return
        visiting.add(node)
        path.append(node)
        task = tasks.get(node)
        if task:
            for dep in task.blocked_by:
                if dep in tasks:
                    _dfs(dep, path)
        path.pop()
        visiting.discard(node)
        visited.add(node)

    for tid in tasks:
        if tid not in visited:
            _dfs(tid, [])


def _simulate_agent_check(agent_name: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Simulate quality gate agent execution for POC."""
    time.sleep(0.001)
    result = dict(_AGENT_RESULTS.get(agent_name, {"output": f"{agent_name} completed", "score": 100.0}))
    if context:
        result["input_context"] = bool(context)
    return result


async def _simulate_agent_check_async(agent_name: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
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

        if not check.passed:
            blockers.append(f"{check.agent}: {', '.join(check.findings)}")
        elif check.findings:
            warnings.append(f"{check.agent}: {', '.join(check.findings)}")

        if check.agent == "security-scanner" and check.score < thresholds["security_threshold"]:
            blockers.append(f"Security score {check.score} below threshold {thresholds['security_threshold']}")
        if check.agent == "code-reviewer" and check.score < thresholds["review_threshold"]:
            blockers.append(f"Review score {check.score} below threshold {thresholds['review_threshold']}")

    gate_score = total_score / len(checks) if checks else 0.0
    overall_passed = len(blockers) == 0
    return overall_passed, gate_score, blockers, warnings


def _convert_security(r: dict[str, Any]) -> GateCheck:
    score = r.get("risk_score", 100.0)
    findings = r.get("vulnerabilities", [])
    passed = score >= 70.0 and len(findings) == 0
    if findings and score >= 70.0:
        passed = True
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
    if findings and score >= 70.0:
        passed = True
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
    if findings and score >= 70.0:
        passed = True
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
    if findings and score >= 70.0:
        passed = True
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
            composition_path = Path(__file__).parent.parent / "composition.toml"
        self._composition_path = composition_path

    def load_composition(self) -> Composition:
        """Load and validate composition.toml."""
        return Composition.from_toml(self._composition_path)

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
                for (task, _), result in zip(coros, results):
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
                overall_passed, gate_score, blockers, warnings = _make_gate_decision(
                    checks, config
                )
                return GateResult(
                    checks=checks,
                    overall_passed=overall_passed,
                    gate_score=gate_score,
                    blockers=blockers,
                    warnings=warnings,
                )

        overall_passed, gate_score, blockers, warnings = _make_gate_decision(
            checks, config
        )
        return GateResult(
            checks=checks,
            overall_passed=overall_passed,
            gate_score=gate_score,
            blockers=blockers,
            warnings=warnings,
        )
