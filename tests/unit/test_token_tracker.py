"""Unit tests for agent_nexus.platform.runtime.token_tracker module."""

from __future__ import annotations

import pytest

from agent_nexus.models.context import ContextBudget, ContextBudgetLogEntry
from agent_nexus.platform.runtime.token_tracker import (
    MAX_TOKENS,
    TokenAlert,
    TokenTracker,
    _alert_from_budget,
)


# ---------------------------------------------------------------------------
# TokenAlert
# ---------------------------------------------------------------------------

class TestTokenAlert:
    """Tests for TokenAlert frozen dataclass."""

    def test_creation(self) -> None:
        """TokenAlert with valid fields can be created."""
        alert = TokenAlert(level="ok", message="Within budget", usage_pct=50.0)
        assert alert.level == "ok"
        assert alert.message == "Within budget"
        assert alert.usage_pct == 50.0

    def test_frozen(self) -> None:
        """Modifying fields on TokenAlert raises FrozenInstanceError."""
        alert = TokenAlert(level="ok", message="Within budget", usage_pct=50.0)
        with pytest.raises(Exception):  # FrozenInstanceError
            alert.level = "compact"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _alert_from_budget helper
# ---------------------------------------------------------------------------

class TestAlertFromBudget:
    """Tests for _alert_from_budget helper function."""

    def test_ok_under_compaction_trigger(self) -> None:
        """Below 80% returns ok."""
        budget = ContextBudget()
        alert = _alert_from_budget(79.0, budget)
        assert alert.level == "ok"

    def test_compact_above_80_percent(self) -> None:
        """Above 80% returns compact (uses strict > comparison)."""
        budget = ContextBudget()
        alert = _alert_from_budget(80.1, budget)
        assert alert.level == "compact"

    def test_truncate_above_90_percent(self) -> None:
        """Above 90% returns truncate (uses strict > comparison)."""
        budget = ContextBudget()
        alert = _alert_from_budget(90.1, budget)
        assert alert.level == "truncate"

    def test_ceiling_above_95_percent(self) -> None:
        """Above 95% returns ceiling (uses strict > comparison)."""
        budget = ContextBudget()
        alert = _alert_from_budget(95.1, budget)
        assert alert.level == "ceiling"


# ---------------------------------------------------------------------------
# TokenTracker
# ---------------------------------------------------------------------------

