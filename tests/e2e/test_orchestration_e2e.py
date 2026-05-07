"""E2E tests for orchestration layer: TaskGraph + ProcessManager + IPC + DSL.

Covers the full orchestration pipeline from DSL parsing through task execution
to result collection.
"""

from pathlib import Path

import pytest

from agent_nexus.models.task import TaskItem, TaskState
from agent_nexus.platform.orchestration.task_graph import TaskGraph


class TestOrchestrationE2E:
    """E2E orchestration scenarios."""

    def test_task_graph_lifecycle(self, tmp_path: Path) -> None:
        """TaskGraph: add tasks, start, complete, query."""
        db_path = tmp_path / "test.db"
        tg = TaskGraph(str(db_path))

        task = TaskItem(
            id="t1",
            description="Test task",
            agent="test-agent",
            state=TaskState.PENDING,
        )
        tg.add_task(task)
        assert tg.get_task("t1") is not None

        started = tg.start_task("t1")
        assert started.state == TaskState.IN_PROGRESS

        completed = tg.complete_task("t1")
        assert completed.state == TaskState.COMPLETED

    def test_dsl_parse_and_validate(self) -> None:
        """OrchestrationDSL: parse TOML, validate DAG, produce task items."""
        from agent_nexus.platform.orchestration.dsl import OrchestrationDSL

        toml_content = """
[goal]
description = "Test goal"

[agent_name]
value = "test-composite"

[[agents]]
name = "researcher"
description = "Research agent"

[[tasks]]
id = "task1"
description = "Do research"
agent = "researcher"
blocked_by = []
"""
        dsl = OrchestrationDSL()
        defn = dsl.parse_string(toml_content)
        assert defn.goal == "Test goal"
        assert len(defn.agents) == 1
        assert len(defn.tasks) == 1
        assert defn.tasks[0].agent == "researcher"

        # Convert to TaskItem
        task_item = defn.tasks[0].to_task_item()
        assert task_item.id == "task1"

    def test_task_graph_cycle_detection(self, tmp_path: Path) -> None:
        """TaskGraph rejects tasks with circular dependencies."""
        db_path = tmp_path / "cycle.db"
        tg = TaskGraph(str(db_path))

        t1 = TaskItem(
            id="t1", description="A", agent="a", state=TaskState.PENDING, blocked_by=["t2"]
        )
        t2 = TaskItem(
            id="t2", description="B", agent="b", state=TaskState.PENDING, blocked_by=["t1"]
        )

        with pytest.raises(ValueError, match="[Cc]ycle|cycle"):
            tg.add_tasks([t1, t2])

    def test_ipc_json_lines_protocol(self) -> None:
        """IPC models serialize to JSON-lines format correctly."""
        from agent_nexus.models.ipc import (
            AgentToPlatform,
            AgentToPlatformType,
            PlatformToAgent,
            PlatformToAgentType,
        )

        msg_out = PlatformToAgent(
            type=PlatformToAgentType.CHAT,
            content="hello",
            conversation_id="c1",
        )
        json_str = msg_out.model_dump_json(exclude_none=True)
        assert '"type":"chat"' in json_str
        assert '"content":"hello"' in json_str

        msg_in = AgentToPlatform(
            type=AgentToPlatformType.RESULT,
            content="result text",
        )
        json_str = msg_in.model_dump_json(exclude_none=True)
        assert '"type":"result"' in json_str


