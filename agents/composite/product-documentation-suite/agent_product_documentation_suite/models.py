"""Data models for product-documentation-suite Composite Agent.

Pydantic v2 frozen models for documentation artifacts and suite results.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DocArtifact(BaseModel):
    """A single documentation artifact produced by the suite.

    Attributes:
        type: Artifact type (e.g. "openapi_spec", "review_report", "localization").
        path: File path where the artifact is stored.
        language: Language code for the artifact (e.g. "en", "zh").
        content_hash: SHA-256 hash of the artifact content for drift detection.
    """

    model_config = ConfigDict(frozen=True)

    type: str
    path: str = ""
    language: str = "en"
    content_hash: str = ""


class DocumentationResult(BaseModel):
    """Final result of the product documentation suite pipeline.

    Attributes:
        artifacts: All documentation artifacts produced.
        coverage_score: API documentation coverage (0.0-1.0).
        drift_report: Description of any detected drift between code and docs.
        success: Whether the pipeline completed successfully.
    """

    model_config = ConfigDict(frozen=True)

    artifacts: list[DocArtifact] = Field(default_factory=list)
    coverage_score: float = 0.0
    drift_report: str = ""
    success: bool = True
