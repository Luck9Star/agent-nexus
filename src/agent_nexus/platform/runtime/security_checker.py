"""SecurityChecker: AST-level safety analysis for Python code.

Orchestrates parsing -> AST walk -> rule application -> violation collection.
Provides a default rule set covering common attack vectors.

Reference: cave-agent/src/cave_agent/security/checker.py
"""

from __future__ import annotations

import ast
import logging

from typing import ClassVar

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
    "threading",  # daemon threads survive timeout → persistent side effects
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
    "builtins",   # access to eval/exec/compile via builtins module
    "pdb",        # interactive debugger can escape sandbox
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
    "getattr",  # variable second-arg bypasses constant-only detection
    "setattr",  # can modify injected objects' attributes
    "delattr",  # can remove safety-related attributes
]

# Qualified method calls that should be blocked regardless of import.
# Format: (module_name, method_name) — matches obj.attr where obj.id
# matches module_name.  This avoids false positives on generic names
# like "run" or "call" while still blocking subprocess.run(), os.system(), etc.
_DEFAULT_FORBIDDEN_QUALIFIED_CALLS: dict[str, set[str]] = {
    "os": {"system", "popen", "spawnl", "spawnv", "spawnle", "spawnve"},
    "subprocess": {"call", "run", "Popen", "check_output", "check_call"},
}

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
    r"\b__builtins__\b",  # bare __builtins__ Name access bypasses AttributeRule
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

    DEFAULT_RULES: ClassVar[list[SecurityRule]] = [
        ImportRule(forbidden=_DEFAULT_FORBIDDEN_IMPORTS),
        FunctionRule(
            forbidden=_DEFAULT_FORBIDDEN_FUNCTIONS,
            qualified_calls=_DEFAULT_FORBIDDEN_QUALIFIED_CALLS,
        ),
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
        self._cache: dict[str, tuple[SecurityViolation, ...]] = {}
        self._cache_max = 128

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

        Empty code and syntax errors are handled before the cache lookup
        since they are cheap and should not pollute the LRU cache.

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
            ast.parse(code)
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

        return list(self._check_cached(code))

    def _check_cached(self, code: str) -> tuple[SecurityViolation, ...]:
        """Cached AST walk + rule application.

        Per-instance cache avoids the memory leak and cross-instance
        contamination that @lru_cache on a method causes (the class-level
        descriptor retains strong references to ``self``).

        Returns:
            Tuple of SecurityViolation objects.
        """
        cached = self._cache.get(code)
        if cached is not None:
            return cached

        tree = ast.parse(code)

        violations: list[SecurityViolation] = []

        # Structural rules: per-node
        for node in ast.walk(tree):
            for rule in self._structural_rules:
                try:
                    violations.extend(rule.check(node))
                except Exception:
                    logger.warning(
                        "Security rule %r failed on node %s — treating as violation",
                        type(rule).__name__,
                        type(node).__name__,
                        exc_info=True,
                    )
                    violations.append(SecurityViolation(
                        rule_type=type(rule).__name__,
                        node_type=type(node).__name__,
                        message=f"Security rule {type(rule).__name__!r} raised an exception — execution blocked for safety",
                    ))

        # Regex rules: once on full source
        for rule in self._regex_rules:
            try:
                violations.extend(rule.check_source(code))
            except Exception:
                logger.warning(
                    "Security rule %r failed on source — treating as violation",
                    type(rule).__name__,
                    exc_info=True,
                )
                violations.append(SecurityViolation(
                    rule_type=type(rule).__name__,
                    node_type="source",
                    message=f"Security rule {type(rule).__name__!r} raised an exception — execution blocked for safety",
                ))

        result = tuple(violations)

        # Evict oldest entry if cache is full (FIFO eviction).
        if len(self._cache) >= self._cache_max:
            self._cache.pop(next(iter(self._cache)))
        self._cache[code] = result
        return result

    def clear_cache(self) -> None:
        """Clear the internal cache. Useful for test cleanup."""
        self._cache.clear()
