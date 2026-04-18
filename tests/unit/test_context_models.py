"""Unit tests for agent_nexus.models.context module."""

import json

import pytest
from pydantic import ValidationError

from agent_nexus.models.context import (
    ContextBudget,
    ContextBudgetLogEntry,
    ContextLevel,
    TokenUsage,
)


# ---------------------------------------------------------------------------
# ContextLevel enum
# ---------------------------------------------------------------------------

class TestContextLevel:
    def test_members(self):
        assert set(ContextLevel) == {
            ContextLevel.L0_IDENTITY,
            ContextLevel.L1_EXECUTION,
            ContextLevel.L2_EXTENDED,
            ContextLevel.L3_RUNTIME,
        }

    def test_values(self):
        assert ContextLevel.L0_IDENTITY == 0
        assert ContextLevel.L1_EXECUTION == 1
        assert ContextLevel.L2_EXTENDED == 2
        assert ContextLevel.L3_RUNTIME == 3

    def test_ordering(self):
        assert ContextLevel.L0_IDENTITY < ContextLevel.L1_EXECUTION
        assert ContextLevel.L1_EXECUTION < ContextLevel.L2_EXTENDED
        assert ContextLevel.L2_EXTENDED < ContextLevel.L3_RUNTIME

    def test_is_int_enum(self):
        assert isinstance(ContextLevel.L0_IDENTITY, int)
        assert ContextLevel.L0_IDENTITY == 0


# ---------------------------------------------------------------------------
# ContextBudget
# ---------------------------------------------------------------------------

class TestContextBudget:
    def test_defaults_match_docs(self):
        cb = ContextBudget()
        assert cb.l0_max == 800
        assert cb.l1_max == 3000
        assert cb.bootstrap_max == 5000
        assert cb.single_file_max == 8000
        assert cb.compaction_trigger == 0.8
        assert cb.compaction_target == 0.4
        assert cb.session_hard_ceiling == 0.95
        assert cb.forced_truncate_threshold == 0.9
        assert cb.min_turns_between_compactions == 5
        assert cb.consecutive_compaction_alert == 3

    def test_custom_values(self):
        cb = ContextBudget(
            l0_max=1000,
            l1_max=5000,
            bootstrap_max=8000,
        )
        assert cb.l0_max == 1000
        assert cb.l1_max == 5000

    def test_frozen(self):
        cb = ContextBudget()
        with pytest.raises(ValidationError):
            cb.l0_max = 2000

    def test_serialization_round_trip(self):
        cb = ContextBudget(l0_max=1200, l1_max=4000)
        data = cb.model_dump()
        cb2 = ContextBudget(**data)
        assert cb2 == cb

    def test_json_serialization(self):
        cb = ContextBudget()
        json_str = cb.model_dump_json()
        cb2 = ContextBudget.model_validate_json(json_str)
        assert cb2 == cb


# ---------------------------------------------------------------------------
# TokenUsage
# ---------------------------------------------------------------------------

class TestTokenUsage:
    def test_defaults(self):
        tu = TokenUsage()
        assert tu.prompt_tokens == 0
        assert tu.completion_tokens == 0
        assert tu.total_tokens == 0
        assert tu.compaction_count == 0
        assert tu.last_compaction_turn == 0

    def test_mutable(self):
        """TokenUsage is explicitly mutable (frozen=False)."""
        tu = TokenUsage()
        tu.prompt_tokens = 100
        tu.completion_tokens = 50
        tu.total_tokens = 150
        assert tu.prompt_tokens == 100
        assert tu.total_tokens == 150

    def test_with_initial_values(self):
        tu = TokenUsage(prompt_tokens=500, completion_tokens=200, total_tokens=700)
        assert tu.prompt_tokens == 500
        assert tu.completion_tokens == 200
        assert tu.total_tokens == 700

    def test_serialization_round_trip(self):
        tu = TokenUsage(prompt_tokens=100, total_tokens=150)
        data = tu.model_dump()
        tu2 = TokenUsage(**data)
        assert tu2 == tu

    def test_json_serialization(self):
        tu = TokenUsage(prompt_tokens=200, completion_tokens=100)
        json_str = tu.model_dump_json()
        tu2 = TokenUsage.model_validate_json(json_str)
        assert tu2 == tu


# ---------------------------------------------------------------------------
# TokenUsage.check_budget()
# ---------------------------------------------------------------------------

class TestTokenUsageCheckBudget:
    def test_within_budget(self):
        tu = TokenUsage(total_tokens=500)
        assert tu.check_budget(context_window=10000) is None

    def test_compaction_threshold(self):
        """81% usage triggers 'compaction' alert."""
        tu = TokenUsage(total_tokens=810)
        assert tu.check_budget(context_window=1000) == "compaction"

    def test_compaction_exact_boundary(self):
        """81% triggers compaction, 80% does not."""
        tu_80 = TokenUsage(total_tokens=800)
        assert tu_80.check_budget(context_window=1000) is None

        tu_81 = TokenUsage(total_tokens=801)
        assert tu_81.check_budget(context_window=1000) == "compaction"

    def test_forced_truncate_threshold(self):
        """91% usage triggers 'forced_truncate' alert."""
        tu = TokenUsage(total_tokens=910)
        assert tu.check_budget(context_window=1000) == "forced_truncate"

    def test_hard_ceiling_threshold(self):
        """96% usage triggers 'hard_ceiling' alert."""
        tu = TokenUsage(total_tokens=960)
        assert tu.check_budget(context_window=1000) == "hard_ceiling"

    def test_priority_hard_ceiling_over_others(self):
        """hard_ceiling is checked first, even if other thresholds are met."""
        tu = TokenUsage(total_tokens=990)
        result = tu.check_budget(context_window=1000)
        assert result == "hard_ceiling"

    def test_zero_context_window(self):
        tu = TokenUsage(total_tokens=100)
        assert tu.check_budget(context_window=0) is None

    def test_negative_context_window(self):
        tu = TokenUsage(total_tokens=100)
        assert tu.check_budget(context_window=-1) is None

    def test_zero_tokens(self):
        tu = TokenUsage(total_tokens=0)
        assert tu.check_budget(context_window=10000) is None


