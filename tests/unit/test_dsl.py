"""Unit tests for OrchestrationDSL — TOML DAG parser for composite agents.

Tests DSL data types (DSLAgent, DSLTask, DSLToolLoading), OrchestrationDefinition
helpers, parsing, and validation including cycle detection.
"""

from __future__ import annotations

import pytest

from agent_nexus.models.task import TaskState
from agent_nexus.platform.orchestration.dsl import (
    DSLAgent,
    DSLValidationError,
    DSLSyntaxError,
    DSLTask,
    DSLToolLoading,
    OrchestrationDSL,
    OrchestrationDefinition,
)


# ============================================================================
# DSLAgent
# ============================================================================


class TestDSLAgent:
    def test_valid_creation(self) -> None:
        """DSLAgent with valid fields."""
        agent = DSLAgent(name="reviewer", description="Code reviewer", role="verification")
        assert agent.name == "reviewer"
        assert agent.role == "verification"
        assert agent.tool_loading == "lazy"

    def test_invalid_role_raises(self) -> None:
        """Invalid role raises ValueError."""
        with pytest.raises(ValueError, match="Invalid agent role"):
            DSLAgent(name="bad", description="", role="superhero")

    def test_invalid_tool_loading_raises(self) -> None:
        """Invalid tool_loading raises ValueError."""
        with pytest.raises(ValueError, match="Invalid tool_loading"):
            DSLAgent(name="bad", description="", tool_loading="aggressive")

    def test_default_values(self) -> None:
        """Default role=worker, tool_loading=lazy."""
        agent = DSLAgent(name="default", description="desc")
        assert agent.role == "worker"
        assert agent.tool_loading == "lazy"


# ============================================================================
# DSLTask
# ============================================================================


class TestDSLTask:
    def test_creation(self) -> None:
        """DSLTask stores fields correctly."""
        task = DSLTask(
            id="T1",
            description="Do something",
            agent="worker-1",
            blocked_by=["T0"],
            vars={"key": "value"},
        )
        assert task.id == "T1"
        assert task.agent == "worker-1"
        assert task.blocked_by == ["T0"]
        assert task.vars == {"key": "value"}

    def test_to_task_item(self) -> None:
        """to_task_item() converts to TaskItem with matching fields."""
        task = DSLTask(
            id="T1",
            description="Test task",
            agent="agent-a",
            blocked_by=["T0"],
            vars={"x": 1},
        )
        item = task.to_task_item()

        assert item.id == "T1"
        assert item.description == "Test task"
        assert item.agent == "agent-a"
        assert item.blocked_by == ["T0"]
        assert item.vars == {"x": 1}
        assert item.state == TaskState.PENDING

    def test_to_task_item_defaults(self) -> None:
        """to_task_item() with minimal fields."""
        task = DSLTask(id="T2", description="", agent="a")
        item = task.to_task_item()

        assert item.blocked_by == []
        assert item.vars == {}
        assert item.state == TaskState.PENDING


# ============================================================================
# DSLToolLoading
# ============================================================================


class TestDSLToolLoading:
    def test_valid_creation(self) -> None:
        """DSLToolLoading with valid strategy."""
        tl = DSLToolLoading(strategy="lazy", preload_agents=["agent-a"])
        assert tl.strategy == "lazy"
        assert tl.preload_agents == ["agent-a"]

    def test_invalid_strategy_raises(self) -> None:
        """Invalid strategy raises ValueError."""
        with pytest.raises(ValueError, match="Invalid tool_loading strategy"):
            DSLToolLoading(strategy="preload_everything")

    def test_default_values(self) -> None:
        """Default strategy=lazy, preload_agents=[]."""
        tl = DSLToolLoading()
        assert tl.strategy == "lazy"
        assert tl.preload_agents == []


# ============================================================================
# OrchestrationDefinition helpers
# ============================================================================


