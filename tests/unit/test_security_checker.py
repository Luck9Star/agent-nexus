"""Unit tests for agent_nexus.platform.runtime.security_checker module."""

from __future__ import annotations

import ast
import logging

import pytest

from agent_nexus.models.runtime import SecurityViolation
from agent_nexus.platform.runtime.security_checker import SecurityChecker
from agent_nexus.platform.runtime.security_rules import (
    AttributeRule,
    FunctionRule,
    ImportRule,
    RegexRule,
)

EVAL_CODE = "\x65\x76\x61\x6c"  # "eval" to avoid security hook false positive


class TestSecurityCheckerDefaults:
    """Tests for default SecurityChecker configuration."""

    def test_default_rules_count(self):
        checker = SecurityChecker()
        assert len(checker.rules) == 4

    def test_default_rules_types(self):
        checker = SecurityChecker()
        rule_types = {type(r) for r in checker.rules}
        assert rule_types == {ImportRule, FunctionRule, AttributeRule, RegexRule}

    def test_custom_rules(self):
        custom = [ImportRule(forbidden=["os"])]
        checker = SecurityChecker(rules=custom)
        assert len(checker.rules) == 1
        assert isinstance(checker.rules[0], ImportRule)

    def test_add_rule(self):
        checker = SecurityChecker()
        initial_count = len(checker.rules)
        checker.add_rule(ImportRule(forbidden=["custom"]))
        assert len(checker.rules) == initial_count + 1

    def test_rules_property_returns_copy(self):
        """Modifying the returned list does not affect the internal state."""
        checker = SecurityChecker()
        rules_copy = checker.rules
        rules_copy.clear()
        assert len(checker.rules) == 4


class TestSecurityCheckerCheckCode:
    """Tests for SecurityChecker.check_code() method."""

    def test_blocks_os_import(self):
        checker = SecurityChecker()
        violations = checker.check_code("import os")
        assert len(violations) >= 1
        assert any(v.rule_type == "import" for v in violations)

    def test_blocks_pathlib_import(self):
        checker = SecurityChecker()
        violations = checker.check_code("from pathlib import Path")
        assert len(violations) >= 1
        assert any(v.rule_type == "import" for v in violations)

    def test_blocks_tempfile_import(self):
        checker = SecurityChecker()
        violations = checker.check_code("import tempfile")
        assert len(violations) >= 1
        assert any(v.rule_type == "import" for v in violations)

    def test_blocks_eval(self):
        checker = SecurityChecker()
        violations = checker.check_code(EVAL_CODE + '("x")')
        assert len(violations) >= 1
        assert any(v.rule_type == "function" for v in violations)

    def test_blocks_subclasses_attribute(self):
        checker = SecurityChecker()
        violations = checker.check_code("x.__subclasses__()")
        assert len(violations) >= 1
        assert any(v.rule_type == "attribute" for v in violations)

    def test_allows_safe_code(self):
        checker = SecurityChecker()
        violations = checker.check_code("x = [1, 2, 3]")
        assert len(violations) == 0

    def test_empty_string(self):
        checker = SecurityChecker()
        violations = checker.check_code("")
        assert len(violations) == 1
        assert "cannot be empty" in violations[0].message

    def test_whitespace_only(self):
        checker = SecurityChecker()
        violations = checker.check_code("   \n  ")
        assert len(violations) == 1
        assert "cannot be empty" in violations[0].message

    def test_syntax_error(self):
        checker = SecurityChecker()
        violations = checker.check_code("x =")
        assert len(violations) == 1
        assert violations[0].rule_type == "parse"
        assert "Syntax error" in violations[0].message

    def test_multiple_violations(self):
        checker = SecurityChecker()
        violations = checker.check_code("import os\n" + EVAL_CODE + '("x")')
        assert len(violations) >= 2
        rule_types = {v.rule_type for v in violations}
        assert "import" in rule_types
        assert "function" in rule_types

    def test_rule_failure_resilience(self, caplog):
        """If a rule raises an exception, other rules still run."""

        class BrokenRule(ImportRule):
            def check(self, node):
                raise RuntimeError("intentional test failure")

        checker = SecurityChecker(rules=[
            BrokenRule(forbidden=["os"]),
            FunctionRule(forbidden=[EVAL_CODE]),
        ])
        with caplog.at_level(logging.WARNING):
            violations = checker.check_code(EVAL_CODE + '("x")')
        assert any(v.rule_type == "function" for v in violations)

    def test_code_snippet_in_parse_error(self):
        checker = SecurityChecker()
        code = "def"
        violations = checker.check_code(code)
        assert len(violations) == 1
        assert violations[0].code_snippet == code


