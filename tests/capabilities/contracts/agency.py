"""Agency Pipeline capability contracts."""

from __future__ import annotations

from tests.capabilities.contracts.schema import (
    CapabilityContract,
    InputSpec,
    OutputSpec,
    QualityThresholds,
)

AGENCY_PIPELINE = CapabilityContract(
    agent_name="agency-pipeline",
    agent_type="agency",
    description="LLM-powered expert orchestration pipeline",
    required_inputs={
        "task": InputSpec(
            type="str",
            description="User task description",
            examples=["Analyze code security of the agent-nexus project"],
        ),
    },
    optional_inputs={
        "vendor_path": InputSpec(
            type="str",
            description="Agency agents vendor path",
            examples=["vendor/agency-agents"],
            required=False,
        ),
        "allowlist": InputSpec(
            type="str",
            description="Expert allowlist YAML path",
            examples=["config/agency-agents-minimal.allowlist.yaml"],
            required=False,
        ),
        "max_parallel": InputSpec(
            type="int",
            description="Max parallel executions",
            examples=["3"],
            required=False,
        ),
    },
    output_schema={
        "plan": OutputSpec(type="dict"),
        "artifacts": OutputSpec(type="list"),
        "integration": OutputSpec(type="str"),
        "qa_score": OutputSpec(type="float"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=200,
        required_keywords=["recommendation", "analysis"],
        score_threshold=0.6,
    ),
    cli_method="run-composition",
)

ALL_AGENCY_CONTRACTS: list[CapabilityContract] = [AGENCY_PIPELINE]
