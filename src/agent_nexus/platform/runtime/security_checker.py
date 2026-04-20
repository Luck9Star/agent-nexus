"""SecurityChecker: AST-level safety analysis for Python code.

Orchestrates parsing -> AST walk -> rule application -> violation collection.
Provides a default rule set covering common attack vectors.

Reference: cave-agent/src/cave_agent/security/checker.py
"""

from __future__ import annotations

import ast
import logging

from agent_nexus.models.runtime import SecurityViolation

from .security_rules import (
    AttributeRule,
    FunctionRule,
    ImportRule,
    RegexRule,
    SecurityRule,
)

logger = logging.getLogger(__name__)

# Default forbidden sets for the standard security policy
_DEFAULT_FORBIDDEN_IMPORTS = [
    "os",
    "subprocess",
    "sys",
    "shutil",
    "signal",
    "ctypes",
    "multiprocessing",
    "importlib",
    "pickle",
    "marshal",
    "code",
    "codeop",
    "runpy",
    "socket",
    "http",
    "urllib",
    "pathlib",   # Path provides file read/write, bypassing open() block
    "tempfile",   # temp file creation bypasses path_rules
]

_DEFAULT_FORBIDDEN_FUNCTIONS = [
    "eval",
    "exec",
    "compile",
    "__import__",
    "open",
    "globals",
    "vars",
    "locals",
    "breakpoint",
    "input",
    "type",  # 3-arg form creates arbitrary classes → MRO sandbox escape
]

_DEFAULT_FORBIDDEN_ATTRIBUTES = [
    "__subclasses__",
    "__globals__",
    "__code__",
    "__builtins__",
    "__bases__",  # MRO chain traversal → sandbox escape
    "__mro__",    # method resolution order → class hierarchy access
]

_DEFAULT_REGEX_PATTERNS = [
    r"getattr\s*\(\s*\w+\s*,\s*['\"](?:eval|exec|compile|__import__)['\"]",
    r"__builtins__\s*\[",
    r"__builtins__\s*\.\s*__getitem__\s*\(",
]


class SecurityChecker:
    """Orchestrate AST-level security analysis.

    Flow: parse code -> walk AST -> apply all rules -> collect violations.

    Example::

        checker = SecurityChecker()
        violations = checker.check_code('import os')
        if violations:
            for v in violations:
                print(f"[{v.rule_type}] {v.message}")
    """

    DEFAULT_RULES: list[SecurityRule] = [
        ImportRule(forbidden=_DEFAULT_FORBIDDEN_IMPORTS),
        FunctionRule(forbidden=_DEFAULT_FORBIDDEN_FUNCTIONS),
        AttributeRule(forbidden=_DEFAULT_FORBIDDEN_ATTRIBUTES),
        RegexRule(
            patterns=_DEFAULT_REGEX_PATTERNS,
            description="Dynamic getattr for forbidden functions",
        ),
    ]

    def __init__(self, rules: list[SecurityRule] | None = None) -> None:
        """Initialize SecurityChecker with specified rules.

        Args:
            rules: Security rules to apply. If None, uses DEFAULT_RULES.
        """
        self._regex_rules: list[RegexRule] = []
        self._structural_rules: list[SecurityRule] = []
        if rules is not None:
            self._rules: list[SecurityRule] = []
            for rule in rules:
                self.add_rule(rule)
        else:
            self._rules = list(self.DEFAULT_RULES)
        self._classify_rules()

    def add_rule(self, rule: SecurityRule) -> None:
        """Add a security rule to the checker.

        Args:
            rule: SecurityRule instance to add.
        """
        self._rules.append(rule)
        self._classify_rules()

    def _classify_rules(self) -> None:
        """Partition self._rules into regex vs structural lists."""
        self._regex_rules = [r for r in self._rules if isinstance(r, RegexRule)]
        self._structural_rules = [r for r in self._rules if not isinstance(r, RegexRule)]

    @property
    def rules(self) -> list[SecurityRule]:
        """Read-only access to the current rule set."""
        return list(self._rules)

    def check_code(self, code: str) -> list[SecurityViolation]:
        """Analyze Python code for security violations.

        Parses the code into an AST and applies all security rules
        to detect issues. Returns empty list if no violations found.

        Args:
            code: Python source code string to analyze.

        Returns:
            List of SecurityViolation objects. Empty if code is safe.
        """
        if not code or not code.strip():
            return [
                SecurityViolation(
                    rule_type="parse",
                    node_type="Module",
                    message="Parse error: Code cannot be empty",
                )
            ]

        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return [
                SecurityViolation(
                    rule_type="parse",
                    node_type="Module",
                    code_snippet=code[:200],
                    message=f"Syntax error: {e}",
                )
            ]
        except Exception as e:
            return [
                SecurityViolation(
                    rule_type="parse",
                    node_type="Module",
                    code_snippet=code[:200],
                    message=f"Parse error: {e}",
                )
            ]

        violations: list[SecurityViolation] = []

        # Structural rules: per-node
        for node in ast.walk(tree):
            for rule in self._structural_rules:
                try:
                    violations.extend(rule.check(node))
                except Exception:
                    logger.warning(
                        "Rule %r failed on node %s",
                        type(rule).__name__,
                        type(node).__name__,
                        exc_info=True,
                    )
                    continue

        # Regex rules: once on full source
        for rule in self._regex_rules:
            try:
                violations.extend(rule.check_source(code))
            except Exception:
                logger.warning(
                    "Rule %r failed on source",
                    type(rule).__name__,
                    exc_info=True,
                )

        return violations
