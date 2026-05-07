"""E2E tests for Python Runtime: variable/function injection and code execution.

Tests the full runtime lifecycle from setup through execution to cleanup.
"""

import asyncio


class TestRuntimeE2E:
    """E2E runtime scenarios."""

    def _run(self, coro):
        """Run an async coroutine synchronously for test convenience."""
        return asyncio.run(coro)

    def test_runtime_execute_basic_code(self):
        """Runtime executes basic Python code and returns result."""
        from agent_nexus.platform.runtime.runtime import PythonRuntime

        rt = PythonRuntime()
        try:
            result = self._run(rt.execute("x = 1 + 2"))
            assert result.success

            value = rt.retrieve("x")
            assert value == 3
        finally:
            rt.close()

    def test_runtime_inject_and_use_variable(self):
        """Runtime injects variable and executes code using it."""
        from agent_nexus.models.runtime import Variable
        from agent_nexus.platform.runtime.runtime import PythonRuntime

        rt = PythonRuntime()
        try:
            rt.inject_variable(
                Variable(
                    name="data",
                    description="input data",
                    value=[1, 2, 3, 4, 5],
                )
            )

            result = self._run(rt.execute("total = sum(data)"))
            assert result.success

            assert rt.retrieve("total") == 15
        finally:
            rt.close()

    def test_runtime_inject_callable(self):
        """Runtime injects callable function and executes code using it."""
        from agent_nexus.platform.runtime.runtime import PythonRuntime

        rt = PythonRuntime()
        try:
            rt.inject_callable("double", lambda x: x * 2, "Double a number")

            result = self._run(rt.execute("result = double(21)"))
            assert result.success

            assert rt.retrieve("result") == 42
        finally:
            rt.close()

    def test_runtime_security_blocks_dangerous_code(self):
        """SecurityChecker blocks imports of forbidden modules."""
        from agent_nexus.platform.runtime.runtime import PythonRuntime

        rt = PythonRuntime()
        try:
            result = self._run(rt.execute("import os"))
            assert not result.success
        finally:
            rt.close()

    def test_runtime_reset_clears_state(self):
        """Runtime reset clears injected variables and execution state."""
        from agent_nexus.models.runtime import Variable
        from agent_nexus.platform.runtime.runtime import PythonRuntime

        rt = PythonRuntime()
        try:
            rt.inject_variable(
                Variable(
                    name="my_var",
                    description="test",
                    value=42,
                )
            )

            result = self._run(rt.execute("y = my_var + 1"))
            assert result.success

            rt.reset()

            # After reset, variable should be gone from namespace
            assert rt.retrieve("my_var") is None
        finally:
            rt.close()
