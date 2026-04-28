"""Phase E tests: DynamicCompositePlanner — DAG generation, blocked_by, max_parallel, integrator."""

import pytest

from agent_nexus.platform.agency.planner import (
    DAGTask,
    DynamicCompositePlanner,
    PlannerInput,
    SubtaskDef,
    generate_toml,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_subtasks() -> list[SubtaskDef]:
    """Three specialist subtasks mimicking the doc §7.3 example."""
    return [
        SubtaskDef(
            id="architecture",
            goal="design integration architecture",
            needed_capabilities=["system_design", "agent_orchestration"],
            output_contract="architecture_plan",
            assigned_agent="agency.software-architect",
        ),
        SubtaskDef(
            id="runtime_risk",
            goal="identify runtime and security risks",
            needed_capabilities=["security_review", "reliability_review"],
            output_contract="risk_report",
            assigned_agent="agency.security-engineer",
        ),
        SubtaskDef(
            id="implementation_plan",
            goal="define phased implementation roadmap",
            needed_capabilities=["technical_planning", "tool_evaluation"],
            output_contract="implementation_plan",
            assigned_agent="agency.tool-evaluator",
        ),
    ]


# ---------------------------------------------------------------------------
# 1. DAG generation with blocked_by
# ---------------------------------------------------------------------------

@pytest.mark.timeout(30)
class TestDAGGeneration:
    """Given subtasks, generate TOML DAG with correct blocked_by relations."""

    def test_basic_dag_has_specialist_tasks(self, sample_subtasks: list[SubtaskDef]) -> None:
        planner = DynamicCompositePlanner()
        dag = planner.plan(sample_subtasks, composition_name="test-composition")

        # Should have 3 specialist tasks + integrate + validate = 5
        task_ids = [t.id for t in dag.tasks]
        assert "architecture" in task_ids
        assert "runtime_risk" in task_ids
        assert "implementation_plan" in task_ids

    def test_integrate_blocked_by_all_specialists(self, sample_subtasks: list[SubtaskDef]) -> None:
        planner = DynamicCompositePlanner()
        dag = planner.plan(sample_subtasks, composition_name="test-composition")

        integrate = next(t for t in dag.tasks if t.id == "integrate")
        # Integrator must be blocked by ALL specialist tasks
        assert set(integrate.blocked_by) == {"architecture", "runtime_risk", "implementation_plan"}

    def test_validate_blocked_by_integrate(self, sample_subtasks: list[SubtaskDef]) -> None:
        planner = DynamicCompositePlanner()
        dag = planner.plan(sample_subtasks, composition_name="test-composition")

        validate = next(t for t in dag.tasks if t.id == "validate")
        assert validate.blocked_by == ["integrate"]

    def test_specialist_tasks_have_no_blocked_by(self, sample_subtasks: list[SubtaskDef]) -> None:
        planner = DynamicCompositePlanner()
        dag = planner.plan(sample_subtasks, composition_name="test-composition")

        specialist_ids = {"architecture", "runtime_risk", "implementation_plan"}
        for task in dag.tasks:
            if task.id in specialist_ids:
                assert task.blocked_by == [], f"{task.id} should have no blocked_by"

    def test_task_agents_preserved(self, sample_subtasks: list[SubtaskDef]) -> None:
        planner = DynamicCompositePlanner()
        dag = planner.plan(sample_subtasks, composition_name="test-composition")

        arch = next(t for t in dag.tasks if t.id == "architecture")
        assert arch.agent == "agency.software-architect"

    def test_task_outputs_preserved(self, sample_subtasks: list[SubtaskDef]) -> None:
        planner = DynamicCompositePlanner()
        dag = planner.plan(sample_subtasks, composition_name="test-composition")

        risk = next(t for t in dag.tasks if t.id == "runtime_risk")
        assert risk.output == "risk_report"

    def test_integrate_agent_is_nexus_integrator(self, sample_subtasks: list[SubtaskDef]) -> None:
        planner = DynamicCompositePlanner()
        dag = planner.plan(sample_subtasks, composition_name="test-composition")

        integrate = next(t for t in dag.tasks if t.id == "integrate")
        assert integrate.agent == "nexus.integrator"

    def test_validate_agent_is_nexus_qa_gate(self, sample_subtasks: list[SubtaskDef]) -> None:
        planner = DynamicCompositePlanner()
        dag = planner.plan(sample_subtasks, composition_name="test-composition")

        validate = next(t for t in dag.tasks if t.id == "validate")
        assert validate.agent == "nexus.qa-gate"


# ---------------------------------------------------------------------------
# 2. max_parallel enforcement
# ---------------------------------------------------------------------------

@pytest.mark.timeout(30)
class TestMaxParallel:
    """Verify max_parallel is respected in the generated DAG."""

    def test_default_max_parallel(self, sample_subtasks: list[SubtaskDef]) -> None:
        planner = DynamicCompositePlanner()
        dag = planner.plan(sample_subtasks, composition_name="test-composition")

        assert dag.max_parallel == 3

    def test_custom_max_parallel(self, sample_subtasks: list[SubtaskDef]) -> None:
        planner = DynamicCompositePlanner()
        dag = planner.plan(
            sample_subtasks,
            composition_name="test-composition",
            max_parallel=5,
        )

        assert dag.max_parallel == 5

    def test_max_parallel_high_value_allowed(self, sample_subtasks: list[SubtaskDef]) -> None:
        """max_parallel higher than specialist count is allowed — router handles actual concurrency."""
        planner = DynamicCompositePlanner()
        dag = planner.plan(
            sample_subtasks,
            composition_name="test-composition",
            max_parallel=100,
        )

        assert dag.max_parallel == 100

    def test_max_parallel_at_least_1(self, sample_subtasks: list[SubtaskDef]) -> None:
        planner = DynamicCompositePlanner()
        dag = planner.plan(
            sample_subtasks,
            composition_name="test-composition",
            max_parallel=0,
        )

        assert dag.max_parallel >= 1


# ---------------------------------------------------------------------------
# 3. TOML generation
# ---------------------------------------------------------------------------

@pytest.mark.timeout(30)
class TestTOMLGeneration:
    """Verify generate_toml produces valid TOML from a DAG."""

    def test_toml_has_composition_header(self, sample_subtasks: list[SubtaskDef]) -> None:
        planner = DynamicCompositePlanner()
        dag = planner.plan(sample_subtasks, composition_name="my-composition")
        toml_str = generate_toml(dag)

        assert '[composition]' in toml_str
        assert 'name = "my-composition"' in toml_str

    def test_toml_has_all_tasks(self, sample_subtasks: list[SubtaskDef]) -> None:
        planner = DynamicCompositePlanner()
        dag = planner.plan(sample_subtasks, composition_name="test")
        toml_str = generate_toml(dag)

        # Every task ID should appear in TOML
        for task in dag.tasks:
            assert f'id = "{task.id}"' in toml_str

    def test_toml_blocked_by_array(self, sample_subtasks: list[SubtaskDef]) -> None:
        planner = DynamicCompositePlanner()
        dag = planner.plan(sample_subtasks, composition_name="test")
        toml_str = generate_toml(dag)

        # integrate should have blocked_by with all specialist IDs
        assert 'blocked_by = ["architecture", "runtime_risk", "implementation_plan"]' in toml_str

    def test_toml_is_parseable(self, sample_subtasks: list[SubtaskDef]) -> None:
        """Generated TOML can be parsed back into a dict."""
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]

        planner = DynamicCompositePlanner()
        dag = planner.plan(sample_subtasks, composition_name="test")
        toml_str = generate_toml(dag)

        parsed = tomllib.loads(toml_str)
        assert "composition" in parsed
        assert "tasks" in parsed
        assert len(parsed["tasks"]) == 5  # 3 specialists + integrate + validate


