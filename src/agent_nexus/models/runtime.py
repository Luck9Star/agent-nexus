"""Python Runtime models: Variable, Function, Type, ExecutionResult, SecurityViolation."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Variable(BaseModel):
    """A named Python object held in the Runtime namespace.

    Variables persist across execution rounds within a single Agent process.
    The actual Python value is not serialized; only metadata is stored here.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    description: str = ""
    value: Any = None
    type_name: str | None = None


class Function(BaseModel):
    """A callable Python function registered in the Runtime namespace.

    The callable itself cannot be serialized; only metadata is stored.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    description: str = ""
    signature: str | None = None
    is_async: bool = False


class RuntimeType(BaseModel):
    """A Python type (class) registered in the Runtime namespace.

    Includes an optional JSON Schema for LLM context injection.
    Named `RuntimeType` to avoid shadowing Python's builtin `type`.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    description: str = ""
    python_type: str | None = None
    json_schema: dict[str, Any] | None = None


class ExecutionResult(BaseModel):
    """Result of executing Python code in the Runtime."""

    model_config = ConfigDict(frozen=True)

    success: bool
    output: str = ""
    error: str | None = None
    variables_created: list[str] = Field(default_factory=list)


class SecurityViolation(BaseModel):
    """A single security rule violation detected by SecurityChecker (AST-level)."""

    model_config = ConfigDict(frozen=True)

    rule_type: str  # e.g. "import", "function", "attribute", "regex"
    node_type: str  # AST node type, e.g. "Import", "Call"
    code_snippet: str = ""
    message: str = ""
