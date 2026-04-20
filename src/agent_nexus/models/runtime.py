"""Python Runtime models: Variable, Function, Type, ExecutionResult, SecurityViolation."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from agent_nexus.models._common import FrozenModel


class Variable(FrozenModel):
    """A named Python object held in the Runtime namespace.

    Variables persist across execution rounds within a single Agent process.
    The actual Python value is not serialized; only metadata is stored here.
    """

    name: str = Field(min_length=1)
    description: str = ""
    value: Any = None
    type_name: str | None = None


class Function(FrozenModel):
    """A callable Python function registered in the Runtime namespace.

    The callable itself cannot be serialized; only metadata is stored.
    """

    name: str = Field(min_length=1)
    description: str = ""
    signature: str | None = None
    is_async: bool = False


class RuntimeType(FrozenModel):
    """A Python type (class) registered in the Runtime namespace.

    Includes an optional JSON Schema for LLM context injection.
    Named `RuntimeType` to avoid shadowing Python's builtin `type`.
    """

    name: str = Field(min_length=1)
    description: str = ""
    python_type: str | None = None
    json_schema: dict[str, Any] | None = None


class ExecutionResult(FrozenModel):
    """Result of executing Python code in the Runtime."""

    success: bool
    output: str = ""
    error: str | None = None
    variables_created: list[str] = Field(default_factory=list)


class SecurityViolation(FrozenModel):
    """A single security rule violation detected by SecurityChecker (AST-level)."""

    rule_type: str = Field(min_length=1)
    node_type: str = Field(min_length=1)
    code_snippet: str = ""
    message: str = ""
