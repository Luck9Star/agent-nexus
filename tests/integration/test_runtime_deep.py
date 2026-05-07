"""Deep runtime dynamic verification: IPythonExecutor edge cases.

Tests real execution paths that mocks cannot validate:
- I/O-bound timeout handling
- Concurrent variable mutation
- Callable injection exception propagation
- Security bypass attempts with real execution
- Namespace isolation under concurrent access
- Reset/close lifecycle edge cases
"""

from __future__ import annotations

import asyncio

import pytest

from agent_nexus.platform.runtime.executor import IPythonExecutor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def executor():
    """Provide a fresh IPythonExecutor per test."""
    ex = IPythonExecutor()
    yield ex
    ex.close()


# ---------------------------------------------------------------------------
# 1. Timeout edge cases with real execution
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestTimeoutRealExecution:
    """Verify timeout handling with actual CPU-bound and I/O-bound code."""

    @pytest.mark.asyncio
    async def test_io_bound_sleep_timeout(self, executor: IPythonExecutor) -> None:
        """I/O-bound time.sleep() must be timed out."""
        result = await executor.execute("import time; time.sleep(3)", timeout=0.5)
        assert result.success is False
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_timeout_minimum_clamp(self, executor: IPythonExecutor) -> None:
        """Timeout values below 0.1s are clamped to 0.1s."""
        result = await executor.execute("import time; time.sleep(1)", timeout=0.05)
        assert result.success is False
        assert "timed out" in result.error.lower()

    @pytest.mark.asyncio
    async def test_recovery_after_timeout_reset(self) -> None:
        """After I/O-bound timeout with short sleep, reset() recovers the executor.

        Uses time.sleep(2) with timeout=0.5s so the thread finishes shortly
        after the timeout fires.  reset() waits up to 5s for the thread,
        so it can succeed once the sleep completes.
        """
        ex = IPythonExecutor()
        try:
            # Trigger timeout with I/O-bound code
            result = await ex.execute("import time; time.sleep(2)", timeout=0.5)
            assert result.success is False
            assert "timed out" in result.error.lower()

            # Wait for the thread to actually finish (sleep(2) completes)
            await asyncio.sleep(2.0)

            # reset() should now succeed — thread is done
            ex.reset()

            # Should work again after reset
            result2 = await ex.execute("x = 42")
            assert result2.success is True
            assert ex.get("x") == 42
        finally:
            ex.close()


# ---------------------------------------------------------------------------
# 2. Concurrent execution edge cases
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestConcurrentExecutionReal:
    """Verify concurrent execution with real code."""

    @pytest.mark.asyncio
    async def test_concurrent_mutation_shared_variable(
        self,
        executor: IPythonExecutor,
    ) -> None:
        """Concurrent writes to same variable are serialized by _exec_lock."""
        # Pre-inject a shared list
        executor.inject("data", [])

        # Launch concurrent executions that modify the same variable
        results = await asyncio.gather(
            executor.execute("data.append(1)"),
            executor.execute("data.append(2)"),
            executor.execute("data.append(3)"),
        )

        # All should succeed
        assert all(r.success for r in results)

        # All three appends should have happened
        data = executor.get("data")
        assert len(data) == 3
        assert sorted(data) == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_sequential_execution_preserves_order(
        self,
        executor: IPythonExecutor,
    ) -> None:
        """Sequential executions preserve variable assignment order."""
        await executor.execute("x = 1")
        await executor.execute("x = x + 1")
        await executor.execute("x = x * 10")

        assert executor.get("x") == 20

    @pytest.mark.asyncio
    async def test_inject_during_execution(self, executor: IPythonExecutor) -> None:
        """Injecting while execution is running is safe (queued or direct)."""
        # Start a slow execution
        task = asyncio.create_task(executor.execute("import time; time.sleep(0.3); x = 1"))

        # Inject while it's running (should be queued or applied after)
        await asyncio.sleep(0.05)
        executor.inject("y", 99)

        result = await task
        assert result.success is True
        assert executor.get("x") == 1
        assert executor.get("y") == 99


