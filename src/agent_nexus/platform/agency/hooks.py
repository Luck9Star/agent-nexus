"""Hook event system for LLMClient — lightweight, zero-overhead when no handlers registered."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class HookEvent(Enum):
    """Events emitted during an LLM call lifecycle."""

    BEFORE_CALL = "before_call"
    AFTER_CALL = "after_call"
    ON_ERROR = "on_error"
    ON_RETRY = "on_retry"


@dataclass
class CallContext:
    """Call context that handlers can modify."""

    model: str
    system_prompt: str
    user_message: str
    temperature: float | None
    response_format: str | None
    timeout: float | None
    call_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    attempt: int = 1
    metadata: dict = field(default_factory=dict)


@dataclass
class CallResult:
    """Call result for after_call handlers."""

    content: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float


@dataclass
class RetryDecision:
    """Return value from on_error handlers."""

    retry: bool
    delay: float = 0.0
    reason: str = ""


class HookAbort(Exception):  # noqa: N818 — intentional name per spec
    """Raise in before_call handler to cancel the call."""


class HookManager:
    """Lightweight event manager. Zero overhead when no handlers registered."""

    def __init__(self) -> None:
        self._handlers: dict[HookEvent, list[Callable]] = defaultdict(list)

    def register(self, event: HookEvent, handler: Callable) -> None:
        """Register a handler for an event."""
        self._handlers[event].append(handler)

    def dispatch(self, event: HookEvent, **kwargs: Any) -> Any:
        """Dispatch event to all registered handlers.

        - Handler exceptions are caught and logged (never propagate to main flow)
        - HookAbort in BEFORE_CALL propagates to cancel the call
        - Returns the last non-None result (for RetryDecision from ON_ERROR)
        """
        handlers = self._handlers.get(event)
        if not handlers:
            return None

        last_result: Any = None
        for handler in handlers:
            try:
                result = handler(**kwargs)
            except HookAbort:
                # HookAbort is the ONLY exception that escapes (BEFORE_CALL)
                raise
            except Exception:
                logger.exception("Hook handler %r raised for event %s", handler, event.value)
                continue
            if result is not None:
                last_result = result
        return last_result
