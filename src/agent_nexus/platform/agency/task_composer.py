"""TaskComposer — orchestrates the full agency pipeline.

select → plan → dispatch → integrate → validate.
"""

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
    from .llm_integrator import LLMIntegrator
    from .llm_planner import LLMPlanner
    from .llm_qa_gate import LLMQualityGate

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
    "orchestration": ["orchestration", "task_decomposition"],
    "coordinate": ["agent_coordination"],
    "coordination": ["agent_coordination"],
    "decomposition": ["task_decomposition"],
    "navigate": ["code_navigation", "architecture_mapping"],
    "compare": ["comparison_analysis"],
    "technology": ["technology_assessment"],
    # Previously unreachable capabilities (audit gap fix)
    "tradeoff": ["tradeoff_analysis"],
    "api_documentation": ["api_documentation", "technical_writing"],
    "code_indexing": ["code_indexing", "lsp_indexing"],
}

# Chinese keyword → required capabilities mapping
_CN_TASK_CAPABILITY_MAP: dict[str, list[str]] = {
    "架构": ["system_design", "architecture_review"],
    "设计": ["system_design"],
    "评审": ["code_review"],
    "安全": ["security_review", "vulnerability_assessment"],
    "测试": ["test_design", "test_analysis"],
    "文档": ["technical_writing", "documentation"],
    "评估": ["tool_evaluation"],
    "可靠性": ["reliability_review"],
    "集成": ["system_design", "tool_evaluation"],
    "后端": ["backend_design", "api_design"],
    "接口": ["api_design"],
    "数据库": ["database_design"],
    "提示词": ["prompt_engineering"],
    "维护性": ["maintainability_review"],
    "漏洞": ["vulnerability_assessment"],
    "事件": ["incident_analysis", "observability"],
    "覆盖": ["coverage_assessment"],
    "索引": ["lsp_indexing", "semantic_analysis"],
    "编排": ["orchestration", "task_decomposition"],
    "协调": ["agent_coordination"],
    "分解": ["task_decomposition"],
    "导航": ["code_navigation", "architecture_mapping"],
    "对比": ["comparison_analysis"],
    "技术": ["technology_assessment"],
    "权衡": ["tradeoff_analysis"],
}


def infer_capabilities(task: str) -> list[str]:
    """Infer required capabilities from task description.

    Supports both English (word-boundary matching) and Chinese
    (substring matching) task descriptions.
    """
    matched: list[str] = []

    # English keyword matching with word boundaries
    task_lower = task.lower()
    for keyword, caps in _TASK_CAPABILITY_MAP.items():
        if len(keyword) < 3:
            continue
        if re.search(rf"\b{re.escape(keyword)}\b", task_lower):
            matched.extend(caps)

    # Chinese keyword matching (no word boundary support for CJK)
    for keyword, caps in _CN_TASK_CAPABILITY_MAP.items():
        if keyword in task:
            matched.extend(caps)

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
        llm_planner: LLMPlanner | None = None,
        llm_integrator: LLMIntegrator | None = None,
        llm_qa_gate: LLMQualityGate | None = None,
        concurrent: bool = False,
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
        llm_planner:
            Optional LLMPlanner for semantic task decomposition.
            Falls back to keyword-based ``infer_capabilities()`` when None.
        llm_integrator:
            Optional LLMIntegrator for semantic artifact synthesis.
            Falls back to ``Integrator.merge()`` when None.
        llm_qa_gate:
            Optional LLMQualityGate for semantic quality evaluation.
            Falls back to structural ``QAGate`` when None.
        concurrent:
            When True, use ThreadPoolExecutor for parallel LLM calls.
            Passed through to DAGDispatcher.
        """
        executor = expert_executor or ProfileBasedExecutor(self.registry)

        # Step 1: Infer capabilities (LLM or keyword fallback)
        if llm_planner is not None:
            planner_output = llm_planner.analyze_task(input.task)
            required_caps = planner_output.capabilities
        else:
            required_caps = infer_capabilities(input.task)

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
                    needed_capabilities=profile.get("capabilities", [])
                    if profile
                    else required_caps,
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
                max_batch_size=input.max_parallel,
                timeout_seconds=input.timeout_seconds,
                concurrent=concurrent,
            )
            dispatch_result = dispatcher.dispatch(dag, input.task)

            if dispatch_result.timed_out:
                raise TimeoutError(
                    f"TaskComposer pipeline timed out after {input.timeout_seconds}s"
                )

            # Propagate partial execution info: track which tasks failed/skipped
            if dispatch_result.failed:
                logger.warning(
                    "TaskComposer: %d of %d specialist tasks failed: %s",
                    len(dispatch_result.failed),
                    len(dag.specialist_tasks),
                    dispatch_result.failed,
                )
                # Log specific error messages for root cause diagnosis
                for tid, err_msg in dispatch_result.errors.items():
                    logger.error(
                        "TaskComposer: task '%s' failed because: %s",
                        tid,
                        err_msg,
                    )
                skipped.update(dispatch_result.failed)

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
                                task.id,
                                task.agent,
                            )
                            failed.add(task.id)
                        executed.add(task.id)

        if not artifacts:
            reason = "no specialists selected" if not selected else "all specialists failed"
            if skipped:
                reason = f"execution failed or skipped: {sorted(skipped)}"
            logger.warning("TaskComposer produced no artifacts: %s", reason)
            return TaskComposerResult(
                task=input.task,
                selected_agents=selected,
                dag=dag,
                qa_passed=False,
                skipped_tasks=list(skipped),
            )

        # Step 5: Integrate (LLM or rule-based fallback)
        if llm_integrator is not None:
            integrated = llm_integrator.synthesize(artifacts, task=input.task)
        else:
            integrated = Integrator.merge(artifacts)

        # Step 6: QA Gate validation (LLM or structural-only)
        # Determine required sections from first selected agent's output contract
        first_profile = self.registry.get(selected[0].agent_id)
        required_sections: list[str] = []
        if first_profile:
            required_sections = first_profile.get("output_contract", {}).get(
                "required_sections", []
            )

        if llm_qa_gate is not None:
            qa_result = llm_qa_gate.evaluate(
                integrated,
                task=input.task,
                required_sections=required_sections,
                task_type=input.mode,
            )
        else:
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
