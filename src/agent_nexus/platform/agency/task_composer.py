"""TaskComposer — orchestrates the full agency pipeline.

select → plan → dispatch → integrate → validate.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .executor import ProfileBasedExecutor
from .integrator import Artifact, IntegratedArtifact, Integrator
from .planner import CompositionDAG, DAGTask, DynamicCompositePlanner, SubtaskDef
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
class _LegacyContext:
    """Mutable state bag for the legacy dispatch loop."""

    executed: set[str] = field(default_factory=set)
    failed: set[str] = field(default_factory=set)
    skipped: set[str] = field(default_factory=set)
    artifacts: list[Artifact] = field(default_factory=list)


@dataclass
class TaskComposerInput:
    """Input for a TaskComposer run."""

    task: str
    mode: str = "plan"
    max_parallel: int = 3
    timeout_seconds: float | None = None
    reasoning_protocol: bool = False


@dataclass
class TaskComposerResult:
    """Output from a TaskComposer run."""

    task: str
    selected_agents: list[SelectionResult] = field(default_factory=list)
    dag: CompositionDAG | None = None
    integrated: IntegratedArtifact | None = None
    qa_passed: bool | None = None
    skipped_tasks: list[str] = field(default_factory=list)
    output_target: str | None = None
    """Detected output intent: ``None``, ``"file"`` (generic), or a specific file path."""
    evolution_triggered: bool = False


# ---------------------------------------------------------------------------
# Output-intent detection (pipeline-level, used by TaskComposer + CLI)
# ---------------------------------------------------------------------------

_SPECIFIC_PATH_PATTERNS: list[str] = [
    r"输出到\s+(\S+\.\w+)",
    r"output\s+to\s+(\S+\.\w+)",
    r"save\s+to\s+(\S+\.\w+)",
    r"写入\s+(\S+\.\w+)",
]
_GENERIC_FILE_PATTERNS: list[str] = [
    r"输出到\s*文件",
    r"output\s+to\s+file",
    r"save\s+to\s+file",
    r"写入\s*文件",
]


def detect_output_target(task: str) -> str | None:
    """Extract output intent from task description.

    Returns a specific file path (e.g. ``"docs/review.md"``) or ``"file"``
    for generic "output to file" intent.  Returns ``None`` when no output
    intent is detected.

    Extracted paths are sanitized: only forward slashes, alphanumeric
    characters, hyphens, underscores, dots, and spaces are kept.
    """
    for pat in _SPECIFIC_PATH_PATTERNS:
        m = re.search(pat, task, re.IGNORECASE)
        if m:
            raw = m.group(1)
            sanitized = re.sub(r"[^A-Za-z0-9/_\-. ]", "", raw)
            return sanitized

    for pat in _GENERIC_FILE_PATTERNS:
        if re.search(pat, task, re.IGNORECASE):
            return "file"

    return None


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
            logger.warning("Skipping short keyword '%s' (< 3 chars) in capability map", keyword)
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
        evolution_callback: Callable[[TaskComposerResult], None] | None = None,
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
        output_target = detect_output_target(input.task)
        executor = expert_executor or ProfileBasedExecutor(self.registry)

        # Step 1: Infer capabilities (LLM or keyword fallback)
        required_caps = self._infer_capabilities(input.task, llm_planner)
        logger.info("TaskComposer: inferred capabilities: %s", required_caps)

        # Step 2: Select specialists
        selected = self.selector.select(
            SelectionRequest(
                task_type=input.mode,
                required_capabilities=required_caps,
                optional_capabilities=[],
                max_agents=5,
                permissions="plan",
            )
        )
        logger.info(
            "TaskComposer: selected %d experts: %s",
            len(selected),
            [s.agent_id for s in selected],
        )
        if not selected:
            return TaskComposerResult(task=input.task, output_target=output_target)

        # Step 3: Build subtasks and DAG
        subtasks = self._build_subtasks(selected, input.task, required_caps)
        dag = self.planner.resolve_dependencies(
            subtasks,
            composition_name=f"task-composer-{input.mode}",
            max_parallel=input.max_parallel,
        )

        # Step 4: Dispatch experts
        if task_graph is not None:
            artifacts, skipped = self._dispatch_via_graph(
                dag,
                input.task,
                executor,
                task_graph,
                input.timeout_seconds,
                input.max_parallel,
                concurrent,
            )
        else:
            artifacts, skipped = self._dispatch_legacy(
                dag,
                input.task,
                executor,
                input.timeout_seconds,
            )

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
                output_target=output_target,
            )

        # Step 5: Integrate (LLM or rule-based fallback)
        logger.info("TaskComposer: integrating %d artifacts", len(artifacts))
        integrated = (
            llm_integrator.synthesize(artifacts, task=input.task)
            if llm_integrator is not None
            else Integrator.merge(artifacts)
        )

        # Step 6: QA Gate validation
        qa_result = self._run_qa_gate(
            integrated,
            selected,
            input,
            llm_qa_gate,
            llm_integrator is not None,
        )

        result = TaskComposerResult(
            task=input.task,
            selected_agents=selected,
            dag=dag,
            integrated=integrated,
            qa_passed=qa_result.passed,
            skipped_tasks=list(skipped),
            output_target=output_target,
        )

        # Step 7: Evolution hook (optional, post-QAGate)
        if evolution_callback is not None and qa_result.passed:
            try:
                evolution_callback(result)
                result.evolution_triggered = True
                logger.info("TaskComposer: evolution callback triggered")
            except Exception:
                logger.warning("TaskComposer: evolution callback failed", exc_info=True)

        return result

    # ------------------------------------------------------------------
    # Private helpers (extracted from run() to reduce complexity)
    # ------------------------------------------------------------------

    def _infer_capabilities(
        self,
        task: str,
        llm_planner: LLMPlanner | None,
    ) -> list[str]:
        """Infer required capabilities from the task description."""
        if llm_planner is not None:
            return llm_planner.analyze_task(task).capabilities
        return infer_capabilities(task)

    def _build_subtasks(
        self,
        selected: list[SelectionResult],
        task: str,
        required_caps: list[str],
    ) -> list[SubtaskDef]:
        """Build subtask definitions from selected specialists."""
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
                    goal=task,
                    needed_capabilities=profile.get("capabilities", [])
                    if profile
                    else required_caps,
                    output_contract=artifact_type,
                    assigned_agent=sel.agent_id,
                )
            )
        return subtasks

    def _dispatch_via_graph(
        self,
        dag: CompositionDAG,
        task: str,
        executor: ExpertExecutor,
        task_graph: TaskGraph,
        timeout_seconds: float | None,
        max_parallel: int,
        concurrent: bool,
    ) -> tuple[list[Artifact], set[str]]:
        """Dispatch through TaskGraph-backed DAGDispatcher."""
        from .dag_dispatcher import DAGDispatcher

        dispatcher = DAGDispatcher(
            graph=task_graph,
            executor=executor,
            max_parallel=max_parallel,
            timeout_seconds=timeout_seconds,
            use_concurrency=concurrent,
        )
        try:
            dispatch_result = dispatcher.dispatch(dag, task)
        finally:
            dispatcher.close()

        if dispatch_result.timed_out:
            raise TimeoutError(f"TaskComposer pipeline timed out after {timeout_seconds}s")

        skipped: set[str] = set()
        if dispatch_result.failed or dispatch_result.cancelled:
            all_failed = dispatch_result.failed + dispatch_result.cancelled
            logger.warning(
                "TaskComposer: %d of %d specialist tasks failed (%d cancelled): %s",
                len(dispatch_result.failed),
                len(dag.specialist_tasks),
                len(dispatch_result.cancelled),
                all_failed,
            )
            for tid, err_msg in dispatch_result.errors.items():
                logger.error(
                    "TaskComposer: task '%s' failed because: %s",
                    tid,
                    err_msg,
                )
            skipped.update(dispatch_result.failed)
            skipped.update(dispatch_result.cancelled)

        return list(dispatch_result.artifacts.values()), skipped

    @staticmethod
    def _check_deadline(deadline: float | None, timeout_seconds: float | None) -> None:
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError(f"TaskComposer pipeline timed out after {timeout_seconds}s")

    @staticmethod
    def _should_skip_task(
        task_def: DAGTask,
        executed: set[str],
        failed: set[str],
        specialist_ids: set[str],
    ) -> bool | None:
        """Return True if task should be skipped, False if ready, None if pending."""
        if task_def.id in executed or task_def.id not in specialist_ids:
            return True
        if any(dep in failed for dep in task_def.blocked_by):
            return False  # blocked by failure — mark failed
        if not all(dep in executed for dep in task_def.blocked_by):
            return None  # still waiting for deps
        return None  # ready to execute (all deps done, none failed)

    def _dispatch_legacy(
        self,
        dag: CompositionDAG,
        task: str,
        executor: ExpertExecutor,
        timeout_seconds: float | None,
    ) -> tuple[list[Artifact], set[str]]:
        """Dispatch via legacy in-process topological loop."""
        specialist_ids = {t.id for t in dag.specialist_tasks}
        executed: set[str] = set()
        failed: set[str] = set()
        skipped: set[str] = set()
        artifacts: list[Artifact] = []
        deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
        ctx = _LegacyContext(executed, failed, skipped, artifacts)

        for _ in range(len(dag.tasks)):
            for task_def in dag.tasks:
                self._check_deadline(deadline, timeout_seconds)
                self._process_legacy_task(
                    task_def,
                    task,
                    executor,
                    specialist_ids,
                    ctx,
                )

        return artifacts, skipped

    def _process_legacy_task(
        self,
        task_def: DAGTask,
        task: str,
        executor: ExpertExecutor,
        specialist_ids: set[str],
        ctx: _LegacyContext,
    ) -> None:
        """Handle a single task in the legacy dispatch loop."""
        verdict = self._should_skip_task(task_def, ctx.executed, ctx.failed, specialist_ids)
        if verdict is True:
            return
        if verdict is False:
            self._mark_legacy_failure(task_def, ctx)
            return
        # verdict is None — check if deps are satisfied
        if all(dep in ctx.executed for dep in task_def.blocked_by):
            self._try_execute_legacy(task_def, task, executor, ctx)

    @staticmethod
    def _mark_legacy_failure(
        task_def: DAGTask,
        ctx: _LegacyContext,
    ) -> None:
        """Mark a task as failed due to dependency failure."""
        if task_def.id not in ctx.skipped:
            logger.warning(
                "Skipping task '%s' (agent '%s'): blocked by failed dependency %s",
                task_def.id,
                task_def.agent,
                [d for d in task_def.blocked_by if d in ctx.failed],
            )
            ctx.skipped.add(task_def.id)
        ctx.failed.add(task_def.id)

    @staticmethod
    def _try_execute_legacy(
        task_def: DAGTask,
        task: str,
        executor: ExpertExecutor,
        ctx: _LegacyContext,
    ) -> None:
        """Attempt to execute a task, recording success or failure."""
        try:
            artifact = executor(task_def.agent, task)
            ctx.executed.add(task_def.id)
            ctx.artifacts.append(artifact)
        except Exception:
            logger.exception(
                "Executor failed for task '%s' (agent '%s') in legacy path",
                task_def.id,
                task_def.agent,
            )
            ctx.failed.add(task_def.id)

    def _run_qa_gate(
        self,
        integrated: IntegratedArtifact,
        selected: list[SelectionResult],
        input: TaskComposerInput,
        llm_qa_gate: LLMQualityGate | None,
        using_llm_integration: bool,
    ):
        """Run QA gate validation (LLM or structural)."""
        first_profile = self.registry.get(selected[0].agent_id)
        required_sections: list[str] = []
        if first_profile:
            required_sections = first_profile.get("output_contract", {}).get(
                "required_sections", []
            )

        if llm_qa_gate is not None:
            return llm_qa_gate.evaluate(
                integrated,
                task=input.task,
                required_sections=[] if using_llm_integration else required_sections,
                task_type=input.mode,
            )

        gate_input = QAGateInput(
            output={"sections": integrated.merged_sections},
            required_sections=required_sections,
            task_type=input.mode,
        )
        return QAGate.run(gate_input)
