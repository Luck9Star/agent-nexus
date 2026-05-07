"""Atomic Agent x CLI mode — structure validation via local_adapter."""

from __future__ import annotations

import pytest

from tests.capabilities.contracts.atomic import ALL_ATOMIC_CONTRACTS
from tests.capabilities.providers.base import build_test_inputs
from tests.capabilities.providers.cli_provider import CLIProvider
from tests.capabilities.validators.structure import StructureValidator


@pytest.fixture(params=ALL_ATOMIC_CONTRACTS, ids=lambda c: c.agent_name)
def contract(request):
    return request.param


@pytest.fixture
def cli_provider():
    return CLIProvider(timeout=10.0)


@pytest.fixture
def validator():
    return StructureValidator()


class TestAtomicCLI:
    """Atomic Agent x CLI mode — CI layer structure validation."""

    def test_agent_local_adapter_responds(self, contract, cli_provider):
        inputs = build_test_inputs(contract)
        result = cli_provider.invoke_sync(contract, inputs)
        assert result.success, f"Agent '{contract.agent_name}' local_adapter failed: {result.error}"

    def test_agent_output_has_required_fields(self, contract, cli_provider, validator):
        inputs = build_test_inputs(contract)
        result = cli_provider.invoke_sync(contract, inputs)
        assert result.success, f"Agent failed: {result.error}"
        validation = validator.validate(contract, result.raw_output)
        assert validation.passed, (
            f"Agent '{contract.agent_name}' validation failed: {validation.failures}"
        )

    def test_agent_output_type_correct(self, contract, cli_provider, validator):
        inputs = build_test_inputs(contract)
        result = cli_provider.invoke_sync(contract, inputs)
        assert result.success, f"Agent failed: {result.error}"
        validation = validator.validate(contract, result.raw_output)
        type_failures = [f for f in validation.failures if "wrong type" in f]
        assert len(type_failures) == 0, f"Type mismatches: {type_failures}"
