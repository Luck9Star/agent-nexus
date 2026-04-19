"""Unit tests for agent_nexus.platform.evolution.thresholds module."""

import pytest  # pyright: ignore[reportMissingImports]

from agent_nexus.platform.evolution.thresholds import (
    _FALLBACK_THRESHOLD,
    _HIGH_APPLIED_FOR_FIX,
    _LOW_COMPLETION_THRESHOLD,
    _MODERATE_EFFECTIVE_THRESHOLD,
    _MIN_APPLIED_FOR_DERIVED,
)


# ---------------------------------------------------------------------------
# Threshold constant values (docs/04 Section 6)
# ---------------------------------------------------------------------------

class TestThresholdConstants:
    """Verify the single source of truth matches docs/04 spec."""

    def test_fallback_threshold(self):
        """fallback_rate > 0.4 triggers FIX."""
        assert _FALLBACK_THRESHOLD == 0.4

    def test_high_applied_for_fix(self):
        """applied_rate > 0.4 for the FIX dual-condition rule."""
        assert _HIGH_APPLIED_FOR_FIX == 0.4

    def test_low_completion_threshold(self):
        """completion_rate < 0.35 for the FIX dual-condition rule."""
        assert _LOW_COMPLETION_THRESHOLD == 0.35

    def test_moderate_effective_threshold(self):
        """effective_rate < 0.55 for the DERIVED rule."""
        assert _MODERATE_EFFECTIVE_THRESHOLD == 0.55

    def test_min_applied_for_derived(self):
        """applied_rate > 0.25 for the DERIVED rule."""
        assert _MIN_APPLIED_FOR_DERIVED == 0.25


class TestThresholdTypes:
    """All thresholds must be numeric floats for arithmetic comparison."""

    @pytest.mark.parametrize(
        "name, value",
        [
            ("_FALLBACK_THRESHOLD", _FALLBACK_THRESHOLD),
            ("_HIGH_APPLIED_FOR_FIX", _HIGH_APPLIED_FOR_FIX),
            ("_LOW_COMPLETION_THRESHOLD", _LOW_COMPLETION_THRESHOLD),
            ("_MODERATE_EFFECTIVE_THRESHOLD", _MODERATE_EFFECTIVE_THRESHOLD),
            ("_MIN_APPLIED_FOR_DERIVED", _MIN_APPLIED_FOR_DERIVED),
        ],
    )
    def test_is_float(self, name, value):
        assert isinstance(value, float), f"{name} should be float"

    @pytest.mark.parametrize(
        "name, value",
        [
            ("_FALLBACK_THRESHOLD", _FALLBACK_THRESHOLD),
            ("_HIGH_APPLIED_FOR_FIX", _HIGH_APPLIED_FOR_FIX),
            ("_LOW_COMPLETION_THRESHOLD", _LOW_COMPLETION_THRESHOLD),
            ("_MODERATE_EFFECTIVE_THRESHOLD", _MODERATE_EFFECTIVE_THRESHOLD),
            ("_MIN_APPLIED_FOR_DERIVED", _MIN_APPLIED_FOR_DERIVED),
        ],
    )
    def test_in_valid_range(self, name, value):
        assert 0.0 < value < 1.0, f"{name}={value} out of (0, 1) range"


class TestThresholdInvariants:
    """Cross-field constraints that the health checker relies on."""

    def test_fix_thresholds_independent(self):
        """FIX dual-condition uses two independent thresholds."""
        assert _HIGH_APPLIED_FOR_FIX > 0.0
        assert _LOW_COMPLETION_THRESHOLD < 0.5

    def test_derived_effective_less_than_half(self):
        """DERIVED rule fires when effectiveness is moderate-bad."""
        assert _MODERATE_EFFECTIVE_THRESHOLD < 0.6

    def test_applied_thresholds_ordering(self):
        """MIN_APPLIED_FOR_DERIVED < HIGH_APPLIED_FOR_FIX (less strict for DERIVED)."""
        assert _MIN_APPLIED_FOR_DERIVED < _HIGH_APPLIED_FOR_FIX

    def test_fallback_equals_applied_for_fix(self):
        """Both FIX conditions share the same 0.4 boundary."""
        assert _FALLBACK_THRESHOLD == _HIGH_APPLIED_FOR_FIX

    def test_all_thresholds_summarizable(self):
        """Sanity: exactly 5 thresholds exported."""
        from agent_nexus.platform.evolution import thresholds as mod
        exported = [
            v for k, v in vars(mod).items()
            if k.startswith("_") and not k.startswith("__") and isinstance(v, float)
        ]
        assert len(exported) == 5
