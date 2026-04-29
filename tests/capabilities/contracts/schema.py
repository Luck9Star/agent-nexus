"""Contract schema types for capability-driven testing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class InputSpec:
    type: str
    description: str
    examples: list[str]
    required: bool = True


@dataclass
class OutputSpec:
    type: str
    required: bool = True
    min_length: int | None = None
    allowed_values: list[str] | None = None


@dataclass
class QualityThresholds:
    min_output_length: int = 50
    max_output_length: int = 50000
    required_keywords: list[str] = field(default_factory=list)
    score_threshold: float = 0.6


@dataclass
class CapabilityContract:
    agent_name: str
    agent_type: Literal["atomic", "composite", "agency"]
    description: str
    required_inputs: dict[str, InputSpec]
    optional_inputs: dict[str, InputSpec]
    output_schema: dict[str, OutputSpec]
    output_format: Literal["json", "text", "structured"]
    quality_thresholds: QualityThresholds
    cli_method: str = "run"
    cli_params_mapping: dict[str, str] = field(default_factory=dict)


@dataclass
class ValidationResult:
    passed: bool
    score: float
    failures: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
