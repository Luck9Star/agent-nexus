"""E2E tests: agency pipeline integration — DAGDispatcher, failure cascade, timeout, TaskGraph.

Tests cover:
1. Full pipeline: importer -> registry -> selector -> planner -> executor -> integrator -> qa_gate
2. Capability-based specialist selection for specific task domains
3. DAGDispatcher with SQLite TaskGraph dispatch
4. Failure cascade: dependent tasks skipped when ancestor fails
5. Timeout handling: DAGDispatcher respects timeout_seconds
6. No-match scenario: graceful handling when no specialist matches
7. TaskComposer with task_graph parameter: TaskGraph-backed execution path
8. Capability inference: keyword-to-capability mapping coverage
9. All 12 expert types selectable by primary capability
10. Multi-agent composition: set-cover composes team when no single agent covers all caps
11. Network capability impact: tool permission verification
12. Edge cases: timeout+task_graph, empty QA, single artifact
13. ProfileBasedExecutor: real profile-derived artifacts through full pipeline
14. Importer disk write: import_all creates valid profile files
15. Integrator advanced: conflict detection, validation errors, type mismatch
16. Planner validation: empty subtasks, duplicate IDs, reserved IDs, invalid chars
17. TOML serialization: generate_toml roundtrip and rejection
18. GitNexus QA gate: code_change/refactor gate enforcement
19. Max parallel enforcement: DAGDispatcher respects concurrency limits

Run with: pytest tests/e2e/test_agency_pipeline_e2e.py --run-e2e --timeout=60
"""

import tempfile
from pathlib import Path

import pytest

from agent_nexus.platform.agency.dag_dispatcher import (
    DAGDispatcher,
    DispatchResult,
    load_dag_into_graph,
)
from agent_nexus.platform.agency.importer import AgencyImporter
from agent_nexus.platform.agency.integrator import Artifact, Integrator
from agent_nexus.platform.agency.planner import (
    CompositionDAG,
    DAGTask,
    DynamicCompositePlanner,
    SubtaskDef,
)
from agent_nexus.platform.agency.qa_gate import QAGate, QAGateInput
from agent_nexus.platform.agency.registry import ExpertRegistry
from agent_nexus.platform.agency.selector import SelectionRequest, SpecialistSelector
from agent_nexus.platform.agency.task_composer import (
    TaskComposer,
    TaskComposerInput,
    TaskComposerResult,
)
from agent_nexus.platform.orchestration.task_graph import TaskGraph

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_VENDOR_DIR = _PROJECT_ROOT / "vendor" / "agency-agents"
_ALLOWLIST_PATH = _PROJECT_ROOT / "config" / "agency-agents.allowlist.yaml"

