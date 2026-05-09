"""Test suite assembly tool — build complete test suites.

Takes generated test cases and assembles them into a complete test suite
with proper imports, fixtures, and framework-specific organization.
"""

from __future__ import annotations

from agent_test_suite_generator.models import TestCase, TestSuite

SUPPORTED_FRAMEWORKS = {"pytest", "unittest"}


def _collect_imports(cases: list[TestCase], framework: str) -> list[str]:
    """Collect required import statements."""
    imports: list[str] = []

    if framework == "pytest":
        imports.append("import pytest")
    elif framework == "unittest":
        imports.append("import unittest")

    # Check if any test uses specific patterns
    all_tags = set()
    for case in cases:
        all_tags.update(case.tags)

    if "edge_case" in all_tags:
        pass  # No special imports needed for edge cases

    imports.append("from unittest.mock import Mock, patch")

    return imports


def _generate_fixtures(cases: list[TestCase]) -> dict[str, str]:
    """Generate fixture definitions based on test cases."""
    fixtures: dict[str, str] = {}

    # Generate a shared mock fixture if needed
    error_tests = [c for c in cases if "error" in c.tags]
    if error_tests:
        fixtures["mock_dependency"] = "return Mock()"

    # Generate sample data fixture if needed
    edge_tests = [c for c in cases if "edge_case" in c.tags]
    if edge_tests:
        fixtures["sample_data"] = "return {}"

    return fixtures


def build_test_suite(cases: list[TestCase], framework: str = "pytest") -> TestSuite:
    """Assemble test cases into a complete test suite.

    Organizes test cases with proper imports, fixtures, and framework-
    specific configuration.

    Args:
        cases: List of TestCase to assemble.
        framework: Test framework to use ("pytest", "unittest").

    Returns:
        TestSuite ready for code generation.

    Raises:
        ValueError: If framework is not supported.
    """
    framework = framework.lower().strip()
    if framework not in SUPPORTED_FRAMEWORKS:
        raise ValueError(
            f"Unsupported framework: '{framework}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_FRAMEWORKS))}"
        )

    imports = _collect_imports(cases, framework)
    fixtures = _generate_fixtures(cases)

    return TestSuite(
        framework=framework,
        cases=cases,
        imports=imports,
        fixtures=fixtures,
    )
