"""QualityGateCoordinator -- DAG-based CI/CD quality gate coordinator.

Parses composition.toml to build the task DAG, then executes all quality
checks in parallel, followed by an aggregate quality gate decision.
"""

from __future__ import annotations

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

    gate_score = total_score / len(checks) if checks else 0.0

    # Apply thresholds
    for check in checks:
        if check.agent == "security-scanner" and check.score < thresholds["security_threshold"]:
            blockers.append(f"Security score {check.score} below threshold {thresholds['security_threshold']}")
        if check.agent == "code-reviewer" and check.score < thresholds["review_threshold"]:
            blockers.append(f"Review score {check.score} below threshold {thresholds['review_threshold']}")

    overall_passed = len(blockers) == 0
    return overall_passed, gate_score, blockers, warnings


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

        try:
            composition = self.load_composition()
        except CompositionError:
            return GateResult()

        execution_order = composition.get_execution_order()
        completed_results: dict[str, dict[str, Any]] = {}
        checks: list[GateCheck] = []

        for group in execution_order:
            for task_id in group:
                task = composition.tasks[task_id]

                # Build context from completed dependencies
                context: dict[str, Any] = {"code_path": code_path, "config": config}
                for dep_id in task.blocked_by:
                    if dep_id in completed_results:
                        context[dep_id] = completed_results[dep_id]

                # Check if this is the quality gate decision merge step
                if task.agent == "quality-gate-decider":
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

                try:
                    result = _simulate_agent_check(task.agent, context)
                    completed_results[task_id] = result

                    # Convert to GateCheck
                    if task.agent == "security-scanner":
                        score = result.get("risk_score", 100.0)
                        findings = result.get("vulnerabilities", [])
                    elif task.agent == "code-reviewer":
                        score = result.get("quality_score", 100.0)
                        findings = result.get("issues", [])
                    elif task.agent == "test-suite-generator":
                        coverage = result.get("coverage", 1.0)
                        failing = result.get("failing_tests", 0)
                        score = coverage * 100
                        findings = (
                            [f"{failing} failing tests"] if failing > 0 else []
                        )
                    else:
                        score = result.get("score", 100.0)
                        findings = result.get("findings", [])

                    passed = score >= 70.0 and len(findings) == 0
                    # For minor findings, still pass but record them
                    if findings and score >= 70.0:
                        passed = True

                    checks.append(
                        GateCheck(
                            agent=task.agent,
                            passed=passed,
                            findings=findings if isinstance(findings, list) else [str(findings)],
                            score=score,
                        )
                    )
                except Exception as exc:
                    checks.append(
                        GateCheck(
                            agent=task.agent,
                            passed=False,
                            findings=[str(exc)],
                            score=0.0,
                        )
                    )

        # If no quality-gate-decider task, make decision directly
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
