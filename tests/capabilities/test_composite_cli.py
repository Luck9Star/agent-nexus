"""Composite Agent x CLI mode — DAG orchestration + structure validation."""

from __future__ import annotations

import pytest

from tests.capabilities.contracts.composite import ALL_COMPOSITE_CONTRACTS
from tests.capabilities.providers.base import build_test_inputs
from tests.capabilities.providers.cli_provider import CLIProvider
from tests.capabilities.validators.orchestration import OrchestrationValidator
from tests.capabilities.validators.structure import StructureValidator


@pytest.fixture(params=ALL_COMPOSITE_CONTRACTS, ids=lambda c: c.agent_name)
def contract(request):
    return request.param


@pytest.fixture
def cli_provider():
    return CLIProvider(timeout=15.0)


@pytest.fixture
def struct_validator():
    return StructureValidator()


@pytest.fixture
def orch_validator():
    return OrchestrationValidator()


class TestCompositeCLI:
    """Composite Agent x CLI mode — DAG orchestration and structure validation."""

    def test_composite_agent_responds(self, contract, cli_provider):
        inputs = build_test_inputs(contract)
        result = cli_provider.invoke_sync(contract, inputs)
        assert result.success, f"Composite '{contract.agent_name}' failed: {result.error}"

    def test_composite_output_structure(self, contract, cli_provider, struct_validator):
        inputs = build_test_inputs(contract)
        result = cli_provider.invoke_sync(contract, inputs)
        assert result.success, f"Agent failed: {result.error}"
        validation = struct_validator.validate(contract, result.raw_output)
        assert validation.passed, (
            f"Composite '{contract.agent_name}' structure: {validation.failures}"
        )

    def test_composite_orchestration(self, contract, cli_provider, orch_validator):
        inputs = build_test_inputs(contract)
        result = cli_provider.invoke_sync(contract, inputs)
        assert result.success, f"Agent failed: {result.error}"
        validation = orch_validator.validate(contract, result.raw_output)
        assert validation.passed, (
            f"Composite '{contract.agent_name}' orchestration: {validation.failures}"
        )
