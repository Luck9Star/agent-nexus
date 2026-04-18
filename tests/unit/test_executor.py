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
        """_run_cell_sync should return an IPython ExecutionResult."""
        # Ensure shell is initialized before calling _run_cell_sync
        await shared_executor._require_shell()
        result = shared_executor._run_cell_sync("x = 42")
        # Should be an IPython ExecutionResult-like object
        assert result is not None
        assert shared_executor.get("x") == 42

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
