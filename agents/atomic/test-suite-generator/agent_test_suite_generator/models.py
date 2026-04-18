"""Data models for test-suite-generator Agent.

Pydantic v2 frozen models for code analysis, test case generation,
and test suite assembly.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TestUnit(BaseModel):  # noqa: N801 — domain name, not a pytest class
    """A testable unit identified in source code.

    Attributes:
        name: Name of the function/method/class.
        type: Type of the unit (function/method/class).
        inputs: List of input parameter descriptions.
        expected: Description of expected output.
        edge_cases: List of edge case descriptions.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    type: str = "function"
    inputs: list[str] = Field(default_factory=list)
    expected: str = ""
    edge_cases: list[str] = Field(default_factory=list)


class TestAnalysis(BaseModel):  # noqa: N801
    """Result of analyzing source code for testable units.

    Attributes:
        units: All identified testable units.
        framework: Recommended test framework.
        coverage_targets: Coverage targets by category.
    """

    model_config = ConfigDict(frozen=True)

    units: list[TestUnit] = Field(default_factory=list)
    framework: str = "pytest"
    coverage_targets: dict[str, float] = Field(default_factory=dict)


class TestCase(BaseModel):
    """A single test case.

    Attributes:
        name: Test case name (should start with test_).
        setup: Setup/arrange step description.
        actions: Action/act step descriptions.
        assertions: Assert descriptions.
        tags: Classification tags (unit, integration, e2e, edge_case).
    """

    model_config = ConfigDict(frozen=True)

    name: str
    setup: str = ""
    actions: list[str] = Field(default_factory=list)
    assertions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class TestSuite(BaseModel):  # noqa: N801
    """Assembled test suite ready for code generation.

    Attributes:
        framework: Test framework to use.
        cases: All test cases in the suite.
        imports: Required import statements.
        fixtures: Fixture definitions (name -> setup code).
    """

    model_config = ConfigDict(frozen=True)

    framework: str = "pytest"
    cases: list[TestCase] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    fixtures: dict[str, str] = Field(default_factory=dict)
