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

    def check(self, node: ast.AST) -> list[SecurityViolation]:
        violations: list[SecurityViolation] = []

        if isinstance(node, ast.Import):
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

        elif isinstance(node, ast.ImportFrom):
            if node.module and self._is_forbidden(node.module):
                violations.append(
                    SecurityViolation(
                        rule_type="import",
                        node_type="ImportFrom",
                        code_snippet=f"from {node.module} import ...",
                        message=f"Forbidden import: 'from {node.module}' at line {node.lineno}",
                    )
                )

        return violations

    def _is_forbidden(self, module_name: str) -> bool:
        """Check if module_name is forbidden (exact match or parent module)."""
        if module_name in self.forbidden:
            return True
        # Block submodules too: "os.path" blocked if "os" is forbidden
        for forbidden_mod in self.forbidden:
            if module_name.startswith(forbidden_mod + "."):
                return True
        return False


class FunctionRule(SecurityRule):
    """Block calls to dangerous functions.

    Checks ast.Call nodes and resolves the function name from
    ast.Name, ast.Attribute, and nested ast.Call patterns.
    Also handles getattr(obj, 'eval') pattern.
    """

    def __init__(self, forbidden: list[str] | set[str]) -> None:
        self.forbidden: set[str] = set(forbidden)

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
                        code_snippet=ast.unparse(node) if hasattr(ast, "unparse") else str(func_name),
                        message=f"Forbidden function call: '{func_name}' at line {node.lineno}",
                    )
                )

            # Also catch attribute-based calls: builtins.eval(...),
            # __builtins__.exec(...), etc.
            if (
                func_name in self.forbidden
                and isinstance(node.func, ast.Attribute)
            ):
                violations.append(
                    SecurityViolation(
                        rule_type="function",
                        node_type="AttributeCall",
                        code_snippet=ast.unparse(node) if hasattr(ast, "unparse") else str(func_name),
                        message=(
                            f"Forbidden function call via attribute: "
                            f"'{func_name}' at line {node.lineno}"
                        ),
                    )
                )

            # Detect getattr(obj, 'eval') pattern
            if (
                isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
            ):
                attr_value = (
                    node.args[1].value
                )
                if attr_value in self.forbidden:
                    violations.append(
                        SecurityViolation(
                            rule_type="function",
                            node_type="Call",
                            code_snippet=ast.unparse(node) if hasattr(ast, "unparse") else "getattr(...)",
                            message=(
                                f"Forbidden dynamic attribute access via getattr: "
                                f"'{attr_value}' at line {node.lineno}"
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

        if isinstance(node, ast.Attribute):
            if node.attr in self.forbidden:
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
