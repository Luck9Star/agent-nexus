"""Composite Agent x API mode — semantic validation with real LLM calls."""

from __future__ import annotations

import pytest

from tests.capabilities.contracts.composite import ALL_COMPOSITE_CONTRACTS
from tests.capabilities.providers.api_provider import APIProvider
from tests.capabilities.providers.base import build_test_inputs
from tests.capabilities.validators.orchestration import OrchestrationValidator
from tests.capabilities.validators.semantic import SemanticValidator
from tests.capabilities.validators.structure import StructureValidator


@pytest.fixture(params=ALL_COMPOSITE_CONTRACTS, ids=lambda c: c.agent_name)
def contract(request):
    return request.param


@pytest.fixture
def api_provider():
    return APIProvider(model="anthropic:claude-haiku-4-5-20251001")


@pytest.fixture
def struct_validator():
    return StructureValidator()


@pytest.fixture
def orch_validator():
    return OrchestrationValidator()


@pytest.fixture
def semantic_validator():
    return SemanticValidator()


@pytest.mark.requires_api
@pytest.mark.capability_release
class TestCompositeAPI:
    """Composite Agent x API mode — Release layer full validation."""

    async def test_api_composite_responds(self, contract, api_provider):
        inputs = build_test_inputs(contract)
        result = await api_provider.invoke(contract, inputs)
        assert result.success, f"API call failed: {result.error}"

    async def test_api_composite_structure(self, contract, api_provider, struct_validator):
        inputs = build_test_inputs(contract)
        result = await api_provider.invoke(contract, inputs)
        assert result.success
        validation = struct_validator.validate(contract, result.raw_output)
        assert validation.score >= 0.5, f"Structure: {validation.failures}"

    async def test_api_composite_semantic(self, contract, api_provider, semantic_validator):
        inputs = build_test_inputs(contract)
        result = await api_provider.invoke(contract, inputs)
        assert result.success
        validation = await semantic_validator.validate(contract, result.raw_output)
        assert validation.score >= contract.quality_thresholds.score_threshold
