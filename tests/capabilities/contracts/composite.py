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
        "checks": OutputSpec(type="list", min_length=0),
        "overall_passed": OutputSpec(type="bool"),
        "gate_score": OutputSpec(type="float"),
        "blockers": OutputSpec(type="list"),
        "warnings": OutputSpec(type="list"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=100,
        required_keywords=["check"],
        score_threshold=0.7,
    ),
    cli_method="run_gate",
)

COMPETITIVE_INTELLIGENCE = CapabilityContract(
    agent_name="competitive-intelligence-briefing",
    agent_type="composite",
    description="Competitive intelligence briefing — sequential chain",
    required_inputs={
        "query": InputSpec(
            type="str",
            description="Research query",
            examples=["AI Agent market competitive landscape"],
        ),
    },
    optional_inputs={
        "framework": InputSpec(
            type="str",
            description="Analysis framework",
            examples=["porter"],
            required=False,
        ),
    },
    output_schema={
        "analysis": OutputSpec(type="dict"),
        "success": OutputSpec(type="bool"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=100,
        required_keywords=["analysis"],
        score_threshold=0.6,
    ),
    cli_method="generate_briefing",
)

DOCUMENT_COMPLIANCE = CapabilityContract(
    agent_name="document-compliance-gateway",
    agent_type="composite",
    description="Document compliance gateway — full parallel + conflict detection",
    required_inputs={
        "document": InputSpec(
            type="str",
            description="Document text to check",
            examples=["This agreement is governed by the laws of..."],
        ),
    },
    optional_inputs={
        "jurisdictions": InputSpec(
            type="list",
            description="Jurisdiction codes",
            examples=['["CN", "US"]'],
            required=False,
        ),
    },
    output_schema={
        "checks": OutputSpec(type="list", min_length=0),
        "overall_score": OutputSpec(type="float"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=80,
        required_keywords=["compliance"],
        score_threshold=0.6,
    ),
    cli_method="check_compliance",
)

FEATURE_DELIVERY = CapabilityContract(
    agent_name="feature-delivery-pipeline",
    agent_type="composite",
    description="Feature delivery pipeline — sequential to parallel",
    required_inputs={
        "spec": InputSpec(
            type="str",
            description="Requirement specification",
            examples=["Implement user login functionality"],
        ),
    },
    optional_inputs={},
    output_schema={
        "artifacts": OutputSpec(type="dict"),
        "success": OutputSpec(type="bool"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=80,
        required_keywords=[],
        score_threshold=0.6,
    ),
    cli_method="run_pipeline",
)

PRODUCT_DOCUMENTATION = CapabilityContract(
    agent_name="product-documentation-suite",
    agent_type="composite",
    description="Product documentation suite — parallel + sequential aggregation",
    required_inputs={
        "code_path": InputSpec(
            type="str",
            description="Source code path",
            examples=["src/agent_nexus/"],
        ),
    },
    optional_inputs={
        "target_langs": InputSpec(
            type="list",
            description="Target language codes",
            examples=["en"],
            required=False,
        ),
    },
    output_schema={
        "artifacts": OutputSpec(type="list", min_length=0),
        "coverage_score": OutputSpec(type="float"),
        "success": OutputSpec(type="bool"),
    },
    output_format="structured",
    quality_thresholds=QualityThresholds(
        min_output_length=100,
        required_keywords=[],
        score_threshold=0.6,
    ),
    cli_method="generate_docs",
)

ALL_COMPOSITE_CONTRACTS: list[CapabilityContract] = [
    CICD_QUALITY_GATE,
    COMPETITIVE_INTELLIGENCE,
    DOCUMENT_COMPLIANCE,
    FEATURE_DELIVERY,
    PRODUCT_DOCUMENTATION,
]