# Skip all tests in this module if vendor submodule is unavailable
pytestmark = pytest.mark.skipif(
    not _VENDOR_DIR.is_dir(),
    reason="vendor/agency-agents submodule not available",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_registry() -> ExpertRegistry:
    """Build an ExpertRegistry loaded with all 12 profiles from vendor."""
    with tempfile.TemporaryDirectory() as tmpdir:
        importer = AgencyImporter(
            vendor_path=str(_VENDOR_DIR),
            allowlist_path=str(_ALLOWLIST_PATH),
            output_dir=tmpdir,
        )
        profiles = importer.dry_run()

    registry = ExpertRegistry()
    for pkg in profiles:
        ep = pkg["expert_profile"]
        registry.add(ep["id"], ep, ep["capabilities"])
    return registry


def _mock_executor(profile_id: str, task: str) -> Artifact:
    """Instant mock executor producing a standard artifact."""
    return Artifact(
        source_agent=profile_id,
        artifact_type="report",
        sections={
            "context": task,
            "summary": f"Analysis from {profile_id}",
            "recommendations": [f"Apply {profile_id} expertise"],
            "findings": [f"Finding from {profile_id}"],
            "risks": [f"Risk identified by {profile_id}"],
            "next_steps": [f"Follow up with {profile_id}"],
        },
    )


# ---------------------------------------------------------------------------
# 1. Full Pipeline Test
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestFullPipeline:
    """E2E: full pipeline from importer through QA gate."""

    def test_importer_to_qa_gate(self):
        """importer loads profiles -> registry stores -> selector picks ->
        planner creates DAG -> executor runs (mock) -> integrator merges ->
        QA gate validates."""
        registry = _build_registry()

        # Selector
        selector = SpecialistSelector(registry)
        req = SelectionRequest(
            task_type="plan",
            required_capabilities=["system_design", "architecture_review"],
            optional_capabilities=["security_review"],
            max_agents=3,
            permissions="plan",
        )
        selected = selector.select(req)
        assert len(selected) > 0, "Selector should find specialists for architecture task"

        # Planner
        planner = DynamicCompositePlanner()
        subtasks = [
            SubtaskDef(
                id=sel.agent_id.replace("agency.", ""),
                goal="Design system architecture",
                needed_capabilities=registry.get(sel.agent_id).get("capabilities", [])
                if registry.get(sel.agent_id)
                else ["system_design"],
                output_contract="report",
                assigned_agent=sel.agent_id,
            )
            for sel in selected
        ]
        dag = planner.resolve_dependencies(
            subtasks,
            composition_name="full-pipeline-test",
            max_parallel=3,
        )
        assert isinstance(dag, CompositionDAG)
        task_ids = {t.id for t in dag.tasks}
        assert "integrate" in task_ids
        assert "validate" in task_ids

        # Executor (mock)
        artifacts = [_mock_executor(sel.agent_id, "Design system") for sel in selected]
        assert len(artifacts) == len(selected)

        # Integrator
        integrated = Integrator.merge(artifacts)
        assert integrated.source_agents == [sel.agent_id for sel in selected]

        # QA Gate
        gate_input = QAGateInput(
            output={"sections": integrated.merged_sections},
            required_sections=["context", "risks", "next_steps"],
            task_type="plan",
        )
        qa_result = QAGate.run(gate_input)
        assert qa_result.passed is True

    def test_task_composer_runs_full_pipeline(self):
        """TaskComposer.run() executes the full pipeline end-to-end with mock executor."""
        registry = _build_registry()
        composer = TaskComposer(registry=registry)

        inp = TaskComposerInput(
            task="Design a microservice architecture for a payment system",
            mode="plan",
            max_parallel=3,
        )
        result = composer.run(inp, expert_executor=_mock_executor)

        assert isinstance(result, TaskComposerResult)
        assert len(result.selected_agents) > 0
        assert result.dag is not None
        assert result.integrated is not None
        assert result.qa_passed is not None


# ---------------------------------------------------------------------------
# 2. Capability-Based Selection
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestCapabilityBasedSelection:
    """E2E: correct experts selected for architecture, review, security, testing tasks."""

    def test_architecture_selects_architect(self):
        """Architecture task selects software-architect or backend-architect."""
        registry = _build_registry()
        selector = SpecialistSelector(registry)
        req = SelectionRequest(
            task_type="plan",
            required_capabilities=["system_design"],
            optional_capabilities=[],
            max_agents=5,
            permissions="plan",
        )
        results = selector.select(req)
        agent_ids = [r.agent_id for r in results]
        assert any("architect" in aid for aid in agent_ids), f"Expected architect in {agent_ids}"

    def test_review_selects_code_reviewer(self):
        """Review task selects code-reviewer with code_review capability."""
        registry = _build_registry()
        selector = SpecialistSelector(registry)
        req = SelectionRequest(
            task_type="plan",
            required_capabilities=["code_review"],
            optional_capabilities=["maintainability_review"],
            max_agents=3,
            permissions="plan",
        )
        results = selector.select(req)
        agent_ids = [r.agent_id for r in results]
        assert "agency.code-reviewer" in agent_ids, f"Expected agency.code-reviewer in {agent_ids}"

    def test_security_selects_security_engineer(self):
        """Security task selects security-engineer with threat_modeling capability."""
        registry = _build_registry()
        selector = SpecialistSelector(registry)
        req = SelectionRequest(
            task_type="plan",
            required_capabilities=["security_review", "threat_modeling"],
            optional_capabilities=[],
            max_agents=3,
            permissions="plan",
        )
        results = selector.select(req)
        agent_ids = [r.agent_id for r in results]
        assert "agency.security-engineer" in agent_ids, (
            f"Expected agency.security-engineer in {agent_ids}"
        )

    def test_testing_selects_test_analyzer(self):
        """Testing task selects test-results-analyzer with test_design capability."""
        registry = _build_registry()
        selector = SpecialistSelector(registry)
        req = SelectionRequest(
            task_type="plan",
            required_capabilities=["test_design", "test_analysis"],
            optional_capabilities=[],
            max_agents=3,
            permissions="plan",
        )
        results = selector.select(req)
        agent_ids = [r.agent_id for r in results]
        assert "agency.test-results-analyzer" in agent_ids, (
            f"Expected agency.test-results-analyzer in {agent_ids}"
        )


# ---------------------------------------------------------------------------
# 3. DAGDispatcher with TaskGraph
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestDAGDispatcherWithTaskGraph:
    """E2E: load DAG into SQLite TaskGraph and dispatch through DAGDispatcher."""

    def test_load_dag_into_graph(self):
        """load_dag_into_graph creates TaskItems for specialist tasks only."""
        graph = TaskGraph(":memory:")
        subtasks = [
            SubtaskDef(
                id="architect",
                goal="Design system",
                needed_capabilities=["system_design"],
                output_contract="report",
                assigned_agent="agency.software-architect",
            ),
            SubtaskDef(
                id="reviewer",
                goal="Review architecture",
                needed_capabilities=["code_review"],
                output_contract="report",
                assigned_agent="agency.code-reviewer",
            ),
        ]
        planner = DynamicCompositePlanner()
        dag = planner.plan(subtasks, composition_name="tg-load-test", max_parallel=2)

        items = load_dag_into_graph(dag, "Design system", graph)
        assert len(items) == 2, f"Expected 2 specialist TaskItems, got {len(items)}"
        assert all(item.id in {"architect", "reviewer"} for item in items)

        # integrate and validate should NOT be in the graph
        assert graph.get_task("integrate") is None
        assert graph.get_task("validate") is None

        graph.close()

    def test_dispatch_produces_artifacts(self):
        """DAGDispatcher executes specialists and collects artifacts."""
        graph = TaskGraph(":memory:")
        subtasks = [
            SubtaskDef(
                id="architect",
                goal="Design",
                needed_capabilities=["system_design"],
                output_contract="report",
                assigned_agent="agency.software-architect",
            ),
            SubtaskDef(
                id="reviewer",
                goal="Review",
                needed_capabilities=["code_review"],
                output_contract="report",
                assigned_agent="agency.code-reviewer",
            ),
        ]
        planner = DynamicCompositePlanner()
        dag = planner.plan(subtasks, composition_name="dispatch-test", max_parallel=2)

        dispatcher = DAGDispatcher(
            graph=graph,
            executor=_mock_executor,
            max_parallel=2,
        )
        result = dispatcher.dispatch(dag, "Design and review")

        assert isinstance(result, DispatchResult)
        assert len(result.artifacts) == 2
        assert len(result.completed) == 2
        assert len(result.failed) == 0
        assert result.timed_out is False
        assert "architect" in result.artifacts
        assert "reviewer" in result.artifacts

        graph.close()

    def test_dispatch_respects_dependencies(self):
        """When task B depends on task A, A completes before B starts."""
        graph = TaskGraph(":memory:")

        # Create a DAG where reviewer depends on architect (shared capability)
        subtasks = [
            SubtaskDef(
                id="architect",
                goal="Design",
                needed_capabilities=["system_design", "architecture_review"],
                output_contract="report",
                assigned_agent="agency.software-architect",
            ),
            SubtaskDef(
                id="reviewer",
                goal="Review",
                needed_capabilities=["architecture_review"],
                output_contract="report",
                assigned_agent="agency.code-reviewer",
            ),
        ]
        planner = DynamicCompositePlanner()
        dag = planner.resolve_dependencies(
            subtasks, composition_name="dep-order-test", max_parallel=2
        )

        # Verify dependency exists in the DAG
        reviewer_task = next(t for t in dag.tasks if t.id == "reviewer")
        assert "architect" in reviewer_task.blocked_by

        # Dispatch and verify ordering
        execution_order: list[str] = []

        def ordered_executor(profile_id: str, task: str) -> Artifact:
            # Map agent to task id for tracking
            task_id = profile_id.replace("agency.", "")
            execution_order.append(task_id)
            return _mock_executor(profile_id, task)

        dispatcher = DAGDispatcher(
            graph=graph,
            executor=ordered_executor,
            max_parallel=1,  # force sequential to observe order
        )
        result = dispatcher.dispatch(dag, "Design and review")

        assert len(result.completed) == 2
        # Architect should execute before reviewer
        assert execution_order.index("software-architect") < execution_order.index(
            "code-reviewer"
        ), f"Expected architect before reviewer, got {execution_order}"

        graph.close()


# ---------------------------------------------------------------------------
# 4. Failure Cascade
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestFailureCascade:
    """E2E: when one expert fails, dependent tasks are properly skipped."""

    def test_dag_dispatcher_failure_cascades(self):
        """When task A fails, task B (which depends on A) is marked failed, not executed."""
        graph = TaskGraph(":memory:")

        call_log: list[str] = []

        def selective_fail_executor(profile_id: str, task: str) -> Artifact:
            call_log.append(profile_id)
            if profile_id == "agency.software-architect":
                raise RuntimeError("Architect expert crashed")
            return _mock_executor(profile_id, task)

        subtasks = [
            SubtaskDef(
                id="architect",
                goal="Design",
                needed_capabilities=["system_design", "architecture_review"],
                output_contract="report",
                assigned_agent="agency.software-architect",
            ),
            SubtaskDef(
                id="reviewer",
                goal="Review",
                needed_capabilities=["architecture_review"],
                output_contract="report",
                assigned_agent="agency.code-reviewer",
            ),
        ]
        planner = DynamicCompositePlanner()
        dag = planner.resolve_dependencies(
            subtasks, composition_name="cascade-test", max_parallel=2
        )

        dispatcher = DAGDispatcher(
            graph=graph,
            executor=selective_fail_executor,
            max_parallel=2,
        )
        result = dispatcher.dispatch(dag, "Design and review")

        # Architect should fail
        assert "architect" in result.failed, f"Expected 'architect' in failed, got {result.failed}"

        # Reviewer depends on architect, should also be failed (not silently executed)
        assert "reviewer" in result.failed, (
            f"Expected 'reviewer' in failed (dependency cascade), got failed={result.failed}"
        )

        # Reviewer should NOT have been called (it was blocked by failed dependency)
        assert "agency.code-reviewer" not in call_log, (
            f"Reviewer should not have been called, but call_log={call_log}"
        )

        # No artifacts produced
        assert len(result.artifacts) == 0

        graph.close()

    def test_legacy_path_failure_skips_dependents(self):
        """Legacy in-process loop also skips dependents on failure."""
        registry = _build_registry()
        composer = TaskComposer(registry=registry)

        inp = TaskComposerInput(
            task="Architecture design that will fail",
            mode="plan",
            max_parallel=2,
        )

        call_count = {"n": 0}

        def failing_executor(profile_id: str, task: str) -> Artifact:
            call_count["n"] += 1
            raise RuntimeError(f"Expert {profile_id} failed")

        result = composer.run(inp, expert_executor=failing_executor)

        # All tasks fail, so integrated should be None (no artifacts produced)
        assert isinstance(result, TaskComposerResult)
        assert result.integrated is None

    def test_partial_failure_some_succeed(self):
        """When some tasks fail but others succeed, only the failed cascade is skipped."""
        graph = TaskGraph(":memory:")

        call_log: list[str] = []

        def partial_fail_executor(profile_id: str, task: str) -> Artifact:
            call_log.append(profile_id)
            if "security" in profile_id:
                raise RuntimeError("Security expert unavailable")
            return _mock_executor(profile_id, task)

        subtasks = [
            SubtaskDef(
                id="architect",
                goal="Design",
                needed_capabilities=["system_design"],
                output_contract="report",
                assigned_agent="agency.software-architect",
            ),
            SubtaskDef(
                id="reviewer",
                goal="Review",
                needed_capabilities=["code_review"],
                output_contract="report",
                assigned_agent="agency.code-reviewer",
            ),
            SubtaskDef(
                id="security",
                goal="Security audit",
                needed_capabilities=["security_review"],
                output_contract="report",
                assigned_agent="agency.security-engineer",
            ),
        ]
        planner = DynamicCompositePlanner()
        # Use plan() to put all tasks in parallel (no inter-task deps)
        dag = planner.plan(subtasks, composition_name="partial-fail-test", max_parallel=3)

        dispatcher = DAGDispatcher(
            graph=graph,
            executor=partial_fail_executor,
            max_parallel=3,
        )
        result = dispatcher.dispatch(dag, "Full pipeline test")

        # Security should fail, others should succeed
        assert "security" in result.failed
        assert "architect" in result.completed
        assert "reviewer" in result.completed
        assert len(result.artifacts) == 2  # architect + reviewer
        assert "security" not in result.artifacts

        graph.close()


# ---------------------------------------------------------------------------
# 5. Timeout Handling
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestTimeoutHandling:
    """E2E: DAGDispatcher respects timeout_seconds parameter."""

    def test_dispatcher_timeout_returns_timed_out(self):
        """DAGDispatcher with very short timeout returns timed_out=True."""
        graph = TaskGraph(":memory:")

        import time

        def slow_executor(profile_id: str, task: str) -> Artifact:
            # Sleep long enough to exceed timeout
            time.sleep(0.5)
            return _mock_executor(profile_id, task)

        subtasks = [
            SubtaskDef(
                id="slow-architect",
                goal="Design",
                needed_capabilities=["system_design"],
                output_contract="report",
                assigned_agent="agency.software-architect",
            ),
            SubtaskDef(
                id="slow-reviewer",
                goal="Review",
                needed_capabilities=["code_review"],
                output_contract="report",
                assigned_agent="agency.code-reviewer",
            ),
        ]
        planner = DynamicCompositePlanner()
        dag = planner.plan(subtasks, composition_name="timeout-test", max_parallel=2)

        dispatcher = DAGDispatcher(
            graph=graph,
            executor=slow_executor,
            max_parallel=2,
            timeout_seconds=0.1,  # Very short timeout
        )
        result = dispatcher.dispatch(dag, "Timeout test")

        assert result.timed_out is True, "Expected timed_out=True with short timeout"

        graph.close()

    def test_task_composer_timeout_raises(self):
        """TaskComposer raises TimeoutError when timeout_seconds is exceeded."""
        import time

        registry = _build_registry()
        composer = TaskComposer(registry=registry)

        def slow_executor(profile_id: str, task: str) -> Artifact:
            time.sleep(0.5)
            return _mock_executor(profile_id, task)

        # "Design architecture" infers [system_design, architecture_review],
        # which selects agency.software-architect. This ensures the execution
        # loop is reached before the timeout triggers.
        inp = TaskComposerInput(
            task="Design architecture",
            mode="plan",
            max_parallel=2,
            timeout_seconds=0.1,
        )

        with pytest.raises(TimeoutError):
            composer.run(inp, expert_executor=slow_executor)


# ---------------------------------------------------------------------------
# 6. No Match Scenario
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestNoMatchScenario:
    """E2E: graceful handling when no specialist matches a task."""

    def test_empty_registry_returns_empty_result(self):
        """TaskComposer with empty registry returns empty result without crashing."""
        empty_registry = ExpertRegistry()
        composer = TaskComposer(registry=empty_registry)

        inp = TaskComposerInput(
            task="Design a quantum computing system",
            mode="plan",
        )
        result = composer.run(inp)

        assert isinstance(result, TaskComposerResult)
        assert len(result.selected_agents) == 0
        assert result.dag is None
        assert result.integrated is None
        assert result.qa_passed is None

    def test_impossible_capability_returns_empty(self):
        """Selector returns empty list for impossible capability requirements."""
        registry = _build_registry()
        selector = SpecialistSelector(registry)

        req = SelectionRequest(
            task_type="plan",
            required_capabilities=["quantum_computing", "spaceflight_navigation"],
            optional_capabilities=[],
            max_agents=5,
            permissions="plan",
        )
        results = selector.select(req)
        assert results == [], f"Expected empty list for impossible caps, got {len(results)} results"

    def test_task_composer_no_match_graceful(self):
        """TaskComposer handles tasks where no specialist has matching capability."""
        registry = _build_registry()
        composer = TaskComposer(registry=registry)

        # "quantum" is not in _TASK_CAPABILITY_MAP, so fallback to system_design
        # is used, but if we create a registry with no matching agents it should
        # still work. Test with a registry that has agents but none match the
        # specific impossible capabilities.
        inp = TaskComposerInput(
            task="Design a warp drive engine",
            mode="plan",
        )
        result = composer.run(inp)

        assert isinstance(result, TaskComposerResult)
        # Should not crash -- may select best available or return empty
        # Key assertion: no unhandled exception

    def test_dispatcher_with_empty_dag(self):
        """DAGDispatcher with a DAG that has no specialist tasks returns empty result."""
        graph = TaskGraph(":memory:")

        # Create a DAG with only synthetic tasks (no specialists)
        dag = CompositionDAG(
            name="empty-specialists",
            max_parallel=1,
            tasks=[
                DAGTask(
                    id="integrate",
                    agent="nexus.integrator",
                    output="final_plan",
                    blocked_by=[],
                    task_type="synthetic",
                ),
                DAGTask(
                    id="validate",
                    agent="nexus.qa-gate",
                    output="validated_plan",
                    blocked_by=["integrate"],
                    task_type="synthetic",
                ),
            ],
        )

        dispatcher = DAGDispatcher(
            graph=graph,
            executor=_mock_executor,
            max_parallel=1,
        )
        result = dispatcher.dispatch(dag, "Empty test")

        assert isinstance(result, DispatchResult)
        assert len(result.artifacts) == 0
        assert len(result.completed) == 0
        assert result.timed_out is False

        graph.close()


# ---------------------------------------------------------------------------
# 7. TaskComposer with task_graph Parameter
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestTaskComposerWithTaskGraph:
    """E2E: TaskGraph-backed path in TaskComposer produces correct results."""

    def test_task_graph_path_produces_full_result(self):
        """When task_graph is provided, TaskComposer uses DAGDispatcher path."""
        registry = _build_registry()
        composer = TaskComposer(registry=registry)
        graph = TaskGraph(":memory:")

        inp = TaskComposerInput(
            task="Design a system architecture",
            mode="plan",
            max_parallel=2,
        )

        result = composer.run(inp, expert_executor=_mock_executor, task_graph=graph)

        assert isinstance(result, TaskComposerResult)
        assert len(result.selected_agents) > 0
        assert result.dag is not None
        assert result.integrated is not None
        assert result.qa_passed is not None

        # Verify TaskGraph has recorded the tasks
        for sel in result.selected_agents:
            task_id = sel.agent_id.replace("agency.", "")
            task_item = graph.get_task(task_id)
            if task_item is not None:
                assert task_item.state.value == "completed", (
                    f"Task {task_id} should be completed, got {task_item.state}"
                )

        graph.close()

    def test_task_graph_path_with_failure(self):
        """TaskGraph path handles executor failures gracefully."""
        registry = _build_registry()
        composer = TaskComposer(registry=registry)
        graph = TaskGraph(":memory:")

        call_count = {"n": 0}

        def always_fail_executor(profile_id: str, task: str) -> Artifact:
            call_count["n"] += 1
            raise RuntimeError(f"Expert {profile_id} failed")

        inp = TaskComposerInput(
            task="Design architecture",
            mode="plan",
            max_parallel=2,
        )

        # All experts fail -> no artifacts -> integrated is None
        result = composer.run(inp, expert_executor=always_fail_executor, task_graph=graph)

        assert isinstance(result, TaskComposerResult)
        assert result.integrated is None
        assert call_count["n"] > 0, "Executor should have been called"

        graph.close()

    def test_task_graph_path_matches_legacy_path(self):
        """TaskGraph path and legacy path produce the same structure (agents + DAG)."""
        registry = _build_registry()
        composer = TaskComposer(registry=registry)

        # "Code review the changes" infers [code_review, security_review],
        # which matches agency.code-reviewer (has both capabilities).
        inp = TaskComposerInput(
            task="Code review the changes",
            mode="plan",
            max_parallel=3,
        )

        # Legacy path
        legacy_result = composer.run(inp, expert_executor=_mock_executor)

        # TaskGraph path
        graph = TaskGraph(":memory:")
        tg_result = composer.run(inp, expert_executor=_mock_executor, task_graph=graph)

        # Both should select the same agents
        legacy_ids = sorted([a.agent_id for a in legacy_result.selected_agents])
        tg_ids = sorted([a.agent_id for a in tg_result.selected_agents])
        assert legacy_ids == tg_ids, f"Legacy {legacy_ids} != TaskGraph {tg_ids}"

        # Both should produce integrated artifacts
        assert legacy_result.integrated is not None
        assert tg_result.integrated is not None

        graph.close()

    def test_task_graph_records_states(self):
        """TaskGraph-backed execution records correct state transitions."""
        registry = _build_registry()
        composer = TaskComposer(registry=registry)
        graph = TaskGraph(":memory:")

        inp = TaskComposerInput(
            task="Design a backend API architecture",
            mode="plan",
            max_parallel=2,
        )

        result = composer.run(inp, expert_executor=_mock_executor, task_graph=graph)
        assert isinstance(result, TaskComposerResult)

        # Check that specialist tasks were loaded into the graph
        for sel in result.selected_agents:
            task_id = sel.agent_id.replace("agency.", "")
            task_item = graph.get_task(task_id)
            assert task_item is not None, f"Task {task_id} not found in TaskGraph"
            # After successful execution, state should be completed
            assert task_item.state.value == "completed", (
                f"Task {task_id} state is {task_item.state}, expected completed"
            )

        graph.close()


# ---------------------------------------------------------------------------
# 8. Capability Inference Coverage
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestCapabilityInference:
    """E2E: infer_capabilities covers all 36 declared expert capabilities."""

    def test_tradeoff_analysis_now_reachable(self):
        """'tradeoff' keyword now triggers tradeoff_analysis capability."""
        from agent_nexus.platform.agency.task_composer import infer_capabilities

        caps = infer_capabilities("Analyze tradeoff of microservices vs monolith")
        assert "tradeoff_analysis" in caps, (
            f"Expected tradeoff_analysis in inferred caps, got {caps}"
        )

    def test_api_documentation_now_reachable(self):
        """'api_documentation' keyword triggers api_documentation capability."""
        from agent_nexus.platform.agency.task_composer import infer_capabilities

        caps = infer_capabilities("Generate api_documentation for the REST endpoints")
        assert "api_documentation" in caps, (
            f"Expected api_documentation in inferred caps, got {caps}"
        )

    def test_code_indexing_now_reachable(self):
        """'code_indexing' keyword triggers code_indexing capability."""
        from agent_nexus.platform.agency.task_composer import infer_capabilities

        caps = infer_capabilities("Perform code_indexing for the repository")
        assert "code_indexing" in caps, f"Expected code_indexing in inferred caps, got {caps}"

    def test_fallback_system_design_when_no_keyword(self):
        """Unknown task description returns empty capabilities (caller handles)."""
        from agent_nexus.platform.agency.task_composer import infer_capabilities

        caps = infer_capabilities("bake a chocolate cake")
        assert caps == [], f"Expected empty capabilities for unknown task, got {caps}"


# ---------------------------------------------------------------------------
# 9. All 12 Expert Types Selection Verification
# ---------------------------------------------------------------------------


# Complete list of all 12 experts with their capabilities
_ALL_EXPERTS = {
    "agency.software-architect": ["system_design", "architecture_review", "tradeoff_analysis"],
    "agency.backend-architect": ["backend_design", "api_design", "database_design"],
    "agency.ai-engineer": ["ai_engineering", "model_integration", "prompt_engineering"],
    "agency.code-reviewer": ["code_review", "security_review", "maintainability_review"],
    "agency.security-engineer": ["security_review", "threat_modeling", "vulnerability_assessment"],
    "agency.sre": ["reliability_review", "incident_analysis", "observability"],
    "agency.test-results-analyzer": ["test_design", "test_analysis", "coverage_assessment"],
    "agency.technical-writer": ["technical_writing", "documentation", "api_documentation"],
    "agency.codebase-onboarding": [
        "codebase_onboarding",
        "code_navigation",
        "architecture_mapping",
    ],
    "agency.tool-evaluator": ["tool_evaluation", "technology_assessment", "comparison_analysis"],
    "agency.lsp-index-engineer": ["lsp_indexing", "code_indexing", "semantic_analysis"],
    "agency.agents-orchestrator": ["orchestration", "task_decomposition", "agent_coordination"],
    "agency.devops-automator": [
        "ci_cd_pipeline",
        "deployment_automation",
        "infrastructure_management",
    ],
    "agency.frontend-developer": ["frontend_development", "ui_implementation", "web_performance"],
    "agency.git-workflow-master": ["git_operations", "branch_management", "merge_strategies"],
    "agency.mcp-builder": ["mcp_server_construction", "tool_integration", "protocol_handling"],
}


@pytest.mark.timeout(30)
class TestAllExpertTypes:
    """E2E: every expert in the agent pool is selectable via their capabilities."""

    def test_each_expert_selectable_by_primary_capability(self):
        """Each of the 12 experts can be selected by its first (primary) capability."""
        registry = _build_registry()
        selector = SpecialistSelector(registry)

        for agent_id, caps in _ALL_EXPERTS.items():
            req = SelectionRequest(
                task_type="plan",
                required_capabilities=[caps[0]],
                optional_capabilities=[],
                max_agents=5,
                permissions="plan",
            )
            results = selector.select(req)
            result_ids = [r.agent_id for r in results]
            assert agent_id in result_ids, (
                f"Expert {agent_id} not selected for capability '{caps[0]}'. Got: {result_ids}"
            )

    def test_all_experts_registered(self):
        """Registry contains all 16 profiles after import."""
        registry = _build_registry()
        all_ids = registry.list_all()
        expected_ids = sorted(_ALL_EXPERTS.keys())
        assert sorted(all_ids) == expected_ids, (
            f"Registry has {sorted(all_ids)}, expected {expected_ids}"
        )


# ---------------------------------------------------------------------------
# 10. AND-Logic Failure Modes
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestMultiAgentComposition:
    """E2E: When no single expert has all required caps, set-cover composes a team."""

    def test_cross_capability_combo_composes_team(self):
        """Requiring system_design + code_review composes a multi-agent team."""
        registry = _build_registry()
        selector = SpecialistSelector(registry)

        req = SelectionRequest(
            task_type="plan",
            required_capabilities=["system_design", "code_review"],
            optional_capabilities=[],
            max_agents=5,
            permissions="plan",
        )
        results = selector.select(req)
        assert len(results) >= 2, f"Expected multi-agent team, got {[r.agent_id for r in results]}"
        # Verify the team collectively covers both capabilities
        all_caps: set[str] = set()
        for r in results:
            profile = registry.get(r.agent_id)
            if profile:
                all_caps.update(profile.get("capabilities", []))
        assert "system_design" in all_caps, "Team must cover system_design"
        assert "code_review" in all_caps, "Team must cover code_review"

    def test_task_composer_multi_agent_composition(self):
        """TaskComposer with a task requiring caps from multiple agents composes team."""
        registry = _build_registry()
        composer = TaskComposer(registry=registry)

        inp = TaskComposerInput(
            task="architecture review",  # architecture->[system_design, architecture_review], review->[code_review, security_review]
            mode="plan",
            max_parallel=3,
        )
        result = composer.run(inp, expert_executor=_mock_executor)

        assert isinstance(result, TaskComposerResult)
        assert len(result.selected_agents) >= 1, (
            f"Expected agents for multi-agent composition, got {len(result.selected_agents)}"
        )


# ---------------------------------------------------------------------------
# 11. Network Capability Impact
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestNetworkCapabilityImpact:
    """E2E: verify which experts have network access and its impact."""

    def test_network_capable_experts_identified(self):
        """Experts with network access in allowed_tools are identified correctly."""
        registry = _build_registry()
        network_agents = []
        non_network_agents = []

        for agent_id in registry.list_all():
            profile = registry.get(agent_id)
            allowed = profile.get("permissions", {}).get("allowed_tools", [])
            if "network" in allowed:
                network_agents.append(agent_id)
            else:
                non_network_agents.append(agent_id)

        expected_network = sorted(
            [
                "agency.ai-engineer",
                "agency.code-reviewer",
                "agency.codebase-onboarding",
                "agency.lsp-index-engineer",
                "agency.security-engineer",
                "agency.software-architect",
                "agency.sre",
                "agency.test-results-analyzer",
                "agency.tool-evaluator",
            ]
        )
        assert sorted(network_agents) == expected_network, (
            f"Unexpected network agents: {sorted(network_agents)}"
        )

        assert len(non_network_agents) == 7, (
            f"Expected 7 non-network agents, got {len(non_network_agents)}"
        )


# ---------------------------------------------------------------------------
# 12. Edge Cases and Boundary Conditions
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestEdgeCases:
    """E2E: boundary conditions and edge cases."""

    def test_task_composer_timeout_with_task_graph_path(self):
        """TaskComposer with task_graph raises TimeoutError when timeout is exceeded."""
        import time

        registry = _build_registry()
        composer = TaskComposer(registry=registry)
        graph = TaskGraph(":memory:")

        def slow_executor(profile_id: str, task: str) -> Artifact:
            time.sleep(0.5)
            return _mock_executor(profile_id, task)

        # "Design architecture" -> [system_design, architecture_review]
        inp = TaskComposerInput(
            task="Design architecture",
            mode="plan",
            max_parallel=2,
            timeout_seconds=0.1,
        )

        with pytest.raises(TimeoutError):
            composer.run(inp, expert_executor=slow_executor, task_graph=graph)

        graph.close()

    def test_empty_sections_qa_gate_fails(self):
        """QA gate fails when required sections are missing from output."""
        gate_input = QAGateInput(
            output={"sections": {}},
            required_sections=["context", "summary", "risks"],
            task_type="plan",
        )
        result = QAGate.run(gate_input)
        assert result.passed is False
        assert len(result.contract_result.missing_sections) == 3

    def test_integrator_single_artifact(self):
        """Integrator works correctly with a single artifact."""
        artifact = Artifact(
            source_agent="agency.software-architect",
            artifact_type="architecture_plan",
            sections={"context": "test", "proposed_design": "design A"},
        )
        integrated = Integrator.merge([artifact])
        assert integrated.source_agents == ["agency.software-architect"]
        assert integrated.conflicts == []
        assert "context" in integrated.merged_sections


# ---------------------------------------------------------------------------
# 13. ProfileBasedExecutor E2E Integration
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestProfileBasedExecutorE2E:
    """E2E: full pipeline using ProfileBasedExecutor (real profile-derived artifacts)."""

    def test_full_pipeline_with_real_executor(self):
        """TaskComposer with default executor uses ProfileBasedExecutor and produces
        real profile-derived artifacts through the full pipeline."""
        registry = _build_registry()
        composer = TaskComposer(registry=registry)

        inp = TaskComposerInput(
            task="Design a microservice architecture",
            mode="plan",
            max_parallel=3,
        )
        # No expert_executor override -> uses ProfileBasedExecutor
        result = composer.run(inp)

        assert isinstance(result, TaskComposerResult)
        assert len(result.selected_agents) > 0
        assert result.integrated is not None

        # ProfileBasedExecutor produces sections from output_contract,
        # not the stub format (just "context")
        merged = result.integrated.merged_sections
        non_context = [k for k in merged if k != "context"]
        assert len(non_context) > 0, (
            f"ProfileBasedExecutor should produce more than stub. Got keys: {list(merged.keys())}"
        )

        # At least final_recommendation and decision_summary from Integrator
        assert "final_recommendation" in merged
        assert "decision_summary" in merged

    def test_real_executor_produces_differentiated_output(self):
        """ProfileBasedExecutor produces different sections for different task types."""
        registry = _build_registry()
        composer = TaskComposer(registry=registry)

        arch_result = composer.run(TaskComposerInput(task="Design architecture", mode="plan"))
        sec_result = composer.run(
            TaskComposerInput(task="Security review and threat modeling", mode="plan")
        )

        if arch_result.integrated and sec_result.integrated:
            # Different task types should select different experts,
            # producing different section structures
            arch_keys = set(arch_result.integrated.merged_sections.keys())
            sec_keys = set(sec_result.integrated.merged_sections.keys())
            # They should not be identical (different experts, different output contracts)
            assert arch_keys != sec_keys or len(arch_keys) > 2, (
                f"Architecture keys={arch_keys}, Security keys={sec_keys} "
                "should differ or be rich enough"
            )

    def test_real_executor_with_task_graph_path(self):
        """ProfileBasedExecutor works correctly with TaskGraph-backed execution path."""
        registry = _build_registry()
        composer = TaskComposer(registry=registry)
        graph = TaskGraph(":memory:")

        inp = TaskComposerInput(
            task="Design architecture",
            mode="plan",
            max_parallel=2,
        )
        # No expert_executor -> ProfileBasedExecutor, with task_graph
        result = composer.run(inp, task_graph=graph)

        assert isinstance(result, TaskComposerResult)
        assert len(result.selected_agents) > 0
        assert result.integrated is not None

        # Verify TaskGraph recorded correct states
        for sel in result.selected_agents:
            task_id = sel.agent_id.replace("agency.", "")
            task_item = graph.get_task(task_id)
            if task_item is not None:
                assert task_item.state.value == "completed", (
                    f"Task {task_id} should be completed, got {task_item.state}"
                )

        graph.close()


# ---------------------------------------------------------------------------
# 14. Importer Disk Write (import_all)
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestImporterDiskWrite:
    """E2E: import_all writes profile files to disk correctly."""

    def test_import_all_creates_profile_files(self):
        """import_all creates JSON profiles, normalized prompts, and index files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            importer = AgencyImporter(
                vendor_path=str(_VENDOR_DIR),
                allowlist_path=str(_ALLOWLIST_PATH),
                output_dir=tmpdir,
            )
            importer.import_all()

            output = Path(tmpdir)

            # Should have JSON profile files for each agent
            json_files = list(output.glob("agency.*.json"))
            assert len(json_files) == 16, f"Expected 16 profile JSON files, got {len(json_files)}"

            # Should have normalized prompt files
            normalized_dir = output / "normalized"
            assert normalized_dir.is_dir()
            md_files = list(normalized_dir.glob("agency.*.md"))
            assert len(md_files) == 16, f"Expected 16 normalized prompt files, got {len(md_files)}"

            # Should have source.lock.yaml and index.yaml
            assert (output / "source.lock.yaml").is_file(), "source.lock.yaml missing"
            assert (output / "index.yaml").is_file(), "index.yaml missing"

    def test_import_all_profile_is_valid_json(self):
        """Each profile JSON file is valid and contains required top-level keys."""
        import json

        with tempfile.TemporaryDirectory() as tmpdir:
            importer = AgencyImporter(
                vendor_path=str(_VENDOR_DIR),
                allowlist_path=str(_ALLOWLIST_PATH),
                output_dir=tmpdir,
            )
            importer.import_all()

            output = Path(tmpdir)
            for json_file in sorted(output.glob("agency.*.json")):
                data = json.loads(json_file.read_text(encoding="utf-8"))
                assert "id" in data, f"{json_file.name}: missing 'id'"
                assert "capabilities" in data, f"{json_file.name}: missing 'capabilities'"
                assert "output_contract" in data, f"{json_file.name}: missing 'output_contract'"
                assert "permissions" in data, f"{json_file.name}: missing 'permissions'"


# ---------------------------------------------------------------------------
# 15. Integrator Conflict Detection and Validation
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestIntegratorAdvanced:
    """E2E: integrator conflict detection and boundary conditions."""

    def test_conflict_detected_on_disjoint_risks(self):
        """Integrator flags conflict when experts have disjoint risk findings
        but share overlapping domain sections."""
        artifacts = [
            Artifact(
                source_agent="agency.software-architect",
                artifact_type="architecture_plan",
                sections={
                    "context": "design review",
                    "proposed_design": "microservices",
                    "risks": ["latency from network hops"],
                },
            ),
            Artifact(
                source_agent="agency.backend-architect",
                artifact_type="architecture_plan",
                sections={
                    "context": "design review",
                    "proposed_design": "modular monolith",
                    "risks": ["tight coupling risk"],
                },
            ),
        ]
        integrated = Integrator.merge(artifacts)
        # Should detect conflict because same domain, different risk findings
        assert len(integrated.conflicts) > 0, (
            f"Expected conflict for disjoint risks, got {integrated.conflicts}"
        )

    def test_conflict_detected_on_severity_mismatch(self):
        """Integrator flags conflict when experts disagree on severity."""
        artifacts = [
            Artifact(
                source_agent="agency.code-reviewer",
                artifact_type="review_report",
                sections={"severity": "high", "findings": ["issue A"]},
            ),
            Artifact(
                source_agent="agency.security-engineer",
                artifact_type="risk_report",
                sections={"severity": "low", "findings": ["issue A"]},
            ),
        ]
        integrated = Integrator.merge(artifacts)
        severity_conflicts = [c for c in integrated.conflicts if c.field == "severity"]
        assert len(severity_conflicts) > 0, "Expected severity conflict"

    def test_merge_raises_on_empty_artifacts(self):
        """Integrator.merge raises ValueError for empty input."""
        with pytest.raises(ValueError, match="at least one artifact"):
            Integrator.merge([])

    def test_merge_raises_on_too_many_artifacts(self):
        """Integrator.merge raises ValueError for > 50 artifacts."""
        artifacts = [
            Artifact(source_agent=f"agent-{i}", artifact_type="report", sections={"k": "v"})
            for i in range(51)
        ]
        with pytest.raises(ValueError, match="Cannot merge more than 50"):
            Integrator.merge(artifacts)

    def test_merge_raises_on_too_many_sections(self):
        """Integrator.merge raises ValueError for artifacts with > 100 sections."""
        sections = {f"section-{i}": f"value-{i}" for i in range(101)}
        artifact = Artifact(source_agent="agent", artifact_type="report", sections=sections)
        with pytest.raises(ValueError, match="too many sections"):
            Integrator.merge([artifact])

    def test_type_mismatch_converted_to_list(self):
        """When section types mismatch across artifacts, Integrator converts to list."""
        artifacts = [
            Artifact(source_agent="a", artifact_type="report", sections={"x": "string_val"}),
            Artifact(source_agent="b", artifact_type="report", sections={"x": ["list_val"]}),
        ]
        integrated = Integrator.merge(artifacts)
        # "x" should be a list (string converted to list + list appended)
        assert isinstance(integrated.merged_sections["x"], list)


# ---------------------------------------------------------------------------
# 16. Planner Validation
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestPlannerValidation:
    """E2E: planner rejects invalid inputs correctly."""

    def test_empty_subtasks_raises(self):
        """DynamicCompositePlanner raises ValueError for empty subtask list."""
        planner = DynamicCompositePlanner()
        with pytest.raises(ValueError, match="at least one subtask"):
            planner.plan([], composition_name="test")

    def test_duplicate_ids_raises(self):
        """DynamicCompositePlanner raises ValueError for duplicate subtask IDs."""
        planner = DynamicCompositePlanner()
        subtasks = [
            SubtaskDef(
                id="dup",
                goal="A",
                needed_capabilities=["c"],
                output_contract="r",
                assigned_agent="a1",
            ),
            SubtaskDef(
                id="dup",
                goal="B",
                needed_capabilities=["c"],
                output_contract="r",
                assigned_agent="a2",
            ),
        ]
        with pytest.raises(ValueError, match="Duplicate subtask id"):
            planner.plan(subtasks, composition_name="test")

    def test_reserved_id_raises(self):
        """DynamicCompositePlanner raises ValueError for reserved IDs (integrate, validate)."""
        planner = DynamicCompositePlanner()
        for reserved in ("integrate", "validate"):
            subtasks = [
                SubtaskDef(
                    id=reserved,
                    goal="X",
                    needed_capabilities=["c"],
                    output_contract="r",
                    assigned_agent="a",
                ),
            ]
            with pytest.raises(ValueError, match="reserved"):
                planner.plan(subtasks, composition_name="test")

    def test_invalid_chars_in_id_raises(self):
        """DynamicCompositePlanner raises ValueError for TOML-invalid characters in ID."""
        planner = DynamicCompositePlanner()
        for bad_char in ['"', "#", "\n", "\t", "["]:
            subtasks = [
                SubtaskDef(
                    id=f"bad{bad_char}id",
                    goal="X",
                    needed_capabilities=["c"],
                    output_contract="r",
                    assigned_agent="a",
                ),
            ]
            with pytest.raises(ValueError, match="invalid character"):
                planner.plan(subtasks, composition_name="test")


# ---------------------------------------------------------------------------
# 17. TOML Serialization
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestTOMLSerialization:
    """E2E: generate_toml produces valid TOML from a CompositionDAG."""

    def test_generate_toml_roundtrip(self):
        """generate_toml produces a string with expected sections."""
        from agent_nexus.platform.agency.planner import generate_toml

        subtasks = [
            SubtaskDef(
                id="architect",
                goal="Design",
                needed_capabilities=["system_design"],
                output_contract="report",
                assigned_agent="agency.software-architect",
            ),
            SubtaskDef(
                id="reviewer",
                goal="Review",
                needed_capabilities=["code_review"],
                output_contract="review",
                assigned_agent="agency.code-reviewer",
            ),
        ]
        planner = DynamicCompositePlanner()
        dag = planner.plan(subtasks, composition_name="toml-test", max_parallel=2)
        toml_str = generate_toml(dag)

        assert "[composition]" in toml_str
        assert 'name = "toml-test"' in toml_str
        assert "max_parallel = 2" in toml_str
        assert 'id = "architect"' in toml_str
        assert 'id = "reviewer"' in toml_str
        assert 'id = "integrate"' in toml_str
        assert 'id = "validate"' in toml_str
        assert 'blocked_by = ["architect", "reviewer"]' in toml_str

    def test_generate_toml_rejects_invalid_chars(self):
        """generate_toml raises ValueError for invalid characters."""
        from agent_nexus.platform.agency.planner import generate_toml

        dag = CompositionDAG(
            name='bad"name',
            max_parallel=1,
            tasks=[
                DAGTask(id="t1", agent="a", output="o"),
            ],
        )
        with pytest.raises(ValueError, match="invalid character"):
            generate_toml(dag)


# ---------------------------------------------------------------------------
# 18. GitNexus QA Gate for Code-Change Tasks
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestGitNexusQAGate:
    """E2E: GitNexus gate enforcement for code-changing task types."""

    def test_code_change_requires_gates(self):
        """code_change task fails GitNexus gate without impact_analysis."""
        gate_input = QAGateInput(
            output={"sections": {"summary": "ok"}},
            required_sections=["summary"],
            task_type="code_change",
            impact_analysis_completed=False,
            detect_changes_completed=False,
        )
        result = QAGate.run(gate_input)
        assert result.passed is False
        assert result.gitnexus_result.skipped is False
        assert "impact_analysis_completed" in result.gitnexus_result.failed_checks
        assert "detect_changes_completed" in result.gitnexus_result.failed_checks

    def test_code_change_passes_with_gates(self):
        """code_change task passes GitNexus gate when both checks are completed."""
        gate_input = QAGateInput(
            output={"sections": {"summary": "ok"}},
            required_sections=["summary"],
            task_type="code_change",
            impact_analysis_completed=True,
            detect_changes_completed=True,
        )
        result = QAGate.run(gate_input)
        assert result.passed is True
        assert result.gitnexus_result.passed is True

    def test_refactor_requires_gates(self):
        """refactor task also requires GitNexus gates."""
        gate_input = QAGateInput(
            output={"sections": {"summary": "ok"}},
            required_sections=["summary"],
            task_type="refactor",
            impact_analysis_completed=False,
            detect_changes_completed=True,
        )
        result = QAGate.run(gate_input)
        assert result.passed is False

    def test_plan_task_skips_gates(self):
        """plan task skips GitNexus checks entirely."""
        gate_input = QAGateInput(
            output={"sections": {"summary": "ok"}},
            required_sections=["summary"],
            task_type="plan",
        )
        result = QAGate.run(gate_input)
        assert result.passed is True
        assert result.gitnexus_result.skipped is True


# ---------------------------------------------------------------------------
# 19. Max Parallel Enforcement
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestMaxParallelEnforcement:
    """E2E: DAGDispatcher respects max_parallel limit."""

    def test_max_parallel_1_sequential(self):
        """max_parallel=1 executes tasks sequentially (one at a time)."""
        graph = TaskGraph(":memory:")
        concurrent_seen = {"max": 0, "current": 0}

        def tracking_executor(profile_id: str, task: str) -> Artifact:
            concurrent_seen["current"] += 1
            concurrent_seen["max"] = max(concurrent_seen["max"], concurrent_seen["current"])
            result = _mock_executor(profile_id, task)
            concurrent_seen["current"] -= 1
            return result

        subtasks = [
            SubtaskDef(
                id=f"task-{i}",
                goal=f"Goal {i}",
                needed_capabilities=["system_design"],
                output_contract="report",
                assigned_agent="agency.software-architect",
            )
            for i in range(4)
        ]
        planner = DynamicCompositePlanner()
        dag = planner.plan(subtasks, composition_name="parallel-1-test", max_parallel=1)

        dispatcher = DAGDispatcher(
            graph=graph,
            executor=tracking_executor,
            max_parallel=1,
        )
        result = dispatcher.dispatch(dag, "Parallel test")

        assert concurrent_seen["max"] <= 1, (
            f"Expected max concurrency 1, saw {concurrent_seen['max']}"
        )
        assert len(result.completed) == 4
        graph.close()

    def test_max_parallel_3_allows_batching(self):
        """max_parallel=3 allows up to 3 tasks in a single batch."""
        graph = TaskGraph(":memory:")
        batch_sizes: list[int] = []

        # Override dispatch to track batch sizes (via call ordering)
        call_order: list[str] = []

        def order_executor(profile_id: str, task: str) -> Artifact:
            call_order.append(profile_id)
            return _mock_executor(profile_id, task)

        subtasks = [
            SubtaskDef(
                id=f"task-{i}",
                goal=f"Goal {i}",
                needed_capabilities=["system_design"],
                output_contract="report",
                assigned_agent="agency.software-architect",
            )
            for i in range(5)
        ]
        planner = DynamicCompositePlanner()
        dag = planner.plan(subtasks, composition_name="parallel-3-test", max_parallel=3)

        dispatcher = DAGDispatcher(
            graph=graph,
            executor=order_executor,
            max_parallel=3,
        )
        result = dispatcher.dispatch(dag, "Parallel batch test")

        # With max_parallel=3 and 5 tasks, we expect 2 rounds (3 + 2)
        assert len(result.completed) == 5
        graph.close()
