"""Tests for good-skill data models.

Covers construction, validation, serialization, and immutability.
"""

from __future__ import annotations

import json

import pytest

from agent_good_skill.models import TaskInput, TaskResult


class TestTaskInput:
    """Tests for TaskInput model."""

    def test_basic_construction(self) -> None:
        t = TaskInput(task="hello")
        assert t.task == "hello"
        assert t.context is None

    def test_with_context(self) -> None:
        t = TaskInput(task="test", context={"key": "value"})
        assert t.context == {"key": "value"}

    def test_frozen(self) -> None:
        t = TaskInput(task="x")
        with pytest.raises(Exception):
            t.task = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        t = TaskInput(task="test", context={"a": 1})
        data = t.model_dump()
        t2 = TaskInput.model_validate(data)
        assert t == t2

    def test_json_serialization(self) -> None:
        t = TaskInput(task="json_test")
        json_str = t.model_dump_json()
        data = json.loads(json_str)
        assert data["task"] == "json_test"


class TestTaskResult:
    """Tests for TaskResult model."""

    def test_success_result(self) -> None:
        r = TaskResult(output="done")
        assert r.output == "done"
        assert r.success is True

    def test_failure_result(self) -> None:
        r = TaskResult(output="", success=False)
        assert r.success is False

    def test_frozen(self) -> None:
        r = TaskResult(output="x")
        with pytest.raises(Exception):
            r.output = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        r = TaskResult(output="result text", success=True)
        data = r.model_dump()
        r2 = TaskResult.model_validate(data)
        assert r == r2
