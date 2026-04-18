"""Unit tests for agent_nexus.platform.runtime.runtime module."""

from __future__ import annotations

import pytest

from agent_nexus.models.runtime import (
    ExecutionResult,
    Function,
    RuntimeType,
    Variable,
)
from agent_nexus.platform.runtime.runtime import PythonRuntime
from agent_nexus.platform.runtime.executor import _IPYTHON_INTERNALS, IPythonExecutor
from agent_nexus.platform.runtime.describer import TieredRuntimeDescriber


# ---------------------------------------------------------------------------
# inject_variable
# ---------------------------------------------------------------------------

class TestInjectVariable:
    """Tests for PythonRuntime.inject_variable()."""

    @pytest.mark.asyncio
    async def test_inject_with_value(self, shared_runtime) -> None:
        shared_runtime.inject_variable(
            Variable(name="data", description="test data", value=[1, 2, 3])
        )
        assert shared_runtime.retrieve("data") == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_inject_without_value(self, shared_runtime) -> None:
        """Variable with value=None is stored in registry and injected as None."""
        var = Variable(name="placeholder", description="no value", value=None)
        shared_runtime.inject_variable(var)
        # Variable is in the registry (describe_variables shows it)
        desc = shared_runtime.describe_variables()
        assert "placeholder" in desc
        # Variable IS in the namespace with value None
        assert shared_runtime.retrieve("placeholder") is None


# ---------------------------------------------------------------------------
# inject_function
# ---------------------------------------------------------------------------

class TestInjectFunction:
    """Tests for PythonRuntime.inject_function()."""

    def test_function_metadata_stored(self, shared_runtime) -> None:
        fn = Function(
            name="process",
            description="Process data",
            signature="(x: list) -> list",
        )
        shared_runtime.inject_function(fn)
        desc = shared_runtime.describe_functions()
        assert "process" in desc
        assert "Process data" in desc


# ---------------------------------------------------------------------------
# inject_callable
# ---------------------------------------------------------------------------

class TestInjectCallable:
    """Tests for PythonRuntime.inject_callable()."""

    @pytest.mark.asyncio
    async def test_inject_callable(self, shared_runtime) -> None:
        shared_runtime.inject_callable("double", lambda x: x * 2, description="Double a value")
        # The callable should be available in the namespace
        result = await shared_runtime.execute("result = double(5)")
        assert result.success is True
        assert shared_runtime.retrieve("result") == 10

    def test_async_detection(self, shared_runtime) -> None:
        async def my_async_func() -> None:
            pass

        shared_runtime.inject_callable("my_async_func", my_async_func)
        desc = shared_runtime.describe_functions()
        assert "(async)" in desc

    def test_sync_not_marked_async(self, shared_runtime) -> None:
        def my_sync_func() -> None:
            pass

        shared_runtime.inject_callable("my_sync_func", my_sync_func)
        desc = shared_runtime.describe_functions()
        # Should NOT contain (async) marker for this function
        assert "my_sync_func" in desc
        # The line for my_sync_func should not have (async)
        lines = desc.strip().split("\n")
        sync_line = [l for l in lines if "my_sync_func" in l][0]
        assert "(async)" not in sync_line


# ---------------------------------------------------------------------------
# inject_type
# ---------------------------------------------------------------------------

class TestInjectType:
    """Tests for PythonRuntime.inject_type()."""

    def test_type_stored(self, shared_runtime) -> None:
        shared_runtime.inject_type(
            RuntimeType(
                name="User",
                description="A user record",
                python_type="dict",
            )
        )
        desc = shared_runtime.describe_types(level="names")
        assert "User" in desc

    def test_describe_types_shows_type(self, shared_runtime) -> None:
        shared_runtime.inject_type(RuntimeType(name="Item", description="An item"))
        desc = shared_runtime.describe_types()
        assert "Item" in desc


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

class TestExecute:
    """Tests for PythonRuntime.execute()."""

    @pytest.mark.asyncio
    async def test_simple_code(self, shared_runtime) -> None:
        result = await shared_runtime.execute("result = 1 + 2")
        assert result.success is True
        assert isinstance(result, ExecutionResult)

    @pytest.mark.asyncio
    async def test_security_block(self, shared_runtime) -> None:
        result = await shared_runtime.execute("import os")
        assert result.success is False
        assert result.error is not None
        assert "security violation" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_and_retrieve(self, shared_runtime) -> None:
        await shared_runtime.execute("x = 42")
        assert shared_runtime.retrieve("x") == 42


