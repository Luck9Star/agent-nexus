"""Unit tests for agent_nexus.platform.runtime.describer module."""

from __future__ import annotations

import pytest

from agent_nexus.models.runtime import (
    Function,
    RuntimeType,
    Variable,
)
from agent_nexus.platform.runtime.describer import TieredRuntimeDescriber
from agent_nexus.platform.runtime.runtime import PythonRuntime


# ---------------------------------------------------------------------------
# l0_context
# ---------------------------------------------------------------------------

class TestL0Context:
    """Tests for TieredRuntimeDescriber.l0_context()."""

    def test_with_variables(self) -> None:
        rt = PythonRuntime()
        rt.inject_variable(Variable(name="count", description="Counter"))
        describer = TieredRuntimeDescriber(rt)
        result = describer.l0_context()
        assert "[Variables]" in result
        assert "count" in result

    def test_with_types(self) -> None:
        rt = PythonRuntime()
        rt.inject_type(RuntimeType(name="User", description="User record"))
        describer = TieredRuntimeDescriber(rt)
        result = describer.l0_context()
        assert "[Available Types]" in result
        assert "User" in result

    def test_empty_runtime(self) -> None:
        rt = PythonRuntime()
        describer = TieredRuntimeDescriber(rt)
        result = describer.l0_context()
        assert result == ""

    def test_with_variables_and_types(self) -> None:
        rt = PythonRuntime()
        rt.inject_variable(Variable(name="data", description="input data"))
        rt.inject_type(RuntimeType(name="Record", description="A record"))
        describer = TieredRuntimeDescriber(rt)
        result = describer.l0_context()
        assert "[Variables]" in result
        assert "[Available Types]" in result


# ---------------------------------------------------------------------------
# l1_context
# ---------------------------------------------------------------------------

class TestL1Context:
    """Tests for TieredRuntimeDescriber.l1_context()."""

    def test_with_functions(self) -> None:
        rt = PythonRuntime()
        rt.inject_function(
            Function(name="process", description="Process data", signature="(x) -> y")
        )
        describer = TieredRuntimeDescriber(rt)
        result = describer.l1_context()
        assert "[Functions]" in result
        assert "process" in result

    def test_with_types(self) -> None:
        rt = PythonRuntime()
        rt.inject_type(RuntimeType(name="User", description="User record"))
        describer = TieredRuntimeDescriber(rt)
        result = describer.l1_context()
        assert "[Types]" in result
        assert "User" in result
        assert "User record" in result

    def test_empty(self) -> None:
        rt = PythonRuntime()
        describer = TieredRuntimeDescriber(rt)
        result = describer.l1_context()
        assert result == ""

    def test_with_functions_and_types(self) -> None:
        rt = PythonRuntime()
        rt.inject_function(
            Function(name="transform", description="Transform data")
        )
        rt.inject_type(RuntimeType(name="Config", description="Configuration"))
        describer = TieredRuntimeDescriber(rt)
        result = describer.l1_context()
        assert "[Functions]" in result
        assert "[Types]" in result


# ---------------------------------------------------------------------------
# l2_context
# ---------------------------------------------------------------------------

class TestL2Context:
    """Tests for TieredRuntimeDescriber.l2_context()."""

    def test_with_schema_types(self) -> None:
        rt = PythonRuntime()
        rt.inject_type(
            RuntimeType(
                name="User",
                description="User object",
                json_schema={"type": "object", "properties": {"name": {"type": "string"}}},
            )
        )
        describer = TieredRuntimeDescriber(rt)
        result = describer.l2_context()
        assert "[Type Schemas]" in result
        assert "User" in result
        assert "Schema:" in result

    def test_empty(self) -> None:
        rt = PythonRuntime()
        describer = TieredRuntimeDescriber(rt)
        result = describer.l2_context()
        assert result == ""

    def test_type_without_schema(self) -> None:
        """Types without json_schema still appear in L2 via Python type fallback."""
        rt = PythonRuntime()
        rt.inject_type(
            RuntimeType(name="Config", description="Config", python_type="dict")
        )
        describer = TieredRuntimeDescriber(rt)
        result = describer.l2_context()
        assert "[Type Schemas]" in result
        assert "Python type: dict" in result


# ---------------------------------------------------------------------------
# l3_value
# ---------------------------------------------------------------------------

class TestL3Value:
    """Tests for TieredRuntimeDescriber.l3_value()."""

    @pytest.mark.asyncio
    async def test_existing_variable(self) -> None:
        rt = PythonRuntime()
        await rt.execute("x = 42")
        describer = TieredRuntimeDescriber(rt)
        result = describer.l3_value("x")
        assert "[Variable: x]" in result
        assert "42" in result

    @pytest.mark.asyncio
    async def test_nonexistent_variable(self) -> None:
        rt = PythonRuntime()
        describer = TieredRuntimeDescriber(rt)
        result = describer.l3_value("nonexistent_xyz")
        assert result == ""

    @pytest.mark.asyncio
    async def test_complex_object(self) -> None:
        rt = PythonRuntime()
        await rt.execute('data = {"name": "Alice", "age": 30}')
        describer = TieredRuntimeDescriber(rt)
        result = describer.l3_value("data")
        assert "[Variable: data]" in result
        assert "Alice" in result

    @pytest.mark.asyncio
    async def test_list_value(self) -> None:
        rt = PythonRuntime()
        await rt.execute("items = [1, 2, 3]")
        describer = TieredRuntimeDescriber(rt)
        result = describer.l3_value("items")
        assert "[Variable: items]" in result
        assert "1" in result
        assert "3" in result
