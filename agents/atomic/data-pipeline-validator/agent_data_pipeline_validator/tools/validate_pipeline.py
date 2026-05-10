"""Pipeline validation tool — validate ETL pipeline configuration.

Checks required fields, validates source/target connections,
verifies step definitions, and detects missing error handling.
"""

from __future__ import annotations

import json
from typing import Any

from agent_data_pipeline_validator.models import PipelineFinding, PipelineValidationResult

# Known step types for validation
_KNOWN_STEP_TYPES = {
    "extract",
    "transform",
    "load",
    "filter",
    "map",
    "aggregate",
    "join",
    "sort",
    "validate",
    "deduplicate",
}


def validate_pipeline(config: str) -> PipelineValidationResult:
    """Validate an ETL pipeline configuration.

    Parses the config as JSON, then checks:
    1. Required top-level fields (name, source, target, steps)
    2. Source and target configuration
    3. Step definitions (type, name, config)
    4. Missing error handling in steps

    Args:
        config: Pipeline configuration as a JSON string.

    Returns:
        PipelineValidationResult with all findings and validation status.
    """
    findings: list[PipelineFinding] = []

    # Parse config
    try:
        pipeline = json.loads(config)
    except json.JSONDecodeError as e:
        return PipelineValidationResult(
            findings=[
                PipelineFinding(
                    severity="error",
                    category="structure",
                    location="<root>",
                    description=f"Invalid JSON: {e}",
                    remediation="Fix JSON syntax errors in the pipeline configuration",
                )
            ],
            is_valid=False,
        )

    if not isinstance(pipeline, dict):
        return PipelineValidationResult(
            findings=[
                PipelineFinding(
                    severity="error",
                    category="structure",
                    location="<root>",
                    description="Pipeline config must be a JSON object",
                    remediation="Ensure the top-level config is an object",
                )
            ],
            is_valid=False,
        )

    # 1. Structure validation
    pipeline_name = pipeline.get("name", "")
    findings.extend(_check_structure(pipeline))

    # 2. Source validation
    findings.extend(_check_source(pipeline.get("source")))

    # 3. Target validation
    findings.extend(_check_target(pipeline.get("target")))

    # 4. Steps validation
    steps = pipeline.get("steps", [])
    step_count = len(steps) if isinstance(steps, list) else 0
    findings.extend(_check_steps(steps))

    is_valid = not any(f.severity == "error" for f in findings)

    return PipelineValidationResult(
        findings=findings,
        is_valid=is_valid,
        step_count=step_count,
        pipeline_name=pipeline_name,
    )


def _check_structure(pipeline: dict) -> list[PipelineFinding]:
    """Check required top-level fields."""
    findings: list[PipelineFinding] = []

    if not pipeline.get("name"):
        findings.append(
            PipelineFinding(
                severity="error",
                category="structure",
                location="<root>",
                description="Missing required 'name' field",
                remediation="Add a 'name' field to identify the pipeline",
            )
        )

    if "source" not in pipeline:
        findings.append(
            PipelineFinding(
                severity="error",
                category="structure",
                location="<root>",
                description="Missing required 'source' field",
                remediation="Add a 'source' object defining the data source",
            )
        )

    if "target" not in pipeline:
        findings.append(
            PipelineFinding(
                severity="error",
                category="structure",
                location="<root>",
                description="Missing required 'target' field",
                remediation="Add a 'target' object defining the data destination",
            )
        )

    if "steps" not in pipeline:
        findings.append(
            PipelineFinding(
                severity="error",
                category="structure",
                location="<root>",
                description="Missing required 'steps' field",
                remediation="Add a 'steps' array with pipeline step definitions",
            )
        )
    elif isinstance(pipeline.get("steps"), list) and len(pipeline["steps"]) == 0:
        findings.append(
            PipelineFinding(
                severity="warning",
                category="structure",
                location="steps",
                description="'steps' array is empty — no processing defined",
                remediation="Define at least one step in the pipeline",
            )
        )

    return findings