# ---------------------------------------------------------------------------
# describe_variables
# ---------------------------------------------------------------------------

class TestDescribeVariables:
    """Tests for PythonRuntime.describe_variables()."""

    def test_with_variables(self, shared_runtime) -> None:
        shared_runtime.inject_variable(Variable(name="count", description="Counter", type_name="int"))
        desc = shared_runtime.describe_variables()
        assert "count" in desc
        assert "Counter" in desc

    def test_empty(self, shared_runtime) -> None:
        assert shared_runtime.describe_variables() == ""


# ---------------------------------------------------------------------------
# describe_functions
# ---------------------------------------------------------------------------

class TestDescribeFunctions:
    """Tests for PythonRuntime.describe_functions()."""

    def test_with_functions(self, shared_runtime) -> None:
        shared_runtime.inject_function(
            Function(name="process", description="Process data", signature="(x) -> y")
        )
        desc = shared_runtime.describe_functions()
        assert "process" in desc
        assert "(x) -> y" in desc
        assert "Process data" in desc

    def test_async_marker(self, shared_runtime) -> None:
        shared_runtime.inject_function(
            Function(name="fetch", description="Fetch data", is_async=True)
        )
        desc = shared_runtime.describe_functions()
        assert "(async)" in desc
        assert "fetch" in desc

    def test_empty(self, shared_runtime) -> None:
        assert shared_runtime.describe_functions() == ""


# ---------------------------------------------------------------------------
# describe_types
# ---------------------------------------------------------------------------

class TestDescribeTypes:
    """Tests for PythonRuntime.describe_types() at various detail levels."""

    def _inject_types(self, rt: PythonRuntime) -> None:
        rt.inject_type(RuntimeType(name="User", description="A user record"))
        rt.inject_type(RuntimeType(name="Item", description="An item"))

    def test_level_names(self, shared_runtime) -> None:
        self._inject_types(shared_runtime)
        desc = shared_runtime.describe_types(level="names")
        assert "User" in desc
        assert "Item" in desc
        # names level should be comma-separated without descriptions
        assert ":" not in desc

    def test_level_summary(self, shared_runtime) -> None:
        self._inject_types(shared_runtime)
        desc = shared_runtime.describe_types(level="summary")
        assert "User" in desc
        assert "A user record" in desc
        assert "Item" in desc
        assert "An item" in desc

    def test_level_schema(self, shared_runtime) -> None:
        shared_runtime.inject_type(
            RuntimeType(
                name="User",
                description="User object",
                json_schema={"type": "object", "properties": {"name": {"type": "string"}}},
            )
        )
        desc = shared_runtime.describe_types(level="schema")
        assert "User" in desc
        assert "Schema:" in desc

    def test_level_schema_python_type_fallback(self, shared_runtime) -> None:
        shared_runtime.inject_type(
            RuntimeType(
                name="Config",
                description="Configuration",
                python_type="dict",
            )
        )
        desc = shared_runtime.describe_types(level="schema")
        assert "Python type: dict" in desc

    def test_unknown_level_falls_back_to_names(self, shared_runtime) -> None:
        self._inject_types(shared_runtime)
        desc = shared_runtime.describe_types(level="nonexistent_level")
        # Should fall back to "names" behavior
        assert "User" in desc
        assert "Item" in desc

    def test_empty(self, shared_runtime) -> None:
        assert shared_runtime.describe_types() == ""


# ---------------------------------------------------------------------------
# _format_signature
# ---------------------------------------------------------------------------

class TestFormatSignature:
    """Tests for PythonRuntime._format_signature()."""

    def test_real_function(self) -> None:
        def example(x: int, y: str = "hi") -> None:
            pass

        sig = PythonRuntime._format_signature(example)
        assert "x" in sig
        assert "y" in sig

    def test_lambda(self) -> None:
        sig = PythonRuntime._format_signature(lambda a, b: a + b)
        assert "a" in sig

    def test_non_callable(self) -> None:
        sig = PythonRuntime._format_signature("not_a_function")
        assert sig == ""


# ============================================================================
# _IPYTHON_INTERNALS no duplicates (from iter39)
# ============================================================================


