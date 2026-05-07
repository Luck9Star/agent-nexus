"""Code analysis tool — identify testable units in source code.

Parses Python source files using the ast module to identify functions,
methods, classes, and their interfaces for test generation.
"""

from __future__ import annotations

import ast
from pathlib import Path

from agent_test_suite_generator.models import TestAnalysis, TestUnit

# Supported languages
SUPPORTED_LANGUAGES = {"python"}

# Edge case templates by type
TYPE_EDGE_CASES: dict[str, list[str]] = {
    "int": ["zero value", "negative value", "very large value", "boundary value (MAX)"],
    "float": ["zero value", "negative value", "NaN", "infinity", "very small value"],
    "str": ["empty string", "very long string", "unicode characters", "special characters"],
    "list": ["empty list", "single element", "very large list", "nested lists"],
    "dict": ["empty dict", "nested dict", "missing keys", "extra keys"],
    "bool": ["True", "False"],
    "None": ["None value"],
    "default": ["None input", "empty input", "unexpected type"],
}


def _infer_type_from_annotation(annotation: ast.expr | None) -> str:
    """Infer parameter type from AST annotation."""
    if annotation is None:
        return "default"

    if isinstance(annotation, ast.Constant):
        return "default"
    if isinstance(annotation, ast.Name):
        return annotation.id.lower()
    if isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name):
        # e.g. list[str], dict[str, int]
        return annotation.value.id.lower()

    return "default"


def _extract_function_info(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    class_name: str | None = None,
) -> TestUnit:
    """Extract testable unit information from a function/method AST node."""
    prefix = f"{class_name}." if class_name else ""
    unit_type = "method" if class_name else "function"

    inputs: list[str] = []
    for arg in node.args.args:
        if arg.arg == "self" or arg.arg == "cls":
            continue
        type_str = _infer_type_from_annotation(arg.annotation)
        default_str = f" (type: {type_str})" if type_str != "default" else ""
        inputs.append(f"{arg.arg}{default_str}")

    # Infer expected from return annotation
    expected = ""
    if node.returns:
        if isinstance(node.returns, ast.Name):
            expected = f"Returns {node.returns.id}"
        elif isinstance(node.returns, ast.Constant) and node.returns.value is None:
            expected = "Returns None"
        else:
            expected = "Has return value"

    # Generate edge cases based on input types
    edge_cases: list[str] = []
    for arg in node.args.args:
        if arg.arg in ("self", "cls"):
            continue
        type_str = _infer_type_from_annotation(arg.annotation)
        cases = TYPE_EDGE_CASES.get(type_str, TYPE_EDGE_CASES["default"])
        edge_cases.extend(f"{arg.arg}: {case}" for case in cases[:3])

    return TestUnit(
        name=f"{prefix}{node.name}",
        type=unit_type,
        inputs=inputs,
        expected=expected,
        edge_cases=edge_cases[:8],
    )


def _analyze_python_file(file_path: str) -> list[TestUnit]:
    """Analyze a Python source file for testable units."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Source file not found: {file_path}")

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=file_path)

    units: list[TestUnit] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            units.append(_extract_function_info(node))

        elif isinstance(node, ast.ClassDef):
            # Add the class itself as a testable unit
            methods = [
                n for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]

            class_unit = TestUnit(
                name=node.name,
                type="class",
                inputs=[f"methods: {len(methods)}"],
                expected="Instance with expected interface",
                edge_cases=["Subclass behavior", "Abstract methods"],
            )
            units.append(class_unit)

            # Add methods
            for method in methods:
                units.append(_extract_function_info(method, class_name=node.name))

    return units


def _compute_coverage_targets(units: list[TestUnit]) -> dict[str, float]:
    """Compute coverage targets based on identified units."""
    targets: dict[str, float] = {
        "statement": 0.80,
        "branch": 0.70,
    }

    function_count = sum(1 for u in units if u.type == "function")
    method_count = sum(1 for u in units if u.type == "method")
    class_count = sum(1 for u in units if u.type == "class")

    # Adjust targets based on code complexity
    total_units = function_count + method_count + class_count
    if total_units > 20:
        targets["statement"] = 0.70
        targets["branch"] = 0.60
    elif total_units <= 5:
        targets["statement"] = 0.90
        targets["branch"] = 0.80

    return targets


def analyze_code_for_tests(file_path: str, language: str = "python") -> TestAnalysis:
    """Analyze source code file for testable units.

    Parses the source file to identify functions, methods, and classes,
    inferring their interfaces, expected outputs, and edge cases.

    Args:
        file_path: Path to the source code file.
        language: Programming language (currently only "python").

    Returns:
        TestAnalysis with identified testable units and coverage targets.

    Raises:
        FileNotFoundError: If the source file does not exist.
        ValueError: If the language is not supported.
    """
    language = language.lower().strip()
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(
            f"Unsupported language: '{language}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGES))}"
        )

    units = _analyze_python_file(file_path)
    coverage_targets = _compute_coverage_targets(units)

    return TestAnalysis(
        units=units,
        framework="pytest",
        coverage_targets=coverage_targets,
    )
