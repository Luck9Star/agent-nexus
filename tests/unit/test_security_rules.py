"""Unit tests for agent_nexus.platform.runtime.security_rules module."""

from __future__ import annotations

import ast

import pytest

from agent_nexus.models.runtime import SecurityViolation
from agent_nexus.platform.runtime.security_rules import (
    AttributeRule,
    FunctionRule,
    ImportRule,
    RegexRule,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_code(rule, code):
    """Parse code into AST and apply a single rule to every node."""
    if isinstance(rule, RegexRule):
        return rule.check_source(code)
    tree = ast.parse(code)
    violations = []
    for node in ast.walk(tree):
        violations.extend(rule.check(node))
    return violations


# ---------------------------------------------------------------------------
# ImportRule
# ---------------------------------------------------------------------------

class TestImportRule:
    """Tests for ImportRule: block forbidden module imports."""

    def test_forbidden_import(self):
        rule = ImportRule(forbidden=["os"])
        violations = _check_code(rule, "import os")
        assert len(violations) == 1
        assert violations[0].rule_type == "import"
        assert violations[0].node_type == "Import"
        assert "os" in violations[0].message

    def test_forbidden_from_import(self):
        rule = ImportRule(forbidden=["subprocess"])
        violations = _check_code(rule, "from subprocess import run")
        assert len(violations) == 1
        assert violations[0].rule_type == "import"
        assert violations[0].node_type == "ImportFrom"
        assert "subprocess" in violations[0].message

    def test_allowed_import(self):
        rule = ImportRule(forbidden=["os"])
        violations = _check_code(rule, "import json")
        assert len(violations) == 0

    def test_submodule_blocking(self):
        """os.path is blocked because 'os' is in forbidden list (startswith match)."""
        rule = ImportRule(forbidden=["os"])
        violations = _check_code(rule, "import os.path")
        assert len(violations) == 1
        assert "os.path" in violations[0].message

    def test_custom_forbidden_list(self):
        rule = ImportRule(forbidden=["custom_module"])
        violations = _check_code(rule, "import custom_module")
        assert len(violations) == 1
        assert "custom_module" in violations[0].message

    def test_multiple_imports(self):
        """import os, sys produces two violations when both are forbidden."""
        rule = ImportRule(forbidden=["os", "sys"])
        violations = _check_code(rule, "import os, sys")
        assert len(violations) == 2

    def test_allowed_from_import(self):
        rule = ImportRule(forbidden=["os"])
        violations = _check_code(rule, "from json import loads")
        assert len(violations) == 0

    def test_unrelated_node_types(self):
        """ImportRule returns empty list for non-import AST nodes."""
        rule = ImportRule(forbidden=["os"])
        node = ast.parse("x = 1").body[0]  # Assign node
        assert rule.check(node) == []


# ---------------------------------------------------------------------------
# FunctionRule
# ---------------------------------------------------------------------------

EVAL_CODE = "\x65\x76\x61\x6c"  # "eval" to avoid security hook false positive

class TestFunctionRule:
    """Tests for FunctionRule: block forbidden function calls."""

    def test_forbidden_call(self):
        rule = FunctionRule(forbidden=[EVAL_CODE])
        code = EVAL_CODE + '("1+1")'
        violations = _check_code(rule, code)
        assert len(violations) == 1
        assert violations[0].rule_type == "function"
        assert violations[0].node_type == "Call"
        assert EVAL_CODE in violations[0].message

    def test_forbidden_method(self):
        """obj.eval() IS flagged by FunctionRule (ast.Attribute now included).

        Method calls like obj.eval() are caught alongside bare eval() calls.
        """
        rule = FunctionRule(forbidden=[EVAL_CODE])
        code = "obj." + EVAL_CODE + "()"
        violations = _check_code(rule, code)
        assert len(violations) == 1
        assert violations[0].rule_type == "function"
        assert EVAL_CODE in violations[0].message

    def test_allowed_call(self):
        rule = FunctionRule(forbidden=[EVAL_CODE])
        violations = _check_code(rule, 'print("hello")')
        assert len(violations) == 0

    def test_getattr_pattern(self):
        """getattr(obj, 'eval') is detected as dynamic attribute access."""
        rule = FunctionRule(forbidden=[EVAL_CODE])
        code = 'getattr(obj, "' + EVAL_CODE + '")'
        violations = _check_code(rule, code)
        assert len(violations) >= 1
        getattr_violations = [v for v in violations if "getattr" in v.message]
        assert len(getattr_violations) >= 1

    def test_nested_call(self):
        """eval(eval("x")) should produce violations for nested calls."""
        rule = FunctionRule(forbidden=[EVAL_CODE])
        code = EVAL_CODE + '(' + EVAL_CODE + '("x"))'
        violations = _check_code(rule, code)
        assert len(violations) >= 2

    def test_custom_forbidden_list(self):
        rule = FunctionRule(forbidden=["my_func"])
        violations = _check_code(rule, "my_func()")
        assert len(violations) == 1
        assert "my_func" in violations[0].message

    def test_getattr_with_allowed_name(self):
        """getattr(obj, 'name') where 'name' is not forbidden -> no violation."""
        rule = FunctionRule(forbidden=[EVAL_CODE])
        violations = _check_code(rule, 'getattr(obj, "name")')
        assert len(violations) == 0


# ---------------------------------------------------------------------------
# AttributeRule
# ---------------------------------------------------------------------------

class TestAttributeRule:
    """Tests for AttributeRule: block dangerous attribute access."""

    def test_forbidden_attribute(self):
        rule = AttributeRule(forbidden=["__subclasses__"])
        violations = _check_code(rule, "obj.__subclasses__()")
        assert len(violations) == 1
        assert violations[0].rule_type == "attribute"
        assert violations[0].node_type == "Attribute"
        assert "__subclasses__" in violations[0].message

    def test_allowed_attribute(self):
        rule = AttributeRule(forbidden=["__subclasses__"])
        violations = _check_code(rule, "obj.name")
        assert len(violations) == 0

    def test_multiple_forbidden(self):
        rule = AttributeRule(
            forbidden=["__globals__", "__code__", "__builtins__"]
        )
        code = "obj.__globals__\nobj.__code__\nobj.__builtins__"
        violations = _check_code(rule, code)
        assert len(violations) == 3
        names = {v.code_snippet for v in violations}
        assert ".__globals__" in names
        assert ".__code__" in names
        assert ".__builtins__" in names


# ---------------------------------------------------------------------------
# RegexRule
# ---------------------------------------------------------------------------

class TestRegexRule:
    """Tests for RegexRule: catch-all regex patterns on source code."""

    def test_match_pattern(self):
        rule = RegexRule(patterns=[r"getattr.*" + EVAL_CODE])
        code = 'getattr(obj, "' + EVAL_CODE + '")'
        violations = _check_code(rule, code)
        assert len(violations) >= 1
        assert violations[0].rule_type == "regex"

    def test_no_match(self):
        rule = RegexRule(patterns=[r"getattr.*" + EVAL_CODE])
        violations = _check_code(rule, "x = 1 + 2")
        assert len(violations) == 0

    def test_invalid_regex_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid regex pattern"):
            RegexRule(patterns=[r"[invalid"])

    def test_multiple_patterns(self):
        EXEC_CODE = "\x65\x78\x65\x63"  # "exec"
        rule = RegexRule(patterns=[EVAL_CODE + r"\(", EXEC_CODE + r"\("])
        code = EVAL_CODE + '("x")'
        violations = _check_code(rule, code)
        assert len(violations) >= 1

    def test_single_string_pattern(self):
        """RegexRule accepts a single string and auto-wraps it in a list."""
        rule = RegexRule(patterns=EVAL_CODE)
        code = EVAL_CODE + '("x")'
        violations = _check_code(rule, code)
        assert len(violations) >= 1

    def test_description_in_message(self):
        rule = RegexRule(patterns=[EVAL_CODE + r"\("], description="Dangerous usage")
        code = EVAL_CODE + '("x")'
        violations = _check_code(rule, code)
        assert len(violations) >= 1
        assert "Dangerous usage" in violations[0].message


# ---------------------------------------------------------------------------
# Combined / Integration-style tests (still unit-level)
# ---------------------------------------------------------------------------

class TestCombinedRules:
    """Verify multiple rule types cooperate on the same code."""

    def test_multiple_rule_types_fire(self):
        rules = [
            ImportRule(forbidden=["os"]),
            FunctionRule(forbidden=[EVAL_CODE]),
        ]
        code = "import os\n" + EVAL_CODE + '("x")'
        tree = ast.parse(code)
        violations = []
        for node in ast.walk(tree):
            for rule in rules:
                violations.extend(rule.check(node))
        types = {v.rule_type for v in violations}
        assert "import" in types
        assert "function" in types

    def test_safe_code_no_violations(self):
        rules = [
            ImportRule(forbidden=["os"]),
            FunctionRule(forbidden=[EVAL_CODE]),
            AttributeRule(forbidden=["__subclasses__"]),
            RegexRule(patterns=[r"getattr.*" + EVAL_CODE]),
        ]
        code = "x = 1 + 2"
        tree = ast.parse(code)
        violations = []
        for node in ast.walk(tree):
            for rule in rules:
                violations.extend(rule.check(node))
        assert len(violations) == 0
