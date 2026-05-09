"""End-to-end dynamic verification: Composite Agent orchestration chain.

Validates the full DSL -> TaskGraph -> execution lifecycle using REAL
composition.toml files from the official composite agents. No mocks.

Chain: composition.toml -> OrchestrationDSL.parse() -> OrchestrationDefinition
       -> DSLTask.to_task_item() -> TaskGraph.add_tasks()
       -> get_ready_tasks / start_task / complete_task / get_parallel_groups
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_nexus.models.task import TaskState
from agent_nexus.platform.orchestration.dsl import (
    DSLValidationError,
    OrchestrationDSL,
)
from agent_nexus.platform.orchestration.task_graph import TaskGraph

# ---------------------------------------------------------------------------
# Paths to real composition.toml files
# ---------------------------------------------------------------------------

_COMPOSITE_ROOT = Path(__file__).parent.parent.parent / "agents" / "composite"

_FEATURE_PIPELINE = _COMPOSITE_ROOT / "feature-delivery-pipeline" / "composition.toml"
_CICD_GATE = _COMPOSITE_ROOT / "cicd-quality-gate" / "composition.toml"
_COMPETITIVE = _COMPOSITE_ROOT / "competitive-intelligence-briefing" / "composition.toml"
_PRODUCT_DOCS = _COMPOSITE_ROOT / "product-documentation-suite" / "composition.toml"
_COMPLIANCE = _COMPOSITE_ROOT / "document-compliance-gateway" / "composition.toml"


# ---------------------------------------------------------------------------
# 1. DSL parsing: real composition.toml files
# ---------------------------------------------------------------------------


class TestRealCompositionParsing:
    """Parse all 5 real composition.toml files successfully."""

    @pytest.mark.parametrize(
        "path,expected_name",
        [
            (_FEATURE_PIPELINE, "feature-delivery-pipeline"),
            (_CICD_GATE, "cicd-quality-gate"),
            (_COMPETITIVE, "competitive-intelligence-briefing"),
            (_PRODUCT_DOCS, "product-documentation-suite"),
            (_COMPLIANCE, "document-compliance-gateway"),
        ],
    )
    def test_parse_real_composition(self, path: Path, expected_name: str) -> None:
        """Each real composition.toml parses without errors."""
        dsl = OrchestrationDSL()
        definition = dsl.parse_file(path)

        assert definition.agent_name == expected_name
        assert len(definition.tasks) > 0
        assert all(t.id for t in definition.tasks)
        assert all(t.agent for t in definition.tasks)

    def test_feature_pipeline_structure(self) -> None:
        """Feature delivery pipeline has correct task structure."""
        dsl = OrchestrationDSL()
        definition = dsl.parse_file(_FEATURE_PIPELINE)

        task_ids = {t.id for t in definition.tasks}
        assert "task1" in task_ids
        assert "task2" in task_ids
        assert "task3" in task_ids
        assert "task4" in task_ids

        # task1 is root (no deps)
        task1 = next(t for t in definition.tasks if t.id == "task1")
        assert task1.blocked_by == []

        # task2, task3, task4 all depend on task1
        for tid in ("task2", "task3", "task4"):
            task = next(t for t in definition.tasks if t.id == tid)
            assert "task1" in task.blocked_by

    def test_cicd_gate_all_parallel(self) -> None:
        """CI/CD quality gate tasks are all independent (parallel)."""
        dsl = OrchestrationDSL()
        definition = dsl.parse_file(_CICD_GATE)

        root_tasks = definition.get_root_tasks()
        assert len(root_tasks) == 3  # All tasks are root (no deps)

        # Verify all tasks have no dependencies
        for task in definition.tasks:
            assert task.blocked_by == []

    def test_all_compositions_validate(self) -> None:
        """All real compositions pass DSL validation."""
        dsl = OrchestrationDSL()
        for path in [_FEATURE_PIPELINE, _CICD_GATE, _COMPETITIVE, _PRODUCT_DOCS, _COMPLIANCE]:
            definition = dsl.parse_file(path)
            result = dsl.validate(definition)
            assert result.is_valid, f"{path.name}: {result.errors}"


# ---------------------------------------------------------------------------
# 2. TaskGraph integration: DSL -> TaskGraph -> execution lifecycle
# ---------------------------------------------------------------------------


class TestDSLToTaskGraphLifecycle:
    """Full lifecycle: parse TOML -> load into TaskGraph -> execute."""

    def test_feature_pipeline_full_lifecycle(self) -> None:
        """Feature delivery pipeline: complete execution lifecycle."""
        dsl = OrchestrationDSL()
        definition = dsl.parse_file(_FEATURE_PIPELINE)

        graph = TaskGraph(":memory:")
        try:
            # Load all tasks via batch add
            task_items = [t.to_task_item() for t in definition.tasks]
            graph.add_tasks(task_items)

            # Only task1 should be ready
            ready = graph.get_ready_tasks()
            assert len(ready) == 1
            assert ready[0].id == "task1"

            # Execute task1
            graph.start_task("task1")
            graph.complete_task("task1")

            # Now task2, task3, task4 should all be ready (parallel)
            ready = graph.get_ready_tasks()
            ready_ids = {t.id for t in ready}
            assert ready_ids == {"task2", "task3", "task4"}

            # Execute all three in parallel
            for tid in ("task2", "task3", "task4"):
                graph.start_task(tid)

            for tid in ("task2", "task3", "task4"):
                graph.complete_task(tid)

            # No more ready tasks
            ready = graph.get_ready_tasks()
            assert len(ready) == 0

            # All tasks completed
            snapshot = graph.get_snapshot()
            for task in snapshot.tasks:
                assert task.state == TaskState.COMPLETED
        finally:
            graph.close()

    def test_cicd_gate_parallel_execution(self) -> None:
        """CI/CD gate: all tasks start simultaneously."""
        dsl = OrchestrationDSL()
        definition = dsl.parse_file(_CICD_GATE)

        graph = TaskGraph(":memory:")
        try:
            task_items = [t.to_task_item() for t in definition.tasks]
            graph.add_tasks(task_items)

            # All tasks should be ready immediately (no deps)
            ready = graph.get_ready_tasks()
            assert len(ready) == 3

            # All can start in parallel
            for task in ready:
                graph.start_task(task.id)

            # None should still be ready (all in_progress)
            ready = graph.get_ready_tasks()
            assert len(ready) == 0

            # Complete all
            for task in definition.tasks:
                graph.complete_task(task.id)
        finally:
            graph.close()

    def test_parallel_groups_match_dependencies(self) -> None:
        """Parallel groups respect dependency structure."""
        dsl = OrchestrationDSL()
        definition = dsl.parse_file(_FEATURE_PIPELINE)

        graph = TaskGraph(":memory:")
        try:
            task_items = [t.to_task_item() for t in definition.tasks]
            graph.add_tasks(task_items)

            groups = graph.get_parallel_groups()
            assert len(groups) == 2  # [task1], [task2, task3, task4]

            # Group 0: task1 alone
            assert len(groups[0]) == 1
            assert groups[0][0].id == "task1"

            # Group 1: task2, task3, task4 in parallel
            group_1_ids = sorted(t.id for t in groups[1])
            assert group_1_ids == ["task2", "task3", "task4"]
        finally:
            graph.close()

    def test_failure_blocks_dependents(self) -> None:
        """Failing task1 blocks task2/3/4 from ever becoming ready."""
        dsl = OrchestrationDSL()
        definition = dsl.parse_file(_FEATURE_PIPELINE)

        graph = TaskGraph(":memory:")
        try:
            task_items = [t.to_task_item() for t in definition.tasks]
            graph.add_tasks(task_items)

            # Start and fail task1
            graph.start_task("task1")
            graph.fail_task("task1")

            # task2/3/4 should NOT become ready
            ready = graph.get_ready_tasks()
            assert len(ready) == 0

            # Blocked tasks should include task2/3/4
            blocked = graph.get_blocked_tasks()
            blocked_ids = {t.id for t in blocked}
            assert "task2" in blocked_ids
            assert "task3" in blocked_ids
            assert "task4" in blocked_ids
        finally:
            graph.close()


# ---------------------------------------------------------------------------
# 3. Cycle detection with real-world scenarios
# ---------------------------------------------------------------------------


class TestCycleDetectionReal:
    """Verify cycle detection works with composition-format TOML."""

    def test_valid_compositions_no_cycles(self) -> None:
        """All real compositions have zero cycles."""
        for path in [_FEATURE_PIPELINE, _CICD_GATE, _COMPETITIVE, _PRODUCT_DOCS, _COMPLIANCE]:
            dsl = OrchestrationDSL()
            definition = dsl.parse_file(path)
            assert (
                definition.get_task_depth(next(t for t in definition.tasks if not t.blocked_by).id)
                == 0
            )

    def test_cycle_toml_rejected(self) -> None:
        """Composition with cycle is rejected at parse time."""
        cycle_toml = """
