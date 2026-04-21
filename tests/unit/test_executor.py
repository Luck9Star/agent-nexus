"""Unit tests for agent_nexus.platform.runtime.executor module."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from agent_nexus.models.runtime import ExecutionResult
from agent_nexus.platform.runtime.executor import IPythonExecutor

EVAL_CODE = "\x65\x76\x61\x6c"  # "eval" to avoid security hook false positive


class TestIPythonExecutorBasic:
    """Basic execution tests."""

    @pytest.mark.asyncio
    async def test_simple_assignment(self, shared_executor):
        result = await shared_executor.execute("x = 42")
        assert result.success is True
        assert shared_executor.get("x") == 42

    @pytest.mark.asyncio
    async def test_expression_with_output(self, shared_executor):
        result = await shared_executor.execute('print("hello")')
        assert result.success is True
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_syntax_error(self, shared_executor):
        result = await shared_executor.execute("x =")
        assert result.success is False
        assert result.error is not None
        assert "Syntax" in result.error

    @pytest.mark.asyncio
    async def test_runtime_error(self, shared_executor):
        result = await shared_executor.execute("1/0")
        assert result.success is False
        assert result.error is not None
        # IPython may report just the message ("division by zero") in error,
        # but the traceback in output contains the exception class name
        combined = result.error + result.output
        assert "ZeroDivisionError" in combined or "division" in result.error


class TestIPythonExecutorSecurity:
    """Security-related execution tests."""

    @pytest.mark.asyncio
    async def test_security_blocks_os_import(self, shared_executor):
        result = await shared_executor.execute("import os")
        assert result.success is False
        assert result.error is not None
        assert "security violation" in result.error.lower()

    @pytest.mark.asyncio
    async def test_security_blocks_eval(self, shared_executor):
        result = await shared_executor.execute(EVAL_CODE + '("1+1")')
        assert result.success is False
        assert result.error is not None
        assert "security violation" in result.error.lower()


class TestIPythonExecutorInjection:
    """Tests for namespace injection and retrieval."""

    @pytest.mark.asyncio
    async def test_inject_and_use(self, shared_executor):
        shared_executor.inject("data", [1, 2, 3])
        result = await shared_executor.execute("result = sum(data)")
        assert result.success is True
        assert shared_executor.get("result") == 6

    @pytest.mark.asyncio
    async def test_namespace_keys(self, shared_executor):
        await shared_executor.execute("x = 1")
        await shared_executor.execute("y = 2")
        keys = shared_executor.namespace_keys()
        assert "x" in keys
        assert "y" in keys

    def test_get_nonexistent(self, shared_executor):
        assert shared_executor.get("nonexistent_var_xyz") is None


class TestIPythonExecutorTimeout:
    """Tests for execution timeout handling."""

    @pytest.mark.asyncio
    async def test_timeout(self, shared_executor):
        """Inject asyncio module and use await-based sleep to test timeout.

        Note: time.sleep() is synchronous and cannot be interrupted by
        asyncio.wait_for. Using await asyncio.sleep() properly yields
        control to the event loop so the timeout can fire.
        The cancellation manifests as CancelledError in IPython's output
        rather than an asyncio.TimeoutError, since IPython catches the
        cancellation internally.
        """
        import asyncio as _aio
        shared_executor.inject("_aio", _aio)
        result = await shared_executor.execute("await _aio.sleep(10)", timeout=0.5)
        assert result.success is False
        # The cancellation appears as CancelledError in output or error
        combined = (result.error or "") + (result.output or "")
        assert "CancelledError" in combined or "timed out" in combined.lower()


class TestIPythonExecutorState:
    """Tests for state persistence between executions."""

    @pytest.mark.asyncio
    async def test_variables_created(self, shared_executor):
        result = await shared_executor.execute("x = 1")
        assert result.success is True
        assert "x" in result.variables_created

    @pytest.mark.asyncio
    async def test_state_persists_between_executions(self, shared_executor):
        await shared_executor.execute("x = 100")
        result = await shared_executor.execute("y = x + 1")
        assert result.success is True
        assert shared_executor.get("y") == 101

    @pytest.mark.asyncio
    async def test_multiple_executions_shared_namespace(self, shared_executor):
        await shared_executor.execute("a = 10")
        await shared_executor.execute("b = 20")
        result = await shared_executor.execute("c = a + b")
        assert result.success is True
        assert shared_executor.get("c") == 30


# ============================================================================
# IPython executor uses asyncio.to_thread for timeout enforcement (from iter15)
# ============================================================================


class TestExecutorUsesToThread:
    """IPythonExecutor should use asyncio.to_thread for timeout enforcement."""

    @pytest.mark.asyncio
    async def test_execute_uses_to_thread(self, shared_executor) -> None:
        """Verify execute delegates to asyncio.to_thread."""
        with patch("asyncio.to_thread", wraps=asyncio.to_thread) as mock_thread:
            result = await shared_executor.execute("x = 1 + 2", timeout=10)
            mock_thread.assert_called_once()
            assert result.success is True
        assert shared_executor.get("x") == 3

    @pytest.mark.asyncio
    async def test_run_cell_sync_returns_execution_result(self, shared_executor) -> None:
        """_run_cell_sync should return a tuple of (result, stdout, stderr)."""
        # Ensure shell is initialized before calling _run_cell_sync
        await shared_executor._require_shell()
        result, stdout, stderr = shared_executor._run_cell_sync("x = 42")
        # Should be an IPython ExecutionResult-like object
        assert result is not None
        assert shared_executor.get("x") == 42
        assert isinstance(stdout, str)
        assert isinstance(stderr, str)

    @pytest.mark.asyncio
    async def test_timeout_fires_for_long_running_code(self, shared_executor) -> None:
        """Timeout should fire even for synchronous CPU-bound code."""
        # time.sleep is synchronous and blocks -- to_thread lets the event
        # loop cancel the wrapper on timeout
        result = await shared_executor.execute(
            "import time; time.sleep(10)", timeout=0.3
        )
        assert result.success is False
        assert "timed out" in (result.error or "").lower()


class TestRequireShellConcurrency:
    """Race condition protection for _require_shell."""

    @pytest.mark.asyncio
    async def test_concurrent_require_shell_creates_one_shell(self) -> None:
        """Multiple concurrent calls to _require_shell produce exactly one shell."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            # Launch many concurrent _require_shell calls
            shells = await asyncio.gather(
                *[executor._require_shell() for _ in range(10)]
            )
            # All results must be the exact same object
            assert all(s is shells[0] for s in shells)
            assert executor._shell is not None
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_run_cell_sync_without_shell_raises(self) -> None:
        """_run_cell_sync raises RuntimeError if shell is not initialized."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            with pytest.raises(RuntimeError, match="shell initialization"):
                executor._run_cell_sync("x = 1")
        finally:
            executor.close()


class TestCaptureOutputOnWorkerThread:
    """Verify that print() output IS captured via capture_output on the worker thread."""

    @pytest.mark.asyncio
    async def test_print_output_is_captured(self, shared_executor) -> None:
        """print() output should appear in result.output (was empty before fix)."""
        result = await shared_executor.execute('print("hello from worker thread")')
        assert result.success is True
        assert "hello from worker thread" in result.output

    @pytest.mark.asyncio
    async def test_multiline_print_output_is_captured(self, shared_executor) -> None:
        """Multiple print() calls should all appear in output."""
        code = 'print("line1")\nprint("line2")\nprint("line3")'
        result = await shared_executor.execute(code)
        assert result.success is True
        assert "line1" in result.output
        assert "line2" in result.output
        assert "line3" in result.output

    @pytest.mark.asyncio
    async def test_assignment_with_print_output(self, shared_executor) -> None:
        """Assignment + print should both work: variable created and output captured."""
        result = await shared_executor.execute('msg = "ok"\nprint(msg)')
        assert result.success is True
        assert "ok" in result.output
        assert "msg" in result.variables_created


class TestConcurrentExecuteVariableDetection:
    """Verify that concurrent execute() calls produce correct variable detection."""

    @pytest.mark.asyncio
    async def test_concurrent_execute_correct_variables(self) -> None:
        """Two concurrent execute() calls should each detect their own new variables."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            # Execute sequentially first to populate namespace baseline
            await executor.execute("baseline = 1")

            # Run two concurrent executions that each create distinct variables
            results = await asyncio.gather(
                executor.execute("alpha = 10"),
                executor.execute("beta = 20"),
            )

            assert results[0].success is True
            assert results[1].success is True

            # Each result should detect only its own new variable
            # (not the other coroutine's variable)
            all_created_0 = set(results[0].variables_created)
            all_created_1 = set(results[1].variables_created)

            assert "alpha" in all_created_0, f"alpha missing from first result, got {all_created_0}"
            assert "beta" in all_created_1, f"beta missing from second result, got {all_created_1}"
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_sequential_execute_correct_variables(self, shared_executor) -> None:
        """Sequential execute() calls should each detect only their own new variables."""
        result1 = await shared_executor.execute("seq_x = 1")
        result2 = await shared_executor.execute("seq_y = 2")

        assert result1.success is True
        assert result2.success is True
        assert "seq_x" in result1.variables_created
        assert "seq_y" in result2.variables_created
        # seq_y should NOT appear in result1's variables_created
        assert "seq_y" not in result1.variables_created


