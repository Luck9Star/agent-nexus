"""Tests for code-reviewer tools.

Covers:
- analyze_code: file analysis, language detection, metric calculation, rule matching
- check_patterns: anti-pattern detection, empty input handling, line number accuracy
- generate_review: severity counting, score calculation, suggestion generation
"""

from __future__ import annotations

import os
import tempfile

import pytest

from agent_code_reviewer.models import (
    CodeAnalysis,
    CodeIssue,
    CodeMetrics,
    PatternMatch,
    ReviewReport,
)
from agent_code_reviewer.tools.analyze_code import (
    _count_classes,
    _count_functions,
    _count_imports,
    _count_lines,
    _detect_language,
    _estimate_complexity,
    _measure_nesting,
    analyze_code,
)
from agent_code_reviewer.tools.check_patterns import check_patterns
from agent_code_reviewer.tools.generate_review import (
    _calculate_score,
    _count_severities,
    _generate_suggestions,
    generate_review,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir():
    """Provide a temporary directory that is cleaned up after the test."""
    with tempfile.TemporaryDirectory() as d:
        yield d


def _write_file(dir_path: str, filename: str, content: str) -> str:
    """Write a file and return its absolute path."""
    filepath = os.path.join(dir_path, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath


# ---------------------------------------------------------------------------
# analyze_code helpers
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    """Tests for _detect_language helper."""

    def test_python_extension(self) -> None:
        assert _detect_language("main.py", "") == "python"

    def test_javascript_extension(self) -> None:
        assert _detect_language("app.js", "") == "javascript"

    def test_typescript_extension(self) -> None:
        assert _detect_language("app.ts", "") == "typescript"
        assert _detect_language("Component.tsx", "") == "typescript"

    def test_rust_extension(self) -> None:
        assert _detect_language("main.rs", "") == "rust"

    def test_java_extension(self) -> None:
        assert _detect_language("App.java", "") == "java"

    def test_go_extension(self) -> None:
        assert _detect_language("main.go", "") == "go"

    def test_cpp_extension(self) -> None:
        assert _detect_language("main.cpp", "") == "cpp"
        assert _detect_language("header.hpp", "") == "cpp"

    def test_kotlin_extension(self) -> None:
        assert _detect_language("App.kt", "") == "kotlin"

    def test_content_heuristic_python(self) -> None:
        assert _detect_language("unknown.txt", "def foo():\n    pass") == "python"

    def test_content_heuristic_javascript(self) -> None:
        assert _detect_language("unknown.txt", "function foo() {}") == "javascript"

    def test_content_heuristic_rust(self) -> None:
        assert _detect_language("unknown.txt", "fn main() {\nlet x = 1;\n}") == "rust"

    def test_content_heuristic_java(self) -> None:
        assert _detect_language("unknown.txt", "public class App {}") == "java"

    def test_unknown_language(self) -> None:
        assert _detect_language("unknown.xyz", "blah blah") == "unknown"


class TestCountLines:
    """Tests for _count_lines helper."""

    def test_empty_list(self) -> None:
        code_lines, total = _count_lines([])
        assert code_lines == 0
        assert total == 0

    def test_code_only(self) -> None:
        code_lines, total = _count_lines(["x = 1", "y = 2"])
        assert code_lines == 2
        assert total == 2

    def test_with_blank_lines(self) -> None:
        code_lines, total = _count_lines(["x = 1", "", "y = 2"])
        assert code_lines == 2
        assert total == 3

    def test_with_comments(self) -> None:
        code_lines, total = _count_lines(["x = 1", "# comment", "// c comment"])
        assert code_lines == 1
        assert total == 3

    def test_all_comments(self) -> None:
        code_lines, total = _count_lines(["# line 1", "# line 2"])
        assert code_lines == 0
        assert total == 2


class TestCountFunctions:
    """Tests for _count_functions helper."""

    def test_python_functions(self) -> None:
        code = "def foo():\n    pass\n\ndef bar():\n    pass\n"
        assert _count_functions(code, "python") == 2

    def test_javascript_functions(self) -> None:
        code = "function foo() {}\nconst bar = () => {}\n"
        assert _count_functions(code, "javascript") >= 2

    def test_rust_functions(self) -> None:
        code = "fn main() {}\nfn helper() {}\n"
        assert _count_functions(code, "rust") == 2

    def test_java_methods(self) -> None:
        code = "public void doThing() {}\nprivate String getName() { return null; }\n"
        assert _count_functions(code, "java") >= 2

    def test_unknown_language(self) -> None:
        assert _count_functions("some code", "unknown") == 0

    def test_empty_code(self) -> None:
        assert _count_functions("", "python") == 0


class TestCountClasses:
    """Tests for _count_classes helper."""

    def test_python_classes(self) -> None:
        code = "class Foo:\n    pass\n\nclass Bar:\n    pass\n"
        assert _count_classes(code, "python") == 2

    def test_javascript_classes(self) -> None:
        code = "class Foo {}\nclass Bar {}\n"
        assert _count_classes(code, "javascript") == 2

    def test_rust_structs(self) -> None:
        code = "struct Foo {}\nenum Bar {}\ntrait Baz {}\nimpl Foo {}\n"
        assert _count_classes(code, "rust") == 4

    def test_java_classes(self) -> None:
        code = "public class App {}\nclass Helper {}\n"
        assert _count_classes(code, "java") >= 2

    def test_unknown_language(self) -> None:
        assert _count_classes("some code", "unknown") == 0


class TestEstimateComplexity:
    """Tests for _estimate_complexity helper."""

    def test_empty_code(self) -> None:
        assert _estimate_complexity("", "python") == 1

    def test_simple_if(self) -> None:
        code = "if x:\n    pass"
        assert _estimate_complexity(code, "python") >= 2

    def test_multiple_branches(self) -> None:
        code = "if a:\n    pass\nelif b:\n    pass\nelse:\n    pass"
        assert _estimate_complexity(code, "python") >= 4

    def test_loop_and_condition(self) -> None:
        code = "for x in items:\n    if x:\n        pass"
        assert _estimate_complexity(code, "python") >= 3


class TestMeasureNesting:
    """Tests for _measure_nesting helper."""

    def test_no_nesting(self) -> None:
        assert _measure_nesting(["x = 1", "y = 2"]) == 0

    def test_single_level(self) -> None:
        assert _measure_nesting(["if True:", "    x = 1"]) == 1

    def test_deep_nesting(self) -> None:
        lines = [
            "if a:",
            "    if b:",
            "        if c:",
            "            if d:",
            "                x = 1",
        ]
        assert _measure_nesting(lines) == 4

    def test_empty_lines_ignored(self) -> None:
        lines = ["if True:", "", "    x = 1"]
        assert _measure_nesting(lines) == 1


class TestCountImports:
    """Tests for _count_imports helper."""

    def test_python_imports(self) -> None:
        code = "import os\nimport sys\nfrom pathlib import Path\n"
        assert _count_imports(code, "python") == 3

    def test_javascript_imports(self) -> None:
        code = "import React from 'react'\nimport { useState } from 'react'\n"
        assert _count_imports(code, "javascript") == 2

    def test_rust_imports(self) -> None:
        code = "use std::io;\nuse std::fs;\n"
        assert _count_imports(code, "rust") == 2

    def test_java_imports(self) -> None:
        code = "import java.util.List;\nimport java.io.File;\n"
        assert _count_imports(code, "java") == 2

    def test_unknown_language(self) -> None:
        assert _count_imports("some code", "unknown") == 0


# ---------------------------------------------------------------------------
# analyze_code -- main function
# ---------------------------------------------------------------------------


class TestAnalyzeCode:
    """Tests for analyze_code tool."""

    def test_file_not_found(self) -> None:
        result = analyze_code("/nonexistent/file.py")
        assert isinstance(result, CodeAnalysis)
        assert result.file_path == "/nonexistent/file.py"
        assert result.issues == []

    def test_empty_file(self, tmp_dir: str) -> None:
        path = _write_file(tmp_dir, "empty.py", "")
        result = analyze_code(path)
        assert isinstance(result, CodeAnalysis)
        assert result.language == "python"
        assert result.metrics.total_lines == 1  # empty string splits to [""]

    def test_simple_python_file(self, tmp_dir: str) -> None:
        code = "import os\nimport sys\n\ndef hello():\n    print('hello')\n"
        path = _write_file(tmp_dir, "hello.py", code)
        result = analyze_code(path)
        assert result.language == "python"
        assert result.metrics.import_count == 2
        assert result.metrics.function_count == 1
        assert result.metrics.lines_of_code > 0

    def test_python_bare_except(self, tmp_dir: str) -> None:
        code = "try:\n    x = 1\nexcept:\n    pass\n"
        path = _write_file(tmp_dir, "bare.py", code)
        result = analyze_code(path)
        rule_ids = [i.rule_id for i in result.issues]
        assert "PY001" in rule_ids

    def test_python_eval_usage(self, tmp_dir: str) -> None:
        code = 'result = eval("1+1")\n'
        path = _write_file(tmp_dir, "eval_code.py", code)
        result = analyze_code(path)
        rule_ids = [i.rule_id for i in result.issues]
        assert "PY002" in rule_ids
        assert all(i.severity == "critical" for i in result.issues if i.rule_id == "PY002")

    def test_python_wildcard_import(self, tmp_dir: str) -> None:
        code = "from os import *\n"
        path = _write_file(tmp_dir, "wild.py", code)
        result = analyze_code(path)
        rule_ids = [i.rule_id for i in result.issues]
        assert "PY003" in rule_ids

    def test_python_print_statement(self, tmp_dir: str) -> None:
        code = 'print("hello")\n'
        path = _write_file(tmp_dir, "print_code.py", code)
        result = analyze_code(path)
        rule_ids = [i.rule_id for i in result.issues]
        assert "PY004" in rule_ids

    def test_python_hardcoded_secret(self, tmp_dir: str) -> None:
        code = 'password = "super_secret_123"\n'
        path = _write_file(tmp_dir, "secret.py", code)
        result = analyze_code(path)
        rule_ids = [i.rule_id for i in result.issues]
        assert "PY005" in rule_ids
        assert all(i.severity == "critical" for i in result.issues if i.rule_id == "PY005")

    def test_python_todo_comments(self, tmp_dir: str) -> None:
        code = "# TODO: fix this later\n# FIXME: broken\n"
        path = _write_file(tmp_dir, "todo.py", code)
        result = analyze_code(path)
        rule_ids = [i.rule_id for i in result.issues]
        py007_count = rule_ids.count("PY007")
        assert py007_count >= 2

    def test_javascript_console_log(self, tmp_dir: str) -> None:
        code = 'console.log("debug")\n'
        path = _write_file(tmp_dir, "debug.js", code)
        result = analyze_code(path)
        rule_ids = [i.rule_id for i in result.issues]
        assert "JS001" in rule_ids

    def test_javascript_var_usage(self, tmp_dir: str) -> None:
        code = "var x = 1\n"
        path = _write_file(tmp_dir, "var_code.js", code)
        result = analyze_code(path)
        rule_ids = [i.rule_id for i in result.issues]
        assert "JS002" in rule_ids

    def test_javascript_loose_equality(self, tmp_dir: str) -> None:
        code = "if (x == 1) {}\nif (y != null) {}\n"
        path = _write_file(tmp_dir, "eq.js", code)
        result = analyze_code(path)
        rule_ids = [i.rule_id for i in result.issues]
        assert "JS003" in rule_ids

    def test_javascript_hardcoded_secret(self, tmp_dir: str) -> None:
        code = 'const password = "secret_value_here"\n'
        path = _write_file(tmp_dir, "secret.js", code)
        result = analyze_code(path)
        rule_ids = [i.rule_id for i in result.issues]
        assert "JS004" in rule_ids

    def test_javascript_dynamic_code_execution(self, tmp_dir: str) -> None:
        code = "var r = eval(userInput)\n"
        path = _write_file(tmp_dir, "dyn.js", code)
        result = analyze_code(path)
        rule_ids = [i.rule_id for i in result.issues]
        assert "JS005" in rule_ids

    def test_rust_unsafe(self, tmp_dir: str) -> None:
        code = "unsafe {\n    *ptr\n}\n"
        path = _write_file(tmp_dir, "unsafe.rs", code)
        result = analyze_code(path)
        rule_ids = [i.rule_id for i in result.issues]
        assert "RS001" in rule_ids

    def test_rust_unwrap(self, tmp_dir: str) -> None:
        code = "let x = option.unwrap();\n"
        path = _write_file(tmp_dir, "unwrap.rs", code)
        result = analyze_code(path)
        rule_ids = [i.rule_id for i in result.issues]
        assert "RS002" in rule_ids

    def test_rust_todo_macro(self, tmp_dir: str) -> None:
        code = "fn stub() {\n    todo!()\n}\n"
        path = _write_file(tmp_dir, "todo_code.rs", code)
        result = analyze_code(path)
        rule_ids = [i.rule_id for i in result.issues]
        assert "RS003" in rule_ids

    def test_java_system_out(self, tmp_dir: str) -> None:
        code = 'System.out.println("debug");\n'
        path = _write_file(tmp_dir, "App.java", code)
        result = analyze_code(path)
        rule_ids = [i.rule_id for i in result.issues]
        assert "JV001" in rule_ids

    def test_java_broad_catch(self, tmp_dir: str) -> None:
        code = "try {\n    doStuff();\n} catch (Exception e) {\n}\n"
        path = _write_file(tmp_dir, "Catch.java", code)
        result = analyze_code(path)
        rule_ids = [i.rule_id for i in result.issues]
        assert "JV002" in rule_ids

    def test_java_hardcoded_secret(self, tmp_dir: str) -> None:
        code = 'String password = "my_secret_pass";\n'
        path = _write_file(tmp_dir, "Secret.java", code)
        result = analyze_code(path)
        rule_ids = [i.rule_id for i in result.issues]
        assert "JV003" in rule_ids

    def test_language_hint_overrides_detection(self, tmp_dir: str) -> None:
        code = "fn main() {}"
        path = _write_file(tmp_dir, "main.txt", code)
        result = analyze_code(path, language="rust")
        assert result.language == "rust"

    def test_metrics_populated(self, tmp_dir: str) -> None:
        code = "import os\n\nclass Foo:\n    def bar(self):\n        if True:\n            pass\n"
        path = _write_file(tmp_dir, "metrics.py", code)
        result = analyze_code(path)
        assert result.metrics.import_count == 1
        assert result.metrics.class_count == 1
        assert result.metrics.function_count == 1
        assert result.metrics.max_complexity >= 2
        assert result.metrics.total_lines > 0


# ---------------------------------------------------------------------------
# check_patterns
# ---------------------------------------------------------------------------


class TestCheckPatterns:
    """Tests for check_patterns tool."""

    def test_empty_input(self) -> None:
        result = check_patterns("")
        assert result == []

    def test_whitespace_only_input(self) -> None:
        result = check_patterns("   \n\n  \t  ")
        assert result == []

    def test_sql_injection_format(self) -> None:
        code = 'cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")\n'
        result = check_patterns(code)
        names = [p.pattern for p in result]
        assert "sql_injection" in names

    def test_hardcoded_secret(self) -> None:
        code = 'password = "super_secret_value_123"\n'
        result = check_patterns(code)
        names = [p.pattern for p in result]
        assert "hardcoded_secret" in names

    def test_insecure_random(self) -> None:
        code = "import random\nrandom.randint(1, 100)\n"
        result = check_patterns(code)
        names = [p.pattern for p in result]
        assert "insecure_random" in names

    def test_empty_catch(self) -> None:
        code = "try:\n    x = 1\nexcept Exception:\n    pass\n"
        result = check_patterns(code)
        names = [p.pattern for p in result]
        assert "empty_catch" in names

    def test_broad_exception(self) -> None:
        code = "try:\n    x = 1\nexcept Exception:\n    handle()\n"
        result = check_patterns(code)
        names = [p.pattern for p in result]
        assert "broad_exception" in names

    def test_deep_nesting(self) -> None:
        code = "if a:\n    if b:\n        if c:\n            if d:\n                pass\n"
        result = check_patterns(code)
        names = [p.pattern for p in result]
        assert "deep_nesting" in names

    def test_magic_number(self) -> None:
        code = "timeout = 300\nmax_retries = 42\n"
        result = check_patterns(code)
        names = [p.pattern for p in result]
        assert "magic_number" in names

    def test_clean_code_no_critical_patterns(self) -> None:
        code = "x = 1\ny = 2\nz = x + y\n"
        result = check_patterns(code)
        critical = [p for p in result if p.severity == "critical"]
        assert len(critical) == 0

    def test_pattern_has_line_number(self) -> None:
        code = 'password = "super_secret_value_123"\n'
        result = check_patterns(code)
        assert len(result) >= 1
        assert all(isinstance(p.line, int) for p in result)

    def test_pattern_has_severity(self) -> None:
        code = 'cursor.execute(f"SELECT * FROM t WHERE id={x}")\n'
        result = check_patterns(code)
        assert len(result) >= 1
        assert all(p.severity in ("critical", "warning", "info") for p in result)

    def test_pattern_has_description(self) -> None:
        code = 'password = "super_secret_value_123"\n'
        result = check_patterns(code)
        assert all(p.description for p in result)


# ---------------------------------------------------------------------------
# generate_review helpers
# ---------------------------------------------------------------------------


class TestCountSeverities:
    """Tests for _count_severities helper."""

    def test_empty_inputs(self) -> None:
        counts = _count_severities([], [])
        assert counts == {"critical": 0, "warning": 0, "info": 0}

    def test_issues_only(self) -> None:
        issues = [
            CodeIssue(severity="critical"),
            CodeIssue(severity="warning"),
            CodeIssue(severity="info"),
        ]
        counts = _count_severities(issues, [])
        assert counts == {"critical": 1, "warning": 1, "info": 1}

    def test_patterns_only(self) -> None:
        patterns = [
            PatternMatch(pattern="a", severity="critical"),
            PatternMatch(pattern="b", severity="warning"),
        ]
        counts = _count_severities([], patterns)
        assert counts == {"critical": 1, "warning": 1, "info": 0}

    def test_mixed_issues_and_patterns(self) -> None:
        issues = [CodeIssue(severity="critical"), CodeIssue(severity="critical")]
        patterns = [PatternMatch(pattern="x", severity="warning")]
        counts = _count_severities(issues, patterns)
        assert counts == {"critical": 2, "warning": 1, "info": 0}


class TestCalculateScore:
    """Tests for _calculate_score helper."""

    def test_no_issues(self) -> None:
        assert _calculate_score({"critical": 0, "warning": 0, "info": 0}) == 100

    def test_critical_deduction(self) -> None:
        score = _calculate_score({"critical": 1, "warning": 0, "info": 0})
        assert score == 85  # 100 - 15

    def test_warning_deduction(self) -> None:
        score = _calculate_score({"critical": 0, "warning": 2, "info": 0})
        assert score == 90  # 100 - 2*5

    def test_info_deduction(self) -> None:
        score = _calculate_score({"critical": 0, "warning": 0, "info": 5})
        assert score == 95  # 100 - 5*1

    def test_score_floor_at_zero(self) -> None:
        score = _calculate_score({"critical": 10, "warning": 10, "info": 10})
        assert score == 0

    def test_score_capped_at_100(self) -> None:
        score = _calculate_score({"critical": 0, "warning": 0, "info": -5})
        assert score == 100

    def test_mixed_deductions(self) -> None:
        score = _calculate_score({"critical": 1, "warning": 1, "info": 5})
        assert score == 75  # 100 - 15 - 5 - 5


class TestGenerateSuggestions:
    """Tests for _generate_suggestions helper."""

    def test_no_issues_returns_default(self) -> None:
        suggestions = _generate_suggestions([], [])
        assert suggestions == ["Code looks good! No major improvements needed."]

    def test_security_issue_triggers_suggestion(self) -> None:
        issues = [CodeIssue(severity="critical", category="security")]
        suggestions = _generate_suggestions(issues, [])
        assert any("Security" in s for s in suggestions)

    def test_hardcoded_secret_pattern(self) -> None:
        patterns = [PatternMatch(pattern="hardcoded_secret", severity="critical")]
        suggestions = _generate_suggestions([], patterns)
        assert any("secrets" in s.lower() for s in suggestions)

    def test_empty_catch_pattern(self) -> None:
        patterns = [PatternMatch(pattern="empty_catch")]
        suggestions = _generate_suggestions([], patterns)
        assert any("error handling" in s.lower() for s in suggestions)

    def test_deep_nesting_pattern(self) -> None:
        patterns = [PatternMatch(pattern="deep_nesting")]
        suggestions = _generate_suggestions([], patterns)
        assert any("complexity" in s.lower() or "helper" in s.lower() for s in suggestions)

    def test_magic_number_pattern(self) -> None:
        patterns = [PatternMatch(pattern="magic_number")]
        suggestions = _generate_suggestions([], patterns)
        assert any("magic number" in s.lower() for s in suggestions)


# ---------------------------------------------------------------------------
# generate_review -- main function
# ---------------------------------------------------------------------------


class TestGenerateReview:
    """Tests for generate_review tool."""

    def test_empty_analysis(self) -> None:
        analysis = CodeAnalysis(file_path="empty.py", language="python")
        report = generate_review(analysis)
        assert isinstance(report, ReviewReport)
        assert report.overall_score == 100
        assert report.findings == []
        assert report.severity_counts == {"critical": 0, "warning": 0, "info": 0}

    def test_with_issues_only(self) -> None:
        issues = [
            CodeIssue(severity="critical", category="security", rule_id="PY005"),
            CodeIssue(severity="warning", category="bug", rule_id="PY001"),
        ]
        analysis = CodeAnalysis(
            file_path="test.py",
            language="python",
            issues=issues,
            metrics=CodeMetrics(lines_of_code=10, total_lines=15),
        )
        report = generate_review(analysis)
        assert len(report.findings) == 2
        assert report.severity_counts["critical"] == 1
        assert report.severity_counts["warning"] == 1
        assert report.overall_score == 80  # 100 - 15 - 5

    def test_with_issues_and_patterns(self) -> None:
        issues = [CodeIssue(severity="info", category="style")]
        analysis = CodeAnalysis(file_path="app.py", language="python", issues=issues)
        patterns = [
            PatternMatch(pattern="sql_injection", severity="critical"),
            PatternMatch(pattern="deep_nesting", severity="warning"),
        ]
        report = generate_review(analysis, patterns)
        assert len(report.findings) == 3  # 1 issue + 2 patterns
        assert report.severity_counts["critical"] == 1
        assert report.severity_counts["warning"] == 1
        assert report.severity_counts["info"] == 1

    def test_summary_contains_file_info(self) -> None:
        analysis = CodeAnalysis(
            file_path="main.py",
            language="python",
            metrics=CodeMetrics(lines_of_code=100, total_lines=150, function_count=5),
        )
        report = generate_review(analysis)
        assert "main.py" in report.summary
        assert "python" in report.summary
        assert "100" in report.summary
        assert "5" in report.summary

    def test_summary_shows_no_issues(self) -> None:
        analysis = CodeAnalysis(file_path="clean.py", language="python")
        report = generate_review(analysis)
        assert "No issues" in report.summary

    def test_summary_shows_issue_count(self) -> None:
        issues = [CodeIssue(severity="info"), CodeIssue(severity="warning")]
        analysis = CodeAnalysis(file_path="messy.py", language="python", issues=issues)
        report = generate_review(analysis)
        assert "2 issue" in report.summary

    def test_suggestions_generated(self) -> None:
        issues = [CodeIssue(severity="critical", category="security")]
        analysis = CodeAnalysis(file_path="vuln.py", language="python", issues=issues)
        report = generate_review(analysis)
        assert len(report.suggestions) >= 1

    def test_patterns_default_to_empty(self) -> None:
        analysis = CodeAnalysis(file_path="test.py", language="python")
        report = generate_review(analysis, patterns=None)
        assert report.findings == []
