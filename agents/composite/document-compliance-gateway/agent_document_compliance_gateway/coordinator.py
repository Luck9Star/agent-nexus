"""ComplianceCoordinator -- DAG-based compliance checking coordinator.

Parses composition.toml to build the task DAG, then executes all compliance
checks in parallel, followed by cross-dimension conflict detection.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

import toml

from agent_document_compliance_gateway.models import (
    CheckStatus,
    ComplianceCheck,
    ComplianceResult,
    ConflictItem,
)

# Simulated dimension results for POC
_DIMENSION_RESULTS: dict[str, dict[str, Any]] = {
    "contract-analyzer": {
        "dimension": "legal",
        "issues": ["Missing liability clause", "Governing law not specified"],
        "score": 72.0,
    },
    "accessibility-auditor": {
        "dimension": "accessibility",
        "issues": ["Images missing alt text", "Color contrast insufficient"],
        "score": 68.0,
    },
    "localization-specialist": {
        "dimension": "localization",
        "issues": ["Untranslated sections found"],
        "score": 85.0,
    },
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
    """Simulate compliance agent execution for POC."""
    time.sleep(0.001)
    result = dict(_DIMENSION_RESULTS.get(agent_name, {"dimension": agent_name, "issues": [], "score": 100.0}))
    if context:
        result["input_context"] = bool(context)
    return result


def _detect_conflicts(checks: list[ComplianceCheck]) -> list[ConflictItem]:
    """Detect cross-dimension conflicts from completed checks.

    This is the synthetic merge step (task4) that analyzes results
    from all three compliance dimensions.
    """
    conflicts: list[ConflictItem] = []

    # Simulated conflict detection logic
    dimensions_with_issues = {
        c.dimension for c in checks if c.issues and c.status != CheckStatus.ERROR
    }

    if len(dimensions_with_issues) >= 2:
        dims = sorted(dimensions_with_issues)
        # Check for potential conflicts between dimensions
        if "legal" in dims and "localization" in dims:
            conflicts.append(
                ConflictItem(
                    dimensions=["legal", "localization"],
                    description="Legal requirements may conflict with localization adaptation",
                    resolution="Review jurisdiction-specific legal terms in localized versions",
                )
            )
        if "accessibility" in dims and "localization" in dims:
            conflicts.append(
                ConflictItem(
                    dimensions=["accessibility", "localization"],
                    description="Accessibility standards may vary across locales",
                    resolution="Apply locale-specific WCAG guidelines",
                )
            )

    return conflicts


def _generate_recommendations(
    checks: list[ComplianceCheck], conflicts: list[ConflictItem]
) -> list[str]:
    """Generate improvement recommendations based on checks and conflicts."""
    recs: list[str] = []
    for check in checks:
        if check.score < 80:
            recs.append(f"Improve {check.dimension} compliance (current score: {check.score})")
    for conflict in conflicts:
        if conflict.resolution:
            recs.append(conflict.resolution)
    return recs


def _compute_overall_score(checks: list[ComplianceCheck]) -> float:
    """Compute weighted average compliance score."""
    if not checks:
        return 0.0
    valid_checks = [c for c in checks if c.status != CheckStatus.ERROR]
    if not valid_checks:
        return 0.0
    return sum(c.score for c in valid_checks) / len(valid_checks)


class ComplianceCoordinator:
    """Coordinates the document compliance checking pipeline.

    Parses composition.toml to build the DAG, then executes all compliance
    checks in parallel, followed by cross-dimension conflict detection.

    Usage:
        coordinator = ComplianceCoordinator()
        result = coordinator.check_compliance("document text", jurisdictions=["CN", "EU"])
        print(result.overall_score)
    """

    def __init__(self, composition_path: Path | None = None) -> None:
        if composition_path is None:
            composition_path = Path(__file__).parent.parent / "composition.toml"
        self._composition_path = composition_path

    def load_composition(self) -> Composition:
        """Load and validate composition.toml."""
        return Composition.from_toml(self._composition_path)

    def check_compliance(
        self, document: str, jurisdictions: list[str] | None = None
    ) -> ComplianceResult:
        """Execute the full compliance checking pipeline.

        Pipeline flow:
        1. Parse composition.toml and build DAG
        2. Execute all root tasks (parallel in production)
        3. Execute conflict-detection merge step
        4. Aggregate into ComplianceResult

        Args:
            document: Document text to check.
            jurisdictions: List of jurisdiction codes (e.g. ["CN", "EU"]).

        Returns:
            ComplianceResult with checks, conflicts, and recommendations.
        """
        if jurisdictions is None:
            jurisdictions = []

        try:
            composition = self.load_composition()
        except CompositionError:
            return ComplianceResult()

        execution_order = composition.get_execution_order()
        completed_results: dict[str, dict[str, Any]] = {}
        checks: list[ComplianceCheck] = []

        for group in execution_order:
            for task_id in group:
                task = composition.tasks[task_id]

                # Build context from completed dependencies
                context: dict[str, Any] = {"document": document, "jurisdictions": jurisdictions}
                for dep_id in task.blocked_by:
                    if dep_id in completed_results:
                        context[dep_id] = completed_results[dep_id]

                # Check if this is the conflict detection merge step
                if task.agent == "conflict-detector":
                    # This is the synthetic merge step
                    conflicts = _detect_conflicts(checks)
                    recommendations = _generate_recommendations(checks, conflicts)
                    overall_score = _compute_overall_score(checks)

                    return ComplianceResult(
                        checks=checks,
                        conflicts=conflicts,
                        overall_score=overall_score,
                        recommendations=recommendations,
                    )

                try:
                    result = _simulate_agent_check(task.agent, context)
                    completed_results[task_id] = result

                    # Convert to ComplianceCheck
                    dimension = result.get("dimension", task.agent)
                    issues = result.get("issues", [])
                    score = result.get("score", 100.0)

                    status = CheckStatus.PASS
                    if issues:
                        status = CheckStatus.FAIL if score < 70 else CheckStatus.WARNING

                    checks.append(
                        ComplianceCheck(
                            dimension=dimension,
                            status=status,
                            issues=issues,
                            score=score,
                        )
                    )
                except Exception:
                    logger.exception(
                        "Compliance check failed for task '%s' (agent='%s')",
                        task_id, task.agent,
                    )
                    checks.append(
                        ComplianceCheck(
                            dimension=task.name,
                            status=CheckStatus.ERROR,
                            issues=[f"Agent {task.agent} failed"],
                            score=0.0,
                        )
                    )

        # If no conflict-detector task, return what we have
        return ComplianceResult(
            checks=checks,
            overall_score=_compute_overall_score(checks),
        )
