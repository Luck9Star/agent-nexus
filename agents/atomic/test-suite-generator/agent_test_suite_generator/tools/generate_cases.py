"""Test case generation tool — generate test cases from code analysis.

Creates test cases for each identified testable unit, covering normal
paths, boundary conditions, and error scenarios.
"""

from __future__ import annotations

from agent_test_suite_generator.models import TestAnalysis, TestCase, TestUnit


def _generate_unit_test(unit: TestUnit) -> TestCase:
    """Generate a basic unit test for a testable unit."""
    assertions: list[str] = []
    if unit.expected:
        assertions.append(f"Verify {unit.expected}")
    else:
        assertions.append(f"Verify {unit.name} executes without error")

    return TestCase(
        name=f"test_{unit.name}_basic",
        setup=f"Prepare test instance for {unit.name}",
        actions=[f"Call {unit.name}({', '.join(unit.inputs[:3])})"],
        assertions=assertions,
        tags=["unit"],
    )


def _generate_edge_case_tests(unit: TestUnit) -> list[TestCase]:
    """Generate edge case tests for a testable unit."""
    cases: list[TestCase] = []

    for i, edge_case in enumerate(unit.edge_cases[:4]):
        assertions: list[str] = []
        if "empty" in edge_case.lower():
            assertions.append("Verify graceful handling of empty input")
        elif "none" in edge_case.lower():
            assertions.append("Verify None handling")
        elif "negative" in edge_case.lower():
            assertions.append("Verify negative value handling")
        elif "large" in edge_case.lower() or "max" in edge_case.lower():
            assertions.append("Verify handling of extreme values")
        else:
            assertions.append(f"Verify correct behavior for {edge_case}")

        cases.append(
            TestCase(
                name=f"test_{unit.name}_edge_{i + 1}",
                setup=f"Set up edge case: {edge_case}",
                actions=[f"Call {unit.name} with {edge_case}"],
                assertions=assertions,
                tags=["unit", "edge_case"],
            )
        )

    return cases


def _generate_class_tests(unit: TestUnit) -> list[TestCase]:
    """Generate class-level tests."""
    cases: list[TestCase] = []

    cases.append(
        TestCase(
            name=f"test_{unit.name}_instantiation",
            setup="Prepare constructor arguments",
            actions=[f"Create instance of {unit.name}"],
            assertions=[
                f"Verify {unit.name} instance is created",
                "Verify instance has expected interface",
            ],
            tags=["unit"],
        )
    )

    cases.append(
        TestCase(
            name=f"test_{unit.name}_inheritance",
            setup="Set up class hierarchy test",
            actions=[f"Check {unit.name} inheritance chain"],
            assertions=["Verify correct parent classes"],
            tags=["unit"],
        )
    )

    return cases


def _generate_error_tests(unit: TestUnit) -> list[TestCase]:
    """Generate error path tests."""
    cases: list[TestCase] = []

    if unit.inputs:
        cases.append(
            TestCase(
                name=f"test_{unit.name}_invalid_input",
                setup="Prepare invalid input data",
                actions=[f"Call {unit.name} with invalid input"],
                assertions=[
                    "Verify appropriate exception is raised",
                    "Verify error message is informative",
                ],
                tags=["unit", "error"],
            )
        )

    return cases


def generate_test_cases(analysis: TestAnalysis) -> list[TestCase]:
    """Generate test cases from code analysis.

    For each testable unit, generates a comprehensive set of test cases
    covering normal paths, edge cases, class instantiation, and error paths.

    Args:
        analysis: TestAnalysis with identified testable units.

    Returns:
        List of TestCase with setup, actions, assertions, and tags.
    """
    all_cases: list[TestCase] = []

    for unit in analysis.units:
        # Basic unit test
        all_cases.append(_generate_unit_test(unit))

        # Edge case tests
        all_cases.extend(_generate_edge_case_tests(unit))

        # Class-specific tests
        if unit.type == "class":
            all_cases.extend(_generate_class_tests(unit))

        # Error path tests
        all_cases.extend(_generate_error_tests(unit))

    return all_cases
