"""Tests for hook event system — HookManager, HookEvent, CallContext, CallResult."""

import pytest

from agent_nexus.platform.agency.hooks import (
    CallContext,
    HookAbort,
    HookEvent,
    HookManager,
    RetryDecision,
)

# ---------------------------------------------------------------------------
# CallContext / CallResult / RetryDecision dataclasses
# ---------------------------------------------------------------------------


class TestDataclasses:
    pass


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
