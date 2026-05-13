"""Unit tests for agent_nexus.models.evolution module."""

import pytest
from pydantic import ValidationError

from agent_nexus.models.evolution import (
    EvolutionContext,
    EvolutionMetrics,
    SkillRecord,
)

# ---------------------------------------------------------------------------
# SkillRecord
# ---------------------------------------------------------------------------


class TestSkillRecord:
    pass


# ---------------------------------------------------------------------------
# EvolutionMetrics
# ---------------------------------------------------------------------------


class TestEvolutionMetrics:
    def test_rejects_completions_exceeding_applied(self):
        with pytest.raises(ValidationError, match="total_completions cannot exceed total_applied"):
            EvolutionMetrics(total_selections=10, total_applied=5, total_completions=8)

    def test_rejects_fallbacks_exceeding_applied(self):
        with pytest.raises(ValidationError, match="total_fallbacks cannot exceed total_applied"):
            EvolutionMetrics(total_selections=10, total_applied=3, total_fallbacks=5)

    def test_rejects_applied_exceeding_selections(self):
        with pytest.raises(ValidationError, match="total_applied cannot exceed total_selections"):
            EvolutionMetrics(total_selections=5, total_applied=10)

    def test_rejects_nonzero_applied_with_zero_selections(self):
        with pytest.raises(ValidationError, match="counter invariant"):
            EvolutionMetrics(total_selections=0, total_applied=1)


# ---------------------------------------------------------------------------
# EvolutionContext
# ---------------------------------------------------------------------------


class TestEvolutionContext:
    pass


# ---------------------------------------------------------------------------
# Validation constraint tests (iter22)
# ---------------------------------------------------------------------------


class TestSkillRecordCounterValidation:
    """Cross-field validation tests for SkillRecord counters."""

    def test_applied_exceeds_selections_rejected(self):
        with pytest.raises(ValidationError, match="total_applied"):
            SkillRecord(
                id="s1",
                name="test",
                total_selections=5,
                total_applied=10,
            )

    def test_completions_plus_fallbacks_exceeds_applied_rejected(self):
        """completions + fallbacks cannot exceed applied."""
        with pytest.raises(
            ValidationError,
            match="total_completions \\+ total_fallbacks cannot exceed total_applied",
        ):
            SkillRecord(
                id="s1",
                name="test",
                total_selections=10,
                total_applied=10,
                total_completions=6,
                total_fallbacks=5,
            )


# ---------------------------------------------------------------------------
# min_length=1 validation tests (iter30)
# ---------------------------------------------------------------------------


class TestMinLengthEvolution:
    """Required string fields in evolution models reject empty strings."""

    def test_skill_record_empty_id(self):
        with pytest.raises(ValidationError):
            SkillRecord(id="", name="skill")

    def test_evolution_context_empty_agent_id(self):
        with pytest.raises(ValidationError):
            EvolutionContext(agent_id="", task_id="t1")


# ---------------------------------------------------------------------------
# Counter invariant tests: total_applied + total_fallbacks <= total_selections
