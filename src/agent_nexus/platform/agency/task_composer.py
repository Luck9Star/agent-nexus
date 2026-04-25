"""TaskComposer — orchestrates the full agency pipeline: select → plan → dispatch → integrate → validate."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

from .executor import ProfileBasedExecutor
from .integrator import Artifact, IntegratedArtifact, Integrator
from .planner import CompositionDAG, DynamicCompositePlanner, SubtaskDef
from .qa_gate import QAGate, QAGateInput
from .registry import ExpertRegistry
from .selector import SelectionRequest, SelectionResult, SpecialistSelector


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


class ExpertExecutor(Protocol):
    """Protocol for expert execution — pluggable for testing or real dispatch."""

    def __call__(self, profile_id: str, task: str) -> Artifact: ...


def _default_expert_executor(profile_id: str, task: str) -> Artifact:
    """Fallback no-op executor -- returns a stub artifact."""
    return Artifact(
        source_agent=profile_id,
        artifact_type="stub",
        sections={"context": task},
    )


# Task type → required capabilities mapping
_TASK_CAPABILITY_MAP: dict[str, list[str]] = {
    "architecture": ["system_design", "architecture_review"],
    "review": ["code_review", "security_review"],
    "security": ["security_review", "threat_modeling"],
    "testing": ["test_design", "test_analysis"],
    "documentation": ["technical_writing"],
    "evaluation": ["tool_evaluation"],
    "onboarding": ["codebase_onboarding"],
    "reliability": ["reliability_review"],
    "integration": ["system_design", "tool_evaluation"],
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
    ) -> TaskComposerResult:
        """Execute the full pipeline."""
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
                    needed_capabilities=required_caps,
                    output_contract=artifact_type,
                    assigned_agent=sel.agent_id,
                )
            )

        dag = self.planner.resolve_dependencies(
            subtasks,
            composition_name=f"task-composer-{input.mode}",
            max_parallel=input.max_parallel,
        )

        # Step 4: Dispatch experts (execute only specialist tasks, in topological order)
        artifacts: list[Artifact] = []
        specialist_ids = {t.id for t in dag.specialist_tasks}

        # Simple topological execution — tasks with no blocked_by first
        executed: set[str] = set()
        deadline = (
            time.monotonic() + input.timeout_seconds
            if input.timeout_seconds is not None
            else None
        )
        for _ in range(len(dag.tasks)):  # iterate enough times
            for task in dag.tasks:
                if deadline is not None and time.monotonic() > deadline:
                    raise TimeoutError(
                        f"TaskComposer pipeline timed out after {input.timeout_seconds}s"
                    )
                if task.id in executed or task.id not in specialist_ids:
                    continue
                if all(dep in executed for dep in task.blocked_by):
                    artifact = executor(task.agent, input.task)
                    artifacts.append(artifact)
                    executed.add(task.id)

        if not artifacts:
            return TaskComposerResult(
                task=input.task,
                selected_agents=selected,
                dag=dag,
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
        )