# ---------------------------------------------------------------------------
# 4. Edge cases
# ---------------------------------------------------------------------------

@pytest.mark.timeout(30)
class TestEdgeCases:
    """Edge cases: empty input, single subtask, duplicate agents."""

    def test_empty_subtasks_raises(self) -> None:
        planner = DynamicCompositePlanner()
        with pytest.raises(ValueError, match="at least one"):
            planner.plan([], composition_name="test")

    def test_single_subtask(self) -> None:
        subtasks = [
            SubtaskDef(
                id="only_task",
                goal="do the thing",
                needed_capabilities=["system_design"],
                output_contract="architecture_plan",
                assigned_agent="agency.software-architect",
            ),
        ]
        planner = DynamicCompositePlanner()
        dag = planner.plan(subtasks, composition_name="single")

        integrate = next(t for t in dag.tasks if t.id == "integrate")
        assert integrate.blocked_by == ["only_task"]

    def test_duplicate_subtask_ids_raises(self) -> None:
        subtasks = [
            SubtaskDef(
                id="dup",
                goal="first",
                needed_capabilities=["system_design"],
                output_contract="architecture_plan",
                assigned_agent="agency.software-architect",
            ),
            SubtaskDef(
                id="dup",
                goal="second",
                needed_capabilities=["code_review"],
                output_contract="review_report",
                assigned_agent="agency.code-reviewer",
            ),
        ]
        planner = DynamicCompositePlanner()
        with pytest.raises(ValueError, match="[Dd]uplicate"):
            planner.plan(subtasks, composition_name="test")

    def test_composition_name_special_chars_raises(self) -> None:
        """composition_name with TOML-special chars should be rejected."""
        subtasks = [
            SubtaskDef(
                id="ok_task",
                goal="test",
                needed_capabilities=["system_design"],
                output_contract="architecture_plan",
                assigned_agent="agency.software-architect",
            ),
        ]
        planner = DynamicCompositePlanner()
        with pytest.raises(ValueError, match="invalid character"):
            planner.plan(subtasks, composition_name='bad"name')

    def test_toml_special_chars_in_agent_output_raises(
        self, sample_subtasks: list[SubtaskDef]
    ) -> None:
        """generate_toml rejects task agent/output with TOML-special chars."""
        planner = DynamicCompositePlanner()
        dag = planner.plan(sample_subtasks, composition_name="test")

        # Tamper with a task to inject special chars
        dag.tasks[0] = DAGTask(
            id="task1",
            agent='bad"agent',
            output="valid_output",
        )
        with pytest.raises(ValueError, match="invalid character"):
            generate_toml(dag)