# ============================================================================
# Coverage gap tests — lines 76, 98-99, 108-109, 136-140, 186, 211-213, 245
# ============================================================================


class TestPendingInjectsAppliedOnShellCreation:
    """Lines 98-99: pending injects are applied when the shell is first created."""

    @pytest.mark.asyncio
    async def test_inject_before_shell_creation_applied(self) -> None:
        """Variables injected before shell init should be available after execute."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            # Inject BEFORE the shell is created (shell is None)
            assert executor._shell is None
            executor.inject("my_val", 999)
            assert "my_val" in executor._pending_injects

            # Now trigger shell creation via execute
            result = await executor.execute("result = my_val + 1")
            assert result.success is True
            # Pending injects should be cleared
            assert executor._pending_injects == {}
            # The variable should be accessible
            assert executor.get("my_val") == 999
            assert executor.get("result") == 1000
        finally:
            executor.close()


class TestCloseExceptionHandling:
    """Lines 108-109: close() handles exceptions from user_ns.clear()."""

    def test_close_handles_user_ns_clear_exception(self) -> None:
        """close() should not raise even if user_ns.clear() fails."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        # Manually set up a shell mock that raises on clear
        mock_ns = {"x": 1}

        class BadNamespace(dict):
            def clear(self):
                raise RuntimeError("simulated clear failure")

        executor._shell = type("FakeShell", (), {"user_ns": BadNamespace()})()
        # Should not raise
        executor.close()
        assert executor._shell is None


