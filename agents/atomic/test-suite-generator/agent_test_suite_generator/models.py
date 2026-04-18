"""Data models for test-suite-generator Agent.

Pydantic v2 frozen models for code analysis, test case generation,
and test suite assembly.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TestUnit(BaseModel):  # noqa: N801 — domain name, not a pytest class
    """A testable unit identified in source code."""
    __test__ = False  # prevent pytest collection

    model_config = ConfigDict(frozen=True)

    name: str
    type: str = "function"
    inputs: list[str] = Field(default_factory=list)
    expected: str = ""
    edge_cases: list[str] = Field(default_factory=list)


class TestAnalysis(BaseModel):  # noqa: N801
    """Result of analyzing source code for testable units."""
    __test__ = False  # prevent pytest collection

    model_config = ConfigDict(frozen=True)

    units: list[TestUnit] = Field(default_factory=list)
    framework: str = "pytest"
    coverage_targets: dict[str, float] = Field(default_factory=dict)


class TestCase(BaseModel):  # noqa: N801
    """A single test case."""
    __test__ = False  # prevent pytest collection

    model_config = ConfigDict(frozen=True)

    name: str
    setup: str = ""
    actions: list[str] = Field(default_factory=list)
    assertions: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class TestSuite(BaseModel):  # noqa: N801
    """Assembled test suite ready for code generation."""
    __test__ = False  # prevent pytest collection

    model_config = ConfigDict(frozen=True)

    framework: str = "pytest"
    cases: list[TestCase] = Field(default_factory=list)
    imports: list[str] = Field(default_factory=list)
    fixtures: dict[str, str] = Field(default_factory=dict)
