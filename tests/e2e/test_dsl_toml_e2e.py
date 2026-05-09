"""E2E: DSL TOML cycle detection — real TOML parsing with cycle rejection.

Validates OrchestrationDSL correctly rejects circular DAGs at parse time,
using real TOML parsing (not pre-built data structures).  This exercises the
full parse→validate→reject pipeline that the MCP Gateway uses when loading
composite agent definitions.

Both canonical and composition TOML formats are tested, since the parser
auto-detects the format and takes different code paths.

Contract: DSLValidationError must be raised for any circular dependency,
matching Rust's ap-core/src/dsl/ validation behavior.
"""

import pytest

from agent_nexus.platform.orchestration.dsl import (
    DSLSyntaxError,
    DSLValidationError,
    OrchestrationDSL,
)


def _task(tid: str, desc: str, agent: str, blocked_by: list[str] | None = None) -> str:
    """Build a single [[tasks]] TOML entry."""
    deps = str(blocked_by) if blocked_by else "[]"
    return (
        f"[[tasks]]\nid = '{tid}'\ndescription = '{desc}'\nagent = '{agent}'\nblocked_by = {deps}\n"
    )


def _canonical(*tasks: str) -> str:
    """Build a complete canonical-format TOML with given task entries."""
    header = (
        "[goal]\ndescription = 'Test orchestration'\n\n"
        "[agent_name]\nvalue = 'test-composite'\n\n"
        "[[agents]]\nname = 'agent-a'\ndescription = 'Agent A'\n\n"
        "[[agents]]\nname = 'agent-b'\ndescription = 'Agent B'\n\n"
        "[[agents]]\nname = 'agent-c'\ndescription = 'Agent C'\n\n"
    )
    return header + "\n".join(tasks)


def _composition(*tasks: str) -> str:
    """Build a composition-format TOML with given task entries."""
    header = "[composition]\nname = 'test-pipeline'\ndescription = 'Test pipeline'\n\n"
    return header + "\n".join(tasks)


def _comp_task(tid: str, name: str, agent: str, blocked_by: list[str] | None = None) -> str:
    """Build a single [tasks.X] TOML entry for composition format."""
    deps = str(blocked_by) if blocked_by else "[]"
    return f"[tasks.{tid}]\nname = '{name}'\nagent = '{agent}'\nblocked_by = {deps}\n"


# ---------------------------------------------------------------------------
# Canonical format: cycle detection at parse time
# ---------------------------------------------------------------------------


class TestCanonicalCycleDetection:
    """Cycle detection via real TOML parsing in canonical format."""

    def test_two_node_cycle_rejected(self) -> None:
        """A->B->A dependency cycle raises DSLValidationError."""
        toml = _canonical(
            _task("t1", "Task 1", "agent-a", ["t2"]),
            _task("t2", "Task 2", "agent-b", ["t1"]),
        )
        with pytest.raises(DSLValidationError, match="[Cc]ycle"):
            OrchestrationDSL().parse_string(toml)

    def test_self_blocking_rejected(self) -> None:
        """A task blocking itself raises DSLValidationError."""
        toml = _canonical(
            _task("t1", "Self-blocker", "agent-a", ["t1"]),
        )
        with pytest.raises(DSLValidationError, match="cannot block itself|self"):
            OrchestrationDSL().parse_string(toml)

    def test_three_node_cycle_rejected(self) -> None:
        """A->B->C->A three-node cycle raises DSLValidationError."""
        toml = _canonical(
            _task("t1", "Task 1", "agent-a", ["t3"]),
            _task("t2", "Task 2", "agent-b", ["t1"]),
            _task("t3", "Task 3", "agent-c", ["t2"]),
        )
        with pytest.raises(DSLValidationError, match="[Cc]ycle"):
            OrchestrationDSL().parse_string(toml)

    def test_valid_linear_dag_accepted(self) -> None:
        """A->B->C linear DAG is accepted."""
        toml = _canonical(
            _task("t1", "Task 1", "agent-a"),
            _task("t2", "Task 2", "agent-b", ["t1"]),
            _task("t3", "Task 3", "agent-c", ["t2"]),
        )
        defn = OrchestrationDSL().parse_string(toml)
        assert len(defn.tasks) == 3
        assert defn.tasks[0].id == "t1"

    def test_valid_diamond_dag_accepted(self) -> None:
        """Diamond DAG (A->B, A->C, B+C->D) is accepted."""
        toml = _canonical(
            _task("t1", "Root", "agent-a"),
            _task("t2", "Left", "agent-b", ["t1"]),
            _task("t3", "Right", "agent-c", ["t1"]),
            _task("t4", "Join", "agent-a", ["t2", "t3"]),
        )
        defn = OrchestrationDSL().parse_string(toml)
        assert len(defn.tasks) == 4
        join = next(t for t in defn.tasks if t.id == "t4")
        assert set(join.blocked_by) == {"t2", "t3"}

    def test_unknown_agent_ref_rejected(self) -> None:
        """Task referencing a non-existent agent raises DSLValidationError."""
        toml = _canonical(
            _task("t1", "Task 1", "nonexistent-agent"),
        )
        with pytest.raises(DSLValidationError, match="unknown agent"):
            OrchestrationDSL().parse_string(toml)