class TestIPythonInternalsNoDuplicates:
    """_IPYTHON_INTERNALS frozenset should not contain duplicate entries."""

    def test_no_duplicate_entries(self) -> None:
        """Each internal key appears exactly once."""
        items = list(_IPYTHON_INTERNALS)
        assert len(items) == len(set(items))

    def test_contains_expected_keys(self) -> None:
        """All expected IPython internal keys are present."""
        expected = {"In", "Out", "exit", "quit", "get_ipython",
                    "_", "__", "___", "_ih", "_oh", "_sh", "_dh"}
        assert _IPYTHON_INTERNALS == expected


# ============================================================================
# Retrieve/get distinguishes None value from missing key (from iter39)
# ============================================================================


class TestRetrieveDistinguishesNoneFromMissing:
    """retrieve() and get() must distinguish between 'value is None' and 'key missing'."""

    @pytest.mark.asyncio
    async def test_executor_get_returns_default_for_missing_key(self) -> None:
        """get() returns the provided default for a non-existent key."""
        executor = IPythonExecutor()
        try:
            sentinel = object()
            result = executor.get("nonexistent_key_xyz", default=sentinel)
            assert result is sentinel
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_executor_get_returns_none_value(self) -> None:
        """get() returns actual None when the variable's value is None."""
        executor = IPythonExecutor()
        try:
            executor.inject("x", None)
            sentinel = object()
            result = executor.get("x", default=sentinel)
            assert result is None
            assert result is not sentinel
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_executor_get_default_none_for_missing(self) -> None:
        """get() returns None (the default) when key is missing and no custom default."""
        executor = IPythonExecutor()
        try:
            result = executor.get("nonexistent_key_xyz")
            assert result is None
        finally:
            executor.close()

    @pytest.mark.asyncio
    async def test_runtime_retrieve_returns_none_for_explicit_none(self) -> None:
        """retrieve() returns None when variable was explicitly injected with value=None."""
        runtime = PythonRuntime()
        try:
            runtime.inject_variable(Variable(name="x", description="test", value=None))
            # After the fix, None is injected into namespace, so retrieve returns None
            result = runtime.retrieve("x")
            assert result is None
        finally:
            runtime.close()

    @pytest.mark.asyncio
    async def test_describer_l3_value_none_variable(self) -> None:
        """l3_value returns 'null' when variable is explicitly set to None."""
        runtime = PythonRuntime()
        try:
            runtime.inject_variable(Variable(name="x", description="test", value=None))
            describer = TieredRuntimeDescriber(runtime)
            result = describer.l3_value("x")
            # After the fix, None is in namespace, so l3_value serializes it
            assert "null" in result.lower()
            assert "[Variable: x]" in result
        finally:
            runtime.close()

    @pytest.mark.asyncio
    async def test_describer_l3_value_zero_not_confused_with_missing(self) -> None:
        """l3_value correctly formats a variable whose value is 0 (falsy but not None)."""
        runtime = PythonRuntime()
        try:
            await runtime.execute("x = 0")
            describer = TieredRuntimeDescriber(runtime)
            result = describer.l3_value("x")
            assert "[Variable: x]" in result
            assert "0" in result
        finally:
            runtime.close()

    @pytest.mark.asyncio
    async def test_describer_l3_value_false_not_confused_with_missing(self) -> None:
        """l3_value correctly formats a variable whose value is False."""
        runtime = PythonRuntime()
        try:
            await runtime.execute("flag = False")
            describer = TieredRuntimeDescriber(runtime)
            result = describer.l3_value("flag")
            assert "[Variable: flag]" in result
            assert "false" in result.lower()
        finally:
            runtime.close()

    @pytest.mark.asyncio
    async def test_describer_l3_value_empty_string_not_confused_with_missing(self) -> None:
        """l3_value correctly formats a variable whose value is empty string."""
        runtime = PythonRuntime()
        try:
            await runtime.execute("s = ''")
            describer = TieredRuntimeDescriber(runtime)
            result = describer.l3_value("s")
            assert "[Variable: s]" in result
        finally:
            runtime.close()


# ============================================================================
# Security bypass vectors (globals/vars/locals + __builtins__ subscript)
# ============================================================================


