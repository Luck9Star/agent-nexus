"""ApiContractTesterAgent — API contract testing and validation specialist.

Two-phase pipeline:
  1. validate_contract() — validate OpenAPI spec structure and consistency
  2. generate_report()   — compile findings into a structured contract report

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_api_contract_tester.models import (
    ContractReport,
    ContractValidationResult,
)
from agent_api_contract_tester.tools.generate_report import (
    generate_report as _gen_report,
)
from agent_api_contract_tester.tools.validate_contract import (
    validate_contract as _validate,
)


class ApiContractTesterAgent:
    """API contract testing and validation specialist.

    This agent provides a two-phase pipeline for contract validation:
    Phase 1 (validate_contract) validates OpenAPI spec structure, schema
    references, and endpoint consistency. Phase 2 (generate_report) compiles
    all findings into a comprehensive report with a coverage score.

    Usage:
        agent = ApiContractTesterAgent()
        result = agent.validate_contract(spec_json_string)
        report = agent.generate_report(result.findings)
        print(report.error_count, report.coverage_score)
    """

    def validate_contract(self, spec_content: str) -> ContractValidationResult:
        """Phase 1: Validate an OpenAPI specification.

        Parses the spec content (JSON), validates required fields, checks
        schema references, and detects missing error responses.

        Args:
            spec_content: OpenAPI spec as a JSON string.

        Returns:
            ContractValidationResult with all findings and validation status.
        """
        return _validate(spec_content)

    def generate_report(self, findings: list) -> ContractReport:
        """Phase 2: Compile findings into a structured contract report.

        Aggregates all findings by severity, generates prioritized
        remediation recommendations, and computes a coverage score.

        Args:
            findings: List of ContractFinding objects or dicts.

        Returns:
            ContractReport with severity counts, recommendations, and score.
        """
        return _gen_report(findings)
