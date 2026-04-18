"""Data models for feature-delivery-pipeline Composite Agent.

Pydantic v2 frozen models for pipeline execution tracking.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StageStatus(StrEnum):
    """Pipeline stage execution status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineStage(BaseModel):
    """A single stage in the pipeline execution.

    Attributes:
        name: Stage name (e.g. "requirements-analysis").
        agent: Atomic agent name assigned to this stage.
        status: Current execution status.
        result: Agent output on success, None otherwise.
        error: Error message if stage failed, None otherwise.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    agent: str
    status: StageStatus = StageStatus.PENDING
    result: dict[str, Any] | None = None
    error: str | None = None


class PipelineResult(BaseModel):
    """Result of the full pipeline execution.

    Attributes:
        spec: Original requirement specification input.
        stages: Ordered list of all pipeline stages with their results.
        artifacts: Aggregated outputs from all completed stages.
        success: Whether all stages completed successfully.
    """

    model_config = ConfigDict(frozen=True)

    spec: str
    stages: list[PipelineStage] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    success: bool = False
