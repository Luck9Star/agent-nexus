"""Context budget models: ContextLevel (L0-L3), ContextBudget, TokenUsage, TokenTracker models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import IntEnum

import logging

from pydantic import BaseModel, ConfigDict, Field, model_validator

logger = logging.getLogger(__name__)

_utc_now = lambda: datetime.now(timezone.utc)


class ContextLevel(IntEnum):
    """Tiered context loading levels.

    L0: Identity core -- injected every turn (<= 800 tokens).
    L1: Execution context -- injected on first turn only (<= 3,000 tokens).
    L2: Extended knowledge -- loaded on demand (0 baseline).
    L3: Runtime data -- dynamic, never pre-loaded.
    """

    L0_IDENTITY = 0
    L1_EXECUTION = 1
    L2_EXTENDED = 2
    L3_RUNTIME = 3


class ContextBudget(BaseModel):
    """Token budget limits for context tiered loading.

    These values define the hard caps for each context layer,
    compaction behavior, and session safety thresholds.
    """

    model_config = ConfigDict(frozen=True)

    l0_max: int = 800
    l1_max: int = 3000
    bootstrap_max: int = 5000  # L0 + L1 combined
    single_file_max: int = 8000  # max chars for a single bootstrap file
    compaction_trigger: float = 0.8  # 80% context usage triggers compaction
    compaction_target: float = 0.4  # compaction reduces to 40%
    session_hard_ceiling: float = 0.95  # 95% forces truncation
    forced_truncate_threshold: float = 0.9  # 90% truncates earliest messages
    min_turns_between_compactions: int = 5
    consecutive_compaction_alert: int = 3

    @model_validator(mode="after")
    def _validate_thresholds(self) -> "ContextBudget":
        """Ensure all threshold values are fractions in 0.0-1.0 range."""
        for field_name in (
            "compaction_trigger",
            "compaction_target",
            "session_hard_ceiling",
            "forced_truncate_threshold",
        ):
            value = getattr(self, field_name)
            if not (0.0 <= value <= 1.0):
                raise ValueError(
                    f"{field_name}={value} is out of range. "
                    "Thresholds must be fractions between 0.0 and 1.0 "
                    "(e.g. 0.8 for 80%)."
                )
        return self


class TokenUsage(BaseModel):
    """Session-scoped token usage tracking.

    Attached to AgentContext for real-time budget checking.
    """

    model_config = ConfigDict(frozen=False)  # mutable counters

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    compaction_count: int = 0
    last_compaction_turn: int = 0

    def check_budget(
        self,
        context_window: int,
        budget: ContextBudget | None = None,
    ) -> str | None:
        """Return alert level or None if within budget.

        Args:
            context_window: Total token limit for the model.
            budget: Optional budget config with configurable thresholds.
                Falls back to ContextBudget defaults if not provided.

        Returns:
            "hard_ceiling" -- forced truncation
            "forced_truncate" -- truncate earliest messages
            "compaction" -- trigger compaction
            None -- within budget
        """
        if budget is None:
            budget = ContextBudget()

        if context_window <= 0:
            logger.warning(
                "context_window=%s is invalid, budget check skipped",
                context_window,
            )
            return None
        ratio = self.total_tokens / context_window
        if ratio > budget.session_hard_ceiling:
            return "hard_ceiling"
        if ratio > budget.forced_truncate_threshold:
            return "forced_truncate"
        if ratio > budget.compaction_trigger:
            return "compaction"
        return None


class ContextBudgetLogEntry(BaseModel):
    """A single entry in the context_budget_log SQLite table.

    Records compaction events and token consumption for observability
    and cross-session analysis.
    """

    model_config = ConfigDict(frozen=True)

    log_id: str
    agent_id: str
    session_id: str
    turn_number: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    layer0_tokens: int = 0
    layer1_tokens: int = 0
    total_tokens: int = 0
    compaction_triggered: bool = False
    timestamp: datetime = Field(default_factory=_utc_now)
