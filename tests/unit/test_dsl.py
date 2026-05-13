"""Unit tests for OrchestrationDSL — TOML DAG parser for composite agents.

Tests DSL data types (DSLAgent, DSLTask, DSLToolLoading), OrchestrationDefinition
helpers, parsing, and validation including cycle detection.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_nexus.models.task import TaskState
from agent_nexus.platform.orchestration.dsl import (
    DSLAgent,
    DSLSyntaxError,
    DSLTask,
    DSLToolLoading,
    DSLValidationError,
    OrchestrationDefinition,
    OrchestrationDSL,
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
        task = DSLTask(id="T2", description="Minimal task", agent="a")
        item = task.to_task_item()

        assert item.description == "Minimal task"
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

    def test_get_task_depth_cycle_no_recursion_error(self) -> None:
        """get_task_depth does NOT raise RecursionError on cyclic deps."""
        agents = {
            "a1": DSLAgent(name="a1", description="Agent 1"),
        }
        tasks = [
            DSLTask(id="T1", description="", agent="a1", blocked_by=["T2"]),
            DSLTask(id="T2", description="", agent="a1", blocked_by=["T1"]),
        ]
        defn = OrchestrationDefinition(
            goal="Cycle",
            agent_name="cycle-test",
            agents=agents,
            tasks=tasks,
            tool_loading=DSLToolLoading(),
        )
        # Should NOT raise RecursionError; returns -1 for cycle
        d1 = defn.get_task_depth("T1")
        d2 = defn.get_task_depth("T2")
        assert d1 == -1
        assert d2 == -1


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
        agents = overrides.get(
            "agents",
            {
                "a1": DSLAgent(name="a1", description="Agent 1"),
            },
        )
        tasks = overrides.get(
            "tasks",
            [
                DSLTask(id="T1", description="Task", agent="a1"),
            ],
        )
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
        result = dsl.validate(defn)

        assert any("unknown agent" in w for w in result.errors)

    def test_validate_unknown_blocked_by(self) -> None:
        """Warning for task referencing unknown blocked_by."""
        defn = self._make_definition_with_warnings(
            tasks=[
                DSLTask(id="T1", description="", agent="a1", blocked_by=["ghost"]),
            ],
        )
        dsl = OrchestrationDSL()
        result = dsl.validate(defn)

        assert any("unknown task" in w for w in result.errors)

    def test_validate_clean(self) -> None:
        """No warnings for valid definition."""
        defn = self._make_definition_with_warnings()
        dsl = OrchestrationDSL()
        result = dsl.validate(defn)

        assert result.is_valid

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
        result = dsl.validate(defn)

        assert any("no tasks" in w for w in result.warnings)

    def test_validate_cycle(self) -> None:
        """Warning for dependency cycle."""
        defn = self._make_definition_with_warnings(
            tasks=[
                DSLTask(id="T1", description="", agent="a1", blocked_by=["T2"]),
                DSLTask(id="T2", description="", agent="a1", blocked_by=["T1"]),
            ],
        )
        dsl = OrchestrationDSL()
        result = dsl.validate(defn)

        assert any("cycle" in e.lower() for e in result.errors)


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


# ============================================================================
# parse_file() -- file-based parsing
# ============================================================================


class TestParseFile:
    def test_parse_file_not_found(self, tmp_path: Any) -> None:
        """parse_file raises DSLSyntaxError for non-existent file."""

        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match="TOML file not found"):
            dsl.parse_file(tmp_path / "nonexistent.toml")


# ============================================================================
# TOML parsing edge cases -- missing fields, invalid types
# ============================================================================


# Helper: wrap partial TOML with required boilerplate to avoid early-exit errors.
def _wrap_toml(partial: str) -> str:
    """Wrap partial TOML content with the required boilerplate sections.

    Only includes boilerplate that is NOT already present in *partial*.
    """
    has_goal = "[goal]" in partial
    has_agent_name = "[agent_name]" in partial
    has_agents = "[[agents]]" in partial
    has_tasks = "[[tasks]]" in partial

    pieces: list[str] = []
    if not has_goal:
        pieces.append('[goal]\ndescription = "Test goal"')
    if not has_agent_name:
        pieces.append('[agent_name]\nvalue = "test-composite"')
    if not has_agents:
        pieces.append('[[agents]]\nname = "a"\ndescription = "Agent A"')
    if not has_tasks:
        pieces.append('[[tasks]]\nid = "T1"\ndescription = "Task"\nagent = "a"')

    return partial + "\n\n" + "\n\n".join(pieces) + "\n"


class TestParseStringEdgeCases:
    """Tests for uncovered error paths in _parse()."""

    # -- [goal] section edge cases (lines 284) --

    def test_goal_description_empty_string(self) -> None:
        """Empty [goal].description raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match=r"\[goal\]\.description must be a non-empty"):
            dsl.parse_string(_wrap_toml("[goal]\ndescription = ''"))

    # -- [agent_name] section edge cases (line 292) --

    def test_agent_name_value_empty(self) -> None:
        """Empty [agent_name].value raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match=r"\[agent_name\]\.value must be a non-empty"):
            dsl.parse_string(_wrap_toml('[agent_name]\nvalue = ""'))

    # -- [[agents]] section edge cases (lines 297, 301, 304, 317-318) --

    def test_agents_not_a_list(self) -> None:
        """[[agents]] as a single table (not array) raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match=r"\[\[agents\]\] must be an array"):
            dsl.parse_string("""
[goal]
description = "Test"

[agent_name]
value = "test"

[agents]
name = "a"
description = "Agent A"

[[tasks]]
id = "T1"
description = "Task"
agent = "a"
""")

    def test_agent_entry_not_a_dict(self) -> None:
        """Agent entry that is a scalar (not table) raises DSLSyntaxError."""
        import toml as toml_lib

        dsl = OrchestrationDSL()
        # Build raw dict directly to bypass TOML's structural constraints.
        raw = toml_lib.loads("""
[goal]
description = "Test"

[agent_name]
value = "test"

[[tasks]]
id = "T1"
description = "Task"
agent = "a"
""")
        raw["agents"] = ["not-a-table"]

        with pytest.raises(DSLSyntaxError, match=r"agents\[0\] must be a table"):
            dsl._parse(raw)

    def test_agent_name_missing_or_empty(self) -> None:
        """Agent with missing name raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match=r"agents\[0\]\.name must be a non-empty"):
            dsl.parse_string("""
