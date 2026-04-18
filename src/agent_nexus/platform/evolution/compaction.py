"""CompactionGuard -- context window protection against compaction loops.

Prevents the compaction death spiral described in docs/04:
    Context overflow -> Compaction -> reinject too much -> overflow again

Protection mechanisms:
  1. Minimum turns between compactions (min_turns_between_compactions=5)
  2. Reinject only L0 + L1 summary after compaction
  3. Forced truncation at 90% context usage
  4. Logging + alerting at consecutive_compaction_alert threshold

Design: CompactionGuard is per-agent, tracks turns since last compaction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_nexus.models.context import ContextBudget, TokenUsage
from agent_nexus.platform.evolution.store import EvolutionStore


@dataclass
class AgentContext:
    """Agent session context for compaction tracking.

    This is a lightweight representation of the agent's current state
    as relevant to compaction decisions.  The actual agent context
    management lives elsewhere; this struct is the interface between
    the agent and CompactionGuard.
    """

    agent_id: str
    session_id: str
    turn_number: int = 0
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    context_window: int = 128_000  # default context window size
    budget: ContextBudget = field(default_factory=ContextBudget)
    last_compaction_turn: int = 0

    # Tiered content for reinjection
    l0_content: str = ""
    l1_content: str = ""


class CompactionGuard:
    """Compaction anti-death-loop guard.

    Ensures compaction is not triggered too frequently and that
    post-compaction reinjection does not immediately re-trigger
    another compaction.

    Args:
        store: EvolutionStore for logging budget events.
        agent_id: Agent this guard is protecting.
    """

    def __init__(
        self,
        store: EvolutionStore,
        agent_id: str,
    ) -> None:
        self._store = store
        self._agent_id = agent_id
        self._consecutive_compactions = 0

    def should_compact(self, ctx: AgentContext) -> bool:
        """Determine whether compaction should be triggered.

        Checks:
          1. Minimum turns since last compaction.
          2. Current token usage vs compaction trigger threshold.

        Args:
            ctx: Current agent context.

        Returns:
            True if compaction should proceed.
        """
        # Check minimum turn gap
        turns_since = ctx.turn_number - ctx.last_compaction_turn
        if turns_since < ctx.budget.min_turns_between_compactions:
            return False

        # Check token usage against trigger threshold
        if ctx.context_window <= 0:
            return False

        usage_ratio = ctx.token_usage.total_tokens / ctx.context_window
        return usage_ratio > ctx.budget.compaction_trigger

    def needs_truncation(self, ctx: AgentContext) -> bool:
        """Check if forced truncation is needed at 90% threshold.

        This is more aggressive than compaction -- it unconditionally
        truncates the earliest messages to bring usage below threshold.
        """
        if ctx.context_window <= 0:
            return False
        ratio = ctx.token_usage.total_tokens / ctx.context_window
        return ratio > ctx.budget.forced_truncate_threshold

    def needs_hard_ceiling(self, ctx: AgentContext) -> bool:
        """Check if hard ceiling (95%) is exceeded.

        At this level, all non-essential context is dropped immediately.
        """
        if ctx.context_window <= 0:
            return False
        ratio = ctx.token_usage.total_tokens / ctx.context_window
        return ratio > ctx.budget.session_hard_ceiling

    def reinject_after_compaction(self, ctx: AgentContext) -> str:
        """Generate content to reinject after compaction.

        Only reinjects L0 + L1 summary.  L2 and L3 are NOT loaded
        to prevent immediate re-overflow.

        Args:
            ctx: Agent context with l0/l1 content populated.

        Returns:
            Compact reinjection string (L0 + L1 only).
        """
        # Build compact L0 content
        l0 = ctx.l0_content or self._build_l0_fallback(ctx)

        # Build compact L1 summary (truncated to budget)
        l1 = self._truncate_to_budget(ctx.l1_content, ctx.budget.l1_max)

        result = f"{l0}\n{l1}"

        # Update tracking
        self._consecutive_compactions += 1

        # Log the compaction event
        result_chars = len(result)
        # Rough token estimate: ~4 chars per token (no tokenizer available)
        estimated_tokens = result_chars // 4
        self._store.log_budget_event(
            agent_name=self._agent_id,
            event_type="compaction",
            tokens_before=ctx.token_usage.total_tokens,
            tokens_after=estimated_tokens,
            details={
                "consecutive_compactions": self._consecutive_compactions,
                "l0_chars": len(l0),
                "l1_chars": len(l1),
                "result_chars": result_chars,
                "estimated_tokens_note": "chars//4 approximation",
            },
        )

        return result

    def check_and_log(self, ctx: AgentContext) -> str | None:
        """Check context health and log budget state.

        Returns alert level or None if within budget:
          - "hard_ceiling" -- forced truncation at 95%
          - "forced_truncate" -- truncate earliest at 90%
          - "compaction" -- trigger compaction at 80%
          - None -- within budget

        Also logs the budget state for observability.
        """
        alert = ctx.token_usage.check_budget(ctx.context_window)

        self._store.log_budget_event(
            agent_name=self._agent_id,
            event_type="budget_check",
            tokens_before=ctx.token_usage.total_tokens,
            tokens_after=ctx.token_usage.total_tokens,
            details={
                "alert": alert,
                "turn": ctx.turn_number,
                "prompt_tokens": ctx.token_usage.prompt_tokens,
                "completion_tokens": ctx.token_usage.completion_tokens,
            },
        )

        return alert

    def reset_consecutive_count(self) -> None:
        """Reset consecutive compaction counter (e.g. after a successful turn)."""
        self._consecutive_compactions = 0

    @property
    def consecutive_compactions(self) -> int:
        """Number of consecutive compaction events."""
        return self._consecutive_compactions

    def should_alert(self, budget: ContextBudget | None = None) -> bool:
        """Check if consecutive compactions exceed alert threshold."""
        b = budget or ContextBudget()
        return self._consecutive_compactions >= b.consecutive_compaction_alert

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_l0_fallback(self, ctx: AgentContext) -> str:
        """Build a minimal L0 content when none is provided."""
        metrics = self._store.get_metrics(self._agent_id)
        return (
            f"Agent: {ctx.agent_id} | "
            f"Session: {ctx.session_id} | "
            f"Turn: {ctx.turn_number} | "
            f"Evolution: sel={metrics.total_selections}, "
            f"comp={metrics.total_completions}"
        )

    @staticmethod
    def _truncate_to_budget(text: str, max_chars: int) -> str:
        """Truncate text to fit within a character budget."""
        if not text or len(text) <= max_chars:
            return text
        return text[:max_chars] + "\n... [truncated]"