# ---------------------------------------------------------------------------
# Composition format: cycle detection at parse time
# ---------------------------------------------------------------------------


class TestCompositionCycleDetection:
    """Cycle detection via real TOML parsing in composition format."""

    def test_two_node_cycle_rejected(self) -> None:
        """Composition format A->B->A cycle raises DSLValidationError."""
        toml = _composition(
            _comp_task("t1", "Task 1", "agent-a", ["t2"]),
            _comp_task("t2", "Task 2", "agent-b", ["t1"]),
        )
        with pytest.raises(DSLValidationError, match="[Cc]ycle"):
            OrchestrationDSL().parse_string(toml)

    def test_self_blocking_rejected(self) -> None:
        """Composition format self-blocking raises DSLValidationError."""
        toml = _composition(
            _comp_task("t1", "Self-blocker", "agent-a", ["t1"]),
        )
        with pytest.raises(DSLValidationError, match="cannot block itself|self"):
            OrchestrationDSL().parse_string(toml)

    def test_valid_pipeline_accepted(self) -> None:
        """Composition format linear pipeline is accepted."""
        toml = _composition(
            _comp_task("t1", "Analyze", "agent-a"),
            _comp_task("t2", "Generate", "agent-b", ["t1"]),
        )
        defn = OrchestrationDSL().parse_string(toml)
        assert len(defn.tasks) == 2

    def test_missing_agent_field_rejected(self) -> None:
        """Composition format task without agent raises DSLSyntaxError."""
        toml = "[composition]\nname = 'x'\ndescription = 'x'\n\n[tasks.t1]\nname = 'No agent'\n"
        with pytest.raises(DSLSyntaxError, match="agent"):
            OrchestrationDSL().parse_string(toml)


# ---------------------------------------------------------------------------
# TOML structural validation
# ---------------------------------------------------------------------------


class TestDSLTOMLStructuralValidation:
    """TOML structural errors are caught before cycle detection."""

    def test_missing_goal_section_rejected(self) -> None:
        """Canonical TOML without [goal] raises DSLSyntaxError."""
        toml = (
            "[agent_name]\nvalue = 'test'\n\n"
            "[[agents]]\nname = 'a'\ndescription = 'A'\n\n"
            "[[tasks]]\nid = 't1'\ndescription = 'T'\nagent = 'a'\n"
        )
        with pytest.raises(DSLSyntaxError, match="goal"):
            OrchestrationDSL().parse_string(toml)

    def test_invalid_toml_syntax_rejected(self) -> None:
        """Malformed TOML raises DSLSyntaxError."""
        with pytest.raises(DSLSyntaxError, match="TOML"):
            OrchestrationDSL().parse_string("this is not valid toml = [broken")

    def test_empty_toml_rejected(self) -> None:
        """Empty TOML string raises DSLSyntaxError."""
        with pytest.raises(DSLSyntaxError):
            OrchestrationDSL().parse_string("")

    def test_duplicate_task_id_rejected(self) -> None:
        """Duplicate task IDs raise DSLSyntaxError."""
        toml = _canonical(
            _task("t1", "First", "agent-a"),
            _task("t1", "Duplicate", "agent-b"),
        )
        with pytest.raises(DSLSyntaxError, match="[Dd]uplicate"):
            OrchestrationDSL().parse_string(toml)


# ---------------------------------------------------------------------------
# DSLTask -> TaskItem conversion contract
# ---------------------------------------------------------------------------


class TestDSLTaskConversion:
    """Verify DSLTask.to_task_item() produces valid TaskItem for TaskGraph."""

    def test_to_task_item_preserves_fields(self) -> None:
        """to_task_item preserves id, description, agent, blocked_by."""
        toml = _canonical(
            _task("t1", "Analyze code", "agent-a"),
            _task("t2", "Generate docs", "agent-b", ["t1"]),
            _task("t3", "Finalize", "agent-a", ["t2"]),
        )
        defn = OrchestrationDSL().parse_string(toml)
        assert len(defn.tasks) == 3

        items = [t.to_task_item() for t in defn.tasks]
        assert items[0].id == "t1"
        assert items[0].description == "Analyze code"
        assert items[0].agent == "agent-a"
        assert items[0].blocked_by == []

        assert items[1].id == "t2"
        assert items[1].blocked_by == ["t1"]

        assert items[2].id == "t3"
        assert items[2].blocked_by == ["t2"]

    def test_to_task_items_feed_into_task_graph(self, tmp_path) -> None:
        """Parsed tasks can be loaded into TaskGraph without errors."""
        from agent_nexus.platform.orchestration.task_graph import TaskGraph

        toml = _canonical(
            _task("t1", "First", "agent-a"),
            _task("t2", "Second", "agent-b", ["t1"]),
        )
        defn = OrchestrationDSL().parse_string(toml)

        graph = TaskGraph(str(tmp_path / "test.db"))
        for task in defn.tasks:
            graph.add_task(task.to_task_item())

        ready = graph.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].id == "t1"
        graph.close()