[goal]
description = "Test"

[agent_name]
value = "test"

[[agents]]
description = "No name here"

[[tasks]]
id = "T1"
description = "Task"
agent = "a"
""")

    # -- [[tasks]] section edge cases (lines 323, 328, 331, 338, 343, 348, 353, 359) --

    def test_tasks_not_a_list(self) -> None:
        """[[tasks]] as a single table (not array) raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match=r"\[\[tasks\]\] must be an array"):
            dsl.parse_string("""
[goal]
description = "Test"

[agent_name]
value = "test"

[[agents]]
name = "a"
description = ""

[tasks]
id = "T1"
description = "Task"
agent = "a"
""")

    def test_task_entry_not_a_dict(self) -> None:
        """Task entry that is a scalar raises DSLSyntaxError."""
        import toml as toml_lib

        dsl = OrchestrationDSL()
        raw = toml_lib.loads("""
[goal]
description = "Test"

[agent_name]
value = "test"

[[agents]]
name = "a"
description = ""
""")
        raw["tasks"] = ["not-a-table"]

        with pytest.raises(DSLSyntaxError, match=r"tasks\[0\] must be a table"):
            dsl._parse(raw)

    def test_task_id_missing(self) -> None:
        """Task with missing id raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match=r"tasks\[0\]\.id must be a non-empty"):
            dsl.parse_string("""
[goal]
description = "Test"

[agent_name]
value = "test"

[[agents]]
name = "a"
description = ""

[[tasks]]
description = "No id"
agent = "a"
""")

    def test_task_agent_missing(self) -> None:
        """Task with missing agent raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match=r"\.agent must be a non-empty"):
            dsl.parse_string("""
[goal]
description = "Test"

[agent_name]
value = "test"

[[agents]]
name = "a"
description = ""

[[tasks]]
id = "T1"
description = "No agent"
""")

    def test_task_blocked_by_not_a_list(self) -> None:
        """Task with blocked_by as string raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match=r"\.blocked_by must be a list"):
            dsl.parse_string("""
[goal]
description = "Test"

[agent_name]
value = "test"

[[agents]]
name = "a"
description = ""

[[tasks]]
id = "T1"
description = "Task"
agent = "a"
blocked_by = "not-a-list"
""")

    def test_task_blocked_by_empty_entry(self) -> None:
        """Task with empty string in blocked_by raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match=r"\.blocked_by\[0\] must be a non-empty"):
            dsl.parse_string("""
[goal]
description = "Test"

[agent_name]
value = "test"

[[agents]]
name = "a"
description = ""

[[tasks]]
id = "T1"
description = "Task"
agent = "a"
blocked_by = [""]
""")

    def test_task_vars_not_a_dict(self) -> None:
        """Task with vars as list raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match=r"\.vars must be a table"):
            dsl.parse_string("""
[goal]
description = "Test"

[agent_name]
value = "test"

[[agents]]
name = "a"
description = ""

[[tasks]]
id = "T1"
description = "Task"
agent = "a"
vars = ["not", "a", "dict"]
""")

    # -- [tool_loading] section edge cases (lines 375, 379, 385-386) --

    def test_tool_loading_not_a_dict(self) -> None:
        """[tool_loading] as a scalar raises DSLSyntaxError."""
        import toml as toml_lib

        dsl = OrchestrationDSL()
        raw = toml_lib.loads("""
[goal]
description = "Test"

[agent_name]
value = "test"

[[agents]]
name = "a"
description = ""

