"""Tests for DynamicCompositePlanner.resolve_dependencies() — dynamic DAG generation."""

import pytest

from agent_nexus.platform.agency.planner import (
    CompositionDAG,
    DynamicCompositePlanner,
    SubtaskDef,
)


@pytest.fixture
def planner() -> DynamicCompositePlanner:
    return DynamicCompositePlanner()


def _subtask(
    id: str,
    caps: list[str],
    agent: str = "agency.test",
    contract: str = "report",
) -> SubtaskDef:
    return SubtaskDef(
        id=id,
        goal=f"subtask {id}",
        needed_capabilities=caps,
        output_contract=contract,
        assigned_agent=agent,
    )


@pytest.mark.timeout(30)
class TestResolveDependencies:
    """resolve_dependencies analyzes capability overlap to build non-trivial DAGs."""

    def test_independent_tasks_no_deps(self, planner: DynamicCompositePlanner) -> None:
        """Tasks with disjoint capabilities should have no inter-task blocked_by."""
        subtasks = [
            _subtask("arch", ["system_design"]),
            _subtask("review", ["code_review"]),
        ]
        dag = planner.resolve_dependencies(subtasks, composition_name="independent")
        specialist = dag.specialist_tasks
        for t in specialist:
            assert t.blocked_by == [], f"{t.id} should have no blocked_by"

    def test_overlapping_caps_subset_creates_dependency(
        self, planner: DynamicCompositePlanner
    ) -> None:
        """If task B's caps are a subset of task A's, B should be blocked by A.

        Mere overlap (neither is a subset) does NOT create a dependency.
        """
        subtasks = [
            _subtask("arch", ["system_design", "architecture_review", "security_review"]),
            _subtask("review", ["architecture_review"]),
        ]
        dag = planner.resolve_dependencies(subtasks, composition_name="dep-chain")
        review_task = next(t for t in dag.specialist_tasks if t.id == "review")
        assert "arch" in review_task.blocked_by

    def test_overlapping_but_different_no_dependency(
        self, planner: DynamicCompositePlanner
    ) -> None:
        """Tasks with overlapping but non-subset caps run in parallel."""
        subtasks = [
            _subtask("arch", ["system_design", "architecture_review"]),
            _subtask("review", ["architecture_review", "code_review"]),
        ]
        dag = planner.resolve_dependencies(subtasks, composition_name="overlap")
        review_task = next(t for t in dag.specialist_tasks if t.id == "review")
        assert review_task.blocked_by == []

    def test_all_specialists_block_integrate(
        self, planner: DynamicCompositePlanner
    ) -> None:
        """Integrate must be blocked by all specialist tasks regardless of deps."""
        subtasks = [
            _subtask("a", ["system_design"]),
            _subtask("b", ["code_review"]),
        ]
        dag = planner.resolve_dependencies(subtasks, composition_name="test")
        integrate = next(t for t in dag.tasks if t.id == "integrate")
        assert set(integrate.blocked_by) == {"a", "b"}

    def test_validate_blocked_by_integrate(
        self, planner: DynamicCompositePlanner
    ) -> None:
        subtasks = [_subtask("x", ["system_design"])]
        dag = planner.resolve_dependencies(subtasks, composition_name="test")
        validate = next(t for t in dag.tasks if t.id == "validate")
        assert validate.blocked_by == ["integrate"]

    def test_chain_dependency(self, planner: DynamicCompositePlanner) -> None:
        """Subset chain: A has superset caps, B is subset of A, C depends on B.

        With subset rule (<=):
        - design: {system_design, tradeoff_analysis} (superset)
        - plan: {system_design} (subset of design → blocked by design)
        - risk: {tradeoff_analysis, security_review} (overlaps design but not subset)
        """
        subtasks = [
            _subtask("design", ["system_design", "tradeoff_analysis"]),
            _subtask("plan", ["system_design"]),
            _subtask("risk", ["tradeoff_analysis", "security_review"]),
        ]
        dag = planner.resolve_dependencies(subtasks, composition_name="chain")
        plan_task = next(t for t in dag.specialist_tasks if t.id == "plan")
        risk_task = next(t for t in dag.specialist_tasks if t.id == "risk")

        # plan is a strict subset of design → blocked by design
        assert "design" in plan_task.blocked_by
        # risk overlaps design (tradeoff_analysis) but is not a subset → parallel
        assert risk_task.blocked_by == []

    def test_empty_subtasks_raises(self, planner: DynamicCompositePlanner) -> None:
        with pytest.raises(ValueError, match="at least one"):
            planner.resolve_dependencies([], composition_name="test")

    def test_returns_composition_dag(self, planner: DynamicCompositePlanner) -> None:
        subtasks = [_subtask("a", ["system_design"])]
        dag = planner.resolve_dependencies(subtasks, composition_name="my-comp")
        assert isinstance(dag, CompositionDAG)
        assert dag.name == "my-comp"

    def test_max_parallel_default(self, planner: DynamicCompositePlanner) -> None:
        subtasks = [_subtask("a", ["system_design"])]
        dag = planner.resolve_dependencies(subtasks, composition_name="test")
        assert dag.max_parallel == 3

    def test_max_parallel_custom(self, planner: DynamicCompositePlanner) -> None:
        subtasks = [_subtask("a", ["system_design"])]
        dag = planner.resolve_dependencies(
            subtasks, composition_name="test", max_parallel=5
        )
        assert dag.max_parallel == 5

    def test_no_self_dependency(self, planner: DynamicCompositePlanner) -> None:
        """A task should never depend on itself."""
        subtasks = [
            _subtask("a", ["system_design"]),
            _subtask("b", ["system_design"]),
        ]
        dag = planner.resolve_dependencies(subtasks, composition_name="test")
        for t in dag.specialist_tasks:
            assert t.id not in t.blocked_by

    def test_duplicate_ids_raises(self, planner: DynamicCompositePlanner) -> None:
        subtasks = [
            _subtask("dup", ["system_design"]),
            _subtask("dup", ["code_review"]),
        ]
        with pytest.raises(ValueError, match="[Dd]uplicate"):
            planner.resolve_dependencies(subtasks, composition_name="test")
