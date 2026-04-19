"""Unit tests for agent_nexus.platform.evolution.compaction module."""

from unittest.mock import MagicMock

from agent_nexus.models.context import ContextBudget, TokenUsage
from agent_nexus.models.evolution import EvolutionMetrics
from agent_nexus.platform.evolution.compaction import (
    AgentContext,
    CompactionGuard,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store() -> MagicMock:
    store = MagicMock()
    store.log_budget_event.return_value = "log-1"
    store.get_metrics.return_value = EvolutionMetrics(
        total_selections=100, total_applied=80,
        total_completions=60, total_fallbacks=20,
    )
    return store


def _make_ctx(
    turn: int = 10,
    prompt: int = 50_000,
    completion: int = 50_000,
    window: int = 128_000,
    last_compaction_turn: int = 0,
    l0: str = "identity",
    l1: str = "execution context",
) -> AgentContext:
    return AgentContext(
        agent_id="agent-1",
        session_id="sess-1",
        turn_number=turn,
        token_usage=TokenUsage(
            prompt_tokens=prompt, completion_tokens=completion,
        ),
        context_window=window,
        budget=ContextBudget(),
        last_compaction_turn=last_compaction_turn,
        l0_content=l0,
        l1_content=l1,
    )


# ---------------------------------------------------------------------------
# should_compact
# ---------------------------------------------------------------------------

class TestShouldCompact:
    def test_returns_true_when_over_trigger_and_enough_turns(self):
        guard = CompactionGuard(store=_make_store(), agent_id="a1")
        ctx = _make_ctx(turn=10, prompt=60_000, completion=60_000, window=128_000)
        assert guard.should_compact(ctx) is True

    def test_returns_false_when_below_trigger(self):
        guard = CompactionGuard(store=_make_store(), agent_id="a1")
        ctx = _make_ctx(turn=10, prompt=10_000, completion=10_000, window=128_000)
        assert guard.should_compact(ctx) is False

    def test_returns_false_when_too_soon_after_last_compaction(self):
        guard = CompactionGuard(store=_make_store(), agent_id="a1")
        # last compaction at turn 8, current turn 9, gap=1 < min_turns=5
        ctx = _make_ctx(turn=9, prompt=80_000, completion=80_000, last_compaction_turn=8)
        assert guard.should_compact(ctx) is False

    def test_returns_false_when_zero_context_window(self):
        guard = CompactionGuard(store=_make_store(), agent_id="a1")
        ctx = _make_ctx(window=0)
        assert guard.should_compact(ctx) is False


# ---------------------------------------------------------------------------
# needs_truncation
# ---------------------------------------------------------------------------

class TestNeedsTruncation:
    def test_returns_true_above_90_percent(self):
        guard = CompactionGuard(store=_make_store(), agent_id="a1")
        ctx = _make_ctx(prompt=60_000, completion=60_000, window=128_000)
        assert guard.needs_truncation(ctx) is True

    def test_returns_false_below_90_percent(self):
        guard = CompactionGuard(store=_make_store(), agent_id="a1")
        ctx = _make_ctx(prompt=50_000, completion=50_000, window=128_000)
        assert guard.needs_truncation(ctx) is False

    def test_returns_false_when_zero_window(self):
        guard = CompactionGuard(store=_make_store(), agent_id="a1")
        ctx = _make_ctx(window=0)
        assert guard.needs_truncation(ctx) is False


# ---------------------------------------------------------------------------
# needs_hard_ceiling
# ---------------------------------------------------------------------------

class TestNeedsHardCeiling:
    def test_returns_true_above_95_percent(self):
        guard = CompactionGuard(store=_make_store(), agent_id="a1")
        ctx = _make_ctx(prompt=63_000, completion=63_000, window=128_000)
        assert guard.needs_hard_ceiling(ctx) is True

    def test_returns_false_below_95_percent(self):
        guard = CompactionGuard(store=_make_store(), agent_id="a1")
        ctx = _make_ctx(prompt=50_000, completion=50_000, window=128_000)
        assert guard.needs_hard_ceiling(ctx) is False


# ---------------------------------------------------------------------------
# reinject_after_compaction
# ---------------------------------------------------------------------------

class TestReinjectAfterCompaction:
    def test_returns_l0_plus_l1_content(self):
        guard = CompactionGuard(store=_make_store(), agent_id="a1")
        ctx = _make_ctx(l0="IDENTITY", l1="EXECUTION")
        result = guard.reinject_after_compaction(ctx)
        assert "IDENTITY" in result
        assert "EXECUTION" in result

    def test_increments_consecutive_compactions(self):
        guard = CompactionGuard(store=_make_store(), agent_id="a1")
        ctx = _make_ctx()
        assert guard.consecutive_compactions == 0
        guard.reinject_after_compaction(ctx)
        assert guard.consecutive_compactions == 1
        guard.reinject_after_compaction(ctx)
        assert guard.consecutive_compactions == 2

    def test_logs_budget_event(self):
        store = _make_store()
        guard = CompactionGuard(store=store, agent_id="a1")
        ctx = _make_ctx()
        guard.reinject_after_compaction(ctx)
        store.log_budget_event.assert_called_once()
        call_kwargs = store.log_budget_event.call_args
        assert call_kwargs.kwargs["event_type"] == "compaction"

    def test_tokens_after_is_estimated_not_raw_chars(self):
        """Regression: tokens_after must be an estimated token count,
        not the raw character count.  Before the fix, tokens_after stored
        len(result) (chars) while tokens_before was real tokens, causing
        silent data corruption in budget analytics."""
        store = _make_store()
        guard = CompactionGuard(store=store, agent_id="a1")
        ctx = _make_ctx(l0="x" * 100, l1="y" * 200)
        guard.reinject_after_compaction(ctx)
        call_kwargs = store.log_budget_event.call_args.kwargs
        result = guard.reinject_after_compaction(ctx)
        result_chars = len(result)
        # tokens_after should be roughly chars//4, NOT chars
        tokens_after = call_kwargs["tokens_after"]
        assert tokens_after < result_chars, (
            f"tokens_after ({tokens_after}) should be much less than "
            f"result chars ({result_chars}) — expected ~chars//4 estimate"
        )
        assert tokens_after == result_chars // 4

    def test_fallback_l0_when_empty(self):
        guard = CompactionGuard(store=_make_store(), agent_id="a1")
        ctx = _make_ctx(l0="")
        result = guard.reinject_after_compaction(ctx)
        # Should build fallback L0 with agent_id and session info
        assert "agent-1" in result
        assert "sess-1" in result


# ---------------------------------------------------------------------------
# check_and_log
# ---------------------------------------------------------------------------

class TestCheckAndLog:
    def test_returns_none_when_within_budget(self):
        guard = CompactionGuard(store=_make_store(), agent_id="a1")
        ctx = _make_ctx(prompt=10_000, completion=10_000)
        result = guard.check_and_log(ctx)
        assert result is None

    def test_returns_compaction_at_80_percent(self):
        guard = CompactionGuard(store=_make_store(), agent_id="a1")
        ctx = _make_ctx(prompt=55_000, completion=55_000)
        result = guard.check_and_log(ctx)
        assert result == "compaction"

    def test_returns_forced_truncate_at_90_percent(self):
        guard = CompactionGuard(store=_make_store(), agent_id="a1")
        ctx = _make_ctx(prompt=60_000, completion=60_000)
        result = guard.check_and_log(ctx)
        assert result == "forced_truncate"

    def test_returns_hard_ceiling_at_95_percent(self):
        guard = CompactionGuard(store=_make_store(), agent_id="a1")
        ctx = _make_ctx(prompt=63_000, completion=63_000)
        result = guard.check_and_log(ctx)
        assert result == "hard_ceiling"


# ---------------------------------------------------------------------------
# should_alert + reset
# ---------------------------------------------------------------------------

class TestAlertAndReset:
    def test_should_alert_after_consecutive_compactions(self):
        store = _make_store()
        guard = CompactionGuard(store=store, agent_id="a1")
        ctx = _make_ctx()
        # Default consecutive_compaction_alert = 3
        guard.reinject_after_compaction(ctx)
        guard.reinject_after_compaction(ctx)
        assert guard.should_alert() is False
        guard.reinject_after_compaction(ctx)
        assert guard.should_alert() is True

    def test_reset_clears_consecutive_count(self):
        guard = CompactionGuard(store=_make_store(), agent_id="a1")
        ctx = _make_ctx()
        guard.reinject_after_compaction(ctx)
        assert guard.consecutive_compactions == 1
        guard.reset_consecutive_count()
        assert guard.consecutive_compactions == 0
        assert guard.should_alert() is False


# ---------------------------------------------------------------------------
# _truncate_to_budget
# ---------------------------------------------------------------------------

class TestTruncateToBudget:
    def test_no_truncation_when_short(self):
        result = CompactionGuard._truncate_to_budget("short", 100)
        assert result == "short"

    def test_truncates_when_over_budget(self):
        text = "a" * 200
        result = CompactionGuard._truncate_to_budget(text, 50)
        assert len(result) < 200
        assert result.endswith("\n... [truncated]")

    def test_handles_empty_string(self):
        result = CompactionGuard._truncate_to_budget("", 100)
        assert result == ""