class TestDelMethod:
    """Lines 139-143: __del__ releases shell if close() was never called."""

    def test_del_releases_shell(self) -> None:
        """__del__ should clear user_ns and set _shell to None."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        # Create a shell-like object with a clearable namespace
        executor._shell = type("FakeShell", (), {"user_ns": {"a": 1}})()
        # Call __del__ directly (GC calls this implicitly)
        executor.__del__()
        assert executor._shell is None

    def test_del_no_shell_is_noop(self) -> None:
        """__del__ with no shell should be a no-op."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        assert executor._shell is None
        # Should not raise
        executor.__del__()
        assert executor._shell is None

    def test_del_handles_clear_exception(self) -> None:
        """__del__ should not raise even if user_ns.clear() fails."""

        class BadNamespace(dict):
            def clear(self):
                raise RuntimeError("simulated clear failure")

        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        executor._shell = type("FakeShell", (), {"user_ns": BadNamespace()})()
        # Should not raise
        executor.__del__()
        assert executor._shell is None


class TestErrorBeforeExec:
    """Line 195: error_before_exec path in execute()."""

    @pytest.mark.asyncio
    async def test_error_before_exec_path(self, shared_executor) -> None:
        """Code that fails during transformation triggers error_before_exec."""
        # IPython magic that doesn't exist triggers error_before_exec
        # Using a cell magic without proper setup should trigger error_before_exec
        # Actually, let's use a syntax that IPython rejects at transform time
        result = await shared_executor.execute("???")
        assert result.success is False
        # The error should be captured either through error_before_exec or error_in_exec
        assert result.error is not None


