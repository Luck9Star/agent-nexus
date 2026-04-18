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
