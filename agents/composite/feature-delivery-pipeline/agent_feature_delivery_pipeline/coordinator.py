"""FeatureDeliveryCoordinator — DAG-based pipeline execution coordinator.

Parses composition.toml to build the task DAG, then executes stages
in dependency order (sequential -> parallel). POC uses simulated
agent execution; production will use ProcessManager subprocess calls.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from agent_nexus.models.composition import Composition, CompositionError
from agent_nexus.platform.utils import resolve_composition_path

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


def _simulate_agent_execution(
    agent_name: str, context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Simulate Atomic Agent execution for POC.

    In production, this would invoke the agent via ProcessManager subprocess.
    Returns a simulated result based on the agent name.

    Args:
        agent_name: Name of the Atomic Agent to execute.
        context: Input context from upstream stages.

    Returns:
        Simulated agent output.
    """
    result = dict(_SIMULATED_RESULTS.get(agent_name, {"output": f"{agent_name} completed"}))
    if context:
        result["input_context"] = bool(context)
    return result


async def _simulate_agent_execution_async(
    agent_name: str, context: dict[str, Any] | None = None
) -> dict[str, Any]:
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

        Resolution order:
        1. Explicit *composition_path* argument (for testing).
        2. ``AGENT_DIR`` env var (platform-injected install root).
        3. ``<package_dir>/composition.toml`` (bundled in wheel).
        4. ``<parent_dir>/composition.toml`` (dev mode).

        Args:
            composition_path: Path to composition.toml. Defaults to
                the one bundled with this agent package.
        """
        if composition_path is None:
            composition_path = resolve_composition_path(__file__)
        self._composition_path = composition_path
        self._composition_cache: Composition | None = None

    def load_composition(self) -> Composition:
        """Load and validate composition.toml (cached).

        Returns:
            Parsed Composition object.

        Raises:
            CompositionError: If the composition is invalid.
        """
        if self._composition_cache is None:
            self._composition_cache = Composition.from_toml(self._composition_path)
        return self._composition_cache

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

    async def run_pipeline_async(self, spec: str) -> PipelineResult:
        """Async version of run_pipeline for use inside an existing event loop."""
        return await self._run_pipeline_async(spec)

    async def _run_pipeline_async(self, spec: str) -> PipelineResult:
        try:
            composition = self.load_composition()
        except CompositionError:
            return PipelineResult(spec=spec, success=False, stages=[], artifacts={})

        execution_order = composition.get_execution_order()
        completed_results: dict[str, dict[str, Any]] = {}
        artifacts: dict[str, Any] = {}
        stages: list[PipelineStage] = []

        for group_idx, group in enumerate(execution_order):
            group_stages, should_stop = await self._execute_pipeline_group(
                group, group_idx, execution_order, composition, completed_results, artifacts
            )
            stages.extend(group_stages)
            if should_stop:
                break

        return PipelineResult(
            spec=spec,
            stages=stages,
            artifacts=artifacts,
            success=all(s.status == StageStatus.COMPLETED for s in stages),
        )

    async def _execute_pipeline_group(
        self,
        group: list[str],
        group_idx: int,
        execution_order: list[list[str]],
        composition,
        completed_results: dict[str, dict[str, Any]],
        artifacts: dict[str, Any],
    ) -> tuple[list[PipelineStage], bool]:
        """Execute one execution group. Returns (stages, should_stop)."""
        coros = []
        for task_id in group:
            task = composition.tasks[task_id]
            context: dict[str, Any] = {}
            for dep_id in task.blocked_by:
                if dep_id in completed_results:
                    context[dep_id] = completed_results[dep_id]
            coros.append((task, _simulate_agent_execution_async(task.agent, context)))

        results = await asyncio.gather(*[c for _, c in coros], return_exceptions=True)
        group_stages: list[PipelineStage] = []
        should_stop = False

        for (task, _), result in zip(coros, results, strict=True):
            if isinstance(result, Exception):
                completed_results[task.id] = {"error": str(result)}
                group_stages.append(
                    PipelineStage(
                        name=task.name,
                        agent=task.agent,
                        status=StageStatus.FAILED,
                        error=str(result),
                    )
                )
                if not task.blocked_by:
                    self._append_remaining_skipped(
                        group_stages, group_idx, execution_order, composition
                    )
                    should_stop = True
                    break
            else:
                completed_results[task.id] = result  # type: ignore[assignment]
                artifacts[task.name] = result
                group_stages.append(
                    PipelineStage(
                        name=task.name,
                        agent=task.agent,
                        status=StageStatus.COMPLETED,
                        result=result,
                    )
                )

        if any(s.status == StageStatus.SKIPPED for s in group_stages):
            should_stop = True
        return group_stages, should_stop

    @staticmethod
    def _append_remaining_skipped(
        stages: list[PipelineStage],
        group_idx: int,
        execution_order: list[list[str]],
        composition,
    ) -> None:
        """Append SKIPPED stages for all remaining tasks after a failure."""
        for remaining_id in (tid for grp in execution_order[group_idx + 1 :] for tid in grp):
            remaining_task = composition.tasks[remaining_id]
            stages.append(
                PipelineStage(
                    name=remaining_task.name,
                    agent=remaining_task.agent,
                    status=StageStatus.SKIPPED,
                )
            )
