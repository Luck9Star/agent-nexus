"""Composite Agent capability contracts."""

from __future__ import annotations

from tests.capabilities.contracts.schema import (
    CapabilityContract,
    InputSpec,
    OutputSpec,
    QualityThresholds,
)

CICD_QUALITY_GATE = CapabilityContract(
    agent_name="cicd-quality-gate",
    agent_type="composite",
    description="CI/CD multi-model parallel quality gate",
    required_inputs={
        "code_path": InputSpec(
            type="str",
            description="Code path to check",
            examples=["src/agent_nexus/"],
        ),
    },
    optional_inputs={
        "config": InputSpec(
            type="dict",
            description="Gate config",
            examples=['{"security_threshold": 80, "review_threshold": 70}'],
            required=False,
        ),
    },
    output_schema={
        "checks": OutputSpec(type="list", min_length=1),
        "overall_passed": OutputSpec(type="bool"),
        "gate_score": OutputSpec(type="float"),
        "blockers": OutputSpec(type="list"),
        "warnings": OutputSpec(type="list"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=100,
        required_keywords=["check", "gate"],
        score_threshold=0.7,
    ),
    cli_method="run",
)

COMPETITIVE_INTELLIGENCE = CapabilityContract(
    agent_name="competitive-intelligence-briefing",
    agent_type="composite",
    description="Competitive intelligence briefing — sequential chain",
    required_inputs={
        "topic": InputSpec(
            type="str",
            description="Analysis topic",
            examples=["AI Agent market competitive landscape"],
        ),
    },
    optional_inputs={},
    output_schema={
        "briefing": OutputSpec(type="str", min_length=50),
        "sources": OutputSpec(type="list"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=100,
        required_keywords=["intelligence", "competitor"],
        score_threshold=0.6,
    ),
    cli_method="run",
)

DOCUMENT_COMPLIANCE = CapabilityContract(
    agent_name="document-compliance-gateway",
    agent_type="composite",
    description="Document compliance gateway — full parallel + conflict detection",
    required_inputs={
        "document_path": InputSpec(
            type="str",
            description="Document path",
            examples=["docs/"],
        ),
    },
    optional_inputs={},
    output_schema={
        "checks": OutputSpec(type="list", min_length=1),
        "compliant": OutputSpec(type="bool"),
        "issues": OutputSpec(type="list"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=80,
        required_keywords=["compliance"],
        score_threshold=0.6,
    ),
    cli_method="run",
)

FEATURE_DELIVERY = CapabilityContract(
    agent_name="feature-delivery-pipeline",
    agent_type="composite",
    description="Feature delivery pipeline — sequential to parallel",
    required_inputs={
        "feature_spec": InputSpec(
            type="str",
            description="Feature specification",
            examples=["Implement user login functionality"],
        ),
    },
    optional_inputs={},
    output_schema={
        "artifacts": OutputSpec(type="list", min_length=1),
        "status": OutputSpec(type="str"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=80,
        required_keywords=["feature", "delivery"],
        score_threshold=0.6,
    ),
    cli_method="run",
)

PRODUCT_DOCUMENTATION = CapabilityContract(
    agent_name="product-documentation-suite",
    agent_type="composite",
    description="Product documentation suite — parallel + sequential aggregation",
    required_inputs={
        "project_path": InputSpec(
            type="str",
            description="Project path",
            examples=["src/agent_nexus/"],
        ),
    },
    optional_inputs={},
    output_schema={
        "documents": OutputSpec(type="list", min_length=1),
        "summary": OutputSpec(type="str", min_length=10),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=100,
        required_keywords=["document"],
        score_threshold=0.6,
    ),
    cli_method="run",
)

ALL_COMPOSITE_CONTRACTS: list[CapabilityContract] = [
    CICD_QUALITY_GATE,
    COMPETITIVE_INTELLIGENCE,
    DOCUMENT_COMPLIANCE,
    FEATURE_DELIVERY,
    PRODUCT_DOCUMENTATION,
]
