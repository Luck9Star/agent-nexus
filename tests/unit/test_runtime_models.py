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
# Variable
# ---------------------------------------------------------------------------


class TestVariable:
    def test_construction_with_all_fields(self):
        v = Variable(name="count", description="Counter", value=42, type_name="int")
        assert v.name == "count"
        assert v.description == "Counter"
        assert v.value == 42
        assert v.type_name == "int"

    def test_defaults(self):
        v = Variable(name="x")
        assert v.description == ""
        assert v.value is None
        assert v.type_name is None

    def test_with_various_values(self):
        v_str = Variable(name="s", value="hello", type_name="str")
        assert v_str.value == "hello"

        v_list = Variable(name="lst", value=[1, 2, 3], type_name="list")
        assert v_list.value == [1, 2, 3]

        v_none = Variable(name="n", value=None, type_name="NoneType")
        assert v_none.value is None

        v_dict = Variable(name="d", value={"key": "val"}, type_name="dict")
        assert v_dict.value["key"] == "val"

    def test_serialization_round_trip(self):
        v = Variable(name="data", value=[1, 2, 3], type_name="list")
        data = v.model_dump()
        v2 = Variable(**data)
        assert v2 == v

    def test_json_serialization(self):
        v = Variable(name="flag", value=True, type_name="bool")
        json_str = v.model_dump_json()
        v2 = Variable.model_validate_json(json_str)
        assert v2 == v


# ---------------------------------------------------------------------------
# Function
# ---------------------------------------------------------------------------


class TestFunction:
    def test_construction_with_all_fields(self):
        f = Function(
            name="process",
            description="Process data",
            signature="(data: list[str]) -> dict",
            is_async=True,
        )
        assert f.name == "process"
        assert f.description == "Process data"
        assert f.signature == "(data: list[str]) -> dict"
        assert f.is_async is True

    def test_defaults(self):
        f = Function(name="noop")
        assert f.description == ""
        assert f.signature is None
        assert f.is_async is False

    def test_serialization_round_trip(self):
        f = Function(name="run", signature="() -> None", is_async=True)
        data = f.model_dump()
        f2 = Function(**data)
        assert f2 == f


# ---------------------------------------------------------------------------
# RuntimeType
# ---------------------------------------------------------------------------


class TestRuntimeType:
    def test_construction_with_all_fields(self):
        rt = RuntimeType(
            name="Document",
            description="A document object",
            python_type="docx.Document",
            json_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
        assert rt.name == "Document"
        assert rt.python_type == "docx.Document"
        assert rt.json_schema is not None
        assert "properties" in rt.json_schema

    def test_defaults(self):
        rt = RuntimeType(name="MyType")
        assert rt.description == ""
        assert rt.python_type is None
        assert rt.json_schema is None

    def test_serialization_round_trip(self):
        rt = RuntimeType(
            name="Config",
            json_schema={"type": "object"},
        )
        data = rt.model_dump()
        rt2 = RuntimeType(**data)
        assert rt2 == rt


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

    def test_defaults(self):
        r = ExecutionResult(success=True)
        assert r.output == ""
        assert r.error is None
        assert r.variables_created == []

    def test_serialization_round_trip(self):
        r = ExecutionResult(
            success=False,
            output="partial",
            error="TypeError",
            variables_created=["tmp"],
        )
        data = r.model_dump()
        r2 = ExecutionResult(**data)
        assert r2 == r

    def test_json_serialization(self):
        r = ExecutionResult(success=True, output="ok")
        json_str = r.model_dump_json()
        r2 = ExecutionResult.model_validate_json(json_str)
        assert r2 == r


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

    def test_defaults(self):
        sv = SecurityViolation(rule_type="function", node_type="Call")
        assert sv.code_snippet == ""
        assert sv.message == ""

    def test_various_rule_types(self):
        types = ["import", "function", "attribute", "regex", "call"]
        for rt in types:
            sv = SecurityViolation(rule_type=rt, node_type="Test")
            assert sv.rule_type == rt

    def test_serialization_round_trip(self):
        sv = SecurityViolation(
            rule_type="import",
            node_type="Import",
            code_snippet="import os",
            message="blocked",
        )
        data = sv.model_dump()
        sv2 = SecurityViolation(**data)
        assert sv2 == sv

    def test_json_serialization(self):
        sv = SecurityViolation(rule_type="attribute", node_type="Attribute", message="no access")
        json_str = sv.model_dump_json()
        sv2 = SecurityViolation.model_validate_json(json_str)
        assert sv2 == sv


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


class TestSecurityViolationMinLength:
    """SecurityViolation.rule_type and node_type must reject empty strings."""

    def test_valid_fields_accepted(self):
        sv = SecurityViolation(rule_type="import", node_type="Import")
        assert sv.rule_type == "import"
        assert sv.node_type == "Import"
