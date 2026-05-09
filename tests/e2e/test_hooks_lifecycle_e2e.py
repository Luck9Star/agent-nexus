"""E2E tests for HookExecutor lifecycle: command hooks, HTTP hooks, event filtering, cleanup.

Exercises real subprocess execution and real HTTP client lifecycle (no mocked internals).
Only the HTTP target is deliberately unreachable (connection-refused) to test error handling.
"""

from __future__ import annotations

import asyncio

import pytest

from agent_nexus.models.hooks import HookDefinition, HookEvent, HookType
from agent_nexus.platform.hooks.executor import HookExecutor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _command_hook(
    command: str,
    event: HookEvent = HookEvent.PRE_EXECUTION,
    block_on_failure: bool = False,
    timeout_seconds: float = 5.0,
) -> HookDefinition:
    return HookDefinition(
        type=HookType.COMMAND,
        event=event,
        command=command,
        block_on_failure=block_on_failure,
        timeout_seconds=timeout_seconds,
    )


def _http_hook(
    url: str,
    event: HookEvent = HookEvent.PRE_EXECUTION,
    block_on_failure: bool = False,
    timeout_seconds: float = 3.0,
) -> HookDefinition:
    return HookDefinition(
        type=HookType.HTTP,
        event=event,
        url=url,
        block_on_failure=block_on_failure,
        timeout_seconds=timeout_seconds,
    )


# ===========================================================================
# Command hook lifecycle
# ===========================================================================


class TestCommandHookLifecycle:
    """E2E tests for COMMAND hook execution using real subprocesses."""

    @pytest.mark.asyncio
    async def test_echo_command_passes(self) -> None:
        """A simple 'echo hello' hook passes and captures output."""
        hook = _command_hook("echo hello")
        executor = HookExecutor(hooks=[hook], allowed_commands=["echo"])

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)

        assert result.blocked is False
        assert len(result.results) == 1
        exec_result = result.results[0]
        assert exec_result.passed is True
        assert exec_result.output == "hello"
        assert exec_result.error is None
        assert exec_result.duration_ms > 0

        await executor.close()

    @pytest.mark.asyncio
    async def test_echo_command_no_resource_leak(self) -> None:
        """close() succeeds cleanly after command-only execution (no HTTP client)."""
        hook = _command_hook("echo test")
        executor = HookExecutor(hooks=[hook], allowed_commands=["echo"])

        await executor.execute_event(HookEvent.PRE_EXECUTION)
        await executor.close()

        # No assertion needed -- close() must not raise

    @pytest.mark.asyncio
    async def test_false_command_blocked_on_failure(self) -> None:
        """'false' (exit 1) with block_on_failure=True sets blocked=True."""
        hook = _command_hook("false", block_on_failure=True)
        executor = HookExecutor(hooks=[hook], allowed_commands=["false"])

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)

        assert result.blocked is True
        assert len(result.results) == 1
        exec_result = result.results[0]
        assert exec_result.passed is False
        assert exec_result.blocked is True

        await executor.close()


# ===========================================================================
# HTTP hook
# ===========================================================================


class TestHttpHook:
    """E2E tests for HTTP hook error handling (no real server)."""

    @pytest.mark.asyncio
    async def test_connection_refused_fails_gracefully(self) -> None:
        """HTTP hook to unreachable localhost returns passed=False with error."""
        hook = _http_hook("http://localhost:9999/nonexistent-hook")
        executor = HookExecutor(hooks=[hook])

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)

        assert result.blocked is False
        assert len(result.results) == 1
        exec_result = result.results[0]
        assert exec_result.passed is False
        # Error should mention the connection failure
        assert exec_result.error is not None
        assert "9999" in exec_result.error or "connect" in exec_result.error.lower()

        await executor.close()

    @pytest.mark.asyncio
    async def test_missing_url_returns_error(self) -> None:
        """HTTP hook without a url field returns a validation error."""
        hook = HookDefinition(
            type=HookType.HTTP,
            event=HookEvent.PRE_EXECUTION,
            url=None,
        )
        executor = HookExecutor(hooks=[hook])

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)

        assert len(result.results) == 1
        exec_result = result.results[0]
        assert exec_result.passed is False
        assert "missing" in (exec_result.error or "").lower()

        await executor.close()


