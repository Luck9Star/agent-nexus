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