# ---------------------------------------------------------------------------
# 5. PlannerInput dataclass
# ---------------------------------------------------------------------------

@pytest.mark.timeout(30)
class TestPlannerInput:
    """PlannerInput convenience wrapper validates its inputs."""

    def test_planner_input_to_dag(self, sample_subtasks: list[SubtaskDef]) -> None:
        inp = PlannerInput(
            subtasks=sample_subtasks,
            composition_name="integration-design",
            max_parallel=3,
        )
        planner = DynamicCompositePlanner()
        dag = planner.plan(inp.subtasks, inp.composition_name, inp.max_parallel)

        assert dag.name == "integration-design"
        assert dag.max_parallel == 3


# ---------------------------------------------------------------------------
# C2 fix: Smart dependency resolution (strict-subset rule)
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestSmartDependencyResolution:
    """resolve_dependencies uses strict-subset rule for blocked_by edges."""

    def test_overlapping_but_different_capabilities_no_block(self) -> None:
        """Tasks with overlapping but different capabilities run in parallel.

        task_a has {code_review, security_review} and task_b has
        {security_review, reliability_review}. Neither is a subset of the
        other, so no blocked_by edges are created.
        """
        subtasks = [
            SubtaskDef(
                id="task_a",
                goal="review code",
                needed_capabilities=["code_review", "security_review"],
                output_contract="report_a",
                assigned_agent="agency.agent-a",
            ),
            SubtaskDef(
                id="task_b",
                goal="security analysis",
                needed_capabilities=["security_review", "reliability_review"],
                output_contract="report_b",
                assigned_agent="agency.agent-b",
            ),
        ]
        planner = DynamicCompositePlanner()
        dag = planner.resolve_dependencies(subtasks, composition_name="overlap-test")

        a_task = next(t for t in dag.tasks if t.id == "task_a")
        b_task = next(t for t in dag.tasks if t.id == "task_b")
        # Neither is a subset of the other → no blocked_by
        assert a_task.blocked_by == []
        assert b_task.blocked_by == []

    def test_strict_subset_creates_dependency(self) -> None:
        """A task whose capabilities are a strict subset of another IS blocked."""
        subtasks = [
            SubtaskDef(
                id="broad_task",
                goal="full review",
                needed_capabilities=["code_review", "security_review", "reliability_review"],
                output_contract="broad_report",
                assigned_agent="agency.agent-a",
            ),
            SubtaskDef(
                id="narrow_task",
                goal="security only",
                needed_capabilities=["security_review"],
                output_contract="narrow_report",
                assigned_agent="agency.agent-b",
            ),
        ]
        planner = DynamicCompositePlanner()
        dag = planner.resolve_dependencies(subtasks, composition_name="subset-test")

        narrow = next(t for t in dag.tasks if t.id == "narrow_task")
        # narrow_task's caps {security_review} is a strict subset of
        # broad_task's caps → narrow_task blocked_by broad_task
        assert "broad_task" in narrow.blocked_by

    def test_identical_capability_sets_run_in_parallel(self) -> None:
        """Identical capability sets: tasks should run in parallel (no dependency).

        resolve_dependencies uses < (strict subset), so identical
        capability sets do NOT create a dependency edge — both tasks
        execute in parallel since neither strictly subsumes the other.
        """
        subtasks = [
            SubtaskDef(
                id="first",
                goal="review code",
                needed_capabilities=["code_review", "security_review"],
                output_contract="report_1",
                assigned_agent="agency.agent-a",
            ),
            SubtaskDef(
                id="second",
                goal="another review",
                needed_capabilities=["code_review", "security_review"],
                output_contract="report_2",
                assigned_agent="agency.agent-b",
            ),
        ]
        planner = DynamicCompositePlanner()
        dag = planner.resolve_dependencies(subtasks, composition_name="identical-test")

        first = next(t for t in dag.tasks if t.id == "first")
        second = next(t for t in dag.tasks if t.id == "second")
        # Neither task is blocked — identical caps run in parallel
        assert first.blocked_by == []
        assert second.blocked_by == []

    def test_three_tasks_partial_subset_no_overlap(self) -> None:
        """A⊂B and C has no overlap with either: only A depends on B, C is free."""
        subtasks = [
            SubtaskDef(
                id="broad",
                goal="broad task",
                needed_capabilities=["system_design", "architecture_review", "tool_evaluation"],
                output_contract="broad_out",
                assigned_agent="agency.agent-a",
            ),
            SubtaskDef(
                id="narrow",
                goal="narrow task",
                needed_capabilities=["system_design"],
                output_contract="narrow_out",
                assigned_agent="agency.agent-b",
            ),
            SubtaskDef(
                id="unrelated",
                goal="unrelated task",
                needed_capabilities=["test_design", "test_analysis"],
                output_contract="test_out",
                assigned_agent="agency.agent-c",
            ),
        ]
        planner = DynamicCompositePlanner()
        dag = planner.resolve_dependencies(subtasks, composition_name="partial-test")

        broad = next(t for t in dag.tasks if t.id == "broad")
        narrow = next(t for t in dag.tasks if t.id == "narrow")
        unrelated = next(t for t in dag.tasks if t.id == "unrelated")

        # narrow is a strict subset of broad → blocked
        assert "broad" in narrow.blocked_by
        # broad is NOT a subset of narrow
        assert broad.blocked_by == []
        # unrelated has no overlap at all → not blocked
        assert unrelated.blocked_by == []
