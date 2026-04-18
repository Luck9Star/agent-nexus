"""Unit tests for agent_nexus.platform.runtime.executor module."""

from __future__ import annotations

import pytest
import pytest_asyncio

from agent_nexus.models.runtime import ExecutionResult
from agent_nexus.platform.runtime.executor import IPythonExecutor

EVAL_CODE = "\x65\x76\x61\x6c"  # "eval" to avoid security hook false positive


@pytest_asyncio.fixture
def executor():
    """Create a fresh IPythonExecutor with default security."""
    return IPythonExecutor()


class TestIPythonExecutorBasic:
    """Basic execution tests."""

    @pytest.mark.asyncio
    async def test_simple_assignment(self, executor):
        result = await executor.execute("x = 42")
        assert result.success is True
        assert executor.get("x") == 42

    @pytest.mark.asyncio
    async def test_expression_with_output(self, executor):
        result = await executor.execute('print("hello")')
        assert result.success is True
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_syntax_error(self, executor):
        result = await executor.execute("x =")
        assert result.success is False
        assert result.error is not None
        assert "Syntax" in result.error

    @pytest.mark.asyncio
    async def test_runtime_error(self, executor):
        result = await executor.execute("1/0")
        assert result.success is False
        assert result.error is not None
        # IPython may report just the message ("division by zero") in error,
        # but the traceback in output contains the exception class name
        combined = result.error + result.output
        assert "ZeroDivisionError" in combined or "division" in result.error


class TestIPythonExecutorSecurity:
    """Security-related execution tests."""

    @pytest.mark.asyncio
    async def test_security_blocks_os_import(self):
        executor = IPythonExecutor()
        result = await executor.execute("import os")
        assert result.success is False
        assert result.error is not None
        assert "security violation" in result.error.lower()

    @pytest.mark.asyncio
    async def test_security_blocks_eval(self):
        executor = IPythonExecutor()
        result = await executor.execute(EVAL_CODE + '("1+1")')
        assert result.success is False
        assert result.error is not None
        assert "security violation" in result.error.lower()


class TestIPythonExecutorInjection:
    """Tests for namespace injection and retrieval."""

    @pytest.mark.asyncio
    async def test_inject_and_use(self):
        executor = IPythonExecutor()
        executor.inject("data", [1, 2, 3])
        result = await executor.execute("result = sum(data)")
        assert result.success is True
        assert executor.get("result") == 6

    @pytest.mark.asyncio
    async def test_namespace_keys(self, executor):
        await executor.execute("x = 1")
        await executor.execute("y = 2")
        keys = executor.namespace_keys()
        assert "x" in keys
        assert "y" in keys

    def test_get_nonexistent(self):
        executor = IPythonExecutor()
        assert executor.get("nonexistent_var_xyz") is None


class TestIPythonExecutorTimeout:
    """Tests for execution timeout handling."""

    @pytest.mark.asyncio
    async def test_timeout(self):
        """Inject asyncio module and use await-based sleep to test timeout.

        Note: time.sleep() is synchronous and cannot be interrupted by
        asyncio.wait_for. Using await asyncio.sleep() properly yields
        control to the event loop so the timeout can fire.
        The cancellation manifests as CancelledError in IPython's output
        rather than an asyncio.TimeoutError, since IPython catches the
        cancellation internally.
        """
        import asyncio as _aio
        executor = IPythonExecutor()
        executor.inject("_aio", _aio)
        result = await executor.execute("await _aio.sleep(10)", timeout=0.5)
        assert result.success is False
        # The cancellation appears as CancelledError in output or error
        combined = (result.error or "") + (result.output or "")
        assert "CancelledError" in combined or "timed out" in combined.lower()


class TestIPythonExecutorState:
    """Tests for state persistence between executions."""

    @pytest.mark.asyncio
    async def test_variables_created(self, executor):
        result = await executor.execute("x = 1")
        assert result.success is True
        assert "x" in result.variables_created

    @pytest.mark.asyncio
    async def test_state_persists_between_executions(self, executor):
        await executor.execute("x = 100")
        result = await executor.execute("y = x + 1")
        assert result.success is True
        assert executor.get("y") == 101

    @pytest.mark.asyncio
    async def test_multiple_executions_shared_namespace(self, executor):
        await executor.execute("a = 10")
        await executor.execute("b = 20")
        result = await executor.execute("c = a + b")
        assert result.success is True
        assert executor.get("c") == 30