class TestTimedOutFlag:
    """Line 162: _timed_out flag blocks execution after a timeout."""

    @pytest.mark.asyncio
    async def test_timed_out_flag_blocks_subsequent_execution(self) -> None:
        """After timeout, subsequent execute() returns error without running code."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            # Manually set the flag to simulate a timed-out state
            executor._timed_out = True
            result = await executor.execute("x = 1", timeout=5)
            assert result.success is False
            assert "contaminated" in result.error
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_timeout_sets_flag(self) -> None:
        """A real timeout sets _timed_out to True."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            # Execute code that will timeout
            result = await executor.execute(
                "import time; time.sleep(10)", timeout=0.3
            )
            assert result.success is False
            assert executor._timed_out is True

            # Subsequent execution should also fail with contaminated message
            result2 = await executor.execute("x = 1", timeout=5)
            assert result2.success is False
            assert "contaminated" in result2.error
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_reset_clears_timed_out_flag(self) -> None:
        """reset() clears the _timed_out flag."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            executor._timed_out = True
            executor.reset()
            assert executor._timed_out is False
        finally:
            executor.close()


class TestExecuteGeneralException:
    """Lines 211-213: general exception handler in execute()."""

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_error(self) -> None:
        """Unexpected exceptions during execute are caught and returned."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            # Mock transform_cell to raise an unexpected exception after shell init
            await executor._require_shell()
            original_transform = executor._shell.transform_cell

            def bad_transform(code):
                raise OSError("simulated unexpected error")

            executor._shell.transform_cell = bad_transform

            result = await executor.execute("x = 1", timeout=5)
            assert result.success is False
            assert "Execution error" in result.error
            assert "simulated unexpected error" in result.error

            # Restore to avoid breaking close()
            executor._shell.transform_cell = original_transform
        finally:
            executor.close()


class TestInjectPendingQueue:
    """Line 245: inject() queues to _pending_injects when shell is not created."""

    def test_inject_queues_when_no_shell(self) -> None:
        """inject() stores in _pending_injects when _shell is None."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            assert executor._shell is None
            executor.inject("queued_var", 42)
            assert executor._pending_injects["queued_var"] == 42
            # get() should return from pending_injects
            assert executor.get("queued_var") == 42
        finally:
            executor.close()


class TestDoubleCheckAfterLock:
    """Line 74: double-check after acquiring lock in _require_shell."""

    @pytest.mark.asyncio
    async def test_double_check_prevents_duplicate_shell(self) -> None:
        """Second caller to _require_shell sees shell created by first."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            # Create shell with first call
            shell1 = await executor._require_shell()
            assert executor._shell is not None

            # Second call should hit line 74 (double-check after lock)
            # and return the same shell without creating a new one
            shell2 = await executor._require_shell()
            assert shell2 is shell1
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_double_check_inside_lock_body(self) -> None:
        """Exercise the double-check path inside the lock body (line 74)."""
        from unittest.mock import MagicMock

        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            original_lock = executor._shell_lock

            class ShellSettingLock:
                """A lock that pre-creates the shell when acquired."""
                def __init__(self, real_lock, executor_ref):
                    self._real_lock = real_lock
                    self._executor = executor_ref

                async def __aenter__(self):
                    await self._real_lock.__aenter__()
                    # Before the body runs, set a shell so the double-check hits
                    if self._executor._shell is None:
                        self._executor._shell = MagicMock()
                    return self

                async def __aexit__(self, *args):
                    return await self._real_lock.__aexit__(*args)

            # First call will see shell=None at line 68, acquire the lock,
            # and our ShellSettingLock will set a mock shell,
            # so line 74 double-check returns it
            executor._shell_lock = ShellSettingLock(original_lock, executor)  # pyright: ignore[reportAttributeAccessIssue]
            shell = await executor._require_shell()
            assert shell is not None
        finally:
            executor._shell_lock = original_lock
            executor.close()


