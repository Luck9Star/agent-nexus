"""Data models for competitive-intelligence-briefing Composite Agent.

Pydantic v2 frozen models for pipeline step tracking and briefing results.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PipelineStep(BaseModel):
    """Tracks the state of a single step in the orchestration pipeline.

    Attributes:
        name: Human-readable step name.
        agent: Atomic agent name responsible for this step.
        input_data: Input payload for the step (serialized).
        output_data: Output payload from the step (serialized), None if not yet run.
        status: Execution status -- "pending", "running", "completed", "failed", "skipped".
    """

    model_config = ConfigDict(frozen=True)

    name: str
    agent: str
    input_data: dict = Field(default_factory=dict)
    output_data: dict | None = None
    status: str = "pending"


class BriefingResult(BaseModel):
    """Final result of the competitive intelligence briefing pipeline.

    Attributes:
        query: Original research query that initiated the pipeline.
        analysis: Serialized market analysis output from Phase 1.
        report_path: Path to the filled report document (Phase 2).
        localizations: Mapping of target language code to localized text.
        success: Whether the entire pipeline completed successfully.
    """

    model_config = ConfigDict(frozen=True)

    query: str
    analysis: dict = Field(default_factory=dict)
    report_path: str = ""
    localizations: dict[str, str] = Field(default_factory=dict)
    success: bool = True