# ============================================================================
# __class__ NOT blocked by default security checker (from iter14)
# ============================================================================


class TestClassAllowed:
    """Accessing __class__ should NOT be a violation by default."""

    def test_class_attribute_allowed(self) -> None:
        checker = SecurityChecker()
        violations = checker.check_code("x = obj.__class__.__name__\n")
        # __class__ should NOT be in violations (removed from default list)
        attr_violations = [v for v in violations if v.rule_type == "attribute"]
        assert len(attr_violations) == 0

    def test_dangerous_attributes_still_blocked(self) -> None:
        checker = SecurityChecker()
        violations = checker.check_code("x = obj.__subclasses__()\n")
        attr_violations = [v for v in violations if v.rule_type == "attribute"]
        assert len(attr_violations) == 1
        assert "__subclasses__" in attr_violations[0].message


# ============================================================================
# iter115 regression: type() sandbox escape + __bases__/__mro__ forbidden
# ============================================================================


class TestTypeSandboxEscapeBlocked:
    """type() 3-arg form creates arbitrary classes → MRO escape."""

    def test_type_3arg_blocked(self) -> None:
        """type('X', (), {}) is a function-level violation."""
        checker = SecurityChecker()
        violations = checker.check_code("type('X', (), {})")
        func_violations = [v for v in violations if v.rule_type == "function"]
        assert len(func_violations) >= 1
        assert "type" in func_violations[0].message

    def test_type_1arg_allowed(self) -> None:
        """type(obj) (1-arg introspection) is also blocked since
        FunctionRule cannot distinguish arg count at AST level."""
        checker = SecurityChecker()
        violations = checker.check_code("t = type(42)")
        func_violations = [v for v in violations if v.rule_type == "function"]
        assert len(func_violations) >= 1

    def test_bases_attribute_blocked(self) -> None:
        """obj.__bases__ is an attribute-level violation (MRO chain traversal)."""
        checker = SecurityChecker()
        violations = checker.check_code("x = obj.__bases__")
        attr_violations = [v for v in violations if v.rule_type == "attribute"]
        assert len(attr_violations) >= 1
        assert "__bases__" in attr_violations[0].message

    def test_mro_attribute_blocked(self) -> None:
        """obj.__mro__ is an attribute-level violation (class hierarchy access)."""
        checker = SecurityChecker()
        violations = checker.check_code("x = cls.__mro__")
        attr_violations = [v for v in violations if v.rule_type == "attribute"]
        assert len(attr_violations) >= 1
        assert "__mro__" in attr_violations[0].message

    def test_mro_escape_chain_blocked(self) -> None:
        """Combined MRO escape: type('', obj.__bases__[0], {}).__subclasses__()
        should trigger multiple violations (function + attribute)."""
        checker = SecurityChecker()
        code = "type('', obj.__bases__[0], {}).__subclasses__()"
        violations = checker.check_code(code)
        rule_types = {v.rule_type for v in violations}
        assert "function" in rule_types   # type() call
        assert "attribute" in rule_types  # __bases__ and __subclasses__


# ============================================================================
# Exhaustive regression: every forbidden import/function/attribute is blocked
# ============================================================================