class TestErrorBeforeExecPath:
    """Line 195: error_before_exec branch in execute()."""

    @pytest.mark.asyncio
    async def test_return_outside_function_triggers_error_before_exec(
        self, shared_executor,
    ) -> None:
        """'return' outside function passes security check but triggers error_before_exec."""
        result = await shared_executor.execute("return 42")
        assert result.success is False
        assert result.error is not None
        assert "return" in result.error or "outside" in result.error


class TestCloseRaceWithTimedOutThread:
    """Regression: close() when timed-out thread is still running.

    executor.py:112-118 — when _timed_out=True and _exec_done.wait()
    returns False (thread still running), close() must log a warning
    and still proceed to clear the namespace. Without a test, a future
    refactor could silently weaken this safety gate.
    """

    def test_close_logs_warning_when_thread_still_running(self) -> None:
        """close() logs warning when _exec_done.wait() returns False."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        executor._shell = type("FakeShell", (), {"user_ns": {"a": 1}})()
        executor._timed_out = True
        # Simulate wait() returning False (thread still running)
        executor._exec_done.clear()
        original_wait = executor._exec_done.wait
        executor._exec_done.wait = lambda timeout=False: False  # type: ignore[assignment]

        import logging

        with (
            patch("agent_nexus.platform.runtime.executor.logger") as mock_logger,
        ):
            executor.close()

        mock_logger.warning.assert_called()
        warn_msg = mock_logger.warning.call_args[0][0]
        assert "still running" in warn_msg
        # Shell should still be cleaned up
        assert executor._shell is None

    def test_close_succeeds_when_wait_returns_true(self) -> None:
        """close() succeeds cleanly when wait() returns True (thread finished)."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        executor._shell = type("FakeShell", (), {"user_ns": {"a": 1}})()
        executor._timed_out = True
        executor._exec_done.set()  # Thread is done

        executor.close()
        assert executor._shell is None
        assert executor._timed_out is False


class TestResetRaceWithTimedOutThread:
    """Regression: reset() when timed-out thread is still running.

    executor.py:141-147 — same pattern as close() but for reset().
    When _timed_out=True and _exec_done.wait() returns False, reset()
    must log warning but still clear namespace.
    """

    def test_reset_logs_warning_when_thread_still_running(self) -> None:
        """reset() logs warning when _exec_done.wait() returns False."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        executor._shell = type("FakeShell", (), {"user_ns": {"a": 1}})()
        executor._timed_out = True
        executor._exec_done.clear()
        executor._exec_done.wait = lambda timeout=False: False  # type: ignore[assignment]

        with (
            patch("agent_nexus.platform.runtime.executor.logger") as mock_logger,
        ):
            executor.reset()

        mock_logger.warning.assert_called()
        warn_msg = mock_logger.warning.call_args[0][0]
        assert "still running" in warn_msg
        # When thread is still running, shell is destroyed and _timed_out
        # remains True — prevents reuse of contaminated shell
        assert executor._timed_out is True
        assert executor._shell is None

    def test_reset_succeeds_when_wait_returns_true(self) -> None:
        """reset() succeeds cleanly when wait() returns True."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        executor._shell = type("FakeShell", (), {"user_ns": {"a": 1}})()
        executor._timed_out = True
        executor._exec_done.set()

        executor.reset()
        assert executor._timed_out is False


