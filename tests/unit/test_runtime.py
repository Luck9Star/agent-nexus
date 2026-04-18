"""Unit tests for agent_nexus.platform.runtime.runtime module."""

from __future__ import annotations

import asyncio

import pytest

from agent_nexus.models.runtime import (
    ExecutionResult,
    Function,
    RuntimeType,
    Variable,
)
from agent_nexus.platform.runtime.runtime import PythonRuntime


# ---------------------------------------------------------------------------
# inject_variable
# ---------------------------------------------------------------------------

class TestInjectVariable:
    """Tests for PythonRuntime.inject_variable()."""

    @pytest.mark.asyncio
    async def test_inject_with_value(self) -> None:
        rt = PythonRuntime()
        rt.inject_variable(
            Variable(name="data", description="test data", value=[1, 2, 3])
        )
        assert rt.retrieve("data") == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_inject_without_value(self) -> None:
        """Variable with value=None is stored in registry but not in namespace."""
        rt = PythonRuntime()
        var = Variable(name="placeholder", description="no value", value=None)
        rt.inject_variable(var)
        # Variable is in the registry (describe_variables shows it)
        desc = rt.describe_variables()
        assert "placeholder" in desc
        # But not in the namespace
        assert rt.retrieve("placeholder") is None


# ---------------------------------------------------------------------------
# inject_function
# ---------------------------------------------------------------------------

class TestInjectFunction:
    """Tests for PythonRuntime.inject_function()."""

    def test_function_metadata_stored(self) -> None:
        rt = PythonRuntime()
        fn = Function(
            name="process",
            description="Process data",
            signature="(x: list) -> list",
        )
        rt.inject_function(fn)
        desc = rt.describe_functions()
        assert "process" in desc
        assert "Process data" in desc


# ---------------------------------------------------------------------------
# inject_callable
# ---------------------------------------------------------------------------

class TestInjectCallable:
    """Tests for PythonRuntime.inject_callable()."""

    @pytest.mark.asyncio
    async def test_inject_callable(self) -> None:
        rt = PythonRuntime()
        rt.inject_callable("double", lambda x: x * 2, description="Double a value")
        # The callable should be available in the namespace
        result = await rt.execute("result = double(5)")
        assert result.success is True
        assert rt.retrieve("result") == 10

    def test_async_detection(self) -> None:
        rt = PythonRuntime()

        async def my_async_func() -> None:
            pass

        rt.inject_callable("my_async_func", my_async_func)
        desc = rt.describe_functions()
        assert "(async)" in desc

    def test_sync_not_marked_async(self) -> None:
        rt = PythonRuntime()

        def my_sync_func() -> None:
            pass

        rt.inject_callable("my_sync_func", my_sync_func)
        desc = rt.describe_functions()
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

    def test_type_stored(self) -> None:
        rt = PythonRuntime()
        rt.inject_type(
            RuntimeType(
                name="User",
                description="A user record",
                python_type="dict",
            )
        )
        desc = rt.describe_types(level="names")
        assert "User" in desc

    def test_describe_types_shows_type(self) -> None:
        rt = PythonRuntime()
        rt.inject_type(RuntimeType(name="Item", description="An item"))
        desc = rt.describe_types()
        assert "Item" in desc


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------

class TestExecute:
    """Tests for PythonRuntime.execute()."""

    @pytest.mark.asyncio
    async def test_simple_code(self) -> None:
        rt = PythonRuntime()
        result = await rt.execute("result = 1 + 2")
        assert result.success is True
        assert isinstance(result, ExecutionResult)

    @pytest.mark.asyncio
    async def test_security_block(self) -> None:
        rt = PythonRuntime()
        result = await rt.execute("import os")
        assert result.success is False
        assert result.error is not None
        assert "security violation" in result.error.lower()

    @pytest.mark.asyncio
    async def test_execute_and_retrieve(self) -> None:
        rt = PythonRuntime()
        await rt.execute("x = 42")
        assert rt.retrieve("x") == 42


# ---------------------------------------------------------------------------
# describe_variables
# ---------------------------------------------------------------------------

class TestDescribeVariables:
    """Tests for PythonRuntime.describe_variables()."""

    def test_with_variables(self) -> None:
        rt = PythonRuntime()
        rt.inject_variable(Variable(name="count", description="Counter", type_name="int"))
        desc = rt.describe_variables()
        assert "count" in desc
        assert "Counter" in desc

    def test_empty(self) -> None:
        rt = PythonRuntime()
        assert rt.describe_variables() == ""


# ---------------------------------------------------------------------------
# describe_functions
# ---------------------------------------------------------------------------

class TestDescribeFunctions:
    """Tests for PythonRuntime.describe_functions()."""

    def test_with_functions(self) -> None:
        rt = PythonRuntime()
        rt.inject_function(
            Function(name="process", description="Process data", signature="(x) -> y")
        )
        desc = rt.describe_functions()
        assert "process" in desc
        assert "(x) -> y" in desc
        assert "Process data" in desc

    def test_async_marker(self) -> None:
        rt = PythonRuntime()
        rt.inject_function(
            Function(name="fetch", description="Fetch data", is_async=True)
        )
        desc = rt.describe_functions()
        assert "(async)" in desc
        assert "fetch" in desc

    def test_empty(self) -> None:
        rt = PythonRuntime()
        assert rt.describe_functions() == ""


# ---------------------------------------------------------------------------
# describe_types
# ---------------------------------------------------------------------------

class TestDescribeTypes:
    """Tests for PythonRuntime.describe_types() at various detail levels."""

    def _make_rt_with_types(self) -> PythonRuntime:
        rt = PythonRuntime()
        rt.inject_type(RuntimeType(name="User", description="A user record"))
        rt.inject_type(RuntimeType(name="Item", description="An item"))
        return rt

    def test_level_names(self) -> None:
        rt = self._make_rt_with_types()
        desc = rt.describe_types(level="names")
        assert "User" in desc
        assert "Item" in desc
        # names level should be comma-separated without descriptions
        assert ":" not in desc

    def test_level_summary(self) -> None:
        rt = self._make_rt_with_types()
        desc = rt.describe_types(level="summary")
        assert "User" in desc
        assert "A user record" in desc
        assert "Item" in desc
        assert "An item" in desc

    def test_level_schema(self) -> None:
        rt = PythonRuntime()
        rt.inject_type(
            RuntimeType(
                name="User",
                description="User object",
                json_schema={"type": "object", "properties": {"name": {"type": "string"}}},
            )
        )
        desc = rt.describe_types(level="schema")
        assert "User" in desc
        assert "Schema:" in desc

    def test_level_schema_python_type_fallback(self) -> None:
        rt = PythonRuntime()
        rt.inject_type(
            RuntimeType(
                name="Config",
                description="Configuration",
                python_type="dict",
            )
        )
        desc = rt.describe_types(level="schema")
        assert "Python type: dict" in desc

    def test_unknown_level_falls_back_to_names(self) -> None:
        rt = self._make_rt_with_types()
        desc = rt.describe_types(level="nonexistent_level")
        # Should fall back to "names" behavior
        assert "User" in desc
        assert "Item" in desc

    def test_empty(self) -> None:
        rt = PythonRuntime()
        assert rt.describe_types() == ""


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