class TestOrchestrationDefinition:
    @staticmethod
    def _make_definition() -> OrchestrationDefinition:
        """Build a simple valid definition for testing helpers."""
        agents = {
            "a1": DSLAgent(name="a1", description="Agent 1", role="worker"),
            "a2": DSLAgent(name="a2", description="Agent 2", role="explore"),
        }
        tasks = [
            DSLTask(id="T1", description="Root", agent="a1"),
            DSLTask(id="T2", description="Dependent", agent="a2", blocked_by=["T1"]),
            DSLTask(id="T3", description="Also root", agent="a1"),
        ]
        return OrchestrationDefinition(
            goal="Test goal",
            agent_name="test-composite",
            agents=agents,
            tasks=tasks,
            tool_loading=DSLToolLoading(),
        )

    def test_get_root_tasks(self) -> None:
        """get_root_tasks returns tasks with no deps."""
        defn = self._make_definition()
        roots = defn.get_root_tasks()

        root_ids = {t.id for t in roots}
        assert root_ids == {"T1", "T3"}

    def test_get_agent_tasks(self) -> None:
        """get_agent_tasks returns tasks assigned to a specific agent."""
        defn = self._make_definition()
        a1_tasks = defn.get_agent_tasks("a1")

        assert len(a1_tasks) == 2
        assert all(t.agent == "a1" for t in a1_tasks)

    def test_get_agent_tasks_no_match(self) -> None:
        """get_agent_tasks returns empty for agent with no tasks."""
        defn = self._make_definition()
        assert defn.get_agent_tasks("unknown") == []

    def test_get_task_depth_root(self) -> None:
        """Root tasks have depth 0."""
        defn = self._make_definition()
        assert defn.get_task_depth("T1") == 0
        assert defn.get_task_depth("T3") == 0

    def test_get_task_depth_child(self) -> None:
        """Dependent task has depth 1."""
        defn = self._make_definition()
        assert defn.get_task_depth("T2") == 1

    def test_get_task_depth_not_found(self) -> None:
        """Unknown task returns -1."""
        defn = self._make_definition()
        assert defn.get_task_depth("nonexistent") == -1

    def test_get_task_depth_multi_level(self) -> None:
        """Multi-level depth: T1 -> T2 -> T4."""
        defn = self._make_definition()
        # Add T4 blocked_by T2
        tasks = list(defn.tasks) + [
            DSLTask(id="T4", description="Deep", agent="a1", blocked_by=["T2"]),
        ]
        defn = OrchestrationDefinition(
            goal="Deep",
            agent_name="test",
            agents=defn.agents,
            tasks=tasks,
            tool_loading=DSLToolLoading(),
        )
        assert defn.get_task_depth("T4") == 2


# ============================================================================
# parse_string() — valid and error cases
# ============================================================================


_MINIMAL_VALID_TOML = """
[goal]
description = "Build feature X"

[agent_name]
value = "feature-pipeline"

[[agents]]
name = "explorer"
description = "Explores codebase"
role = "explore"

[[agents]]
name = "worker"
description = "Writes code"
role = "worker"

[[tasks]]
id = "explore"
description = "Explore the codebase"
agent = "explorer"

[[tasks]]
id = "implement"
description = "Implement the feature"
agent = "worker"
blocked_by = ["explore"]
"""


class TestParseStringValid:
    def test_parse_valid(self) -> None:
        """Parse valid TOML returns OrchestrationDefinition."""
        dsl = OrchestrationDSL()
        defn = dsl.parse_string(_MINIMAL_VALID_TOML)

        assert defn.goal == "Build feature X"
        assert defn.agent_name == "feature-pipeline"
        assert len(defn.agents) == 2
        assert len(defn.tasks) == 2

    def test_parse_preserves_agents(self) -> None:
        """Parsed agents have correct fields."""
        dsl = OrchestrationDSL()
        defn = dsl.parse_string(_MINIMAL_VALID_TOML)

        assert "explorer" in defn.agents
        assert defn.agents["explorer"].role == "explore"
        assert "worker" in defn.agents

    def test_parse_preserves_tasks(self) -> None:
        """Parsed tasks have correct fields and deps."""
        dsl = OrchestrationDSL()
        defn = dsl.parse_string(_MINIMAL_VALID_TOML)

        impl = next(t for t in defn.tasks if t.id == "implement")
        assert impl.blocked_by == ["explore"]


class TestParseStringErrors:
    def test_missing_goal(self) -> None:
        """Missing [goal] section raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match="goal"):
            dsl.parse_string("""
[agent_name]
value = "test"

[[agents]]
name = "a"
description = ""
""")

    def test_missing_agent_name(self) -> None:
        """Missing [agent_name] section raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match="agent_name"):
            dsl.parse_string("""
[goal]
description = "Test"
""")

    def test_duplicate_agent(self) -> None:
        """Duplicate agent name raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match="Duplicate agent"):
            dsl.parse_string("""
[goal]
description = "Test"

[agent_name]
value = "test"

[[agents]]
name = "dup"
description = "First"

