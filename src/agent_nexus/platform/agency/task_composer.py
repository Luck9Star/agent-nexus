"""TaskComposer — orchestrates the full agency pipeline: select → plan → dispatch → integrate → validate."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .executor import ProfileBasedExecutor
from .integrator import Artifact, IntegratedArtifact, Integrator
from .planner import CompositionDAG, DynamicCompositePlanner, SubtaskDef
from .qa_gate import QAGate, QAGateInput
from .registry import ExpertRegistry
from .selector import SelectionRequest, SelectionResult, SpecialistSelector

if TYPE_CHECKING:
    from agent_nexus.platform.orchestration.task_graph import TaskGraph

    from .dag_dispatcher import ExpertExecutor

logger = logging.getLogger(__name__)


@dataclass
class TaskComposerInput:
    """Input for a TaskComposer run."""

    task: str
    mode: str = "plan"
    max_parallel: int = 3
    timeout_seconds: float | None = None


@dataclass
class TaskComposerResult:
    """Output from a TaskComposer run."""

    task: str
    selected_agents: list[SelectionResult] = field(default_factory=list)
    dag: CompositionDAG | None = None
    integrated: IntegratedArtifact | None = None
    qa_passed: bool | None = None
    skipped_tasks: list[str] = field(default_factory=list)




# Task type → required capabilities mapping
_TASK_CAPABILITY_MAP: dict[str, list[str]] = {
    "architecture": ["system_design", "architecture_review"],
    "review": ["code_review", "security_review"],
    "security": ["security_review", "threat_modeling"],
    "testing": ["test_design", "test_analysis"],
    "documentation": ["technical_writing", "documentation"],
    "evaluation": ["tool_evaluation"],
    "onboarding": ["codebase_onboarding"],
    "reliability": ["reliability_review"],
    "integration": ["system_design", "tool_evaluation"],
    # Extended mappings for broader expert pool coverage
    "backend": ["backend_design", "api_design", "database_design"],
    "api": ["api_design"],
    "database": ["database_design"],
    "ai_engineering": ["ai_engineering", "prompt_engineering"],
    "model_integration": ["model_integration"],
    "prompt": ["prompt_engineering"],
    "maintainability": ["maintainability_review"],
    "vulnerability": ["vulnerability_assessment"],
    "incident": ["incident_analysis", "observability"],
    "observability": ["observability"],
    "coverage": ["coverage_assessment"],
    "index": ["lsp_indexing", "semantic_analysis"],
    "orchestrat": ["orchestration", "task_decomposition"],
    "coordinate": ["agent_coordination"],
    "decompos": ["task_decomposition"],
    "navigate": ["code_navigation", "architecture_mapping"],
    "compare": ["comparison_analysis"],
    "technology": ["technology_assessment"],
    # Previously unreachable capabilities (audit gap fix)
    "tradeoff": ["tradeoff_analysis"],
    "api_documentation": ["api_documentation", "technical_writing"],
    "code_indexing": ["code_indexing", "lsp_indexing"],
}


def _infer_capabilities(task: str) -> list[str]:
    """Infer required capabilities from task description."""
    task_lower = task.lower()
    matched: list[str] = []
    for keyword, caps in _TASK_CAPABILITY_MAP.items():
        if re.search(rf'\b{re.escape(keyword)}\b', task_lower):
            matched.extend(caps)
    # Default fallback: broad capabilities
    if not matched:
        matched = ["system_design"]
    return list(dict.fromkeys(matched))  # deduplicate preserving order


class TaskComposer:
    """Orchestrates the full agency pipeline.

    Pipeline steps:
    1. Infer required capabilities from task
    2. Select specialists via SpecialistSelector
    3. Generate DAG via DynamicCompositePlanner
    4. Dispatch expert tasks (via pluggable executor)
    5. Integrate artifacts via Integrator
    6. Validate via QAGate
    """

    def __init__(self, registry: ExpertRegistry) -> None:
        self.registry = registry
        self.selector = SpecialistSelector(registry)
        self.planner = DynamicCompositePlanner()

    def run(
        self,
        input: TaskComposerInput,
        expert_executor: ExpertExecutor | None = None,
        task_graph: TaskGraph | None = None,
    ) -> TaskComposerResult:
        """Execute the full pipeline.

        Parameters
        ----------
        input:
            Task description, mode, parallelism, and timeout.
        expert_executor:
            Override for the default executor. When *task_graph* is provided
            and this is None, ProfileBasedExecutor is used for in-process
            execution with TaskGraph state tracking.
        task_graph:
            When provided, the DAG is loaded into this TaskGraph and executed
            through DAGDispatcher (proper dependency tracking, state
            transitions, failure propagation). When None, the legacy
            in-process loop is used (backward compatible).
        """
        executor = expert_executor or ProfileBasedExecutor(self.registry)

        # Step 1: Infer capabilities
        required_caps = _infer_capabilities(input.task)

        # Step 2: Select specialists
        selection_request = SelectionRequest(
            task_type=input.mode,
            required_capabilities=required_caps,
            optional_capabilities=[],
            max_agents=5,
            permissions="plan",
        )
        selected = self.selector.select(selection_request)

        if not selected:
            return TaskComposerResult(task=input.task)

        # Step 3: Build subtask definitions and generate DAG
        subtasks: list[SubtaskDef] = []
        for sel in selected:
            profile = self.registry.get(sel.agent_id)
            artifact_type = (
                profile.get("output_contract", {}).get("artifact_type", "report")
                if profile
                else "report"
            )
            subtasks.append(
                SubtaskDef(
                    id=sel.agent_id.replace("agency.", ""),
                    goal=input.task,
                    needed_capabilities=profile.get("capabilities", []) if profile else required_caps,
                    output_contract=artifact_type,
                    assigned_agent=sel.agent_id,
                )
            )

        dag = self.planner.resolve_dependencies(
            subtasks,
            composition_name=f"task-composer-{input.mode}",
            max_parallel=input.max_parallel,
        )

        # Step 4: Dispatch experts
        artifacts: list[Artifact] = []
        skipped: set[str] = set()

        if task_graph is not None:
            # Dispatch through TaskGraph-backed DAGDispatcher (G4 bridge)
            from .dag_dispatcher import DAGDispatcher

            dispatcher = DAGDispatcher(
                graph=task_graph,
                executor=executor,
                max_parallel=input.max_parallel,
                timeout_seconds=input.timeout_seconds,
            )
            dispatch_result = dispatcher.dispatch(dag, input.task)

            if dispatch_result.timed_out:
                raise TimeoutError(
                    f"TaskComposer pipeline timed out after {input.timeout_seconds}s"
                )

            # Preserve ordering: artifacts in the order they completed
            artifacts = list(dispatch_result.artifacts.values())
        else:
            # Legacy in-process topological loop (backward compatible)
            specialist_ids = {t.id for t in dag.specialist_tasks}
            executed: set[str] = set()
            failed: set[str] = set()
            deadline = (
                time.monotonic() + input.timeout_seconds
                if input.timeout_seconds is not None
                else None
            )
            for _ in range(len(dag.tasks)):
                for task in dag.tasks:
                    if deadline is not None and time.monotonic() > deadline:
                        raise TimeoutError(
                            f"TaskComposer pipeline timed out after {input.timeout_seconds}s"
                        )
                    if task.id in executed or task.id not in specialist_ids:
                        continue
                    if any(dep in failed for dep in task.blocked_by):
                        if task.id not in skipped:
                            logger.warning(
                                "Skipping task '%s' (agent '%s'): blocked by failed dependency %s",
                                task.id,
                                task.agent,
                                [d for d in task.blocked_by if d in failed],
                            )
                            skipped.add(task.id)
                        failed.add(task.id)  # treat as failed so dependents skip too
                        continue
                    if all(dep in executed for dep in task.blocked_by):
                        try:
                            artifact = executor(task.agent, input.task)
                            artifacts.append(artifact)
                        except Exception:
                            logger.exception(
                                "Executor failed for task '%s' (agent '%s') in legacy path",
                                task.id, task.agent,
                            )
                            failed.add(task.id)
                        executed.add(task.id)

        if not artifacts:
            return TaskComposerResult(
                task=input.task,
                selected_agents=selected,
                dag=dag,
                qa_passed=False,  # No artifacts = execution failure, not just "no match"
                skipped_tasks=list(skipped),
            )

        # Step 5: Integrate
        integrated = Integrator.merge(artifacts)

        # Step 6: QA Gate validation
        # Determine required sections from first selected agent's output contract
        first_profile = self.registry.get(selected[0].agent_id)
        required_sections: list[str] = []
        if first_profile:
            required_sections = (
                first_profile.get("output_contract", {}).get("required_sections", [])
            )

        gate_input = QAGateInput(
            output={"sections": integrated.merged_sections},
            required_sections=required_sections,
            task_type=input.mode,
        )
        qa_result = QAGate.run(gate_input)

        return TaskComposerResult(
            task=input.task,
            selected_agents=selected,
            dag=dag,
            integrated=integrated,
            qa_passed=qa_result.passed,
            skipped_tasks=list(skipped),
        )
