"""Unit tests for agent_nexus.models.runtime module."""

import pytest
from pydantic import ValidationError

from agent_nexus.models.runtime import (
    ExecutionResult,
    Function,
    RuntimeType,
    SecurityViolation,
    Variable,
)

# ---------------------------------------------------------------------------
# ExecutionResult
# ---------------------------------------------------------------------------


class TestExecutionResult:
    def test_success_result(self):
        r = ExecutionResult(success=True, output="42")
        assert r.success is True
        assert r.output == "42"
        assert r.error is None
        assert r.variables_created == []

    def test_failure_result(self):
        r = ExecutionResult(success=False, error="NameError: x is not defined")
        assert r.success is False
        assert r.error == "NameError: x is not defined"

    def test_with_variables_created(self):
        r = ExecutionResult(
            success=True,
            output="",
            variables_created=["x", "y", "z"],
        )
        assert len(r.variables_created) == 3


# ---------------------------------------------------------------------------
# SecurityViolation
# ---------------------------------------------------------------------------


class TestSecurityViolation:
    def test_construction_with_all_fields(self):
        sv = SecurityViolation(
            rule_type="import",
            node_type="Import",
            code_snippet="import os",
            message="Dangerous import: os",
        )
        assert sv.rule_type == "import"
        assert sv.node_type == "Import"
        assert sv.code_snippet == "import os"
        assert sv.message == "Dangerous import: os"


# ---------------------------------------------------------------------------
# Iteration 33 fix: Runtime model name min_length=1 validation
# ---------------------------------------------------------------------------


class TestRuntimeNameMinLength:
    """Runtime model name fields reject empty strings."""

    def test_variable_empty_name(self):
        with pytest.raises(ValidationError):
            Variable(name="", type_name="str", value="x")

    def test_function_empty_name(self):
        with pytest.raises(ValidationError):
            Function(name="", description="empty name function")

    def test_runtime_type_empty_name(self):
        with pytest.raises(ValidationError):
            RuntimeType(name="", description="empty name type")


# ---------------------------------------------------------------------------
# SecurityViolation min_length=1 validation (iter88)
# ---------------------------------------------------------------------------
