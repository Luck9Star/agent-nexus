"""FeatureDeliveryCoordinator — DAG-based pipeline execution coordinator.

Parses composition.toml to build the task DAG, then executes stages
in dependency order (sequential -> parallel). POC uses simulated
agent execution; production will use ProcessManager subprocess calls.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import toml

from agent_feature_delivery_pipeline.models import (
    PipelineResult,
    PipelineStage,
    StageStatus,
)

# Simulated agent outputs for POC
_SIMULATED_RESULTS: dict[str, dict[str, Any]] = {
    "requirements-analyzer": {
        "summary": "Requirements analyzed successfully",
        "requirements": [
            {"id": "REQ-001", "description": "Core functionality"},
            {"id": "REQ-002", "description": "Error handling"},
        ],
        "constraints": ["Must be backward compatible"],
    },
    "api-doc-generator": {
        "document": "API Documentation generated",
        "endpoints": [
            {"method": "POST", "path": "/api/v1/resource"},
            {"method": "GET", "path": "/api/v1/resource/{id}"},
        ],
    },
    "test-suite-generator": {
        "test_file": "test_suite.py",
        "test_count": 12,
        "coverage_target": 0.85,
    },
    "code-reviewer": {
        "review_summary": "Code review completed",
        "issues": [],
        "score": 92,
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
        """Compute parallel execution groups (BFS by dependency depth).

        Returns a list of groups, where each group is a list of task IDs
        that can execute in parallel.
        """
        groups: list[list[str]] = []
        completed: set[str] = set()
        remaining = set(self.tasks.keys())

        while remaining:
            # Find tasks whose dependencies are all completed
            ready = [
                tid for tid in remaining
                if all(dep in completed for dep in self.tasks[tid].blocked_by)
            ]
            if not ready:
                # Should not happen if no cycles, but guard against infinite loop
                break
            groups.append(sorted(ready))
            completed.update(ready)
            remaining -= set(ready)

        return groups

    @classmethod
    def from_toml(cls, path: Path) -> Composition:
        """Parse composition.toml file.

        Args:
            path: Path to composition.toml.

        Returns:
            Parsed Composition object.

        Raises:
            CompositionError: If the file is invalid.
        """
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

        # Validate: all blocked_by references exist
        for tid, task in tasks.items():
            for dep in task.blocked_by:
                if dep not in tasks:
                    raise CompositionError(
                        f"Task '{tid}' blocked_by unknown task '{dep}'"
                    )

        # Validate: no cycles
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


def _simulate_agent_execution(agent_name: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Simulate Atomic Agent execution for POC.

    In production, this would invoke the agent via ProcessManager subprocess.
    Returns a simulated result based on the agent name.

    Args:
        agent_name: Name of the Atomic Agent to execute.
        context: Input context from upstream stages.

    Returns:
        Simulated agent output.
    """
    # Small delay to simulate work
    time.sleep(0.001)
    result = dict(_SIMULATED_RESULTS.get(agent_name, {"output": f"{agent_name} completed"}))
    if context:
        result["input_context"] = bool(context)
    return result


async def _simulate_agent_execution_async(agent_name: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Async version of _simulate_agent_execution for use with asyncio.gather."""
    return _simulate_agent_execution(agent_name, context)


class FeatureDeliveryCoordinator:
    """Coordinates the feature delivery pipeline execution.

    Parses composition.toml to build the DAG, then executes stages
    in dependency order. Supports sequential -> parallel execution.

    Usage:
        coordinator = FeatureDeliveryCoordinator()
        result = coordinator.run_pipeline("实现用户注册 API")
        print(result.success, len(result.stages))
    """

    def __init__(self, composition_path: Path | None = None) -> None:
        """Initialize with optional composition.toml path.

        Args:
            composition_path: Path to composition.toml. Defaults to
                the one bundled with this agent package.
        """
        if composition_path is None:
            composition_path = Path(__file__).parent.parent / "composition.toml"
        self._composition_path = composition_path

    def load_composition(self) -> Composition:
        """Load and validate composition.toml.

        Returns:
            Parsed Composition object.

        Raises:
            CompositionError: If the composition is invalid.
        """
        return Composition.from_toml(self._composition_path)

    def run_pipeline(self, spec: str) -> PipelineResult:
        """Execute the full pipeline for a given specification.

        Pipeline flow:
        1. Parse composition.toml and build DAG
        2. Execute root task (requirements-analyzer) first
        3. Execute dependent tasks in parallel after root completes
        4. Aggregate results into PipelineResult

        Args:
            spec: Requirement specification text.

        Returns:
            PipelineResult with all stage outputs and artifacts.
        """
        return asyncio.run(self._run_pipeline_async(spec))

    async def _run_pipeline_async(self, spec: str) -> PipelineResult:
        try:
            composition = self.load_composition()
        except CompositionError as exc:
            return PipelineResult(spec=spec, success=False, stages=[], artifacts={})

        stages: list[PipelineStage] = []
        completed_results: dict[str, dict[str, Any]] = {}
        artifacts: dict[str, Any] = {}
        all_success = True

        execution_order = composition.get_execution_order()

        for group_idx, group in enumerate(execution_order):
            group_results: list[PipelineStage] = []

            coros = []
            for task_id in group:
                task = composition.tasks[task_id]
                context: dict[str, Any] = {}
                for dep_id in task.blocked_by:
                    if dep_id in completed_results:
                        context[dep_id] = completed_results[dep_id]
                coros.append((task, context, _simulate_agent_execution_async(task.agent, context)))

            results = await asyncio.gather(
                *[c for _, _, c in coros],
                return_exceptions=True,
            )

            for (task, context, _), result in zip(coros, results):
                if isinstance(result, Exception):
                    all_success = False
                    completed_results[task.id] = {"error": str(result)}

                    stage = PipelineStage(
                        name=task.name,
                        agent=task.agent,
                        status=StageStatus.FAILED,
                        error=str(result),
                    )
                    group_results.append(stage)

                    if not task.blocked_by:
                        for remaining_id in (
                            tid
                            for grp in execution_order[group_idx + 1 :]
                            for tid in grp
                        ):
                            remaining_task = composition.tasks[remaining_id]
                            group_results.append(
                                PipelineStage(
                                    name=remaining_task.name,
                                    agent=remaining_task.agent,
                                    status=StageStatus.SKIPPED,
                                )
                            )
                        break
                else:
                    completed_results[task.id] = result  # type: ignore[assignment]
                    artifacts[task.name] = result
                    group_results.append(
                        PipelineStage(
                            name=task.name,
                            agent=task.agent,
                            status=StageStatus.COMPLETED,
                            result=result,
                        )
                    )

            stages.extend(group_results)

            if any(s.status == StageStatus.SKIPPED for s in stages):
                break

        return PipelineResult(
            spec=spec,
            stages=stages,
            artifacts=artifacts,
            success=all_success and all(
                s.status == StageStatus.COMPLETED for s in stages
            ),
        )
