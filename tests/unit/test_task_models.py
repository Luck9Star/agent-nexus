"""Unit tests for agent_nexus.models.task module."""

import json

import pytest
from pydantic import ValidationError

from agent_nexus.models.task import TaskGraphSnapshot, TaskItem, TaskState

# ---------------------------------------------------------------------------
# TaskState enum
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TaskItem
# ---------------------------------------------------------------------------


class TestTaskItem:
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

    def test_serialization_round_trip(self):
        t1 = TaskItem(id="t1", description="First", agent="w1")
        snap = TaskGraphSnapshot(tasks=[t1], parallel_groups=[["t1"]])
        data = snap.model_dump()
        snap2 = TaskGraphSnapshot(**data)
        assert snap2 == snap