class TestTokenTracker:
    """Tests for TokenTracker class."""

    # ------------------------------------------------------------------
    # __init__
    # ------------------------------------------------------------------

    def test_default_init(self) -> None:
        """Default max_tokens=200000, auto-generated session_id."""
        tracker = TokenTracker()
        assert tracker._max_tokens == MAX_TOKENS
        assert MAX_TOKENS == 200_000
        assert tracker._session_id != ""
        assert len(tracker._session_id) == 12  # uuid4().hex[:12]

    def test_custom_init(self) -> None:
        """Custom budget, max_tokens, session_id are respected."""
        custom_budget = ContextBudget(
            compaction_trigger=0.7,
            forced_truncate_threshold=0.85,
            session_hard_ceiling=0.9,
        )
        tracker = TokenTracker(
            budget=custom_budget,
            max_tokens=100_000,
            session_id="test-session-123",
        )
        assert tracker._budget is custom_budget
        assert tracker._max_tokens == 100_000
        assert tracker._session_id == "test-session-123"

    # ------------------------------------------------------------------
    # record_usage
    # ------------------------------------------------------------------

    def test_record_usage_returns_ok(self) -> None:
        """Under 80% returns level='ok'."""
        tracker = TokenTracker(max_tokens=200_000)
        alert = tracker.record_usage(100_000)
        assert alert.level == "ok"
        assert "Within budget" in alert.message

    def test_record_usage_compact_alert(self) -> None:
        """Above 80% returns level='compact' (uses strict > comparison)."""
        tracker = TokenTracker(max_tokens=100_000)
        # 80.1% = 80100 tokens
        alert = tracker.record_usage(80_100)
        assert alert.level == "compact"
        assert "Compaction threshold" in alert.message

    def test_record_usage_truncate_alert(self) -> None:
        """Above 90% returns level='truncate' (uses strict > comparison)."""
        tracker = TokenTracker(max_tokens=100_000)
        # 90.1% = 90100 tokens
        alert = tracker.record_usage(90_100)
        assert alert.level == "truncate"
        assert "Forced truncate" in alert.message

    def test_record_usage_ceiling_alert(self) -> None:
        """Above 95% returns level='ceiling' (uses strict > comparison)."""
        tracker = TokenTracker(max_tokens=100_000)
        # 95.1% = 95100 tokens
        alert = tracker.record_usage(95_100)
        assert alert.level == "ceiling"
        assert "Hard ceiling" in alert.message

    def test_total_tokens_accumulates(self) -> None:
        """Multiple record_usage calls accumulate correctly."""
        tracker = TokenTracker(max_tokens=200_000)
        assert tracker.total_tokens == 0

        tracker.record_usage(10_000)
        assert tracker.total_tokens == 10_000

        tracker.record_usage(15_000)
        assert tracker.total_tokens == 25_000

        tracker.record_usage(5_000)
        assert tracker.total_tokens == 30_000

    def test_record_usage_creates_log_entry(self) -> None:
        """Each record_usage call creates a ContextBudgetLogEntry."""
        tracker = TokenTracker(max_tokens=100_000, session_id="log-test")
        tracker.record_usage(50_000, agent_name="test-agent")
        tracker.record_usage(30_000, agent_name="test-agent")

        log = tracker.get_log()
        assert len(log) == 2

        # Verify first log entry
        entry1 = log[0]
        assert isinstance(entry1, ContextBudgetLogEntry)
        assert entry1.agent_id == "test-agent"
        assert entry1.session_id == "log-test"
        assert entry1.turn_number == 1
        assert entry1.total_tokens == 50_000

        # Verify second log entry has accumulated total
        entry2 = log[1]
        assert entry2.turn_number == 2
        assert entry2.total_tokens == 80_000

    # ------------------------------------------------------------------
    # remaining_budget
    # ------------------------------------------------------------------

    def test_remaining_budget(self) -> None:
        """Correct remaining budget calculation."""
        tracker = TokenTracker(max_tokens=100_000)
        assert tracker.remaining_budget == 100_000

        tracker.record_usage(30_000)
        assert tracker.remaining_budget == 70_000

        tracker.record_usage(20_000)
        assert tracker.remaining_budget == 50_000

    def test_remaining_budget_clamped_to_zero(self) -> None:
        """Remaining budget is clamped to 0 when exceeded."""
        tracker = TokenTracker(max_tokens=100_000)
        tracker.record_usage(120_000)
        assert tracker.remaining_budget == 0

    # ------------------------------------------------------------------
    # usage_pct
    # ------------------------------------------------------------------

    def test_usage_pct(self) -> None:
        """Correct percentage calculation."""
        tracker = TokenTracker(max_tokens=100_000)

        tracker.record_usage(50_000)
        assert tracker.usage_pct == 50.0

        tracker.record_usage(25_000)
        assert tracker.usage_pct == 75.0

    def test_usage_pct_zero_max_raises(self) -> None:
        """max_tokens=0 raises ValueError."""
        with pytest.raises(ValueError, match="max_tokens must be >= 1"):
            TokenTracker(max_tokens=0)

    # ------------------------------------------------------------------
    # get_log
    # ------------------------------------------------------------------

    def test_get_log(self) -> None:
        """Returns log entries with correct fields."""
        tracker = TokenTracker(max_tokens=100_000, session_id="get-log-test")
        tracker.record_usage(10_000, agent_name="agent-a")
        tracker.record_usage(20_000, agent_name="agent-b")

        log = tracker.get_log()
        assert len(log) == 2

        # Check entry fields
        entry = log[0]
        assert entry.agent_id == "agent-a"
        assert entry.session_id == "get-log-test"
        assert entry.turn_number == 1
        assert entry.total_tokens == 10_000
        assert entry.log_id != ""

    # ------------------------------------------------------------------
    # reset
    # ------------------------------------------------------------------

    def test_reset(self) -> None:
        """Clears total, turn, and log."""
        tracker = TokenTracker(max_tokens=100_000)
        tracker.record_usage(30_000)
        tracker.record_usage(20_000)
        assert tracker.total_tokens == 50_000
        assert len(tracker.get_log()) == 2

        tracker.reset()

        assert tracker.total_tokens == 0
        assert tracker._turn == 0
        assert tracker.get_log() == []

    # ------------------------------------------------------------------
    # usage_pct property edge cases
    # ------------------------------------------------------------------

    def test_usage_pct_at_zero_tokens(self) -> None:
        """usage_pct is 0.0 when no tokens recorded."""
        tracker = TokenTracker(max_tokens=100_000)
        assert tracker.usage_pct == 0.0

    def test_usage_pct_above_thresholds(self) -> None:
        """Values above thresholds return correct alert levels (strict > comparison)."""
        # Above 80%
        tracker80 = TokenTracker(max_tokens=100_000)
        alert80 = tracker80.record_usage(80_100)
        assert alert80.level == "compact"

        # Above 90%
        tracker90 = TokenTracker(max_tokens=100_000)
        alert90 = tracker90.record_usage(90_100)
        assert alert90.level == "truncate"

        # Above 95%
        tracker95 = TokenTracker(max_tokens=100_000)
        alert95 = tracker95.record_usage(95_100)
        assert alert95.level == "ceiling"


