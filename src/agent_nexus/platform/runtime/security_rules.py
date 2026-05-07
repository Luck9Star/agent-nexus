"""Security rules for AST-level code analysis.

Four rule types cover different attack vectors:
- ImportRule: Block dangerous module imports
- FunctionRule: Block dangerous function calls
- AttributeRule: Block dangerous attribute access
- RegexRule: Catch-all regex patterns

Reference: cave-agent/src/cave_agent/security/rules.py
"""

from __future__ import annotations

import ast
import logging
import re
from abc import ABC, abstractmethod
from typing import ClassVar

from agent_nexus.models.runtime import SecurityViolation

logger = logging.getLogger(__name__)


class SecurityRule(ABC):
    """Abstract base class for security rules.

    All security rules must inherit from this class and implement
    the check method to analyze AST nodes for violations.
    """

    @abstractmethod
    def check(self, node: ast.AST) -> list[SecurityViolation]:
        """Check if the AST node violates this rule.

        Args:
            node: AST node to analyze.

        Returns:
            List of SecurityViolation found (empty if none).
        """


class ImportRule(SecurityRule):
    """Block imports of forbidden modules.

    Checks ast.Import and ast.ImportFrom nodes against a forbidden list.
    Matches exact module names or submodules (startswith matching).
    """

    def __init__(self, forbidden: list[str] | set[str]) -> None:
        self.forbidden: set[str] = set(forbidden)
        self._forbidden_prefixes: tuple[str, ...] = tuple(f"{mod}." for mod in self.forbidden)


    def _check_import_node(self, node: ast.Import) -> list[SecurityViolation]:
        violations: list[SecurityViolation] = []
        for alias in node.names:
            if self._is_forbidden(alias.name):
                violations.append(
                    SecurityViolation(
                        rule_type="import",
                        node_type="Import",
                        code_snippet=f"import {alias.name}",
                        message=f"Forbidden import: '{alias.name}' at line {node.lineno}",
                    )
                )
        return violations

    def _resolve_module_name(self, node: ast.ImportFrom) -> str | None:
        """Strip leading dots from relative import module names."""
        module_name = node.module
        if module_name and node.level > 0:
            module_name = module_name.lstrip(".")
        return module_name

    def _check_wildcard_relative(self, node: ast.ImportFrom) -> list[SecurityViolation]:
        """Detect `from . import *` wildcard relative imports."""
        if (
            node.level > 0
            and node.module is None
            and any(alias.name == "*" for alias in node.names)
        ):
            return [
                SecurityViolation(
                    rule_type="import",
                    node_type="ImportFrom",
                    code_snippet="from . import *",
                    message=f"Wildcard relative import at line {node.lineno}",
                )
            ]
        return []

    def _check_imported_names(self, node: ast.ImportFrom) -> list[SecurityViolation]:
        """Check individual imported names for forbidden symbols."""
        violations: list[SecurityViolation] = []
        for alias in node.names:
            if alias.name and alias.name != "*" and self._is_forbidden(alias.name):
                violations.append(
                    SecurityViolation(
                        rule_type="import",
                        node_type="ImportFrom",
                        code_snippet=f"from . import {alias.name}",
                        message=f"Forbidden import: '{alias.name}' at line {node.lineno}",
                    )
                )
        return violations

    def _check_import_from_node(self, node: ast.ImportFrom) -> list[SecurityViolation]:
        violations: list[SecurityViolation] = []

        module_name = self._resolve_module_name(node)

        # Check module path (e.g. "from os import path")
        if module_name and self._is_forbidden(module_name):
            violations.append(
                SecurityViolation(
                    rule_type="import",
                    node_type="ImportFrom",
                    code_snippet=f"from {node.module} import ...",
                    message=f"Forbidden import: 'from {node.module}' at line {node.lineno}",
                )
            )

        violations.extend(self._check_wildcard_relative(node))

        # Only check names when module-level check didn't already catch it.
        if not violations:
            violations.extend(self._check_imported_names(node))

        return violations

    def check(self, node: ast.AST) -> list[SecurityViolation]:
        if isinstance(node, ast.Import):
            return self._check_import_node(node)
        elif isinstance(node, ast.ImportFrom):
            return self._check_import_from_node(node)
        return []

    def _is_forbidden(self, module_name: str) -> bool:
        """Check if module_name is forbidden (exact match or parent module)."""
        # Block submodules too: "os.path" blocked if "os" is forbidden.
        # str.startswith(tuple) is a single C-level call checking all prefixes.
        return module_name in self.forbidden or module_name.startswith(self._forbidden_prefixes)