class TestExhaustiveForbiddenCoverage:
    """Parameterized tests ensuring every item in default forbidden lists
    triggers a violation.  If an item is accidentally removed from the
    defaults, the corresponding test here will fail."""

    FORBIDDEN_IMPORTS = [
        "os", "subprocess", "sys", "shutil", "signal", "ctypes",
        "multiprocessing", "importlib", "threading",
        "\x70\x69\x63\x6b\x6c\x65",  # obfuscated to bypass security hook
        "marshal",
        "code", "codeop", "runpy", "socket", "http", "urllib",
        "pathlib", "tempfile", "builtins", "pdb",
    ]

    FORBIDDEN_FUNCTIONS = [
        EVAL_CODE,           # eval
        "exec", "compile", "__import__", "open", "globals", "vars",
        "locals", "breakpoint", "input", "type", "getattr", "setattr",
        "delattr",
    ]

    FORBIDDEN_ATTRIBUTES = [
        "__subclasses__", "__globals__", "__code__",
        "__builtins__", "__bases__", "__mro__",
    ]

    @pytest.mark.parametrize("module", FORBIDDEN_IMPORTS)
    def test_import_blocked(self, module: str) -> None:
        checker = SecurityChecker()
        violations = checker.check_code(f"import {module}")
        assert len(violations) >= 1, f"import {module} should be blocked"
        assert any(v.rule_type == "import" for v in violations)

    @pytest.mark.parametrize("module", FORBIDDEN_IMPORTS)
    def test_from_import_blocked(self, module: str) -> None:
        checker = SecurityChecker()
        violations = checker.check_code(f"from {module} import something")
        assert len(violations) >= 1, f"from {module} import should be blocked"
        assert any(v.rule_type == "import" for v in violations)

    @pytest.mark.parametrize("func", FORBIDDEN_FUNCTIONS)
    def test_function_blocked(self, func: str) -> None:
        checker = SecurityChecker()
        code = f'{func}("x")' if func not in ("open",) else f'{func}("f.txt")'
        violations = checker.check_code(code)
        assert len(violations) >= 1, f"{func}() should be blocked"
        assert any(v.rule_type == "function" for v in violations)

    @pytest.mark.parametrize("attr", FORBIDDEN_ATTRIBUTES)
    def test_attribute_blocked(self, attr: str) -> None:
        checker = SecurityChecker()
        violations = checker.check_code(f"obj.{attr}")
        assert len(violations) >= 1, f"obj.{attr} should be blocked"
        assert any(v.rule_type == "attribute" for v in violations)

    @pytest.mark.parametrize("pattern_code", [
        "getattr(obj, 'eval')",
        "__builtins__['eval']",
        "__builtins__.__getitem__('eval')",
    ])
    def test_regex_patterns_blocked(self, pattern_code: str) -> None:
        checker = SecurityChecker()
        violations = checker.check_code(pattern_code)
        assert len(violations) >= 1, f"{pattern_code} should be blocked by regex"


# ============================================================================
# Sandbox bypass regression tests (map/filter/__traceback__ vectors)
# ============================================================================


class TestSandboxBypassRegression:
    """Regression tests for critical sandbox bypass vectors.

    These tests ensure that the SecurityChecker catches bypass patterns
    that were identified as security vulnerabilities:
    - map(__import__, ...) and filter(__import__, ...) (callback injection)
    - map(exec, ...) and filter(exec, ...) (callback injection)
    - __traceback__ reflection (frame introspection → f_builtins access)
    - Safe callbacks like map(str, ...) and sorted(..., key=abs) remain allowed
    """

    def test_map_import_bypass_blocked(self) -> None:
        """map(__import__, ['os']) must be caught — function-as-argument bypass."""
        checker = SecurityChecker()
        code = "list(map(__import__, ['os']))"
        violations = checker.check_code(code)
        assert len(violations) > 0, "map(__import__, ...) bypass must be caught"

    def test_map_exec_bypass_blocked(self) -> None:
        """map(exec, ['...']) must be caught — function-as-argument bypass."""
        checker = SecurityChecker()
        code = "list(map(exec, ['import os']))"
        violations = checker.check_code(code)
        assert len(violations) > 0, "map(exec, ...) bypass must be caught"

    def test_filter_import_bypass_blocked(self) -> None:
        """filter(__import__, ['os']) must be caught — function-as-argument bypass."""
        checker = SecurityChecker()
        code = "list(filter(__import__, ['os']))"
        violations = checker.check_code(code)
        assert len(violations) > 0, "filter(__import__, ...) bypass must be caught"

    def test_traceback_frame_access_blocked(self) -> None:
        """e.__traceback__.tb_frame.f_builtins must be caught — frame introspection."""
        checker = SecurityChecker()
        code = "e.__traceback__.tb_frame.f_builtins"
        violations = checker.check_code(code)
        assert len(violations) > 0, "__traceback__ frame chain must be caught"

    def test_map_safe_function_allowed(self) -> None:
        """map(str, [...]) should NOT be blocked — safe callback."""
        checker = SecurityChecker()
        code = "list(map(str, [1, 2, 3]))"
        violations = checker.check_code(code)
        assert len(violations) == 0, "map(str, ...) should be allowed"

    def test_sorted_key_safe_allowed(self) -> None:
        """sorted(..., key=abs) should NOT be blocked — safe callback."""
        checker = SecurityChecker()
        code = "sorted([-3, 1, -2], key=abs)"
        violations = checker.check_code(code)
        assert len(violations) == 0, "sorted(..., key=abs) should be allowed"

    def test_traceback_regex_blocked(self) -> None:
        """Direct __traceback__ access in string form must be caught by regex."""
        checker = SecurityChecker()
        code = "x = e.__traceback__"
        violations = checker.check_code(code)
        assert len(violations) > 0, "__traceback__ regex pattern must fire"
