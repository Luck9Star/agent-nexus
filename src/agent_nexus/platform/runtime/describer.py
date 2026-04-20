"""TieredRuntimeDescriber: L0-L3 context generation for LLM injection.

Generates tiered descriptions of the runtime state for injecting into
LLM prompts at different detail levels, optimizing token usage.

L0 (~100 tokens, every turn): Variable names + descriptions + Type names
L1 (~500 tokens, first turn): Function signatures + relevant Type schemas
L2 (on-demand): Full Type JSON Schema + Memory/history
L3 (runtime dynamic): Variable current values

Reference: docs/03-python-runtime.md Section 5.8
"""

from __future__ import annotations

import json
import logging
import re as _re

from .runtime import PythonRuntime

logger = logging.getLogger(__name__)


class TieredRuntimeDescriber:
    """Generate tiered context descriptions for LLM injection.

    Usage::

        runtime = PythonRuntime()
        runtime.inject_variable(Variable(name="data", description="input"))
        describer = TieredRuntimeDescriber(runtime)

        # Every turn (~100 tokens)
        l0 = describer.l0_context()

        # First turn (~500 tokens)
        l1 = describer.l1_context()

        # On-demand full schema
        l2 = describer.l2_context()

        # Runtime dynamic value
        l3 = describer.l3_value("data")
    """

    def __init__(self, runtime: PythonRuntime) -> None:
        self._runtime = runtime

    def l0_context(self) -> str:
        """Generate L0 context block (~100 tokens).

        Includes variable names + descriptions + type names.
        Suitable for injection on every LLM turn.

        Returns:
            Formatted context string.
        """
        parts: list[str] = []

        variables_desc = self._runtime.describe_variables()
        if variables_desc:
            parts.append(f"[Variables]\n{variables_desc}")

        type_names = self._runtime.describe_types(level="names")
        if type_names:
            parts.append(f"[Available Types] {type_names}")

        return "\n\n".join(parts) if parts else ""

    def l1_context(self) -> str:
        """Generate L1 context block (~500 tokens).

        Includes function signatures with descriptions and
        type summaries (names + descriptions).

        Returns:
            Formatted context string.
        """
        parts: list[str] = []

        functions_desc = self._runtime.describe_functions()
        if functions_desc:
            parts.append(f"[Functions]\n{functions_desc}")

        types_summary = self._runtime.describe_types(level="summary")
        if types_summary:
            parts.append(f"[Types]\n{types_summary}")

        return "\n\n".join(parts) if parts else ""

    def l2_context(self) -> str:
        """Generate L2 context block (on-demand).

        Includes full JSON Schema for all registered types.
        This can be large; use only when the LLM needs type details.

        Returns:
            Formatted context string with full type schemas.
        """
        parts: list[str] = []

        types_schema = self._runtime.describe_types(level="schema")
        if types_schema:
            parts.append(f"[Type Schemas]\n{types_schema}")

        return "\n\n".join(parts) if parts else ""

    def l3_value(self, var_name: str) -> str:
        """Generate L3 context for a specific variable's current value.

        Retrieves the actual runtime value and formats it for LLM context.

        Args:
            var_name: Name of the variable to describe.

        Returns:
            Formatted string with the variable's current value,
            or empty string if not found.
        """
        # Sanitize var_name: strip newlines and control characters to prevent
        # injection of fake context lines into the description output.
        safe_name = _re.sub(r"[\x00-\x1f\x7f\u200b-\u200f\u2028-\u202e\ufeff]", "", var_name).strip()
        if not safe_name:
            return ""

        _MISSING = object()
        value = self._runtime.retrieve(safe_name, default=_MISSING)
        if value is _MISSING:
            return ""

        try:
            # Try JSON first for clean serialization
            formatted = json.dumps(value, indent=2, default=str)
        except (TypeError, ValueError):
            formatted = str(value)

        return f"[Variable: {safe_name}]\n{formatted}"
