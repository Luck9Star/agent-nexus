"""TokenTracker: session-scoped token usage monitor.

Wraps ContextBudget and TokenUsage models with runtime tracking.
Emits tiered alerts when token consumption crosses thresholds.

Reference: docs/06 Section 8.5
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass

from agent_nexus.models.context import (
    BudgetAlertLevel,
    ContextBudget,
    ContextBudgetLogEntry,
)

# Default maximum context window in tokens.
MAX_TOKENS: int = 200_000

# Alert level for "no alert" (within budget).  Not part of BudgetAlertLevel
# because it represents the absence of an alert rather than a budget breach.
_ALERT_OK = "ok"


@dataclass(frozen=True)
class TokenAlert:
    """Alert emitted when token usage crosses a budget threshold."""

    level: str  # "ok" | BudgetAlertLevel members
    message: str
    usage_pct: float


def _alert_from_budget(
    usage_pct: float,
    budget: ContextBudget,
) -> TokenAlert:
    """Map a usage percentage to a tiered TokenAlert."""
    if usage_pct > budget.session_hard_ceiling * 100:
        return TokenAlert(
            level=BudgetAlertLevel.HARD_CEILING,
            message="Hard ceiling reached — session must be truncated",
            usage_pct=usage_pct,
        )
    if usage_pct > budget.forced_truncate_threshold * 100:
        return TokenAlert(
            level=BudgetAlertLevel.FORCED_TRUNCATE,
            message="Forced truncate threshold reached — earliest messages will be dropped",
            usage_pct=usage_pct,
        )
    if usage_pct > budget.compaction_trigger * 100:
        return TokenAlert(
            level=BudgetAlertLevel.COMPACTION,
            message="Compaction threshold reached — context will be compacted",
            usage_pct=usage_pct,
        )
    return TokenAlert(
        level=_ALERT_OK,
        message="Within budget",
        usage_pct=usage_pct,
    )


# Maximum number of log entries retained per session.
_MAX_LOG_SIZE = 1000


class TokenTracker:
    """Track token usage across a session with tiered alerts.

    Usage::

        tracker = TokenTracker()
        alert = tracker.record_usage(1500, agent_name="code-reviewer")
        if alert.level != _ALERT_OK:
            print(f"Budget warning: {alert.message}")
    """

    def __init__(
        self,
        budget: ContextBudget | None = None,
        *,
        max_tokens: int = MAX_TOKENS,
        session_id: str = "",
    ) -> None:
        """Initialize with optional budget (defaults to ``ContextBudget()``).

        Args:
            budget: ContextBudget with threshold constants.
            max_tokens: Maximum token budget for the session.
            session_id: Optional session identifier for log entries.
        """
        self._budget = budget if budget is not None else ContextBudget()
        if max_tokens < 1:
            raise ValueError(
                f"max_tokens must be >= 1, got {max_tokens}"
            )
        self._max_tokens = max_tokens
        self._session_id = session_id or uuid.uuid4().hex[:12]
        self._total: int = 0
        self._turn: int = 0
        self._log: deque[ContextBudgetLogEntry] = deque(maxlen=_MAX_LOG_SIZE)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_usage(
        self,
        tokens_used: int,
        agent_name: str = "default",
    ) -> TokenAlert:
        """Record token consumption for this turn.

        Args:
            tokens_used: Number of tokens consumed in this turn.
                Must be non-negative.
            agent_name: Optional agent name for attribution.

        Returns:
            TokenAlert indicating current threshold level.

        Raises:
            ValueError: If tokens_used is negative.
        """
        if tokens_used < 0:
            raise ValueError(
                f"tokens_used must be non-negative, got {tokens_used}"
            )
        self._turn += 1
        self._total += tokens_used

        # Build a log entry for observability.
        entry = ContextBudgetLogEntry(
            log_id=uuid.uuid4().hex,
            agent_id=agent_name,
            session_id=self._session_id,
            turn_number=self._turn,
            total_tokens=self._total,
        )
        self._log.append(entry)

        # Check thresholds and return alert.
        pct = self.usage_pct
        return _alert_from_budget(pct, self._budget)

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed this session."""
        return self._total

    @property
    def remaining_budget(self) -> int:
        """Remaining tokens before hard ceiling."""
        remaining = self._max_tokens - self._total
        return max(0, remaining)

    @property
    def usage_pct(self) -> float:
        """Current usage as percentage of max tokens."""
        return (self._total / self._max_tokens) * 100

    def get_log(self) -> list[ContextBudgetLogEntry]:
        """Get all recorded log entries."""
        return list(self._log)

    def reset(self) -> None:
        """Reset for a new session."""
        self._total = 0
        self._turn = 0
        self._log.clear()