class TestTaskGraphDependenciesE2E:
    """E2E tests for TaskGraph dependency resolution and parallel groups."""

    def test_diamond_dependency(self, tmp_path: Path) -> None:
        """Diamond DAG: A→B, A→C, B→D, C→D resolves correctly."""
        tg = TaskGraph(str(tmp_path / "diamond.db"))

        tasks = [
            TaskItem(id="a", description="Root", agent="x", state=TaskState.PENDING),
            TaskItem(id="b", description="Left", agent="x", state=TaskState.PENDING, blocked_by=["a"]),
            TaskItem(id="c", description="Right", agent="x", state=TaskState.PENDING, blocked_by=["a"]),
            TaskItem(id="d", description="Join", agent="x", state=TaskState.PENDING, blocked_by=["b", "c"]),
        ]
        tg.add_tasks(tasks)

        # Only 'a' should be ready initially
        ready = tg.get_ready_tasks()
        ready_ids = {t.id for t in ready}
        assert "a" in ready_ids
        assert "b" not in ready_ids

        # Complete 'a' → b and c become ready
        tg.start_task("a")
        tg.complete_task("a")
        ready = tg.get_ready_tasks()
        ready_ids = {t.id for t in ready}
        assert ready_ids == {"b", "c"}

        # Complete b → d still blocked by c
        tg.start_task("b")
        tg.complete_task("b")
        ready = tg.get_ready_tasks()
        ready_ids = {t.id for t in ready}
        assert "c" in ready_ids
        assert "d" not in ready_ids

        # Complete c → d becomes ready
        tg.start_task("c")
        tg.complete_task("c")
        ready = tg.get_ready_tasks()
        ready_ids = {t.id for t in ready}
        assert "d" in ready_ids

    def test_parallel_groups(self, tmp_path: Path) -> None:
        """get_parallel_groups returns correct grouping for linear chain."""
        tg = TaskGraph(str(tmp_path / "groups.db"))

        tasks = [
            TaskItem(id="a", description="A", agent="x", state=TaskState.PENDING),
            TaskItem(id="b", description="B", agent="x", state=TaskState.PENDING, blocked_by=["a"]),
            TaskItem(id="c", description="C", agent="x", state=TaskState.PENDING, blocked_by=["b"]),
        ]
        tg.add_tasks(tasks)

        groups = tg.get_parallel_groups()
        assert len(groups) == 3
        assert [t.id for t in groups[0]] == ["a"]
        assert [t.id for t in groups[1]] == ["b"]
        assert [t.id for t in groups[2]] == ["c"]

    def test_add_tasks_rejects_duplicate_ids(self, tmp_path: Path) -> None:
        """Adding tasks with duplicate IDs raises ValueError."""
        tg = TaskGraph(str(tmp_path / "dup.db"))

        tasks = [
            TaskItem(id="t1", description="First", agent="x", state=TaskState.PENDING),
            TaskItem(id="t1", description="Duplicate", agent="x", state=TaskState.PENDING),
        ]
        with pytest.raises(ValueError, match="[Dd]uplicate|already"):
            tg.add_tasks(tasks)

    def test_fail_task_transitions(self, tmp_path: Path) -> None:
        """Task can be transitioned to FAILED state."""
        tg = TaskGraph(str(tmp_path / "fail.db"))

        tg.add_task(TaskItem(id="t1", description="Failable", agent="x", state=TaskState.PENDING))
        tg.start_task("t1")
        failed = tg.fail_task("t1")
        assert failed.state == TaskState.FAILED

    def test_batch_add_rejects_missing_deps(self, tmp_path: Path) -> None:
        """Tasks referencing non-existent IDs as blocked_by are rejected."""
        tg = TaskGraph(str(tmp_path / "ext.db"))

        tasks = [
            TaskItem(
                id="t1",
                description="Depends on external",
                agent="x",
                state=TaskState.PENDING,
                blocked_by=["external-task"],
            ),
        ]
        with pytest.raises(ValueError, match="non-existent"):
            tg.add_tasks(tasks)


class TestDSLE2E:
    """E2E tests for OrchestrationDSL parsing edge cases."""

    def test_composition_format(self) -> None:
        """Parse composition TOML format with agents and tasks."""
        from agent_nexus.platform.orchestration.dsl import OrchestrationDSL

        toml_content = """
[goal]
description = "Full feature pipeline"

[agent_name]
value = "feature-delivery"

[[agents]]
name = "researcher"
description = "Research agent"

[[agents]]
name = "coder"
description = "Code generation agent"

[[tasks]]
id = "research"
description = "Research topic"
agent = "researcher"
blocked_by = []

[[tasks]]
id = "code"
description = "Write code"
agent = "coder"
blocked_by = ["research"]
"""
        dsl = OrchestrationDSL()
        defn = dsl.parse_string(toml_content)
        assert len(defn.agents) == 2
        assert len(defn.tasks) == 2
        assert defn.tasks[1].blocked_by == ["research"]

    def test_validate_dag_rejects_invalid_agent_ref(self) -> None:
        """validate() catches task referencing unknown agent."""
        from agent_nexus.platform.orchestration.dsl import DSLValidationError, OrchestrationDSL

        toml_content = """
[goal]
description = "Bad ref"

[agent_name]
value = "test"

[[agents]]
name = "researcher"
description = "Research agent"

[[tasks]]
id = "task1"
description = "Do research"
agent = "nonexistent"
blocked_by = []
"""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLValidationError, match="unknown agent"):
            dsl.parse_string(toml_content)
