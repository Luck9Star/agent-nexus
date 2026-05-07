"""Tests for hook event system — HookManager, HookEvent, CallContext, CallResult."""

import pytest

from agent_nexus.platform.agency.hooks import (
    CallContext,
    CallResult,
    HookAbort,
    HookEvent,
    HookManager,
    RetryDecision,
)

# ---------------------------------------------------------------------------
# CallContext / CallResult / RetryDecision dataclasses
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_call_context_creation(self):
        """CallContext fields are set correctly."""
        ctx = CallContext(
            model="gpt-4",
            system_prompt="You are helpful.",
            user_message="Hello",
            temperature=0.7,
            response_format=None,
            timeout=30.0,
        )
        assert ctx.model == "gpt-4"
        assert ctx.system_prompt == "You are helpful."
        assert ctx.user_message == "Hello"
        assert ctx.temperature == 0.7
        assert ctx.call_id  # auto-generated UUID
        assert ctx.attempt == 1
        assert ctx.metadata == {}

    def test_call_context_is_mutable(self):
        """Handlers can mutate CallContext fields."""
        ctx = CallContext(
            model="gpt-4",
            system_prompt="",
            user_message="",
            temperature=None,
            response_format=None,
            timeout=None,
        )
        ctx.model = "claude-3"
        ctx.temperature = 0.5
        assert ctx.model == "claude-3"
        assert ctx.temperature == 0.5

    def test_call_result_creation(self):
        """CallResult holds result data."""
        result = CallResult(
            content="Hello!",
            model="gpt-4",
            input_tokens=10,
            output_tokens=5,
            latency_ms=120.5,
        )
        assert result.content == "Hello!"
        assert result.input_tokens == 10
        assert result.latency_ms == 120.5

    def test_retry_decision_defaults(self):
        """RetryDecision has sensible defaults."""
        rd = RetryDecision(retry=True)
        assert rd.retry is True
        assert rd.delay == 0.0
        assert rd.reason == ""


# ---------------------------------------------------------------------------
# HookManager register + dispatch
# ---------------------------------------------------------------------------


class TestHookManagerDispatch:
    def test_register_and_dispatch_all_events(self):
        """Handler fires for each of the 4 HookEvent values."""
        received: list[HookEvent] = []

        mgr = HookManager()
        for event in HookEvent:

            def _make_handler(e: HookEvent):
                def handler(**kwargs):
                    received.append(e)

                return handler

            mgr.register(event, _make_handler(event))

        for event in HookEvent:
            mgr.dispatch(event)

        assert set(received) == set(HookEvent)

    def test_dispatch_no_handlers_returns_none(self):
        """Default HookManager (no handlers) returns None without error."""
        mgr = HookManager()
        result = mgr.dispatch(HookEvent.BEFORE_CALL, context=None)
        assert result is None

    def test_dispatch_returns_last_non_none_result(self):
        """dispatch returns the last handler's non-None return value."""
        mgr = HookManager()

        mgr.register(HookEvent.ON_ERROR, lambda **kw: RetryDecision(retry=False))
        mgr.register(HookEvent.ON_ERROR, lambda **kw: RetryDecision(retry=True, reason="retry"))

        result = mgr.dispatch(HookEvent.ON_ERROR)
        assert isinstance(result, RetryDecision)
        assert result.retry is True
        assert result.reason == "retry"

    def test_multiple_handlers_same_event(self):
        """All handlers fire; results accumulate."""
        calls = []

        mgr = HookManager()
        mgr.register(HookEvent.AFTER_CALL, lambda **kw: calls.append("first"))
        mgr.register(HookEvent.AFTER_CALL, lambda **kw: calls.append("second"))
        mgr.register(HookEvent.AFTER_CALL, lambda **kw: calls.append("third"))

        mgr.dispatch(HookEvent.AFTER_CALL)
        assert calls == ["first", "second", "third"]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestHookErrorHandling:
    def test_handler_exception_caught_not_propagated(self):
        """Non-HookAbort exceptions are caught; dispatch continues."""
        mgr = HookManager()

        def bad_handler(**kwargs):
            raise ValueError("boom")

        def good_handler(**kwargs):
            return "ok"

        mgr.register(HookEvent.AFTER_CALL, bad_handler)
        mgr.register(HookEvent.AFTER_CALL, good_handler)

        result = mgr.dispatch(HookEvent.AFTER_CALL)
        assert result == "ok"  # good handler still ran

    def test_hook_abort_propagates(self):
        """HookAbort from BEFORE_CALL propagates to caller."""
        mgr = HookManager()

        def abort_handler(**kwargs):
            raise HookAbort("cancelled by policy")

        mgr.register(HookEvent.BEFORE_CALL, abort_handler)

        with pytest.raises(HookAbort, match="cancelled by policy"):
            mgr.dispatch(HookEvent.BEFORE_CALL)


# ---------------------------------------------------------------------------
# Handler can mutate CallContext
# ---------------------------------------------------------------------------


class TestHandlerContextMutation:
    def test_handler_mutates_context(self):
        """BEFORE_CALL handler can modify the CallContext."""
        mgr = HookManager()
        ctx = CallContext(
            model="gpt-4",
            system_prompt="original",
            user_message="hi",
            temperature=None,
            response_format=None,
            timeout=None,
        )

        def tweak(**kwargs):
            ctx_ref = kwargs["context"]
            ctx_ref.system_prompt = "modified"

        mgr.register(HookEvent.BEFORE_CALL, tweak)
        mgr.dispatch(HookEvent.BEFORE_CALL, context=ctx)

        assert ctx.system_prompt == "modified"

    def test_handler_receives_kwargs(self):
        """Handler receives all kwargs passed to dispatch."""
        mgr = HookManager()
        received = {}

        def capture(**kwargs):
            received.update(kwargs)

        mgr.register(HookEvent.ON_RETRY, capture)
        mgr.dispatch(HookEvent.ON_RETRY, attempt=3, reason="timeout")

        assert received["attempt"] == 3
        assert received["reason"] == "timeout"