# ---------------------------------------------------------------------------
# 3. Callable injection and execution
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestCallableInjectionReal:
    """Verify callable injection with real execution."""

    @pytest.mark.asyncio
    async def test_callable_injection_and_invocation(
        self,
        executor: IPythonExecutor,
    ) -> None:
        """Injected callable can be invoked in user code."""
        executor.inject("add", lambda a, b: a + b)
        result = await executor.execute("result = add(3, 4)")
        assert result.success is True
        assert executor.get("result") == 7

    @pytest.mark.asyncio
    async def test_callable_exception_propagates(
        self,
        executor: IPythonExecutor,
    ) -> None:
        """Exception in injected callable propagates as execution error.

        result.error contains the exception message (e.g. 'division by zero').
        result.output contains the full traceback with the exception class name.
        """
        executor.inject("boom", lambda: 1 / 0)
        result = await executor.execute("boom()")
        assert result.success is False
        assert "ZeroDivisionError" in result.output or "division by zero" in result.error

    @pytest.mark.asyncio
    async def test_callable_with_closure_over_injected(
        self,
        executor: IPythonExecutor,
    ) -> None:
        """Lambda closure over injected variables works."""
        executor.inject("add_base", lambda x: x + 10)
        result = await executor.execute("y = add_base(5)")
        assert result.success is True
        assert executor.get("y") == 15

    @pytest.mark.asyncio
    async def test_large_object_injection(self, executor: IPythonExecutor) -> None:
        """Injecting a large object doesn't crash the executor."""
        big_data = list(range(100_000))
        executor.inject("big_list", big_data)
        result = await executor.execute("total = sum(big_list)")
        assert result.success is True
        assert executor.get("total") == sum(range(100_000))


# ---------------------------------------------------------------------------
# 4. Security bypass attempts with real execution
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestSecurityBypassRealExecution:
    """Attempt real security bypasses -- all must be blocked BEFORE execution."""

    @pytest.mark.asyncio
    async def test_os_system_blocked(self, executor: IPythonExecutor) -> None:
        """os.system() must be blocked by qualified-call rule."""
        result = await executor.execute("import os; os.system('echo pwned')")
        assert result.success is False
        assert "security violation" in result.error.lower()

    @pytest.mark.asyncio
    async def test_subprocess_run_blocked(self, executor: IPythonExecutor) -> None:
        """subprocess.run() must be blocked by qualified-call rule."""
        result = await executor.execute("import subprocess; subprocess.run(['ls'])")
        assert result.success is False
        assert "security violation" in result.error.lower()

    @pytest.mark.asyncio
    async def test_os_popen_blocked(self, executor: IPythonExecutor) -> None:
        """os.popen() must be blocked by qualified-call rule."""
        result = await executor.execute("import os; os.popen('whoami')")
        assert result.success is False
        assert "security violation" in result.error.lower()

    @pytest.mark.asyncio
    async def test_builtins_subscript_exec_blocked(
        self,
        executor: IPythonExecutor,
    ) -> None:
        """__builtins__['exec']('code') must be blocked."""
        result = await executor.execute("__builtins__['exec']('print(1)')")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_getattr_dynamic_eval_blocked(
        self,
        executor: IPythonExecutor,
    ) -> None:
        """getattr(obj, 'eval') dynamic dispatch must be blocked."""
        result = await executor.execute("getattr(__builtins__, 'eval')('1+1')")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_type_three_arg_class_creation_blocked(
        self,
        executor: IPythonExecutor,
    ) -> None:
        """type('X', bases, dict) sandbox escape must be blocked."""
        result = await executor.execute("type('Evil', (), {'x': 1})")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_mro_chain_traversal_blocked(
        self,
        executor: IPythonExecutor,
    ) -> None:
        """str.__mro__ chain traversal must be blocked."""
        result = await executor.execute("str.__mro__")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_pathlib_import_blocked(self, executor: IPythonExecutor) -> None:
        """import pathlib must be blocked (Path provides file I/O)."""
        result = await executor.execute("import pathlib; pathlib.Path('/etc/passwd')")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_builtins_import_blocked(self, executor: IPythonExecutor) -> None:
        """import builtins must be blocked (access to eval/exec/compile)."""
        result = await executor.execute("import builtins; builtins.eval('1')")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_pdb_import_blocked(self, executor: IPythonExecutor) -> None:
        """import pdb must be blocked (interactive debugger escapes sandbox)."""
        result = await executor.execute("import pdb; pdb.set_trace()")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_safe_code_still_works(
        self,
        executor: IPythonExecutor,
    ) -> None:
        """Legitimate safe code still executes after all security rules."""
        result = await executor.execute("x = [i**2 for i in range(10)]\ntotal = sum(x)")
        assert result.success is True
        assert executor.get("total") == 285

    @pytest.mark.asyncio
    async def test_json_import_allowed(self, executor: IPythonExecutor) -> None:
        """import json is allowed (safe stdlib module)."""
        result = await executor.execute("import json\ndata = json.dumps({'key': 'value'})")
        assert result.success is True
        assert executor.get("data") == '{"key": "value"}'

    @pytest.mark.asyncio
    async def test_math_import_allowed(self, executor: IPythonExecutor) -> None:
        """import math is allowed."""
        result = await executor.execute("import math\nresult = math.sqrt(144)")
        assert result.success is True
        assert executor.get("result") == 12.0