class FunctionRule(SecurityRule):
    """Block calls to dangerous functions.

    Checks ast.Call nodes and resolves the function name from
    ast.Name, ast.Attribute, and nested ast.Call patterns.
    Also handles getattr(obj, 'eval') pattern.

    Supports qualified-call blocking: ``qualified_calls`` maps
    module-name to set of forbidden method names, e.g.
    ``{"os": {"system", "popen"}}`` blocks ``os.system()`` but not
    ``pipeline.run()``.
    """

    def __init__(
        self,
        forbidden: list[str] | set[str],
        qualified_calls: dict[str, set[str]] | None = None,
    ) -> None:
        self.forbidden: set[str] = set(forbidden)
        self.qualified_calls: dict[str, set[str]] = qualified_calls or {}

    # Keyword argument names that accept callables (e.g. sorted(..., key=exec))
    _CALLBACK_KWARGS: ClassVar[set[str]] = {
        "key",
        "func",
        "function",
        "callback",
        "fn",
        "f",
    }

    def check(self, node: ast.AST) -> list[SecurityViolation]:
        violations: list[SecurityViolation] = []

        if isinstance(node, ast.Call):
            func_name = self._get_function_name(node.func)
            # Only check bare function calls (ast.Name) against the
            # forbidden list.  Method calls (ast.Attribute) like
            # ``re.compile()`` should NOT be flagged here -- the
            # AttributeRule already covers dangerous attribute access
            # such as ``__builtins__``.
            if func_name in self.forbidden and isinstance(node.func, ast.Name):
                violations.append(
                    SecurityViolation(
                        rule_type="function",
                        node_type="Call",
                        code_snippet=ast.unparse(node),
                        message=f"Forbidden function call: '{func_name}' at line {node.lineno}",
                    )
                )

            # Also catch attribute-based calls: builtins.eval(...),
            # __builtins__.exec(...), etc.
            if func_name in self.forbidden and isinstance(node.func, ast.Attribute):
                violations.append(
                    SecurityViolation(
                        rule_type="function",
                        node_type="AttributeCall",
                        code_snippet=ast.unparse(node),
                        message=(
                            f"Forbidden function call via attribute: "
                            f"'{func_name}' at line {node.lineno}"
                        ),
                    )
                )

            # Qualified-call check: block os.system(), subprocess.run(), etc.
            # without false-positive on generic names like pipeline.run().
            if (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in self.qualified_calls
                and node.func.attr in self.qualified_calls[node.func.value.id]
            ):
                qualified = f"{node.func.value.id}.{node.func.attr}"
                violations.append(
                    SecurityViolation(
                        rule_type="function",
                        node_type="QualifiedCall",
                        code_snippet=ast.unparse(node),
                        message=f"Forbidden qualified call: '{qualified}' at line {node.lineno}",
                    )
                )

            # --- Bypass-vector protection ---
            # Detect forbidden function names passed as *arguments* to other
            # calls.  This catches patterns like:
            #   map(__import__, ["os"])
            #   sorted(data, key=exec)
            #   filter(compile, items)
            violations.extend(self._check_callback_args(node))

        return violations

    def _check_callback_args(self, call_node: ast.Call) -> list[SecurityViolation]:
        """Detect forbidden function names used as callback arguments.

        Higher-order functions like map(), filter(), sorted() accept a
        callable as their first (or keyword) argument.  Passing a
        forbidden function name bypasses the normal Call check because
        the forbidden name is not the function *being* called -- it is
        an argument that will be invoked later.

        We also check ALL keyword arguments whose name suggests a
        callable parameter (key=, func=, callback=, etc.).
        """
        violations: list[SecurityViolation] = []

        # Check positional arguments for forbidden function names.
        # Catches: map(__import__, ...), filter(exec, ...), etc.
        for arg in call_node.args:
            if isinstance(arg, ast.Name) and arg.id in self.forbidden:
                violations.append(
                    SecurityViolation(
                        rule_type="function",
                        node_type="CallArgument",
                        code_snippet=ast.unparse(call_node),
                        message=(
                            f"Forbidden function passed as argument: "
                            f"'{arg.id}' at line {call_node.lineno}"
                        ),
                    )
                )

        # Check keyword arguments whose name suggests a callable value.
        for kw in call_node.keywords:
            if (
                kw.arg in self._CALLBACK_KWARGS
                and isinstance(kw.value, ast.Name)
                and kw.value.id in self.forbidden
            ):
                violations.append(
                    SecurityViolation(
                        rule_type="function",
                        node_type="CallKeywordArgument",
                        code_snippet=ast.unparse(call_node),
                        message=(
                            f"Forbidden function passed as keyword argument "
                            f"'{kw.arg}': '{kw.value.id}' at line {call_node.lineno}"
                        ),
                    )
                )

        return violations

    def _get_function_name(self, func_node: ast.AST) -> str:
        """Extract function name from various call patterns."""
        if isinstance(func_node, ast.Name):
            return func_node.id
        elif isinstance(func_node, ast.Attribute):
            # For calls like obj.method(), return the method name
            return func_node.attr
        elif isinstance(func_node, ast.Call):
            # For nested calls, recurse to find the innermost function
            return self._get_function_name(func_node.func)
        return ""


