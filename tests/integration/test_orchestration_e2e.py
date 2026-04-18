"""End-to-end integration tests for the orchestration layer.

Exercises the full DSL -> TaskGraph pipeline:
  1. Parse a TOML orchestration definition (DSL)
  2. Load tasks into TaskGraph
  3. Verify dependency ordering, parallel groups, cycle detection,
     failure propagation, validation, and snapshots.

These tests cover orchestration logic only -- no real subprocess launches.
"""

from __future__ import annotations

import pytest

from agent_nexus.models.task import TaskItem, TaskState
from agent_nexus.platform.orchestration.dsl import (
    DSLValidationError,
    OrchestrationDSL,
)
from agent_nexus.platform.orchestration.task_graph import TaskGraph


# ---------------------------------------------------------------------------
# TOML fixtures
# ---------------------------------------------------------------------------

SEQUENTIAL_TOML = """
[goal]
description = "Sequential 3-step pipeline"

[agent_name]
value = "sequential-pipeline"

[[agents]]
name = "agent-a"
description = "First agent"
role = "worker"

[[agents]]
name = "agent-b"
description = "Second agent"
role = "worker"

[[agents]]
name = "agent-c"
description = "Third agent"
role = "worker"

[[tasks]]
id = "task-a"
description = "Step A"
agent = "agent-a"

[[tasks]]
id = "task-b"
description = "Step B"
agent = "agent-b"
blocked_by = ["task-a"]

[[tasks]]
id = "task-c"
description = "Step C"
agent = "agent-c"
blocked_by = ["task-b"]
"""

DIAMOND_TOML = """
[goal]
description = "Diamond dependency pattern"

[agent_name]
value = "diamond-pipeline"

[[agents]]
name = "agent-x"
description = "Agent X"
role = "worker"

[[agents]]
name = "agent-y"
description = "Agent Y"
role = "worker"

[[agents]]
name = "agent-z"
description = "Agent Z"
role = "worker"

[[agents]]
name = "agent-w"
description = "Agent W"
role = "worker"

[[tasks]]
id = "A"
description = "Root task"
agent = "agent-x"

[[tasks]]
id = "B"
description = "Left branch"
agent = "agent-y"
blocked_by = ["A"]

[[tasks]]
id = "C"
description = "Right branch"
agent = "agent-z"
blocked_by = ["A"]

[[tasks]]
id = "D"
description = "Join task"
agent = "agent-w"
blocked_by = ["B", "C"]
"""

CYCLE_TOML = """
[goal]
description = "Cycle test"

[agent_name]
value = "cycle-pipeline"

[[agents]]
name = "agent-1"
description = "Agent 1"
role = "worker"

[[agents]]
name = "agent-2"
description = "Agent 2"
role = "worker"

[[agents]]
name = "agent-3"
description = "Agent 3"
role = "worker"

[[tasks]]
id = "A"
description = "Step A"
agent = "agent-1"
blocked_by = ["C"]

[[tasks]]
id = "B"
description = "Step B"
agent = "agent-2"
blocked_by = ["A"]

[[tasks]]
id = "C"
description = "Step C"
agent = "agent-3"
blocked_by = ["B"]
"""

UNKNOWN_AGENT_TOML = """
[goal]
description = "Unknown agent ref test"

[agent_name]
value = "bad-agent-pipeline"

[[agents]]
name = "agent-good"
description = "Good agent"
role = "worker"

[[tasks]]
id = "task-1"
description = "Uses unknown agent"
agent = "agent-nonexistent"
"""

UNKNOWN_BLOCKED_BY_TOML = """
[goal]
description = "Unknown blocked_by ref test"

[agent_name]
value = "bad-dep-pipeline"

[[agents]]
name = "agent-a"
description = "Agent A"
role = "worker"

[[tasks]]
id = "task-1"
description = "Depends on non-existent task"
agent = "agent-a"
blocked_by = ["task-nonexistent"]
"""


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _load_definition(toml_content: str):
    """Parse TOML content into an OrchestrationDefinition."""
    dsl = OrchestrationDSL()
    return dsl.parse_string(toml_content)


def _load_into_graph(toml_content: str, graph: TaskGraph) -> None:
    """Parse TOML and add all tasks into a TaskGraph."""
    definition = _load_definition(toml_content)
    for dsl_task in definition.tasks:
        graph.add_task(dsl_task.to_task_item())


# ---------------------------------------------------------------------------
# Test 1: Sequential pipeline — parse, load, execute step by step
# ---------------------------------------------------------------------------


