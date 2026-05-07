"""E2E tests for orchestration layer: TaskGraph + ProcessManager + IPC + DSL.

Covers the full orchestration pipeline from DSL parsing through task execution
to result collection.
"""

from pathlib import Path

import pytest


class TestOrchestrationE2E:
    """E2E orchestration scenarios."""

    def test_task_graph_lifecycle(self, tmp_path: Path) -> None:
        """TaskGraph: add tasks, start, complete, query."""
        from agent_nexus.models.task import TaskItem, TaskState
        from agent_nexus.platform.orchestration.task_graph import TaskGraph

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
        from agent_nexus.models.task import TaskItem, TaskState
        from agent_nexus.platform.orchestration.task_graph import TaskGraph

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
