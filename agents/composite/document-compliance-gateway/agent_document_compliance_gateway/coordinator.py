"""ComplianceCoordinator -- DAG-based compliance checking coordinator.

Parses composition.toml to build the task DAG, then executes all compliance
checks in parallel, followed by cross-dimension conflict detection.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from agent_nexus.models.composition import Composition, CompositionError
from agent_nexus.platform.utils import resolve_composition_path

from agent_document_compliance_gateway.models import (
    CheckStatus,
    ComplianceCheck,
    ComplianceResult,
    ConflictItem,
)

logger = logging.getLogger(__name__)

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


def _simulate_agent_check(agent_name: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Simulate compliance agent execution for POC."""
    result = dict(
        _DIMENSION_RESULTS.get(agent_name, {"dimension": agent_name, "issues": [], "score": 100.0})
    )
    if context:
        result["input_context"] = bool(context)
    return result


async def _simulate_agent_check_async(
    agent_name: str, context: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Async version of _simulate_agent_check for use with asyncio.gather."""
    return _simulate_agent_check(agent_name, context)


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
            composition_path = resolve_composition_path(__file__)
        self._composition_path = composition_path
        self._composition_cache: Composition | None = None

    def load_composition(self) -> Composition:
        """Load and validate composition.toml (cached)."""
        if self._composition_cache is None:
            self._composition_cache = Composition.from_toml(self._composition_path)
        return self._composition_cache

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
        return asyncio.run(self._check_compliance_async(document, jurisdictions))

    async def check_compliance_async(
        self, document: str, jurisdictions: list[str] | None = None
    ) -> ComplianceResult:
        """Async version of check_compliance for use inside an existing event loop."""
        if jurisdictions is None:
            jurisdictions = []
        return await self._check_compliance_async(document, jurisdictions)

    async def _check_compliance_async(
        self, document: str, jurisdictions: list[str]
    ) -> ComplianceResult:
        try:
            composition = self.load_composition()
        except CompositionError:
            return ComplianceResult()

        execution_order = composition.get_execution_order()
        completed_results: dict[str, dict[str, Any]] = {}
        checks: list[ComplianceCheck] = []

        for group in execution_order:
            context_base: dict[str, Any] = {"document": document, "jurisdictions": jurisdictions}
            merge_task = None
            non_merge_tasks = []
            for task_id in group:
                task = composition.tasks[task_id]
                if task.agent == "conflict-detector":
                    merge_task = task
                else:
                    non_merge_tasks.append(task)

            if non_merge_tasks:
                await self._execute_compliance_group(
                    non_merge_tasks, context_base, completed_results, checks
                )

            if merge_task is not None:
                return self._build_compliance_result_with_conflicts(checks)

        return ComplianceResult(
            checks=checks,
            overall_score=_compute_overall_score(checks),
        )

    async def _execute_compliance_group(
        self,
        tasks: list,
        context_base: dict[str, Any],
        completed_results: dict[str, dict[str, Any]],
        checks: list[ComplianceCheck],
    ) -> None:
        """Execute a group of compliance check tasks concurrently."""
        coros = []
        for task in tasks:
            context = dict(context_base)
            for dep_id in task.blocked_by:
                if dep_id in completed_results:
                    context[dep_id] = completed_results[dep_id]
            coros.append((task, _simulate_agent_check_async(task.agent, context)))
        results = await asyncio.gather(*[c for _, c in coros], return_exceptions=True)
        for (task, _), result in zip(coros, results, strict=True):
            if isinstance(result, Exception):
                logger.exception(
                    "Compliance check failed for task '%s' (agent='%s')", task.id, task.agent
                )
                checks.append(
                    ComplianceCheck(
                        dimension=task.name,
                        status=CheckStatus.ERROR,
                        issues=[f"Agent {task.agent} failed"],
                        score=0.0,
                    )
                )
            else:
                completed_results[task.id] = result
                dimension = result.get("dimension", task.agent)
                issues = result.get("issues", [])
                score = result.get("score", 100.0)
                status = CheckStatus.PASS
                if issues:
                    status = CheckStatus.FAIL if score < 70 else CheckStatus.WARNING
                checks.append(
                    ComplianceCheck(dimension=dimension, status=status, issues=issues, score=score)
                )

    def _build_compliance_result_with_conflicts(
        self, checks: list[ComplianceCheck]
    ) -> ComplianceResult:
        """Build ComplianceResult with conflict detection and recommendations."""
        conflicts = _detect_conflicts(checks)
        recommendations = _generate_recommendations(checks, conflicts)
        return ComplianceResult(
            checks=checks,
            conflicts=conflicts,
            overall_score=_compute_overall_score(checks),
            recommendations=recommendations,
        )
