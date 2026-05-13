"""Unit tests for agent_nexus.models.permission module."""

import pytest
from pydantic import ValidationError

from agent_nexus.models.permission import (
    PathRule,
)

# ---------------------------------------------------------------------------
# PathRule
# ---------------------------------------------------------------------------


class TestPathRule:
    def test_missing_pattern_raises(self):
        with pytest.raises(ValidationError):
            PathRule()


# ---------------------------------------------------------------------------
# PermissionConfig
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# PermissionDecision
# ---------------------------------------------------------------------------


class TestPermissionDecision:
    pass
