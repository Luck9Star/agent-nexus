"""Data models for good-skill Agent.

Pydantic v2 models for task execution.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TaskInput(BaseModel):
    """Input for a good-skill task.

    Attributes:
        task: Task description string.
        context: Optional context dictionary.
    """

    model_config = ConfigDict(frozen=True)

    task: str
    context: dict | None = None


class TaskResult(BaseModel):
    """Result from a good-skill task execution.

    Attributes:
        output: The task result string.
        success: Whether execution succeeded.
    """

    model_config = ConfigDict(frozen=True)

    output: str
    success: bool = True
