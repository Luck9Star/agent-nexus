"""Unit tests for agent_nexus.models.evolution module."""


import pytest
from pydantic import ValidationError

from agent_nexus.models.evolution import (
    EvolutionContext,
    EvolutionMetrics,
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)

# ---------------------------------------------------------------------------
# SkillRecord
# ---------------------------------------------------------------------------


class TestSkillRecord:
    def test_with_quality_counters(self):
        sr = SkillRecord(
            id="skill-1",
            name="test",
            total_selections=100,
            total_applied=80,
            total_completions=70,
            total_fallbacks=10,
        )
        assert sr.total_selections == 100
        assert sr.total_applied == 80
        assert sr.total_completions == 70
        assert sr.total_fallbacks == 10

    def test_with_lineage(self):
        lineage = SkillLineage(origin=SkillOrigin.FIXED, generation=2)
        sr = SkillRecord(id="skill-1", name="test", lineage=lineage)
        assert sr.lineage.origin is SkillOrigin.FIXED
        assert sr.lineage.generation == 2


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
    def test_with_error(self):
        ec = EvolutionContext(
            agent_id="agent-1",
            task_id="task-1",
            execution_error="TimeoutError: execution timed out",
        )
        assert ec.execution_error is not None
        assert "Timeout" in ec.execution_error


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
# ---------------------------------------------------------------------------


class TestEvolutionMetricsCounterInvariant:
    """Counter invariants for EvolutionMetrics."""

    def test_zero_selections_with_applied_rejected(self):
        with pytest.raises(
            ValidationError,
            match="zero selections requires zero applied and zero fallbacks",
        ):
            EvolutionMetrics(total_selections=0, total_applied=1, total_fallbacks=0)

    def test_completions_plus_fallbacks_exceeds_applied(self):
        with pytest.raises(
            ValidationError,
            match="total_completions \\+ total_fallbacks cannot exceed total_applied",
        ):
            EvolutionMetrics(
                total_selections=10,
                total_applied=5,
                total_completions=3,
                total_fallbacks=3,
            )

    def test_applied_exceeds_selections_rejected(self):
        with pytest.raises(ValidationError, match="total_applied cannot exceed total_selections"):
            EvolutionMetrics(total_selections=5, total_applied=6)