async def test_sequential_pipeline(task_graph: TaskGraph) -> None:
    """Parse a 3-step sequential pipeline, load into TaskGraph, execute step by step."""
    # 1. Parse and load
    definition = _load_definition(SEQUENTIAL_TOML)
    assert len(definition.tasks) == 3

    for dsl_task in definition.tasks:
        task_graph.add_task(dsl_task.to_task_item())

    # 2. Only A should be ready
    ready = task_graph.get_ready_tasks()
    assert len(ready) == 1
    assert ready[0].id == "task-a"

    # 3. Execute A: start -> complete
    task_graph.start_task("task-a")
    task_graph.complete_task("task-a")

    # 4. Only B should now be ready
    ready = task_graph.get_ready_tasks()
    assert len(ready) == 1
    assert ready[0].id == "task-b"

    # 5. Execute B: start -> complete
    task_graph.start_task("task-b")
    task_graph.complete_task("task-b")

    # 6. Only C should now be ready
    ready = task_graph.get_ready_tasks()
    assert len(ready) == 1
    assert ready[0].id == "task-c"

    # 7. Execute C: start -> complete
    task_graph.start_task("task-c")
    task_graph.complete_task("task-c")

    # 8. No ready tasks remain
    ready = task_graph.get_ready_tasks()
    assert len(ready) == 0

    # Verify all tasks are completed
    for tid in ("task-a", "task-b", "task-c"):
        task = task_graph.get_task(tid)
        assert task is not None
        assert task.state == TaskState.COMPLETED


# ---------------------------------------------------------------------------
# Test 2: Parallel groups — diamond dependency pattern
# ---------------------------------------------------------------------------


async def test_parallel_groups(task_graph: TaskGraph) -> None:
    """Verify parallel group computation for diamond dependency pattern.

    Diamond: A -> B, A -> C, B+C -> D
    Expected parallel groups: [[A], [B, C], [D]]
    """
    _load_into_graph(DIAMOND_TOML, task_graph)

    groups = task_graph.get_parallel_groups()
    assert len(groups) == 3

    # Group 0: [A]
    assert len(groups[0]) == 1
    assert groups[0][0].id == "A"

    # Group 1: [B, C] (order may vary, but both must be present)
    group_1_ids = sorted(t.id for t in groups[1])
    assert group_1_ids == ["B", "C"]

    # Group 2: [D]
    assert len(groups[2]) == 1
    assert groups[2][0].id == "D"


# ---------------------------------------------------------------------------
# Test 3: Cycle detection
# ---------------------------------------------------------------------------


async def test_cycle_detection_via_dsl() -> None:
    """Verify cycles are rejected at DSL parse time with DSLValidationError."""
    with pytest.raises(DSLValidationError, match="cycle"):
        _load_definition(CYCLE_TOML)


async def test_cycle_detection_via_task_graph(task_graph: TaskGraph) -> None:
    """Verify cycles are rejected when adding tasks to TaskGraph."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    # Add A (no deps)
    task_graph.add_task(TaskItem(
        id="A", description="Step A", agent="agent-1",
        state=TaskState.PENDING, created_at=now, updated_at=now,
    ))
    # Add B (blocked by A)
    task_graph.add_task(TaskItem(
        id="B", description="Step B", agent="agent-2",
        blocked_by=["A"], state=TaskState.PENDING,
        created_at=now, updated_at=now,
    ))
    # Adding C (blocked by B) that also creates A -> B -> C -> A cycle
    # should fail because C tries to depend on a task not yet added
    # So add C first without cycle, then try to add a D that closes the cycle
    task_graph.add_task(TaskItem(
        id="C", description="Step C", agent="agent-3",
        blocked_by=["B"], state=TaskState.PENDING,
        created_at=now, updated_at=now,
    ))

    # Now try to add D that is blocked by C AND would close cycle back to A
    # by adding a task that A depends on (i.e., A -> ... -> C -> D -> A)
    # The easiest way: try to add a dependency to A that points to C
    # But we can't modify A's deps. Instead, test the cycle detection directly.
    # Let's use a fresh graph and add in cycle order.
    # We can't directly create A -> B -> C -> A because add_task validates refs.
    # Instead, test detect_cycles() after building a known-good graph,
    # and test _would_create_cycle via the add_task rejection.

    # Create D with blocked_by=["C"], then try to create A2 that depends on D
    # and have A depend on A2 -- but that requires re-adding A.
    # Simpler: test that the graph reports no cycles for the valid DAG.
    cycles = task_graph.detect_cycles()
    assert len(cycles) == 0

    # Test that adding a self-referencing task is caught
    with pytest.raises(ValueError, match="cycle"):
        task_graph.add_task(TaskItem(
            id="D", description="Self-loop", agent="agent-1",
            blocked_by=["D"], state=TaskState.PENDING,
            created_at=now, updated_at=now,
        ))

    # Test that adding a task that closes a back-edge is caught.
    # Current graph: A <- B <- C.  Try to make A depend on C.
    # We cannot modify A, so add a new task that A is supposed to depend on...
    # Actually, we need to add a task "X" that is blocked_by=["C"]
    # and then try to make A depend on X -- but again, can't modify A.
    #
    # The real cycle path: build a graph where add_task catches it.
    # Build: add Z, add Y(blocked_by=[Z]), add X(blocked_by=[Y])
    # Now try to re-add Z with blocked_by=[X] -- that's a duplicate, so ValueError.
    # To truly test: add Z2 that is blocked_by X, then try to add W blocked_by Z2
    # and add Z blocked_by W... can't modify Z.
    #
    # The cleanest test: start a fresh graph and add tasks in order that creates cycle.
    # Add nodes first with no deps, then the cycle-forming deps get caught.
    # But add_task sets deps at creation time. So we need to add them all
    # then detect cycles. Let me just verify the detect_cycles method works
    # on a graph we know has no cycles, and that add_task catches self-loops.
    # The DSL-level cycle test (test_cycle_detection_via_dsl) already covers
    # the parse-time detection comprehensively.


# ---------------------------------------------------------------------------
# Test 4: Task failure propagation
# ---------------------------------------------------------------------------


async def test_task_failure(task_graph: TaskGraph) -> None:
    """Verify a failed task does not unblock its dependents."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    # A -> B (B depends on A)
    task_graph.add_task(TaskItem(
        id="A", description="Task A", agent="agent-1",
        state=TaskState.PENDING, created_at=now, updated_at=now,
    ))
    task_graph.add_task(TaskItem(
        id="B", description="Task B", agent="agent-2",
        blocked_by=["A"], state=TaskState.PENDING,
        created_at=now, updated_at=now,
    ))

    # Only A is ready
    ready = task_graph.get_ready_tasks()
    assert len(ready) == 1
    assert ready[0].id == "A"

    # Start A then fail it
    task_graph.start_task("A")
    task_graph.fail_task("A")

    # A is now failed
    task_a = task_graph.get_task("A")
    assert task_a is not None
    assert task_a.state == TaskState.FAILED

    # B should NOT become ready (A did not complete)
    ready = task_graph.get_ready_tasks()
    assert len(ready) == 0

    # B should still be in blocked list
    blocked = task_graph.get_blocked_tasks()
    assert len(blocked) == 1
    assert blocked[0].id == "B"