# ===========================================================================
# Multiple hooks & event filtering
# ===========================================================================


class TestMultipleHooksAndFiltering:
    """E2E tests for sequential hook execution and event-based filtering."""

    @pytest.mark.asyncio
    async def test_two_hooks_sequential_execution(self) -> None:
        """Two COMMAND hooks for the same event both execute in order."""
        hooks = [
            _command_hook("echo first"),
            _command_hook("echo second"),
        ]
        executor = HookExecutor(hooks=hooks, allowed_commands=["echo"])

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)

        assert len(result.results) == 2
        assert result.results[0].passed is True
        assert result.results[0].output == "first"
        assert result.results[1].passed is True
        assert result.results[1].output == "second"
        assert result.blocked is False

        await executor.close()

    @pytest.mark.asyncio
    async def test_event_filtering_only_runs_matching_hooks(self) -> None:
        """Hooks for PRE_EXECUTION only run when that event is triggered."""
        hooks = [
            _command_hook("echo pre", event=HookEvent.PRE_EXECUTION),
            _command_hook("echo post", event=HookEvent.POST_EXECUTION),
        ]
        executor = HookExecutor(hooks=hooks, allowed_commands=["echo"])

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)

        # Only the PRE_EXECUTION hook should have run
        assert len(result.results) == 1
        assert result.results[0].output == "pre"

        await executor.close()

    @pytest.mark.asyncio
    async def test_block_on_failure_stops_remaining_hooks(self) -> None:
        """A failing blocking hook prevents subsequent hooks from running."""
        hooks = [
            _command_hook("false", block_on_failure=True),
            _command_hook("echo second"),
        ]
        executor = HookExecutor(hooks=hooks, allowed_commands=["false", "echo"])

        result = await executor.execute_event(HookEvent.PRE_EXECUTION)

        assert result.blocked is True
        # Only the first hook should have run
        assert len(result.results) == 1
        assert result.results[0].passed is False

        await executor.close()


# ===========================================================================
# Resource cleanup
# ===========================================================================


class TestHookExecutorClose:
    """E2E tests for HookExecutor.close() resource cleanup."""

    @pytest.mark.asyncio
    async def test_close_after_http_cleans_up_client(self) -> None:
        """Triggering HTTP client creation then calling close() releases resources."""
        # Use a non-private IP (93.184.216.34, example.com) so URL validation
        # passes and the httpx client is actually created.  Port 9999 will
        # refuse the connection, but that's fine -- we only care about lifecycle.
        hook = _http_hook("http://93.184.216.34:9999/test")
        executor = HookExecutor(hooks=[hook])

        # Execute to trigger http client lazy-init
        await executor.execute_event(HookEvent.PRE_EXECUTION)
        assert executor._http_client is not None
        assert not executor._http_client.is_closed

        await executor.close()
        assert executor._http_client.is_closed

    @pytest.mark.asyncio
    async def test_close_without_http_is_noop(self) -> None:
        """close() on an executor that never created an HTTP client is safe."""
        hook = _command_hook("echo hello")
        executor = HookExecutor(hooks=[hook], allowed_commands=["echo"])

        await executor.execute_event(HookEvent.PRE_EXECUTION)
        assert executor._http_client is None

        await executor.close()
        # Still None, not crashed
        assert executor._http_client is None

    @pytest.mark.asyncio
    async def test_double_close_is_safe(self) -> None:
        """Calling close() twice does not raise."""
        hook = _http_hook("http://93.184.216.34:9999/test")
        executor = HookExecutor(hooks=[hook])

        await executor.execute_event(HookEvent.PRE_EXECUTION)
        await executor.close()
        await executor.close()  # second call must not raise
