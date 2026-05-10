"""Data models for data-pipeline-validator Agent.

Pydantic v2 frozen models for ETL pipeline validation,
finding tracking, and report generation.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PipelineFinding(BaseModel):
    """A single pipeline validation finding.

    Attributes:
        severity: Issue severity — error, warning, or info.
        category: Finding category (e.g. "structure", "source", "target", "step", "error_handling").
        location: Location within the pipeline config (e.g. "steps[0]", "source").
        description: Human-readable description of the issue.
        remediation: Suggested fix for the issue.
    """

    model_config = ConfigDict(frozen=True)

    severity: str
    category: str
    location: str
    description: str = ""
    remediation: str = ""


class PipelineValidationResult(BaseModel):
    """Result of validating a pipeline configuration.

    Attributes:
        findings: All validation findings discovered.
        is_valid: Whether the pipeline passed all critical checks.
        step_count: Number of steps in the pipeline.
        pipeline_name: Name of the pipeline, if specified.
    """

    model_config = ConfigDict(frozen=True)

    findings: list[PipelineFinding] = Field(default_factory=list)
    is_valid: bool = True
    step_count: int = 0
    pipeline_name: str = ""


class PipelineReport(BaseModel):
    """Comprehensive pipeline validation report.

    Attributes:
        error_count: Number of error-level findings.
        warning_count: Number of warning-level findings.
        info_count: Number of info-level findings.
        findings: All validation findings.
        recommendations: Prioritized list of remediation recommendations.
        step_count: Number of pipeline steps validated.
    """

    model_config = ConfigDict(frozen=True)

    error_count: int = 0
    warning_count: int = 0
    info_count: int = 0
    findings: list[PipelineFinding] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    step_count: int = 0