# ---------------------------------------------------------------------------
# 5. Reset/Close lifecycle edge cases
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestLifecycleEdgeCases:
    """Verify reset/close edge cases with real execution."""

    @pytest.mark.asyncio
    async def test_close_prevents_further_execution(self) -> None:
        """After close(), execute() returns error."""
        ex = IPythonExecutor()
        result = await ex.execute("x = 1")
        assert result.success is True

        ex.close()

        result2 = await ex.execute("y = 2")
        assert result2.success is False
        assert "closed" in result2.error.lower()

    @pytest.mark.asyncio
    async def test_reset_preserves_ipython_internals(self) -> None:
        """reset() preserves IPython internals (In, Out, exit, etc.)."""
        ex = IPythonExecutor()
        try:
            await ex.execute("x = 1")
            ex.reset()

            # Namespace should be empty but IPython internals preserved
            keys = ex.namespace_keys()
            assert "x" not in keys

            # Shell should still work
            result = await ex.execute("y = 2")
            assert result.success is True
            assert ex.get("y") == 2
        finally:
            ex.close()

    @pytest.mark.asyncio
    async def test_double_reset_is_safe(self) -> None:
        """Calling reset() twice doesn't crash."""
        ex = IPythonExecutor()
        try:
            await ex.execute("x = 1")
            ex.reset()
            ex.reset()  # Second reset -- should not raise

            result = await ex.execute("y = 3")
            assert result.success is True
        finally:
            ex.close()

    @pytest.mark.asyncio
    async def test_get_before_shell_creation(self) -> None:
        """get() before first execute() returns pending injects."""
        ex = IPythonExecutor()
        try:
            ex.inject("a", 42)
            # get() should find the pending inject
            assert ex.get("a") == 42
            assert ex.get("nonexistent", "default") == "default"
        finally:
            ex.close()

    @pytest.mark.asyncio
    async def test_namespace_keys_excludes_underscore_prefix(
        self,
        executor: IPythonExecutor,
    ) -> None:
        """Variables starting with _ are excluded from namespace_keys."""
        await executor.execute("_private = 1\npublic = 2")
        keys = executor.namespace_keys()
        assert "public" in keys
        assert "_private" not in keys
