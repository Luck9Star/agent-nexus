"""Unit tests for agent_nexus.models.task — TaskItem and TaskState."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_nexus.models.task import TaskItem, TaskState


# ---------------------------------------------------------------------------
# TaskState enum
# ---------------------------------------------------------------------------


class TestTaskState:
    def test_all_states_exist(self):
        assert set(TaskState) == {
            TaskState.PENDING,
            TaskState.IN_PROGRESS,
            TaskState.COMPLETED,
            TaskState.FAILED,
        }

    def test_string_values(self):
        assert TaskState.PENDING == "pending"
        assert TaskState.IN_PROGRESS == "in_progress"
        assert TaskState.COMPLETED == "completed"
        assert TaskState.FAILED == "failed"

    def test_is_str_enum(self):
        assert isinstance(TaskState.PENDING, str)


# ---------------------------------------------------------------------------
# TaskItem construction (happy path)
# ---------------------------------------------------------------------------


class TestTaskItemConstruction:
    def test_minimal_valid_task(self):
        task = TaskItem(id="A", description="do thing", agent="worker")
        assert task.id == "A"
        assert task.description == "do thing"
        assert task.agent == "worker"
        assert task.blocked_by == []
        assert task.vars == {}
        assert task.state == TaskState.PENDING
        assert task.result is None

    def test_full_construction(self):
        task = TaskItem(
            id="B",
            description="full task",
            agent="planner",
            blocked_by=["A"],
            vars={"key": "val"},
            state=TaskState.IN_PROGRESS,
            result={"output": 42},
        )
        assert task.blocked_by == ["A"]
        assert task.vars == {"key": "val"}
        assert task.state == TaskState.IN_PROGRESS
        assert task.result == {"output": 42}

    def test_multiple_deps(self):
        task = TaskItem(
            id="C",
            description="multi-dep",
            agent="runner",
            blocked_by=["A", "B"],
        )
        assert task.blocked_by == ["A", "B"]

    def test_empty_blocked_by_no_false_positive(self):
        task = TaskItem(
            id="A",
            description="no deps",
            agent="worker",
            blocked_by=[],
        )
        assert task.id not in task.blocked_by

    def test_dep_on_different_id_is_fine(self):
        task = TaskItem(
            id="A",
            description="depends on B",
            agent="worker",
            blocked_by=["B", "C"],
        )
        assert task.id == "A"
        assert "A" not in task.blocked_by

    def test_frozen(self):
        task = TaskItem(id="A", description="t", agent="w")
        with pytest.raises(ValidationError):
            task.state = TaskState.COMPLETED  # type: ignore[misc]

    def test_serialization_round_trip(self):
        task = TaskItem(
            id="X",
            description="round trip",
            agent="y",
            blocked_by=["Z"],
        )
        data = task.model_dump()
        task2 = TaskItem(**data)
        assert task2 == task


# ---------------------------------------------------------------------------
# TaskItem self-reference validator
# ---------------------------------------------------------------------------


class TestTaskItemSelfReference:
    def test_self_block_raises(self):
        with pytest.raises(ValidationError, match="cannot block itself"):
            TaskItem(
                id="A",
                description="self-loop",
                agent="worker",
                blocked_by=["A"],
            )

    def test_self_ref_among_other_deps_raises(self):
        with pytest.raises(ValidationError, match="cannot block itself"):
            TaskItem(
                id="A",
                description="hidden self-loop",
                agent="worker",
                blocked_by=["B", "A", "C"],
            )

    def test_no_self_ref_with_same_prefix(self):
        """Names like 'A-1' and 'A' should not trigger false positive."""
        task = TaskItem(
            id="A",
            description="prefix-safe",
            agent="worker",
            blocked_by=["A-1"],
        )
        assert task.blocked_by == ["A-1"]


# ---------------------------------------------------------------------------
# TaskItem field constraints
# ---------------------------------------------------------------------------


class TestTaskItemFieldConstraints:
    def test_empty_id_rejected(self):
        with pytest.raises(ValidationError):
            TaskItem(id="", description="t", agent="w")

    def test_empty_description_rejected(self):
        with pytest.raises(ValidationError):
            TaskItem(id="A", description="", agent="w")

    def test_empty_agent_rejected(self):
        with pytest.raises(ValidationError):
            TaskItem(id="A", description="t", agent="")

    def test_missing_id_rejected(self):
        with pytest.raises(ValidationError):
            TaskItem(description="t", agent="w")  # type: ignore[call-arg]

    def test_missing_description_rejected(self):
        with pytest.raises(ValidationError):
            TaskItem(id="A", agent="w")  # type: ignore[call-arg]

    def test_missing_agent_rejected(self):
        with pytest.raises(ValidationError):
            TaskItem(id="A", description="t")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# iter100 regression: TaskState.BLOCKED removed
# ---------------------------------------------------------------------------

class TestTaskStateBlockedRemoved:
    def test_blocked_not_in_enum(self):
        """BLOCKED was a dead member never used in src/ — removed."""
        assert not hasattr(TaskState, "BLOCKED")

    def test_pending_to_failed_transition(self):
        """PENDING -> FAILED is valid (upstream dependency failure)."""
        task = TaskItem(id="T1", description="t", agent="w", state=TaskState.PENDING)
        updated = task.model_copy(update={"state": TaskState.FAILED})
        assert updated.state == TaskState.FAILED
