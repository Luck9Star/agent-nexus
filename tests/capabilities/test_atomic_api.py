"""Atomic Agent x API mode — semantic validation with real LLM calls."""

from __future__ import annotations

import pytest

from tests.capabilities.contracts.atomic import KEY_ATOMIC_CONTRACTS
from tests.capabilities.providers.api_provider import APIProvider
from tests.capabilities.providers.base import build_test_inputs
from tests.capabilities.validators.semantic import SemanticValidator
from tests.capabilities.validators.structure import StructureValidator


@pytest.fixture(params=KEY_ATOMIC_CONTRACTS, ids=lambda c: c.agent_name)
def contract(request):
    return request.param


@pytest.fixture
def api_provider():
    return APIProvider(model="anthropic:claude-haiku-4-5-20251001")


@pytest.fixture
def struct_validator():
    return StructureValidator()


@pytest.fixture
def semantic_validator():
    return SemanticValidator()


@pytest.mark.requires_api
class TestAtomicAPI:
    """Atomic Agent x API mode — Release layer semantic validation."""

    async def test_api_call_succeeds(self, contract, api_provider):
        inputs = build_test_inputs(contract)
        result = await api_provider.invoke(contract, inputs)
        assert result.success, f"API call failed: {result.error}"
        assert result.raw_output is not None
        assert result.duration_ms > 0

    async def test_api_output_structure(self, contract, api_provider, struct_validator):
        inputs = build_test_inputs(contract)
        result = await api_provider.invoke(contract, inputs)
        assert result.success, f"API call failed: {result.error}"
        validation = struct_validator.validate(contract, result.raw_output)
        assert validation.score >= 0.5, (
            f"Structure score too low for '{contract.agent_name}': "
            f"{validation.score} — {validation.failures}"
        )

    async def test_api_output_semantic(self, contract, api_provider, semantic_validator):
        inputs = build_test_inputs(contract)
        result = await api_provider.invoke(contract, inputs)
        assert result.success, f"API call failed: {result.error}"
        validation = await semantic_validator.validate(contract, result.raw_output)
        assert validation.score >= contract.quality_thresholds.score_threshold, (
            f"Semantic score {validation.score} below threshold "
            f"{contract.quality_thresholds.score_threshold}: {validation.failures}"
        )