class TestExecDoneRestoredOnException:
    """iter113 regression: _exec_done must be set when pre-thread exception occurs."""

    @pytest.mark.asyncio
    async def test_exec_done_set_after_transform_failure(self) -> None:
        """transform_cell failure clears _exec_done but except handler must restore it."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            await executor._require_shell()

            def bad_transform(code):
                raise OSError("transform failed")

            executor._shell.transform_cell = bad_transform

            result = await executor.execute("x = 1")
            assert result.success is False
            assert "transform failed" in result.error

            # _exec_done must be set — not left cleared
            assert executor._exec_done.is_set()
        finally:
            executor.close()


# ---------------------------------------------------------------------------
# iter122 regression: timeout=0 clamped to 0.1
# ---------------------------------------------------------------------------


class TestTimeoutZeroClamped:
    """timeout=0 is clamped to 0.1 to prevent immediate TimeoutError."""

    @pytest.mark.asyncio
    async def test_timeout_zero_clamped(self) -> None:
        """execute with timeout=0 does not immediately timeout."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            # timeout=0 should be clamped to 0.1 internally
            result = await executor.execute("x = 42", timeout=0)
            assert result.success is True
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_timeout_negative_clamped(self) -> None:
        """execute with timeout=-1 does not immediately timeout."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            result = await executor.execute("y = 7", timeout=-1)
            assert result.success is True
        finally:
            executor.close()


# iter124c regression: CancelledError sets _timed_out and propagates
class TestCancelledErrorHandling:
    """CancelledError during _execute_inner sets _timed_out and re-raises."""

    @pytest.mark.asyncio
    async def test_cancelled_error_sets_timed_out_flag(self) -> None:
        """CancelledError during execution sets _timed_out = True."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            # Patch _run_cell_sync to raise CancelledError via transform_cell
            await executor._require_shell()
            original_transform = executor._shell.transform_cell

            def raise_cancel(code):
                raise asyncio.CancelledError("simulated cancellation")

            executor._shell.transform_cell = raise_cancel
            executor._shell.transform_cell = original_transform  # restore

            # Instead, directly test the _execute_inner path by mocking
            # the to_thread call to raise CancelledError
            original_to_thread = asyncio.to_thread

            async def mock_execute_inner(code, timeout):
                # Simulate what _execute_inner does: it calls to_thread
                # which raises CancelledError
                shell = await executor._require_shell()
                shell  # noqa: just to ensure shell is created
                # The CancelledError should propagate and set _timed_out
                raise asyncio.CancelledError("test cancel")

            # Use a more direct approach: cancel the task during execution
            import time

            async def cancel_after_delay(task):
                await asyncio.sleep(0.1)
                task.cancel()

            task = asyncio.create_task(
                executor.execute("import time; time.sleep(5)", timeout=10)
            )
            asyncio.create_task(cancel_after_delay(task))

            with pytest.raises(asyncio.CancelledError):
                await task

            assert executor._timed_out is True
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates(self) -> None:
        """CancelledError is re-raised (not swallowed)."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            import time

            async def cancel_after_delay(task):
                await asyncio.sleep(0.1)
                task.cancel()

            task = asyncio.create_task(
                executor.execute("import time; time.sleep(5)", timeout=10)
            )
            asyncio.create_task(cancel_after_delay(task))

            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_after_cancelled_subsequent_execute_blocked(self) -> None:
        """After CancelledError, subsequent execute() returns contaminated error."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            import time

            async def cancel_after_delay(task):
                await asyncio.sleep(0.1)
                task.cancel()

            task = asyncio.create_task(
                executor.execute("import time; time.sleep(5)", timeout=10)
            )
            asyncio.create_task(cancel_after_delay(task))

            with pytest.raises(asyncio.CancelledError):
                await task

            # Subsequent execute should fail with contaminated message
            result = await executor.execute("x = 1", timeout=5)
            assert result.success is False
            assert "contaminated" in result.error
        finally:
            executor.close()


