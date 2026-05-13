"""Unit tests for defaults module: constants, built-in providers, and tier map.

Focuses on structural correctness of DEFAULT_PROVIDERS, MODEL_TIER_MAP,
ENV_VAR_OVERRIDES, and path constants.
"""

from __future__ import annotations

from agent_nexus.models.agent import ModelTier
from agent_nexus.models.config import ProviderApiType
from agent_nexus.platform.config import (
    DEFAULT_PROVIDERS,
    MODEL_TIER_MAP,
)

# ============================================================================
# DEFAULT_PROVIDERS
# ============================================================================


class TestDefaultProviders:
    """Verify structure and types of built-in provider definitions."""

    def test_anthropic_uses_messages_api(self) -> None:
        anthropic = DEFAULT_PROVIDERS["anthropic"]
        assert anthropic["api"] == ProviderApiType.ANTHROPIC_MESSAGES


# ============================================================================
# MODEL_TIER_MAP
# ============================================================================


class TestModelTierMap:
    """Verify MODEL_TIER_MAP completeness and format."""

    def test_all_tiers_mapped(self) -> None:
        for tier in ModelTier:
            assert tier in MODEL_TIER_MAP, f"Tier {tier} missing from MODEL_TIER_MAP"


# ============================================================================
# ENV_VAR_OVERRIDES
# ============================================================================