# ---------------------------------------------------------------------------
# Regression: Negative tokens_used rejection
# ---------------------------------------------------------------------------


class TestTokenTrackerNegativeRejection:
    """record_usage must reject negative tokens_used."""

    def test_negative_tokens_raises(self) -> None:
        """Negative tokens_used raises ValueError."""
        tracker = TokenTracker(max_tokens=100_000)
        with pytest.raises(ValueError, match="tokens_used must be non-negative"):
            tracker.record_usage(-1)

    def test_negative_large_raises(self) -> None:
        """Large negative tokens_used raises ValueError."""
        tracker = TokenTracker(max_tokens=100_000)
        with pytest.raises(ValueError, match="tokens_used must be non-negative"):
            tracker.record_usage(-999_999)

    def test_zero_tokens_accepted(self) -> None:
        """Zero tokens_used is valid (no-op turn)."""
        tracker = TokenTracker(max_tokens=100_000)
        alert = tracker.record_usage(0)
        assert alert.level == "ok"
        assert tracker.total_tokens == 0
        assert tracker._turn == 1

    def test_negative_does_not_corrupt_total(self) -> None:
        """Negative tokens_used does not change internal state."""
        tracker = TokenTracker(max_tokens=100_000)
        tracker.record_usage(50_000)
        assert tracker.total_tokens == 50_000

        with pytest.raises(ValueError):
            tracker.record_usage(-10_000)

        # Total should be unchanged after rejected call
        assert tracker.total_tokens == 50_000
        # Turn should be unchanged too (increment happens after validation)
        assert tracker._turn == 1


class TestLogTrimming:
    """Log trimming when entries exceed _MAX_LOG_SIZE (1000)."""

    def test_log_trimmed_after_max_size(self) -> None:
        """When log exceeds _MAX_LOG_SIZE (1000), oldest entries are dropped."""
        from agent_nexus.platform.runtime.token_tracker import _MAX_LOG_SIZE

        tracker = TokenTracker(max_tokens=200_000_000)
        # Record one more than the max to trigger trimming.
        for i in range(_MAX_LOG_SIZE + 1):
            tracker.record_usage(1, agent_name=f"agent-{i}")

        log = tracker.get_log()
        assert len(log) == _MAX_LOG_SIZE
        # The oldest entry (turn 1) should have been trimmed.
        assert log[0].turn_number == 2
        # Total tokens should still be accurate (not trimmed).
        assert tracker.total_tokens == _MAX_LOG_SIZE + 1


# iter122 regression: max_tokens minimum guard

class TestTokenTrackerMaxTokensGuard:
    """TokenTracker raises ValueError when max_tokens < 1."""

    def test_max_tokens_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="max_tokens must be >= 1"):
            TokenTracker(max_tokens=0)

    def test_max_tokens_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="max_tokens must be >= 1"):
            TokenTracker(max_tokens=-10)

    def test_max_tokens_one_accepted(self) -> None:
        tracker = TokenTracker(max_tokens=1)
        assert tracker._max_tokens == 1
