"""Unit tests for agent_nexus.models.evolution module."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from agent_nexus.models.evolution import (
    EvolutionContext,
    EvolutionMetrics,
    EvolutionType,
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)

# ---------------------------------------------------------------------------
# EvolutionType enum
# ---------------------------------------------------------------------------


class TestEvolutionType:
    def test_members(self):
        assert set(EvolutionType) == {
            EvolutionType.FIX,
            EvolutionType.DERIVED,
            EvolutionType.CAPTURED,
        }

    def test_values(self):
        assert EvolutionType.FIX == "fix"
        assert EvolutionType.DERIVED == "derived"
        assert EvolutionType.CAPTURED == "captured"

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            EvolutionType("unknown")


# ---------------------------------------------------------------------------
# SkillOrigin enum
# ---------------------------------------------------------------------------


class TestSkillOrigin:
    def test_members(self):
        assert set(SkillOrigin) == {
            SkillOrigin.IMPORTED,
            SkillOrigin.CAPTURED,
            SkillOrigin.DERIVED,
            SkillOrigin.FIXED,
        }

    def test_values(self):
        assert SkillOrigin.IMPORTED == "imported"
        assert SkillOrigin.CAPTURED == "captured"
        assert SkillOrigin.DERIVED == "derived"
        assert SkillOrigin.FIXED == "fixed"


# ---------------------------------------------------------------------------
# SkillLineage
# ---------------------------------------------------------------------------


class TestSkillLineage:
    def test_defaults(self):
        lin = SkillLineage()
        assert lin.origin is SkillOrigin.IMPORTED
        assert lin.generation == 0
        assert lin.parent_skill_ids == []
        assert lin.content_diff is None
        assert lin.content_snapshot is None

    def test_with_parents(self):
        lin = SkillLineage(
            origin=SkillOrigin.FIXED,
            generation=3,
            parent_skill_ids=["skill-1", "skill-2"],
            content_diff="@@ -1,3 +1,3 @@",
        )
        assert lin.origin is SkillOrigin.FIXED
        assert lin.generation == 3
        assert len(lin.parent_skill_ids) == 2
        assert lin.content_diff == "@@ -1,3 +1,3 @@"

    def test_captured_no_parents(self):
        lin = SkillLineage(
            origin=SkillOrigin.CAPTURED,
            generation=1,
            content_snapshot={"skill.md": "skill content here"},
        )
        assert lin.parent_skill_ids == []
        assert lin.content_snapshot == {"skill.md": "skill content here"}

    def test_frozen(self):
        lin = SkillLineage()
        with pytest.raises(ValidationError):
            lin.generation = 5

    def test_serialization_round_trip(self):
        lin = SkillLineage(
            origin=SkillOrigin.DERIVED,
            generation=2,
            parent_skill_ids=["p1"],
        )
        data = lin.model_dump()
        lin2 = SkillLineage(**data)
        assert lin2 == lin


# ---------------------------------------------------------------------------
# SkillRecord
# ---------------------------------------------------------------------------


class TestSkillRecord:
    def test_construction_with_required_fields(self):
        sr = SkillRecord(id="skill-1", name="fill-template")
        assert sr.id == "skill-1"
        assert sr.name == "fill-template"

    def test_defaults(self):
        sr = SkillRecord(id="skill-1", name="test")
        assert sr.version == "1.0.0"
        assert sr.lineage == SkillLineage()
        assert sr.directory == ""
        assert sr.is_active is True
        assert sr.total_selections == 0
        assert sr.total_applied == 0
        assert sr.total_completions == 0
        assert sr.total_fallbacks == 0
        assert isinstance(sr.first_seen, datetime)
        assert isinstance(sr.last_updated, datetime)

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

    def test_frozen(self):
        sr = SkillRecord(id="skill-1", name="test")
        with pytest.raises(ValidationError):
            sr.is_active = False

    def test_serialization_round_trip(self):
        sr = SkillRecord(
            id="skill-1",
            name="test",
            version="2.0.0",
            total_selections=50,
        )
        data = sr.model_dump()
        sr2 = SkillRecord(**data)
        assert sr2 == sr

    def test_json_serialization(self):
        sr = SkillRecord(id="skill-1", name="test")
        json_str = sr.model_dump_json()
        sr2 = SkillRecord.model_validate_json(json_str)
        assert sr2 == sr

    def test_inactive_skill(self):
        sr = SkillRecord(id="skill-1", name="test", is_active=False)
        assert sr.is_active is False

    def test_missing_required_fields(self):
        with pytest.raises(ValidationError):
            SkillRecord()


# ---------------------------------------------------------------------------
# EvolutionMetrics
# ---------------------------------------------------------------------------


class TestEvolutionMetrics:
    def test_defaults(self):
        em = EvolutionMetrics()
        assert em.total_selections == 0
        assert em.total_applied == 0
        assert em.total_completions == 0
        assert em.total_fallbacks == 0

    def test_with_values(self):
        em = EvolutionMetrics(
            total_selections=200,
            total_applied=160,
            total_completions=140,
            total_fallbacks=20,
        )
        assert em.total_selections == 200
        assert em.total_applied == 160

    def test_frozen(self):
        em = EvolutionMetrics()
        with pytest.raises(ValidationError):
            em.total_selections = 100

    def test_serialization_round_trip(self):
        em = EvolutionMetrics(total_selections=10, total_applied=8)
        data = em.model_dump()
        em2 = EvolutionMetrics(**data)
        assert em2 == em

    def test_json_serialization(self):
        em = EvolutionMetrics(total_selections=10)
        json_str = em.model_dump_json()
        em2 = EvolutionMetrics.model_validate_json(json_str)
        assert em2 == em

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
    def test_construction_with_required_fields(self):
        ec = EvolutionContext(agent_id="agent-1", task_id="task-1")
        assert ec.agent_id == "agent-1"
        assert ec.task_id == "task-1"
        assert ec.task_description == ""
        assert ec.task_completed is False
        assert ec.skill_ids_used == []
        assert ec.execution_output is None
        assert ec.execution_error is None

    def test_full_construction(self):
        ec = EvolutionContext(
            agent_id="agent-1",
            task_id="task-1",
            task_description="Fill the document",
            task_completed=True,
            skill_ids_used=["skill-1", "skill-2"],
            execution_output="Document filled successfully",
        )
        assert ec.task_completed is True
        assert len(ec.skill_ids_used) == 2

    def test_with_error(self):
        ec = EvolutionContext(
            agent_id="agent-1",
            task_id="task-1",
            execution_error="TimeoutError: execution timed out",
        )
        assert ec.execution_error is not None
        assert "Timeout" in ec.execution_error

    def test_with_skills_applied(self):
        ec = EvolutionContext(
            agent_id="a",
            task_id="t",
            skills_applied=["s1", "s2"],
            skills_fell_back=["s3"],
        )
        assert ec.skills_applied == ["s1", "s2"]
        assert ec.skills_fell_back == ["s3"]

    def test_defaults_empty_lists(self):
        ec = EvolutionContext(agent_id="a", task_id="t")
        assert ec.skills_applied == []
        assert ec.skills_fell_back == []

    def test_frozen(self):
        ec = EvolutionContext(agent_id="a", task_id="t")
        with pytest.raises(ValidationError):
            ec.agent_id = "b"

    def test_serialization_round_trip(self):
        ec = EvolutionContext(
            agent_id="a",
            task_id="t",
            task_completed=True,
        )
        data = ec.model_dump()
        ec2 = EvolutionContext(**data)
        assert ec2 == ec


# ---------------------------------------------------------------------------
# Validation constraint tests (iter22)
# ---------------------------------------------------------------------------


class TestSkillLineageGenerationValidation:
    """Field constraint tests for SkillLineage.generation."""

    def test_generation_rejects_negative(self):
        with pytest.raises(ValidationError, match="greater than or equal to 0"):
            SkillLineage(generation=-1)

    def test_generation_accepts_zero(self):
        lin = SkillLineage(generation=0)
        assert lin.generation == 0

    def test_generation_accepts_positive(self):
        lin = SkillLineage(generation=42)
        assert lin.generation == 42


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

    def test_completions_exceeds_applied_rejected(self):
        with pytest.raises(ValidationError, match="total_completions cannot exceed total_applied"):
            SkillRecord(
                id="s1",
                name="test",
                total_selections=10,
                total_applied=5,
                total_completions=8,
            )

    def test_fallbacks_exceeds_applied_rejected(self):
        with pytest.raises(ValidationError, match="total_fallbacks cannot exceed total_applied"):
            SkillRecord(
                id="s1",
                name="test",
                total_selections=10,
                total_applied=3,
                total_fallbacks=5,
            )

    def test_valid_counters_accepted(self):
        sr = SkillRecord(
            id="s1",
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

    def test_all_zero_counters_valid(self):
        sr = SkillRecord(id="s1", name="test")
        assert sr.total_selections == 0
        assert sr.total_applied == 0
        assert sr.total_completions == 0
        assert sr.total_fallbacks == 0

    def test_all_equal_counters_valid(self):
        """Edge case: applied equals selections, zero fallbacks is valid."""
        sr = SkillRecord(
            id="s1",
            name="test",
            total_selections=10,
            total_applied=10,
            total_completions=10,
            total_fallbacks=0,
        )
        assert sr.total_selections == 10

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

    def test_skill_record_empty_name(self):
        with pytest.raises(ValidationError):
            SkillRecord(id="s1", name="")

    def test_evolution_context_empty_agent_id(self):
        with pytest.raises(ValidationError):
            EvolutionContext(agent_id="", task_id="t1")

    def test_evolution_context_empty_task_id(self):
        with pytest.raises(ValidationError):
            EvolutionContext(agent_id="a1", task_id="")


# ---------------------------------------------------------------------------
# Counter invariant tests: total_applied + total_fallbacks <= total_selections
# ---------------------------------------------------------------------------


class TestSkillRecordCounterInvariant:
    """Counter invariants: applied <= selections, fallbacks <= applied, completions <= applied."""

    def test_valid_counters_pass(self):
        sr = SkillRecord(
            id="s1",
            name="test",
            total_selections=10,
            total_applied=8,
            total_fallbacks=3,
            total_completions=5,
        )
        assert sr.total_selections == 10
        assert sr.total_applied == 8
        assert sr.total_fallbacks == 3

    def test_rejected_applied_exceeds_selections(self):
        with pytest.raises(ValidationError, match="total_applied cannot exceed total_selections"):
            SkillRecord(
                id="s1",
                name="test",
                total_selections=5,
                total_applied=6,
                total_fallbacks=0,
            )

    def test_rejected_fallbacks_exceeds_applied(self):
        with pytest.raises(ValidationError, match="total_fallbacks cannot exceed total_applied"):
            SkillRecord(
                id="s1",
                name="test",
                total_selections=10,
                total_applied=3,
                total_fallbacks=4,
            )

    def test_rejected_completions_exceeds_applied(self):
        with pytest.raises(ValidationError, match="total_completions cannot exceed total_applied"):
            SkillRecord(
                id="s1",
                name="test",
                total_selections=10,
                total_applied=3,
                total_completions=4,
            )

    def test_zero_selections_requires_zero_applied(self):
        with pytest.raises(
            ValidationError,
            match="zero selections requires zero applied and zero fallbacks",
        ):
            SkillRecord(
                id="s1",
                name="test",
                total_selections=0,
                total_applied=1,
                total_fallbacks=0,
            )

    def test_zero_selections_with_fallbacks_rejected(self):
        with pytest.raises(
            ValidationError,
            match="zero selections requires zero applied and zero fallbacks",
        ):
            SkillRecord(
                id="s1",
                name="test",
                total_selections=0,
                total_applied=0,
                total_fallbacks=1,
            )

    def test_default_values_all_zero_pass(self):
        sr = SkillRecord(id="s1", name="test")
        assert sr.total_selections == 0
        assert sr.total_applied == 0
        assert sr.total_fallbacks == 0

    def test_applied_equals_selections_with_fallbacks(self):
        """Edge: all selections applied, some fell back."""
        sr = SkillRecord(
            id="s1",
            name="test",
            total_selections=10,
            total_applied=10,
            total_fallbacks=3,
        )
        assert sr.total_applied == sr.total_selections


class TestSkillRecordCompletionsFallbacksInvariant:
    """completions + fallbacks <= applied for SkillRecord."""

    def test_completions_plus_fallbacks_exceeds_applied(self):
        with pytest.raises(
            ValidationError,
            match="total_completions \\+ total_fallbacks cannot exceed total_applied",
        ):
            SkillRecord(
                id="s1",
                name="test",
                total_selections=10,
                total_applied=5,
                total_completions=3,
                total_fallbacks=3,
            )

    def test_completions_plus_fallbacks_equals_applied_valid(self):
        sr = SkillRecord(
            id="s1",
            name="test",
            total_selections=10,
            total_applied=8,
            total_completions=5,
            total_fallbacks=3,
        )
        assert sr.total_completions + sr.total_fallbacks == sr.total_applied


class TestEvolutionMetricsCounterInvariant:
    """Counter invariants for EvolutionMetrics."""

    def test_zero_selections_with_applied_rejected(self):
        with pytest.raises(
            ValidationError,
            match="zero selections requires zero applied and zero fallbacks",
        ):
            EvolutionMetrics(total_selections=0, total_applied=1, total_fallbacks=0)

    def test_zero_selections_with_fallbacks_rejected(self):
        with pytest.raises(
            ValidationError,
            match="zero selections requires zero applied and zero fallbacks",
        ):
            EvolutionMetrics(total_selections=0, total_applied=0, total_fallbacks=1)

    def test_zero_selections_all_zero_valid(self):
        em = EvolutionMetrics(total_selections=0, total_applied=0, total_fallbacks=0)
        assert em.total_selections == 0

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

    def test_completions_plus_fallbacks_equals_applied_valid(self):
        em = EvolutionMetrics(
            total_selections=10,
            total_applied=8,
            total_completions=5,
            total_fallbacks=3,
        )
        assert em.total_completions + em.total_fallbacks == em.total_applied

    def test_applied_exceeds_selections_rejected(self):
        with pytest.raises(ValidationError, match="total_applied cannot exceed total_selections"):
            EvolutionMetrics(total_selections=5, total_applied=6)