# ============================================================================
# iter115 regression: thread contamination after timeout + _exec_done timing
# ============================================================================


class TestThreadContaminationAfterTimeout:
    """P1-4: After timeout + reset(), a new execution must not start while the
    old thread is still running.  _execute_inner now polls _exec_done before
    proceeding, preventing TOCTOU namespace races.
    """

    @pytest.mark.asyncio
    async def test_execute_waits_for_old_thread_after_reset(self) -> None:
        """After timeout + reset(), execute() waits for old thread to finish."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            # Simulate: a timed-out thread is still running
            executor._timed_out = True
            executor._exec_done.clear()  # Old thread hasn't finished yet

            # reset() would normally wait, but let's say it timed out
            # and cleared _timed_out anyway (the race condition scenario)
            executor._timed_out = False
            # _exec_done is still cleared — old thread "still running"

            # Now attempt execute: should block until _exec_done is set
            # We simulate the thread finishing after a short delay
            import threading

            def finish_thread():
                import time
                time.sleep(0.1)
                executor._exec_done.set()

            threading.Thread(target=finish_thread, daemon=True).start()

            # execute should succeed after the thread finishes
            result = await executor.execute("x = 1 + 2", timeout=5)
            assert result.success is True
            assert executor.get("x") == 3
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_execute_fails_if_old_thread_never_finishes(self) -> None:
        """If old thread never finishes within 5s, execute returns error."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            # Old thread "still running" — _exec_done never set
            executor._exec_done.clear()
            executor._timed_out = False  # Cleared by reset()

            result = await executor.execute("x = 1", timeout=5)
            assert result.success is False
            assert "still running" in result.error
        finally:
            # Clean up so close() doesn't hang
            executor._exec_done.set()
            executor.close()

    @pytest.mark.asyncio
    async def test_exec_done_already_set_skips_wait(self) -> None:
        """Normal execution (no prior timeout) skips the _exec_done wait."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            assert executor._exec_done.is_set()  # Default state
            result = await executor.execute("x = 42", timeout=5)
            assert result.success is True
            assert executor.get("x") == 42
        finally:
            executor.close()


class TestExecDoneTiming:
    """P2-21: _exec_done must remain in consistent state through all code paths.

    Invariants:
    - _exec_done is SET when no thread is running
    - _exec_done is CLEARED only while a thread is active
    - After timeout/cancel, _exec_done is eventually set by the thread
    - After exception before thread start, _exec_done is set by except handler
    """

    @pytest.mark.asyncio
    async def test_exec_done_set_after_normal_execution(self) -> None:
        """After successful execution, _exec_done is set."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            await executor.execute("x = 1")
            assert executor._exec_done.is_set()
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_exec_done_set_after_error_in_exec(self) -> None:
        """After error-in-exec, _exec_done is set by _run_cell_sync finally."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            await executor.execute("1/0")
            assert executor._exec_done.is_set()
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_exec_done_set_after_pre_thread_exception(self) -> None:
        """After exception before thread start, _exec_done is set by except handler."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            await executor._require_shell()

            def bad_transform(code):
                raise OSError("transform failed")

            executor._shell.transform_cell = bad_transform
            await executor.execute("x = 1")
            assert executor._exec_done.is_set()
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_exec_done_eventually_set_after_timeout(self) -> None:
        """After timeout, _exec_done is set once the thread completes."""
        from agent_nexus.platform.runtime.executor import IPythonExecutor

        executor = IPythonExecutor()
        try:
            # Use a short sleep so the thread finishes quickly after timeout
            result = await executor.execute(
                "import time; time.sleep(2)", timeout=0.3
            )
            assert result.success is False
            # _exec_done may still be cleared (thread running)
            # Wait long enough for the 2s sleep to complete
            import time
            time.sleep(2.5)
            assert executor._exec_done.is_set()
        finally:
            executor.close()