def _check_source(source: Any) -> list[PipelineFinding]:
    """Validate source configuration."""
    findings: list[PipelineFinding] = []

    if source is None:
        return findings

    if not isinstance(source, dict):
        findings.append(
            PipelineFinding(
                severity="error",
                category="source",
                location="source",
                description="Source must be a JSON object",
                remediation="Define source as an object with 'type' and connection config",
            )
        )
        return findings

    if not source.get("type"):
        findings.append(
            PipelineFinding(
                severity="error",
                category="source",
                location="source",
                description="Missing source 'type' field",
                remediation="Specify the source type (e.g. 'database', 'file', 'api')",
            )
        )

    # Check for connection config
    has_connection = any(k in source for k in ("connection", "path", "url", "host", "endpoint"))
    if not has_connection and source.get("type"):
        findings.append(
            PipelineFinding(
                severity="warning",
                category="source",
                location="source",
                description="Source has no connection configuration",
                remediation="Add connection details (connection, path, url, or endpoint)",
            )
        )

    return findings


def _check_target(target: Any) -> list[PipelineFinding]:
    """Validate target configuration."""
    findings: list[PipelineFinding] = []

    if target is None:
        return findings

    if not isinstance(target, dict):
        findings.append(
            PipelineFinding(
                severity="error",
                category="target",
                location="target",
                description="Target must be a JSON object",
                remediation="Define target as an object with 'type' and connection config",
            )
        )
        return findings

    if not target.get("type"):
        findings.append(
            PipelineFinding(
                severity="error",
                category="target",
                location="target",
                description="Missing target 'type' field",
                remediation="Specify the target type (e.g. 'database', 'file', 'api')",
            )
        )

    has_connection = any(k in target for k in ("connection", "path", "url", "host", "endpoint"))
    if not has_connection and target.get("type"):
        findings.append(
            PipelineFinding(
                severity="warning",
                category="target",
                location="target",
                description="Target has no connection configuration",
                remediation="Add connection details (connection, path, url, or endpoint)",
            )
        )

    return findings


def _check_steps(steps: Any) -> list[PipelineFinding]:
    """Validate step definitions."""
    findings: list[PipelineFinding] = []

    if not isinstance(steps, list):
        return findings

    for i, step in enumerate(steps):
        location = f"steps[{i}]"

        if not isinstance(step, dict):
            findings.append(
                PipelineFinding(
                    severity="error",
                    category="step",
                    location=location,
                    description=f"Step {i} must be a JSON object",
                    remediation="Define each step as an object with 'type' and optional config",
                )
            )
            continue

        # Check step name
        if not step.get("name"):
            findings.append(
                PipelineFinding(
                    severity="warning",
                    category="step",
                    location=location,
                    description=f"Step {i} missing 'name' field",
                    remediation="Add a descriptive 'name' to each step",
                )
            )

        # Check step type
        step_type = step.get("type")
        if not step_type:
            findings.append(
                PipelineFinding(
                    severity="error",
                    category="step",
                    location=location,
                    description=f"Step {i} missing required 'type' field",
                    remediation="Add a 'type' to define the step operation",
                )
            )
        elif step_type not in _KNOWN_STEP_TYPES:
            findings.append(
                PipelineFinding(
                    severity="info",
                    category="step",
                    location=location,
                    description=f"Step {i} has unknown type '{step_type}'",
                    remediation=(
                        f"Consider using a known step type: {', '.join(sorted(_KNOWN_STEP_TYPES))}"
                    ),
                )
            )

        # Check error handling
        has_error_handling = any(
            k in step for k in ("on_error", "error_handler", "retry", "fallback")
        )
        if not has_error_handling:
            findings.append(
                PipelineFinding(
                    severity="info",
                    category="error_handling",
                    location=location,
                    description=f"Step {i} ({step.get('name', step_type or 'unnamed')}) "
                    "has no error handling configuration",
                    remediation="Consider adding 'on_error' or 'retry' configuration",
                )
            )

    return findings
