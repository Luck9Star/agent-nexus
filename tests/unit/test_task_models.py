"""Unit tests for agent_nexus.models.task module."""

import json

import pytest
from pydantic import ValidationError

from agent_nexus.models.task import TaskGraphSnapshot, TaskItem, TaskState


# ---------------------------------------------------------------------------
# TaskState enum
# ---------------------------------------------------------------------------

class TestTaskState:
    def test_members(self):
        assert set(TaskState) == {
            TaskState.PENDING,
            TaskState.IN_PROGRESS,
            TaskState.COMPLETED,
            TaskState.FAILED,
        }

    def test_values(self):
        assert TaskState.PENDING == "pending"
        assert TaskState.IN_PROGRESS == "in_progress"
        assert TaskState.COMPLETED == "completed"
        assert TaskState.FAILED == "failed"

    def test_from_string(self):
        assert TaskState("pending") is TaskState.PENDING
        assert TaskState("in_progress") is TaskState.IN_PROGRESS

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            TaskState("unknown")


# ---------------------------------------------------------------------------
# TaskItem
# ---------------------------------------------------------------------------

class TestTaskItem:
    def test_construction_with_required_fields(self):
        t = TaskItem(id="t1", description="Do work", agent="worker-1")
        assert t.id == "t1"
        assert t.description == "Do work"
        assert t.agent == "worker-1"

    def test_default_state_is_pending(self):
        t = TaskItem(id="t1", description="Do work", agent="worker-1")
        assert t.state is TaskState.PENDING

    def test_default_blocked_by_empty(self):
        t = TaskItem(id="t1", description="Do work", agent="worker-1")
        assert t.blocked_by == []

    def test_default_vars_empty(self):
        t = TaskItem(id="t1", description="Do work", agent="worker-1")
        assert t.vars == {}

    def test_default_result_none(self):
        t = TaskItem(id="t1", description="Do work", agent="worker-1")
        assert t.result is None

    def test_with_blocked_by(self):
        t = TaskItem(
            id="t2",
            description="Follow-up",
            agent="worker-2",
            blocked_by=["t1"],
        )
        assert t.blocked_by == ["t1"]

    def test_with_state(self):
        t = TaskItem(
            id="t1",
            description="Do work",
            agent="worker-1",
            state=TaskState.IN_PROGRESS,
        )
        assert t.state is TaskState.IN_PROGRESS

    def test_with_result(self):
        t = TaskItem(
            id="t1",
            description="Do work",
            agent="worker-1",
            state=TaskState.COMPLETED,
            result={"output": "done"},
        )
        assert t.result == {"output": "done"}

    def test_with_vars(self):
        t = TaskItem(
            id="t1",
            description="Do work",
            agent="worker-1",
            vars={"input_file": "/tmp/data.csv"},
        )
        assert t.vars["input_file"] == "/tmp/data.csv"

    def test_frozen_raises_on_mutation(self):
        t = TaskItem(id="t1", description="Do work", agent="worker-1")
        with pytest.raises(ValidationError):
            t.state = TaskState.COMPLETED

    def test_frozen_raises_on_field_change(self):
        t = TaskItem(id="t1", description="Do work", agent="worker-1")
        with pytest.raises(ValidationError):
            t.id = "t2"

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            TaskItem()

    def test_serialization_round_trip(self):
        t = TaskItem(
            id="t1",
            description="Do work",
            agent="worker-1",
            blocked_by=["t0"],
            state=TaskState.IN_PROGRESS,
        )
        data = t.model_dump()
        t2 = TaskItem(**data)
        assert t2 == t

    def test_json_serialization(self):
        t = TaskItem(
            id="t1",
            description="Do work",
            agent="worker-1",
            result="done",
        )
        json_str = t.model_dump_json()
        parsed = json.loads(json_str)
        assert parsed["id"] == "t1"
        assert parsed["result"] == "done"
        t2 = TaskItem.model_validate_json(json_str)
        assert t2 == t

    def test_empty_blocked_by_list(self):
        t = TaskItem(id="t1", description="Do work", agent="worker-1", blocked_by=[])
        assert t.blocked_by == []

    def test_multiple_blocked_by(self):
        t = TaskItem(
            id="t3",
            description="Aggregate",
            agent="worker-3",
            blocked_by=["t1", "t2"],
        )
        assert len(t.blocked_by) == 2


# ---------------------------------------------------------------------------
# TaskGraphSnapshot
# ---------------------------------------------------------------------------

class TestTaskItemSelfReference:
    """TaskItem must reject self-referencing blocked_by (guaranteed deadlock)."""

    def test_self_reference_rejected(self) -> None:
        with pytest.raises(ValidationError, match="cannot block itself"):
            TaskItem(
                id="t1",
                description="Do work",
                agent="worker-1",
                blocked_by=["t1"],
            )

    def test_self_reference_among_others_rejected(self) -> None:
        with pytest.raises(ValidationError, match="cannot block itself"):
            TaskItem(
                id="t1",
                description="Do work",
                agent="worker-1",
                blocked_by=["t0", "t1", "t2"],
            )

    def test_other_references_allowed(self) -> None:
        t = TaskItem(
            id="t1",
            description="Do work",
            agent="worker-1",
            blocked_by=["t0", "t2"],
        )
        assert t.blocked_by == ["t0", "t2"]

    def test_empty_blocked_by_still_allowed(self) -> None:
        t = TaskItem(id="t1", description="Do work", agent="worker-1")
        assert t.blocked_by == []


class TestTaskGraphSnapshot:
    def test_default_construction(self):
        snap = TaskGraphSnapshot()
        assert snap.tasks == []
        assert snap.parallel_groups == []

    def test_with_tasks(self):
        t1 = TaskItem(id="t1", description="First", agent="w1")
        t2 = TaskItem(id="t2", description="Second", agent="w2")
        snap = TaskGraphSnapshot(
            tasks=[t1, t2],
            parallel_groups=[["t1", "t2"]],
        )
        assert len(snap.tasks) == 2
        assert snap.parallel_groups == [["t1", "t2"]]

    def test_frozen(self):
        snap = TaskGraphSnapshot()
        with pytest.raises(ValidationError):
            snap.tasks = []

    def test_serialization_round_trip(self):
        t1 = TaskItem(id="t1", description="First", agent="w1")
        snap = TaskGraphSnapshot(tasks=[t1], parallel_groups=[["t1"]])
        data = snap.model_dump()
        snap2 = TaskGraphSnapshot(**data)
        assert snap2 == snap


# ---------------------------------------------------------------------------
# min_length=1 validation tests (iter30)
# ---------------------------------------------------------------------------


class TestTaskItemMinLength:
    """Required string fields in TaskItem reject empty strings."""

    def test_empty_id(self):
        with pytest.raises(ValidationError):
            TaskItem(id="", description="d", agent="a")

    def test_empty_description(self):
        with pytest.raises(ValidationError):
            TaskItem(id="t1", description="", agent="a")

    def test_empty_agent(self):
        with pytest.raises(ValidationError):
            TaskItem(id="t1", description="d", agent="")
