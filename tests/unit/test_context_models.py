"""Unit tests for agent_nexus.models.context module."""


import pytest
from pydantic import ValidationError

from agent_nexus.models.context import (
    BudgetAlertLevel,
    ContextBudget,
    ContextBudgetLogEntry,
    TokenUsage,
)

# ---------------------------------------------------------------------------
# TokenUsage
# ---------------------------------------------------------------------------


class TestTokenUsage:
    def test_mutable(self):
        """TokenUsage is explicitly mutable (frozen=False)."""
        tu = TokenUsage()
        tu.prompt_tokens = 100
        tu.completion_tokens = 50
        assert tu.prompt_tokens == 100
        assert tu.completion_tokens == 50
        assert tu.total_tokens == 150  # auto-synced via computed_field

    def test_with_initial_values(self):
        tu = TokenUsage(prompt_tokens=500, completion_tokens=200)
        assert tu.prompt_tokens == 500
        assert tu.completion_tokens == 200
        assert tu.total_tokens == 700  # auto-synced by validator


# ---------------------------------------------------------------------------
# TokenUsage.check_budget()
# ---------------------------------------------------------------------------


class TestTokenUsageCheckBudget:
    def test_within_budget(self):
        tu = TokenUsage(prompt_tokens=400, completion_tokens=100)
        assert tu.total_tokens == 500
        assert tu.check_budget(context_window=10000) is None

    def test_compaction_threshold(self):
        """81% usage triggers 'compaction' alert."""
        tu = TokenUsage(prompt_tokens=700, completion_tokens=110)
        assert tu.total_tokens == 810
        assert tu.check_budget(context_window=1000) == BudgetAlertLevel.COMPACTION

    def test_forced_truncate_threshold(self):
        """91% usage triggers 'forced_truncate' alert."""
        tu = TokenUsage(prompt_tokens=800, completion_tokens=110)
        assert tu.total_tokens == 910
        assert tu.check_budget(context_window=1000) == BudgetAlertLevel.FORCED_TRUNCATE

    def test_hard_ceiling_threshold(self):
        """96% usage triggers 'hard_ceiling' alert."""
        tu = TokenUsage(prompt_tokens=800, completion_tokens=160)
        assert tu.total_tokens == 960
        assert tu.check_budget(context_window=1000) == BudgetAlertLevel.HARD_CEILING

    def test_zero_context_window(self):
        tu = TokenUsage(prompt_tokens=80, completion_tokens=20)
        assert tu.total_tokens == 100
        assert tu.check_budget(context_window=0) is None


# ---------------------------------------------------------------------------
# ContextBudgetLogEntry
# ---------------------------------------------------------------------------


class TestContextBudgetLogEntry:
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


# ============================================================================
# ContextBudget validates threshold range (0.0 - 1.0) (from iter16)
# ============================================================================


class TestContextBudgetValidation:
    """ContextBudget must reject thresholds > 1.0."""

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


# ============================================================================
# ContextBudget integer fields reject zero and negative (from iter33)
# ============================================================================


class TestContextBudgetFieldValidation:
    """ContextBudget integer fields reject zero and negative values."""

    def test_l0_max_zero_rejected(self):
        with pytest.raises(ValidationError):
            ContextBudget(l0_max=0)

    def test_l0_max_negative_rejected(self):
        with pytest.raises(ValidationError):
            ContextBudget(l0_max=-1)

    def test_min_turns_zero_rejected(self):
        with pytest.raises(ValidationError):
            ContextBudget(min_turns_between_compactions=0)


# ============================================================================
# TokenUsage always syncs total_tokens (from iter39)
# ============================================================================


class TestTokenUsageAlwaysSyncs:
    """TokenUsage.total_tokens is always derived from prompt + completion."""

    def test_total_tokens_updates_after_mutation(self) -> None:
        """Mutating prompt/completion immediately updates total_tokens."""
        tu = TokenUsage(prompt_tokens=10, completion_tokens=20)
        assert tu.total_tokens == 30
        tu.prompt_tokens = 50
        assert tu.total_tokens == 70  # no stale value

    def test_nonzero_components_sync(self) -> None:
        """Non-zero components always produce correct total."""
        tu = TokenUsage(prompt_tokens=200, completion_tokens=300)
        assert tu.total_tokens == 500


# ============================================================================
# ContextBudget bootstrap_max >= l0_max + l1_max (from iter39)
# ============================================================================


class TestContextBudgetBootstrapValidation:
    """ContextBudget rejects l0_max + l1_max exceeding bootstrap_max."""

    def test_rejects_l0_l1_exceeding_bootstrap(self) -> None:
        """l0_max + l1_max > bootstrap_max must raise ValueError."""
        with pytest.raises(Exception, match="exceeds bootstrap_max"):
            ContextBudget(l0_max=3000, l1_max=3000, bootstrap_max=5000)

    def test_accepts_exact_match(self) -> None:
        """l0_max + l1_max == bootstrap_max is valid."""
        cb = ContextBudget(l0_max=3000, l1_max=2000, bootstrap_max=5000)
        assert cb.l0_max + cb.l1_max == cb.bootstrap_max


class TestContextBudgetThresholdOrdering:
    """ContextBudget rejects trigger <= target and truncate >= ceiling."""

    def test_rejects_trigger_equal_target(self) -> None:
        with pytest.raises(ValidationError, match="compaction_trigger"):
            ContextBudget(compaction_trigger=0.5, compaction_target=0.5)

    def test_rejects_truncate_above_ceiling(self) -> None:
        with pytest.raises(ValidationError, match="forced_truncate_threshold"):
            ContextBudget(forced_truncate_threshold=0.95, session_hard_ceiling=0.9)


# ---------------------------------------------------------------------------
# ContextBudgetLogEntry token count ge=0 validation (iter88)
# ---------------------------------------------------------------------------


class TestContextBudgetLogEntryTokenValidation:
    """ContextBudgetLogEntry token count fields reject negative values."""

    def test_negative_prompt_tokens_rejected(self):
        with pytest.raises(ValidationError):
            ContextBudgetLogEntry(log_id="l", agent_id="a", session_id="s", prompt_tokens=-1)

    def test_negative_total_tokens_rejected(self):
        with pytest.raises(ValidationError):
            ContextBudgetLogEntry(log_id="l", agent_id="a", session_id="s", total_tokens=-1)

    def test_zero_tokens_accepted(self):
        entry = ContextBudgetLogEntry(
            log_id="l",
            agent_id="a",
            session_id="s",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )
        assert entry.prompt_tokens == 0
        assert entry.total_tokens == 0
