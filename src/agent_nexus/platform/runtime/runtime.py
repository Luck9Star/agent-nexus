"""PythonRuntime: high-level runtime with Variable/Function/Type management.

Composes IPythonExecutor with namespace management for agent code execution.
Provides injection of Variables, Functions, and Types into the IPython namespace
for LLM-generated code to use.

Reference: cave-agent/src/cave_agent/runtime/ipython_runtime.py
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any

from agent_nexus.models.runtime import (
    ExecutionResult,
    Function,
    RuntimeType,
    Variable,
)

from .executor import IPythonExecutor
from .security_checker import SecurityChecker

logger = logging.getLogger(__name__)


class PythonRuntime:
    """High-level runtime with Variable/Function/Type management.

    Composes IPythonExecutor with namespace management. Variables, Functions,
    and Types are tracked in registries and injected into the IPython namespace
    for LLM-generated code to use.

    Usage::

        runtime = PythonRuntime()
        runtime.inject_variable(Variable(name="data", description="input data", value=[1, 2, 3]))
        runtime.inject_function(Function(name="process", description="process data", signature="(x: list) -> list"))
        result = await runtime.execute("result = process(data)")
    """

    def __init__(self, security_checker: SecurityChecker | None = None) -> None:
        self._security = security_checker or SecurityChecker()
        self._executor = IPythonExecutor(security_checker=self._security)
        self._variables: dict[str, Variable] = {}
        self._functions: dict[str, Function] = {}
        self._types: dict[str, RuntimeType] = {}

    def close(self) -> None:
        """Release the underlying IPythonExecutor and its resources."""
        self._executor.close()

    def reset(self) -> None:
        """Reset runtime to clean state, reusing the underlying shell.

        Clears all injected variables, functions, types and the
        IPython namespace without destroying the heavy InteractiveShell.
        """
        self._variables.clear()
        self._functions.clear()
        self._types.clear()
        self._executor.reset()

    # ── Injection ──────────────────────────────────────────────────────

    def inject_variable(self, variable: Variable) -> None:
        """Inject a variable into both registry and IPython namespace.

        Args:
            variable: Variable model with name, description, and value.
        """
        self._variables[variable.name] = variable
        if variable.value is not None:
            self._executor.inject(variable.name, variable.value)

    def inject_function(self, function: Function) -> None:
        """Inject a callable function into both registry and namespace.

        The Function model holds metadata; the actual callable is resolved
        from the function value (stored in Variable-like fashion via the
        Function model's extra fields or by direct injection).

        Args:
            function: Function model with name, description, and signature.
        """
        self._functions[function.name] = function

    def inject_callable(self, name: str, fn: Any, description: str = "") -> None:
        """Inject a Python callable into both registry and namespace.

        Convenience method that auto-detects async functions.

        Args:
            name: Function name for the namespace.
            fn: Python callable to inject.
            description: Human-readable description for LLM context.
        """
        sig = self._format_signature(fn)
        is_async = inspect.iscoroutinefunction(fn)
        func_model = Function(
            name=name,
            description=description,
            signature=sig,
            is_async=is_async,
        )
        self._functions[name] = func_model
        self._executor.inject(name, fn)

    def inject_type(self, type_obj: RuntimeType) -> None:
        """Inject a type into the registry.

        Types are not injected into the namespace directly; they are
        available for LLM context description via describe_types().

        Args:
            type_obj: RuntimeType model with name, description, and optional schema.
        """
        self._types[type_obj.name] = type_obj

    # ── Execution ──────────────────────────────────────────────────────

    async def execute(self, code: str) -> ExecutionResult:
        """Execute code in the IPython runtime.

        Args:
            code: Python source code to execute.

        Returns:
            ExecutionResult with success status, output, and error info.
        """
        return await self._executor.execute(code)

    # ── Retrieval ──────────────────────────────────────────────────────

    _MISSING = object()

    def retrieve(self, name: str, default: Any = None) -> Any:
        """Retrieve a runtime value by name.

        Looks up in the IPython namespace directly.

        Args:
            name: Variable name to retrieve.
            default: Value to return if the name is not found (default None).

        Returns:
            The Python object, or *default* if not found.
        """
        return self._executor.get(name, default=default)

    # ── Description (for LLM context injection) ────────────────────────

    def describe_variables(self) -> str:
        """L0 description: variable names, descriptions, and type names.

        Returns a formatted string suitable for LLM context injection.
        """
        if not self._variables:
            return ""
        lines: list[str] = []
        for var in self._variables.values():
            type_info = f" ({var.type_name})" if var.type_name else ""
            lines.append(f"  - {var.name}{type_info}: {var.description}")
        return "\n".join(lines)

    def describe_functions(self) -> str:
        """L1 description: function signatures with descriptions.

        Returns a formatted string suitable for LLM context injection.
        """
        if not self._functions:
            return ""
        lines: list[str] = []
        for func in self._functions.values():
            sig = func.signature or ""
            async_marker = " (async)" if func.is_async else ""
            lines.append(f"  - {func.name}{async_marker}{sig}: {func.description}")
        return "\n".join(lines)

    def describe_types(self, level: str = "names") -> str:
        """Describe registered types at the given detail level.

        Args:
            level: Detail level.
                - "names": Type names only (L0).
                - "summary": Names + descriptions (L0 extended).
                - "schema": Full JSON Schema for each type (L2).

        Returns:
            Formatted string for LLM context injection.
        """
        if not self._types:
            return ""
        if level == "names":
            return ", ".join(self._types.keys())
        elif level == "summary":
            lines: list[str] = []
            for t in self._types.values():
                lines.append(f"  - {t.name}: {t.description}")
            return "\n".join(lines)
        elif level == "schema":
            parts: list[str] = []
            for t in self._types.values():
                header = f"  {t.name}"
                if t.description:
                    header += f": {t.description}"
                parts.append(header)
                if t.json_schema:
                    parts.append(f"    Schema: {json.dumps(t.json_schema, indent=2)}")
                elif t.python_type:
                    parts.append(f"    Python type: {t.python_type}")
            return "\n".join(parts)
        return ", ".join(self._types.keys())

    # ── Internal helpers ───────────────────────────────────────────────

    @staticmethod
    def _format_signature(fn: Any) -> str:
        """Extract a readable signature string from a callable."""
        try:
            sig = inspect.signature(fn)
            return str(sig)
        except (ValueError, TypeError):
            return ""