[composition]
name = "cycle-test"
description = "Has a cycle"

[tasks.A]
name = "Task A"
agent = "agent-1"
blocked_by = ["B"]

[tasks.B]
name = "Task B"
agent = "agent-2"
blocked_by = ["A"]
"""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLValidationError, match="cycle"):
            dsl.parse_string(cycle_toml)


# ---------------------------------------------------------------------------
# 4. Composition model (Composition.from_toml) integration
# ---------------------------------------------------------------------------


class TestCompositionModelIntegration:
    """Verify Composition model also parses real files correctly."""

    def test_feature_pipeline_from_toml(self) -> None:
        """Composition.from_toml parses feature-delivery-pipeline."""
        from agent_nexus.models.composition import Composition

        comp = Composition.from_toml(_FEATURE_PIPELINE)
        assert comp.name == "feature-delivery-pipeline"
        assert len(comp.tasks) == 4

        root_tasks = comp.get_root_tasks()
        assert len(root_tasks) == 1
        assert root_tasks[0].id == "task1"

    def test_execution_order_matches_dsl(self) -> None:
        """Composition.get_execution_order matches DSL parallel groups."""
        from agent_nexus.models.composition import Composition

        comp = Composition.from_toml(_FEATURE_PIPELINE)
        groups = comp.get_execution_order()

        # Group 0: task1
        assert groups[0] == ["task1"]
        # Group 1: task2, task3, task4 (sorted)
        assert sorted(groups[1]) == ["task2", "task3", "task4"]

    def test_dependents_lookup(self) -> None:
        """get_dependents returns tasks blocked by the given task."""
        from agent_nexus.models.composition import Composition

        comp = Composition.from_toml(_FEATURE_PIPELINE)
        dependents = comp.get_dependents("task1")
        dependent_ids = {t.id for t in dependents}
        assert dependent_ids == {"task2", "task3", "task4"}

    def test_cicd_all_roots(self) -> None:
        """CI/CD gate: all tasks are root tasks."""
        from agent_nexus.models.composition import Composition

        comp = Composition.from_toml(_CICD_GATE)
        root_tasks = comp.get_root_tasks()
        assert len(root_tasks) == 3

    def test_all_compositions_parse_via_model(self) -> None:
        """All real compositions parse via Composition.from_toml."""
        from agent_nexus.models.composition import Composition

        for path in [_FEATURE_PIPELINE, _CICD_GATE, _COMPETITIVE, _PRODUCT_DOCS, _COMPLIANCE]:
            comp = Composition.from_toml(path)
            assert comp.name
            assert len(comp.tasks) > 0