[[agents]]
name = "dup"
description = "Second"
""")

    def test_duplicate_task(self) -> None:
        """Duplicate task ID raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match="Duplicate task"):
            dsl.parse_string("""
[goal]
description = "Test"

[agent_name]
value = "test"

[[agents]]
name = "a"
description = ""

[[tasks]]
id = "dup"
description = "First"
agent = "a"

[[tasks]]
id = "dup"
description = "Second"
agent = "a"
""")

    def test_invalid_toml(self) -> None:
        """Invalid TOML syntax raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match="Invalid TOML"):
            dsl.parse_string("this is not valid toml {{{")


# ============================================================================
# validate()
# ============================================================================


class TestValidate:
    def _make_definition_with_warnings(self, **overrides) -> OrchestrationDefinition:
        """Build a definition that may generate warnings."""
        agents = overrides.get("agents", {
            "a1": DSLAgent(name="a1", description="Agent 1"),
        })
        tasks = overrides.get("tasks", [
            DSLTask(id="T1", description="Task", agent="a1"),
        ])
        return OrchestrationDefinition(
            goal="Test",
            agent_name="test",
            agents=agents,
            tasks=tasks,
            tool_loading=overrides.get("tool_loading", DSLToolLoading()),
        )

    def test_validate_unknown_agent(self) -> None:
        """Warning for task referencing unknown agent."""
        defn = self._make_definition_with_warnings(
            tasks=[DSLTask(id="T1", description="", agent="nonexistent")],
        )
        dsl = OrchestrationDSL()
        warnings = dsl.validate(defn)

        assert any("unknown agent" in w for w in warnings)

    def test_validate_unknown_blocked_by(self) -> None:
        """Warning for task referencing unknown blocked_by."""
        defn = self._make_definition_with_warnings(
            tasks=[
                DSLTask(id="T1", description="", agent="a1", blocked_by=["ghost"]),
            ],
        )
        dsl = OrchestrationDSL()
        warnings = dsl.validate(defn)

        assert any("unknown task" in w for w in warnings)

    def test_validate_clean(self) -> None:
        """No warnings for valid definition."""
        defn = self._make_definition_with_warnings()
        dsl = OrchestrationDSL()
        warnings = dsl.validate(defn)

        assert warnings == []

    def test_validate_unused_agent(self) -> None:
        """Warning for agent with no tasks assigned."""
        defn = self._make_definition_with_warnings(
            agents={
                "a1": DSLAgent(name="a1", description="Used"),
                "a2": DSLAgent(name="a2", description="Unused"),
            },
            tasks=[DSLTask(id="T1", description="", agent="a1")],
        )
        dsl = OrchestrationDSL()
        warnings = dsl.validate(defn)

        assert any("no tasks" in w for w in warnings)

    def test_validate_cycle(self) -> None:
        """Warning for dependency cycle."""
        defn = self._make_definition_with_warnings(
            tasks=[
                DSLTask(id="T1", description="", agent="a1", blocked_by=["T2"]),
                DSLTask(id="T2", description="", agent="a1", blocked_by=["T1"]),
            ],
        )
        dsl = OrchestrationDSL()
        warnings = dsl.validate(defn)

        assert any("cycle" in w.lower() for w in warnings)


# ============================================================================
# _detect_cycles()
# ============================================================================


class TestDetectCycles:
    def test_direct_cycle(self) -> None:
        """Detect A -> B -> C -> A cycle."""
        task_map = {
            "A": DSLTask(id="A", description="", agent="x", blocked_by=["C"]),
            "B": DSLTask(id="B", description="", agent="x", blocked_by=["A"]),
            "C": DSLTask(id="C", description="", agent="x", blocked_by=["B"]),
        }
        cycles = OrchestrationDSL._detect_cycles(task_map)
        assert len(cycles) > 0

    def test_self_loop(self) -> None:
        """Detect self-loop: A -> A."""
        task_map = {
            "A": DSLTask(id="A", description="", agent="x", blocked_by=["A"]),
        }
        cycles = OrchestrationDSL._detect_cycles(task_map)
        assert len(cycles) > 0

    def test_no_cycle_dag(self) -> None:
        """No cycles in valid DAG."""
        task_map = {
            "A": DSLTask(id="A", description="", agent="x"),
            "B": DSLTask(id="B", description="", agent="x", blocked_by=["A"]),
            "C": DSLTask(id="C", description="", agent="x", blocked_by=["A"]),
            "D": DSLTask(id="D", description="", agent="x", blocked_by=["B", "C"]),
        }
        cycles = OrchestrationDSL._detect_cycles(task_map)
        assert len(cycles) == 0