# ---------------------------------------------------------------------------
# ContextBudgetLogEntry
# ---------------------------------------------------------------------------

class TestContextBudgetLogEntry:
    def test_construction_with_required_fields(self):
        entry = ContextBudgetLogEntry(
            log_id="log-1",
            agent_id="agent-1",
            session_id="sess-1",
        )
        assert entry.log_id == "log-1"
        assert entry.agent_id == "agent-1"
        assert entry.session_id == "sess-1"

    def test_defaults(self):
        entry = ContextBudgetLogEntry(
            log_id="log-1",
            agent_id="agent-1",
            session_id="sess-1",
        )
        assert entry.turn_number is None
        assert entry.prompt_tokens == 0
        assert entry.completion_tokens == 0
        assert entry.layer0_tokens == 0
        assert entry.layer1_tokens == 0
        assert entry.total_tokens == 0
        assert entry.compaction_triggered is False
        assert entry.timestamp.tzinfo is not None  # timezone-aware

    def test_full_construction(self):
        entry = ContextBudgetLogEntry(
            log_id="log-1",
            agent_id="agent-1",
            session_id="sess-1",
            turn_number=5,
            prompt_tokens=500,
            completion_tokens=200,
            layer0_tokens=300,
            layer1_tokens=150,
            total_tokens=700,
            compaction_triggered=True,
            timestamp="2026-04-18T12:00:00+00:00",
        )
        assert entry.turn_number == 5
        assert entry.compaction_triggered is True
        assert entry.total_tokens == 700

    def test_frozen(self):
        entry = ContextBudgetLogEntry(
            log_id="log-1", agent_id="a", session_id="s"
        )
        with pytest.raises(ValidationError):
            entry.log_id = "changed"

    def test_serialization_round_trip(self):
        entry = ContextBudgetLogEntry(
            log_id="log-1",
            agent_id="a",
            session_id="s",
            total_tokens=100,
        )
        data = entry.model_dump()
        entry2 = ContextBudgetLogEntry(**data)
        assert entry2 == entry


# ============================================================================
# ContextBudget validates threshold range (0.0 - 1.0) (from iter16)
# ============================================================================


class TestContextBudgetValidation:
    """ContextBudget must reject thresholds > 1.0."""

    def test_valid_default_thresholds(self) -> None:
        budget = ContextBudget()
        assert budget.session_hard_ceiling == 0.95
        assert budget.compaction_trigger == 0.8

    def test_valid_custom_fractional_thresholds(self) -> None:
        budget = ContextBudget(
            session_hard_ceiling=0.99,
            forced_truncate_threshold=0.85,
            compaction_trigger=0.7,
            compaction_target=0.3,
        )
        assert budget.session_hard_ceiling == 0.99

    def test_rejects_session_hard_ceiling_above_one(self) -> None:
        with pytest.raises(ValueError, match="fraction"):
            ContextBudget(session_hard_ceiling=95.0)

    def test_rejects_forced_truncate_threshold_above_one(self) -> None:
        with pytest.raises(ValueError, match="fraction"):
            ContextBudget(forced_truncate_threshold=90.0)

    def test_rejects_compaction_trigger_above_one(self) -> None:
        with pytest.raises(ValueError, match="fraction"):
            ContextBudget(compaction_trigger=80.0)

    def test_rejects_compaction_target_above_one(self) -> None:
        with pytest.raises(ValueError, match="fraction"):
            ContextBudget(compaction_target=1.5)

    def test_boundary_value_one_is_accepted(self) -> None:
        budget = ContextBudget(session_hard_ceiling=1.0)
        assert budget.session_hard_ceiling == 1.0

    def test_zero_value_is_accepted(self) -> None:
        budget = ContextBudget(compaction_trigger=0.1, compaction_target=0.0)
        assert budget.compaction_trigger == 0.1
        assert budget.compaction_target == 0.0


# ============================================================================
# ContextBudget rejects negative thresholds (from iter21)
# ============================================================================


class TestContextBudgetNegativeThresholds:
    def test_negative_compaction_trigger_rejected(self) -> None:
        with pytest.raises(Exception, match="out of range"):
            ContextBudget(compaction_trigger=-0.1)

    def test_negative_session_hard_ceiling_rejected(self) -> None:
        with pytest.raises(Exception, match="out of range"):
            ContextBudget(session_hard_ceiling=-1.0)

    def test_negative_forced_truncate_rejected(self) -> None:
        with pytest.raises(Exception, match="out of range"):
            ContextBudget(forced_truncate_threshold=-0.5)

    def test_negative_compaction_target_rejected(self) -> None:
        with pytest.raises(Exception, match="out of range"):
            ContextBudget(compaction_target=-0.1)

    def test_zero_is_accepted(self) -> None:
        cfg = ContextBudget(compaction_trigger=0.1, compaction_target=0.0)
        assert cfg.compaction_trigger == 0.1
        assert cfg.compaction_target == 0.0

    def test_one_is_accepted(self) -> None:
        cfg = ContextBudget(session_hard_ceiling=1.0, forced_truncate_threshold=0.95)
        assert cfg.session_hard_ceiling == 1.0