class TestSecurityCheckerBypass:
    """Verify that introspection bypass vectors are blocked by the security checker.

    These test that:
    - globals(), vars(), locals() are blocked as function calls
    - __builtins__['exec'] subscript access is blocked
    - __builtins__['open'] subscript access is blocked
    - Normal safe code still passes (regression)
    """

    @pytest.mark.asyncio
    async def test_globals_blocked(self) -> None:
        """globals() leaks the full namespace — must be blocked."""
        runtime = PythonRuntime()
        try:
            result = await runtime.execute("g = globals()")
            assert result.success is False
            assert result.error is not None
            assert "security violation" in result.error.lower()
        finally:
            runtime.close()

    @pytest.mark.asyncio
    async def test_vars_blocked(self) -> None:
        """vars() exposes __dict__ of the current scope — must be blocked."""
        runtime = PythonRuntime()
        try:
            result = await runtime.execute("v = vars()")
            assert result.success is False
            assert result.error is not None
            assert "security violation" in result.error.lower()
        finally:
            runtime.close()

    @pytest.mark.asyncio
    async def test_locals_blocked(self) -> None:
        """locals() exposes local namespace — must be blocked."""
        runtime = PythonRuntime()
        try:
            result = await runtime.execute("l = locals()")
            assert result.success is False
            assert result.error is not None
            assert "security violation" in result.error.lower()
        finally:
            runtime.close()

    @pytest.mark.asyncio
    async def test_builtins_subscript_exec_blocked(self) -> None:
        """__builtins__['exec'] is a sandbox escape vector — must be blocked."""
        runtime = PythonRuntime()
        try:
            result = await runtime.execute("__builtins__['exec']('pass')")
            assert result.success is False
            assert result.error is not None
            assert "security violation" in result.error.lower()
        finally:
            runtime.close()

    @pytest.mark.asyncio
    async def test_builtins_subscript_open_blocked(self) -> None:
        """__builtins__['open'] is a file access escape vector — must be blocked."""
        runtime = PythonRuntime()
        try:
            result = await runtime.execute("__builtins__['open']('/etc/passwd')")
            assert result.success is False
            assert result.error is not None
            assert "security violation" in result.error.lower()
        finally:
            runtime.close()

    @pytest.mark.asyncio
    async def test_safe_code_still_passes(self) -> None:
        """Regression: normal safe code must still execute successfully."""
        runtime = PythonRuntime()
        try:
            result = await runtime.execute("x = 1 + 2")
            assert result.success is True
            assert runtime.retrieve("x") == 3
        finally:
            runtime.close()


# ============================================================================
# Security Checker additional blocks (breakpoint/input/importlib/pickle/socket)
# ============================================================================


class TestSecurityCheckerAdditionalBlocks:
    """Verify that breakpoint(), input(), and dangerous imports are blocked."""

    @pytest.mark.asyncio
    async def test_breakpoint_blocked(self) -> None:
        """breakpoint() drops into pdb giving unrestricted runtime access — must be blocked."""
        runtime = PythonRuntime()
        try:
            result = await runtime.execute("breakpoint()")
            assert result.success is False
            assert result.error is not None
            assert "security violation" in result.error.lower()
        finally:
            runtime.close()

    @pytest.mark.asyncio
    async def test_input_blocked(self) -> None:
        """input() can hang the executor or probe the environment — must be blocked."""
        runtime = PythonRuntime()
        try:
            result = await runtime.execute("input('prompt')")
            assert result.success is False
            assert result.error is not None
            assert "security violation" in result.error.lower()
        finally:
            runtime.close()

    @pytest.mark.asyncio
    async def test_importlib_blocked(self) -> None:
        """importlib bypasses all import restrictions — must be blocked."""
        runtime = PythonRuntime()
        try:
            result = await runtime.execute("import importlib")
            assert result.success is False
            assert result.error is not None
            assert "security violation" in result.error.lower()
        finally:
            runtime.close()

    @pytest.mark.asyncio
    async def test_pickle_blocked(self) -> None:
        """Deserialisation of untrusted data leads to arbitrary code execution — must be blocked."""
        runtime = PythonRuntime()
        try:
            result = await runtime.execute("import pickle")
            assert result.success is False
            assert result.error is not None
            assert "security violation" in result.error.lower()
        finally:
            runtime.close()

    @pytest.mark.asyncio
    async def test_socket_blocked(self) -> None:
        """socket provides raw network access — must be blocked."""
        runtime = PythonRuntime()
        try:
            result = await runtime.execute("import socket")
            assert result.success is False
            assert result.error is not None
            assert "security violation" in result.error.lower()
        finally:
            runtime.close()

    @pytest.mark.asyncio
    async def test_safe_code_still_passes(self) -> None:
        """Regression: normal safe code must still execute successfully."""
        runtime = PythonRuntime()
        try:
            result = await runtime.execute("x = 1 + 2")
            assert result.success is True
            assert runtime.retrieve("x") == 3
        finally:
            runtime.close()
