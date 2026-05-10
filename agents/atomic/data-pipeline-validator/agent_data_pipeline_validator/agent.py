"""DataPipelineValidatorAgent — ETL pipeline validation specialist.

Two-phase pipeline:
  1. validate_pipeline() — validate ETL pipeline configuration and data flow
  2. generate_report()   — compile findings into a structured report

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_data_pipeline_validator.models import (
    PipelineReport,
    PipelineValidationResult,
)
from agent_data_pipeline_validator.tools.generate_report import (
    generate_report as _gen_report,
)
from agent_data_pipeline_validator.tools.validate_pipeline import (
    validate_pipeline as _validate,
)


class DataPipelineValidatorAgent:
    """ETL pipeline validation specialist.

    This agent provides a two-phase pipeline for ETL validation:
    Phase 1 (validate_pipeline) validates the pipeline configuration
    structure, data source/target, and step definitions. Phase 2
    (generate_report) compiles all findings into a comprehensive report.

    Usage:
        agent = DataPipelineValidatorAgent()
        result = agent.validate_pipeline(config_json)
        report = agent.generate_report(result.findings)
        print(report.error_count, report.step_count)
    """

    def validate_pipeline(self, config: str) -> PipelineValidationResult:
        """Phase 1: Validate an ETL pipeline configuration.

        Parses the config as JSON, validates required fields, checks
        source/target connections, and verifies step definitions.

        Args:
            config: Pipeline configuration as a JSON string.

        Returns:
            PipelineValidationResult with all findings and validation status.
        """
        return _validate(config)

    def generate_report(self, findings: list) -> PipelineReport:
        """Phase 2: Compile findings into a structured pipeline report.

        Aggregates all findings by severity and generates prioritized
        remediation recommendations.

        Args:
            findings: List of PipelineFinding objects or dicts.

        Returns:
            PipelineReport with severity counts and recommendations.
        """
        return _gen_report(findings)