class AttributeRule(SecurityRule):
    """Block access to dangerous attributes.

    Checks ast.Attribute nodes against a forbidden list.
    Blocks introspection attributes like __subclasses__, __globals__, etc.
    """

    def __init__(self, forbidden: list[str] | set[str]) -> None:
        self.forbidden: set[str] = set(forbidden)

    def check(self, node: ast.AST) -> list[SecurityViolation]:
        violations: list[SecurityViolation] = []

        if isinstance(node, ast.Attribute) and node.attr in self.forbidden:
            violations.append(
                SecurityViolation(
                    rule_type="attribute",
                    node_type="Attribute",
                    code_snippet=f".{node.attr}",
                    message=f"Forbidden attribute access: '{node.attr}' at line {node.lineno}",
                )
            )

        return violations


class RegexRule(SecurityRule):
    """Catch-all regex patterns on source code.

    Runs compiled regex against ast.unparse(node) to detect
    suspicious patterns not easily caught by structural rules.
    """

    def __init__(self, patterns: list[str] | str, description: str = "") -> None:
        if isinstance(patterns, str):
            patterns = [patterns]
        self.description = description
        self.patterns: list[re.Pattern[str]] = []
        for pattern in patterns:
            try:
                self.patterns.append(re.compile(pattern, re.MULTILINE | re.DOTALL))
            except re.error as e:
                raise ValueError(f"Invalid regex pattern '{pattern}': {e}") from e

    def check(self, node: ast.AST) -> list[SecurityViolation]:
        # RegexRule operates on full source via check_source(), not per-node.
        # SecurityChecker routes RegexRule to check_source() explicitly.
        return []

    def check_source(self, source: str) -> list[SecurityViolation]:
        """Check the full source code string against regex patterns.

        This is the primary method for regex rules -- runs all patterns
        against the complete source string for efficiency.
        """
        violations: list[SecurityViolation] = []
        for compiled_pattern in self.patterns:
            if compiled_pattern.search(source):
                desc = self.description or compiled_pattern.pattern
                violations.append(
                    SecurityViolation(
                        rule_type="regex",
                        node_type="Module",
                        code_snippet=source[:200],
                        message=f"Regex rule violation: '{desc}'",
                    )
                )
        return violations