[[tasks]]
id = "T1"
description = "Task"
agent = "a"
""")
        raw["tool_loading"] = "lazy"

        with pytest.raises(DSLSyntaxError, match=r"\[tool_loading\] must be a table"):
            dsl._parse(raw)

    def test_tool_loading_preload_agents_not_a_list(self) -> None:
        """preload_agents as string raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match="preload_agents must be a list"):
            dsl.parse_string("""
[goal]
description = "Test"

[agent_name]
value = "test"

[[agents]]
name = "a"
description = ""

[[tasks]]
id = "T1"
description = "Task"
agent = "a"

[tool_loading]
strategy = "lazy"
preload_agents = "not-a-list"
""")


# ============================================================================
# validate() edge cases -- preload_agents unknown ref (lines 263-264)
# ============================================================================


class TestValidatePreloadAgents:
    def test_validate_preload_unknown_agent(self) -> None:
        """Warning when preload_agents references unknown agent."""
        defn = OrchestrationDefinition(
            goal="Test",
            agent_name="test",
            agents={"a1": DSLAgent(name="a1", description="Agent")},
            tasks=[DSLTask(id="T1", description="", agent="a1")],
            tool_loading=DSLToolLoading(strategy="lazy", preload_agents=["ghost"]),
        )
        dsl = OrchestrationDSL()
        result = dsl.validate(defn)

        assert any("preload_agents references unknown agent 'ghost'" in e for e in result.errors)


# ============================================================================
# DSLValidationError from _parse -- cycles and unknown refs raise during parse
# ============================================================================


class TestParseValidationErrors:
    def test_parse_raises_on_cycle(self) -> None:
        """Parsing TOML with a cycle raises DSLValidationError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLValidationError, match="DSL validation failed"):
            dsl.parse_string("""
[goal]
description = "Test"

[agent_name]
value = "test"

[[agents]]
name = "a"
description = ""

[[tasks]]
id = "T1"
description = "Task 1"
agent = "a"
blocked_by = ["T2"]

[[tasks]]
id = "T2"
description = "Task 2"
agent = "a"
blocked_by = ["T1"]
""")


# ============================================================================
# MessagingConfig
# ============================================================================


class TestMessagingConfig:
    pass


# ============================================================================
# [messaging] TOML parsing
# ============================================================================


class TestMessagingTomlParsing:
    def test_parse_without_messaging_section(self) -> None:
        """Definition without [messaging] gets defaults."""
        dsl = OrchestrationDSL()
        defn = dsl.parse_string(_MINIMAL_VALID_TOML)
        assert defn.messaging.enabled is True
        assert defn.messaging.max_message_size == 1_048_576
        assert defn.messaging.request_timeout == 30.0

    def test_parse_messaging_section_canonical(self) -> None:
        """Parse [messaging] in canonical TOML format."""
        toml_str = (
            _MINIMAL_VALID_TOML
            + """
[messaging]
enabled = false
max_message_size = 4096
request_timeout = 10.0
allowed_channels = ["chat"]
"""
        )
        dsl = OrchestrationDSL()
        defn = dsl.parse_string(toml_str)
        assert defn.messaging.enabled is False
        assert defn.messaging.max_message_size == 4096
        assert defn.messaging.request_timeout == 10.0
        assert defn.messaging.allowed_channels == ["chat"]

    def test_parse_messaging_section_composition_format(self) -> None:
        """Parse [messaging] in composition TOML format."""
        toml_str = """
[composition]
name = "test-pipeline"
description = "A test pipeline"

[tasks.T1]
name = "step-1"
agent = "worker-a"

[messaging]
enabled = true
max_message_size = 8192
request_timeout = 15.0
"""
        dsl = OrchestrationDSL()
        defn = dsl.parse_string(toml_str)
        assert defn.messaging.enabled is True
        assert defn.messaging.max_message_size == 8192
        assert defn.messaging.request_timeout == 15.0

    def test_messaging_not_a_table(self) -> None:
        """[messaging] as scalar raises DSLSyntaxError."""
        import toml as toml_lib

        dsl = OrchestrationDSL()
        raw = toml_lib.loads(_MINIMAL_VALID_TOML)
        raw["messaging"] = "bad"
        with pytest.raises(DSLSyntaxError, match=r"\[messaging\] must be a table"):
            dsl._parse(raw)

    def test_messaging_enabled_not_bool(self) -> None:
        """[messaging].enabled as string raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match="enabled must be a boolean"):
            dsl.parse_string(
                _MINIMAL_VALID_TOML
                + """
[messaging]
enabled = "yes"
"""
            )

    def test_messaging_max_size_not_positive(self) -> None:
        """[messaging].max_message_size as negative raises DSLSyntaxError."""
        dsl = OrchestrationDSL()
        with pytest.raises(DSLSyntaxError, match="max_message_size must be a positive"):
            dsl.parse_string(
                _MINIMAL_VALID_TOML
                + """
[messaging]
max_message_size = -1
"""
            )

    def test_messaging_partial_section(self) -> None:
        """Partial [messaging] section uses defaults for missing fields."""
        dsl = OrchestrationDSL()
        defn = dsl.parse_string(
            _MINIMAL_VALID_TOML
            + """
[messaging]
enabled = false
"""
        )
        assert defn.messaging.enabled is False
        assert defn.messaging.max_message_size == 1_048_576
        assert defn.messaging.request_timeout == 30.0