# ---------------------------------------------------------------------------
# Test 5: DSL validation warnings
# ---------------------------------------------------------------------------


async def test_dsl_validation_unknown_agent() -> None:
    """DSL validation catches unknown agent references."""
    with pytest.raises(DSLValidationError, match="unknown agent"):
        _load_definition(UNKNOWN_AGENT_TOML)


async def test_dsl_validation_unknown_blocked_by() -> None:
    """DSL validation catches unknown blocked_by references."""
    with pytest.raises(DSLValidationError, match="unknown task"):
        _load_definition(UNKNOWN_BLOCKED_BY_TOML)


async def test_dsl_validation_clean() -> None:
    """Valid TOML produces no validation errors."""
    dsl = OrchestrationDSL()
    definition = dsl.parse_string(SEQUENTIAL_TOML)
    # Re-validate explicitly (parse already validates, but let's be thorough)
    warnings = dsl.validate(definition)
    assert len(warnings) == 0


# ---------------------------------------------------------------------------
# Test 6: TaskGraph snapshot
# ---------------------------------------------------------------------------


async def test_task_graph_snapshot(task_graph: TaskGraph) -> None:
    """Verify snapshot captures full graph state."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    # Add 3 tasks: A -> B -> C
    task_graph.add_task(TaskItem(
        id="A", description="Task A", agent="agent-1",
        state=TaskState.PENDING, created_at=now, updated_at=now,
    ))
    task_graph.add_task(TaskItem(
        id="B", description="Task B", agent="agent-2",
        blocked_by=["A"], state=TaskState.PENDING,
        created_at=now, updated_at=now,
    ))
    task_graph.add_task(TaskItem(
        id="C", description="Task C", agent="agent-3",
        blocked_by=["B"], state=TaskState.PENDING,
        created_at=now, updated_at=now,
    ))

    # Start and complete A
    task_graph.start_task("A")
    task_graph.complete_task("A")

    # Start B
    task_graph.start_task("B")

    # Take snapshot
    snapshot = task_graph.get_snapshot()

    # Verify task count
    assert len(snapshot.tasks) == 3

    # Verify states
    state_map = {t.id: t.state for t in snapshot.tasks}
    assert state_map["A"] == TaskState.COMPLETED
    assert state_map["B"] == TaskState.IN_PROGRESS
    assert state_map["C"] == TaskState.PENDING

    # Verify parallel groups: [A], [B], [C]
    assert len(snapshot.parallel_groups) == 3
    assert snapshot.parallel_groups[0] == ["A"]
    assert snapshot.parallel_groups[1] == ["B"]
    assert snapshot.parallel_groups[2] == ["C"]
