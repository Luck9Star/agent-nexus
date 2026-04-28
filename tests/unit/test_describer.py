"""Unit tests for agent_nexus.platform.runtime.describer module."""

from __future__ import annotations

import pytest

from agent_nexus.models.runtime import (
    Function,
    RuntimeType,
    Variable,
)
from agent_nexus.platform.runtime.describer import TieredRuntimeDescriber


# ---------------------------------------------------------------------------
# l0_context
# ---------------------------------------------------------------------------

class TestL0Context:
    """Tests for TieredRuntimeDescriber.l0_context()."""

    def test_with_variables(self, shared_runtime) -> None:
        shared_runtime.inject_variable(Variable(name="count", description="Counter"))
        describer = TieredRuntimeDescriber(shared_runtime)
        result = describer.l0_context()
        assert "[Variables]" in result
        assert "count" in result

    def test_with_types(self, shared_runtime) -> None:
        shared_runtime.inject_type(RuntimeType(name="User", description="User record"))
        describer = TieredRuntimeDescriber(shared_runtime)
        result = describer.l0_context()
        assert "[Available Types]" in result
        assert "User" in result

    def test_empty_runtime(self, shared_runtime) -> None:
        describer = TieredRuntimeDescriber(shared_runtime)
        result = describer.l0_context()
        assert result == ""

    def test_with_variables_and_types(self, shared_runtime) -> None:
        shared_runtime.inject_variable(Variable(name="data", description="input data"))
        shared_runtime.inject_type(RuntimeType(name="Record", description="A record"))
        describer = TieredRuntimeDescriber(shared_runtime)
        result = describer.l0_context()
        assert "[Variables]" in result
        assert "[Available Types]" in result


# ---------------------------------------------------------------------------
# l1_context
# ---------------------------------------------------------------------------

class TestL1Context:
    """Tests for TieredRuntimeDescriber.l1_context()."""

    def test_with_functions(self, shared_runtime) -> None:
        shared_runtime.inject_function(
            Function(name="process", description="Process data", signature="(x) -> y")
        )
        describer = TieredRuntimeDescriber(shared_runtime)
        result = describer.l1_context()
        assert "[Functions]" in result
        assert "process" in result

    def test_with_types(self, shared_runtime) -> None:
        shared_runtime.inject_type(RuntimeType(name="User", description="User record"))
        describer = TieredRuntimeDescriber(shared_runtime)
        result = describer.l1_context()
        assert "[Types]" in result
        assert "User" in result
        assert "User record" in result

    def test_empty(self, shared_runtime) -> None:
        describer = TieredRuntimeDescriber(shared_runtime)
        result = describer.l1_context()
        assert result == ""

    def test_with_functions_and_types(self, shared_runtime) -> None:
        shared_runtime.inject_function(
            Function(name="transform", description="Transform data")
        )
        shared_runtime.inject_type(RuntimeType(name="Config", description="Configuration"))
        describer = TieredRuntimeDescriber(shared_runtime)
        result = describer.l1_context()
        assert "[Functions]" in result
        assert "[Types]" in result


# ---------------------------------------------------------------------------
# l2_context
# ---------------------------------------------------------------------------

class TestL2Context:
    """Tests for TieredRuntimeDescriber.l2_context()."""

    def test_with_schema_types(self, shared_runtime) -> None:
        shared_runtime.inject_type(
            RuntimeType(
                name="User",
                description="User object",
                json_schema={"type": "object", "properties": {"name": {"type": "string"}}},
            )
        )
        describer = TieredRuntimeDescriber(shared_runtime)
        result = describer.l2_context()
        assert "[Type Schemas]" in result
        assert "User" in result
        assert "Schema:" in result

    def test_empty(self, shared_runtime) -> None:
        describer = TieredRuntimeDescriber(shared_runtime)
        result = describer.l2_context()
        assert result == ""

    def test_type_without_schema(self, shared_runtime) -> None:
        """Types without json_schema still appear in L2 via Python type fallback."""
        shared_runtime.inject_type(
            RuntimeType(name="Config", description="Config", python_type="dict")
        )
        describer = TieredRuntimeDescriber(shared_runtime)
        result = describer.l2_context()
        assert "[Type Schemas]" in result
        assert "Python type: dict" in result


# ---------------------------------------------------------------------------
# l3_value
# ---------------------------------------------------------------------------

class TestL3Value:
    """Tests for TieredRuntimeDescriber.l3_value()."""

    @pytest.mark.asyncio
    async def test_existing_variable(self, shared_runtime) -> None:
        await shared_runtime.execute("x = 42")
        describer = TieredRuntimeDescriber(shared_runtime)
        result = describer.l3_value("x")
        assert "[Variable: x]" in result
        assert "42" in result

    @pytest.mark.asyncio
    async def test_nonexistent_variable(self, shared_runtime) -> None:
        describer = TieredRuntimeDescriber(shared_runtime)
        result = describer.l3_value("nonexistent_xyz")
        assert result == ""

    @pytest.mark.asyncio
    async def test_complex_object(self, shared_runtime) -> None:
        await shared_runtime.execute('data = {"name": "Alice", "age": 30}')
        describer = TieredRuntimeDescriber(shared_runtime)
        result = describer.l3_value("data")
        assert "[Variable: data]" in result
        assert "Alice" in result

    @pytest.mark.asyncio
    async def test_list_value(self, shared_runtime) -> None:
        await shared_runtime.execute("items = [1, 2, 3]")
        describer = TieredRuntimeDescriber(shared_runtime)
        result = describer.l3_value("items")
        assert "[Variable: items]" in result
        assert "1" in result
        assert "3" in result
