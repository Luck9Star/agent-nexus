"""Comprehensive tests for test-suite-generator agent.

Covers:
- Models: construction, validation, serialization, immutability
- analyze_code_for_tests: file parsing, unit identification, edge cases, coverage targets
- generate_test_cases: unit tests, edge case tests, class tests, error tests
- build_test_suite: import collection, fixture generation, framework validation
- Agent: three-phase pipeline
- MCP adapter: server creation
- Local adapter: message dispatch
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from agent_test_suite_generator.agent import TestSuiteGeneratorAgent
from agent_test_suite_generator.local_adapter import handle_message
from agent_test_suite_generator.models import (
    TestCase,
    TestAnalysis,
    TestSuite,
    TestUnit,
)
from agent_test_suite_generator.tools.analyze_code import (
    SUPPORTED_LANGUAGES,
    _compute_coverage_targets,
    _extract_function_info,
    _infer_type_from_annotation,
    analyze_code_for_tests,
)
from agent_test_suite_generator.tools.build_suite import (
    SUPPORTED_FRAMEWORKS,
    _collect_imports,
    _generate_fixtures,
    build_test_suite,
)
from agent_test_suite_generator.tools.generate_cases import (
    _generate_edge_case_tests,
    _generate_error_tests,
    _generate_unit_test,
    generate_test_cases,
)


# ---------------------------------------------------------------------------
# Sample Python source for testing analysis
# ---------------------------------------------------------------------------

SAMPLE_PYTHON_SOURCE = '''\
"""Sample module for testing."""

from typing import Optional


def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def greet(name: str) -> str:
    """Greet a person."""
    return f"Hello, {name}!"


class Calculator:
    """Simple calculator class."""

    def __init__(self, precision: int = 2) -> None:
        self.precision = precision

    def divide(self, a: float, b: float) -> float:
        """Divide a by b."""
        if b == 0:
            raise ValueError("Division by zero")
        return round(a / b, self.precision)

    def multiply(self, a: float, b: float) -> float:
        """Multiply two numbers."""
        return round(a * b, self.precision)
'''

EMPTY_PYTHON_SOURCE = '"""Empty module."""\n'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_file() -> str:
    """Create a temporary Python source file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(SAMPLE_PYTHON_SOURCE)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def empty_file() -> str:
    """Create an empty Python source file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(EMPTY_PYTHON_SOURCE)
        path = f.name
    yield path
    os.unlink(path)


@pytest.fixture
def agent() -> TestSuiteGeneratorAgent:
    """Provide a TestSuiteGeneratorAgent instance."""
    return TestSuiteGeneratorAgent()


# ---------------------------------------------------------------------------
# Models — construction, validation, serialization
# ---------------------------------------------------------------------------


class TestTestUnit:
    """Tests for TestUnit model."""

    def test_basic_construction(self) -> None:
        u = TestUnit(name="add")
        assert u.name == "add"
        assert u.type == "function"
        assert u.inputs == []
        assert u.expected == ""
        assert u.edge_cases == []

    def test_full_construction(self) -> None:
        u = TestUnit(
            name="Calculator.divide",
            type="method",
            inputs=["a: float", "b: float"],
            expected="Returns float",
            edge_cases=["b: zero value", "b: negative value"],
        )
        assert u.type == "method"
        assert len(u.inputs) == 2
        assert len(u.edge_cases) == 2

    def test_frozen(self) -> None:
        u = TestUnit(name="test")
        with pytest.raises(Exception):
            u.name = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        u = TestUnit(name="func", type="function", inputs=["a: int"])
        data = u.model_dump()
        u2 = TestUnit.model_validate(data)
        assert u == u2


class TestTestAnalysis:
    """Tests for TestAnalysis model."""

    def test_empty(self) -> None:
        ta = TestAnalysis()
        assert ta.units == []
        assert ta.framework == "pytest"
        assert ta.coverage_targets == {}

    def test_with_units(self) -> None:
        ta = TestAnalysis(
            units=[TestUnit(name="add")],
            framework="pytest",
            coverage_targets={"statement": 0.8},
        )
        assert len(ta.units) == 1
        assert ta.coverage_targets["statement"] == 0.8

    def test_frozen(self) -> None:
        ta = TestAnalysis()
        with pytest.raises(Exception):
            ta.framework = "changed"  # type: ignore[misc]


class TestTestCase:
    """Tests for TestCase model."""

    def test_basic(self) -> None:
        tc = TestCase(name="test_add_basic")
        assert tc.name == "test_add_basic"
        assert tc.setup == ""
        assert tc.actions == []
        assert tc.assertions == []
        assert tc.tags == []

    def test_full(self) -> None:
        tc = TestCase(
            name="test_add_basic",
            setup="Create calculator",
            actions=["Call add(1, 2)"],
            assertions=["Result is 3"],
            tags=["unit"],
        )
        assert len(tc.actions) == 1
        assert "unit" in tc.tags

    def test_frozen(self) -> None:
        tc = TestCase(name="test")
        with pytest.raises(Exception):
            tc.name = "changed"  # type: ignore[misc]

    def test_serialization_roundtrip(self) -> None:
        tc = TestCase(name="test_x", actions=["do thing"], tags=["edge_case"])
        data = tc.model_dump()
        tc2 = TestCase.model_validate(data)
        assert tc == tc2


class TestTestSuite:
    """Tests for TestSuite model."""

    def test_empty(self) -> None:
        ts = TestSuite()
        assert ts.framework == "pytest"
        assert ts.cases == []
        assert ts.imports == []
        assert ts.fixtures == {}

    def test_full(self) -> None:
        ts = TestSuite(
            framework="pytest",
            cases=[TestCase(name="test_1")],
            imports=["import pytest"],
            fixtures={"sample": "return {}"},
        )
        assert len(ts.cases) == 1
        assert "import pytest" in ts.imports

    def test_frozen(self) -> None:
        ts = TestSuite()
        with pytest.raises(Exception):
            ts.framework = "changed"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# analyze_code_for_tests — code analysis
# ---------------------------------------------------------------------------


class TestAnalyzeCode:
    """Tests for analyze_code_for_tests tool."""

    def test_file_not_found(self) -> None:
        with pytest.raises(FileNotFoundError):
            analyze_code_for_tests("/nonexistent/file.py")

    def test_unsupported_language(self) -> None:
        with pytest.raises(ValueError, match="Unsupported language"):
            analyze_code_for_tests("file.py", "rust")

    def test_detects_functions(self, sample_file: str) -> None:
        result = analyze_code_for_tests(sample_file)
        func_names = [u.name for u in result.units if u.type == "function"]
        assert "add" in func_names
        assert "greet" in func_names

    def test_detects_class(self, sample_file: str) -> None:
        result = analyze_code_for_tests(sample_file)
        class_names = [u.name for u in result.units if u.type == "class"]
        assert "Calculator" in class_names

    def test_detects_methods(self, sample_file: str) -> None:
        result = analyze_code_for_tests(sample_file)
        method_names = [u.name for u in result.units if u.type == "method"]
        assert "Calculator.divide" in method_names
        assert "Calculator.multiply" in method_names

    def test_inputs_extracted(self, sample_file: str) -> None:
        result = analyze_code_for_tests(sample_file)
        add_unit = next(u for u in result.units if u.name == "add")
        assert len(add_unit.inputs) == 2
        assert any("a" in i for i in add_unit.inputs)
        assert any("b" in i for i in add_unit.inputs)

    def test_return_type_extracted(self, sample_file: str) -> None:
        result = analyze_code_for_tests(sample_file)
        add_unit = next(u for u in result.units if u.name == "add")
        assert "int" in add_unit.expected

    def test_edge_cases_generated(self, sample_file: str) -> None:
        result = analyze_code_for_tests(sample_file)
        add_unit = next(u for u in result.units if u.name == "add")
        assert len(add_unit.edge_cases) > 0

    def test_coverage_targets_set(self, sample_file: str) -> None:
        result = analyze_code_for_tests(sample_file)
        assert "statement" in result.coverage_targets
        assert "branch" in result.coverage_targets
        assert 0.0 < result.coverage_targets["statement"] <= 1.0

    def test_empty_file(self, empty_file: str) -> None:
        result = analyze_code_for_tests(empty_file)
        assert result.units == []

    def test_framework_default(self, sample_file: str) -> None:
        result = analyze_code_for_tests(sample_file)
        assert result.framework == "pytest"

    def test_language_case_insensitive(self, sample_file: str) -> None:
        result = analyze_code_for_tests(sample_file, "Python")
        assert len(result.units) > 0


class TestInferTypeFromAnnotation:
    """Tests for _infer_type_from_annotation helper."""

    def test_none_annotation(self) -> None:
        import ast

        assert _infer_type_from_annotation(None) == "default"

    def test_name_annotation(self) -> None:
        import ast

        node = ast.Name(id="int")
        assert _infer_type_from_annotation(node) == "int"

    def test_subscript_annotation(self) -> None:
        import ast

        node = ast.Subscript(
            value=ast.Name(id="list"), slice=ast.Name(id="str")
        )
        assert _infer_type_from_annotation(node) == "list"


class TestComputeCoverageTargets:
    """Tests for _compute_coverage_targets helper."""

    def test_few_units(self) -> None:
        units = [TestUnit(name=f"u{i}") for i in range(3)]
        targets = _compute_coverage_targets(units)
        assert targets["statement"] == 0.90

    def test_many_units(self) -> None:
        units = [TestUnit(name=f"u{i}") for i in range(25)]
        targets = _compute_coverage_targets(units)
        assert targets["statement"] == 0.70

    def test_normal_units(self) -> None:
        units = [TestUnit(name=f"u{i}") for i in range(10)]
        targets = _compute_coverage_targets(units)
        assert targets["statement"] == 0.80


# ---------------------------------------------------------------------------
# generate_test_cases — test case generation
# ---------------------------------------------------------------------------


class TestGenerateTestCases:
    """Tests for generate_test_cases tool."""

    def test_generates_cases(self) -> None:
        analysis = TestAnalysis(
            units=[TestUnit(name="add", inputs=["a", "b"], expected="Returns int")],
        )
        cases = generate_test_cases(analysis)
        assert len(cases) > 0

    def test_unit_test_generated(self) -> None:
        analysis = TestAnalysis(
            units=[TestUnit(name="add", inputs=["a", "b"])],
        )
        cases = generate_test_cases(analysis)
        unit_tests = [c for c in cases if "basic" in c.name]
        assert len(unit_tests) >= 1

    def test_edge_cases_generated(self) -> None:
        analysis = TestAnalysis(
            units=[TestUnit(name="add", inputs=["a"], edge_cases=["a: zero value"])],
        )
        cases = generate_test_cases(analysis)
        edge_tests = [c for c in cases if "edge_case" in c.tags]
        assert len(edge_tests) >= 1

    def test_class_tests_generated(self) -> None:
        analysis = TestAnalysis(
            units=[TestUnit(name="Calculator", type="class")],
        )
        cases = generate_test_cases(analysis)
        class_tests = [c for c in cases if "instantiation" in c.name or "inheritance" in c.name]
        assert len(class_tests) >= 1

    def test_error_tests_generated(self) -> None:
        analysis = TestAnalysis(
            units=[TestUnit(name="divide", inputs=["a", "b"])],
        )
        cases = generate_test_cases(analysis)
        error_tests = [c for c in cases if "error" in c.tags]
        assert len(error_tests) >= 1

    def test_empty_analysis(self) -> None:
        cases = generate_test_cases(TestAnalysis())
        assert cases == []

    def test_test_names_start_with_test(self) -> None:
        analysis = TestAnalysis(
            units=[TestUnit(name="func", inputs=["x"])],
        )
        cases = generate_test_cases(analysis)
        for case in cases:
            assert case.name.startswith("test_")

    def test_full_pipeline_cases(self, sample_file: str) -> None:
        analysis = analyze_code_for_tests(sample_file)
        cases = generate_test_cases(analysis)
        assert len(cases) >= len(analysis.units)  # At least one test per unit


class TestGenerateUnitTest:
    """Tests for _generate_unit_test helper."""

    def test_basic(self) -> None:
        unit = TestUnit(name="add", inputs=["a", "b"], expected="Returns int")
        tc = _generate_unit_test(unit)
        assert tc.name == "test_add_basic"
        assert "unit" in tc.tags
        assert len(tc.assertions) > 0

    def test_no_expected(self) -> None:
        unit = TestUnit(name="process", inputs=["data"])
        tc = _generate_unit_test(unit)
        assert "executes" in tc.assertions[0]


class TestGenerateEdgeCaseTests:
    """Tests for _generate_edge_case_tests helper."""

    def test_with_edge_cases(self) -> None:
        unit = TestUnit(
            name="add",
            inputs=["a"],
            edge_cases=["a: zero value", "a: negative value"],
        )
        cases = _generate_edge_case_tests(unit)
        assert len(cases) == 2
        assert all("edge_case" in c.tags for c in cases)

    def test_no_edge_cases(self) -> None:
        unit = TestUnit(name="add", inputs=["a"])
        cases = _generate_edge_case_tests(unit)
        assert cases == []


# ---------------------------------------------------------------------------
# build_test_suite — suite assembly
# ---------------------------------------------------------------------------


class TestBuildTestSuite:
    """Tests for build_test_suite tool."""

    def test_pytest_framework(self) -> None:
        cases = [TestCase(name="test_basic", tags=["unit"])]
        suite = build_test_suite(cases, "pytest")
        assert suite.framework == "pytest"
        assert "import pytest" in suite.imports

    def test_unittest_framework(self) -> None:
        cases = [TestCase(name="test_basic", tags=["unit"])]
        suite = build_test_suite(cases, "unittest")
        assert suite.framework == "unittest"
        assert "import unittest" in suite.imports

    def test_unsupported_framework(self) -> None:
        cases = [TestCase(name="test_basic")]
        with pytest.raises(ValueError, match="Unsupported framework"):
            build_test_suite(cases, "jest")

    def test_empty_cases(self) -> None:
        suite = build_test_suite([], "pytest")
        assert suite.cases == []

    def test_fixtures_generated_for_edge_cases(self) -> None:
        cases = [TestCase(name="test_edge", tags=["edge_case"])]
        suite = build_test_suite(cases, "pytest")
        assert "sample_data" in suite.fixtures

    def test_fixtures_generated_for_errors(self) -> None:
        cases = [TestCase(name="test_err", tags=["error"])]
        suite = build_test_suite(cases, "pytest")
        assert "mock_dependency" in suite.fixtures

    def test_mock_import(self) -> None:
        cases = [TestCase(name="test_basic")]
        suite = build_test_suite(cases, "pytest")
        assert any("mock" in imp.lower() for imp in suite.imports)

    def test_framework_case_insensitive(self) -> None:
        cases = [TestCase(name="test_basic")]
        suite = build_test_suite(cases, "PYTEST")
        assert suite.framework == "pytest"


class TestCollectImports:
    """Tests for _collect_imports helper."""

    def test_pytest(self) -> None:
        imports = _collect_imports([], "pytest")
        assert "import pytest" in imports

    def test_unittest(self) -> None:
        imports = _collect_imports([], "unittest")
        assert "import unittest" in imports


class TestGenerateFixtures:
    """Tests for _generate_fixtures helper."""

    def test_no_special_cases(self) -> None:
        fixtures = _generate_fixtures([TestCase(name="test_basic")])
        # May or may not have fixtures depending on tags
        assert isinstance(fixtures, dict)

    def test_error_case_fixtures(self) -> None:
        cases = [TestCase(name="test_err", tags=["error"])]
        fixtures = _generate_fixtures(cases)
        assert "mock_dependency" in fixtures


# ---------------------------------------------------------------------------
# Agent — three-phase pipeline
# ---------------------------------------------------------------------------


class TestTestSuiteGeneratorAgent:
    """Tests for TestSuiteGeneratorAgent class."""

    def test_analyze_code(
        self, agent: TestSuiteGeneratorAgent, sample_file: str
    ) -> None:
        result = agent.analyze_code_for_tests(sample_file)
        assert isinstance(result, TestAnalysis)
        assert len(result.units) > 0

    def test_generate_test_cases(
        self, agent: TestSuiteGeneratorAgent, sample_file: str
    ) -> None:
        analysis = agent.analyze_code_for_tests(sample_file)
        cases = agent.generate_test_cases(analysis)
        assert len(cases) > 0
        assert all(isinstance(c, TestCase) for c in cases)

    def test_build_test_suite(
        self, agent: TestSuiteGeneratorAgent, sample_file: str
    ) -> None:
        analysis = agent.analyze_code_for_tests(sample_file)
        cases = agent.generate_test_cases(analysis)
        suite = agent.build_test_suite(cases, "pytest")
        assert isinstance(suite, TestSuite)
        assert suite.framework == "pytest"
        assert len(suite.cases) > 0

    def test_full_pipeline(
        self, agent: TestSuiteGeneratorAgent, sample_file: str
    ) -> None:
        # Phase 1: analyze
        analysis = agent.analyze_code_for_tests(sample_file)
        assert len(analysis.units) >= 5  # 2 functions + 1 class + 3 methods

        # Phase 2: generate
        cases = agent.generate_test_cases(analysis)
        assert len(cases) > len(analysis.units)

        # Phase 3: build
        suite = agent.build_test_suite(cases)
        assert suite.framework == "pytest"
        assert len(suite.imports) > 0

    def test_file_not_found(self, agent: TestSuiteGeneratorAgent) -> None:
        with pytest.raises(FileNotFoundError):
            agent.analyze_code_for_tests("/nonexistent.py")

    def test_empty_file(
        self, agent: TestSuiteGeneratorAgent, empty_file: str
    ) -> None:
        analysis = agent.analyze_code_for_tests(empty_file)
        assert analysis.units == []
        cases = agent.generate_test_cases(analysis)
        assert cases == []


# ---------------------------------------------------------------------------
# MCP adapter
# ---------------------------------------------------------------------------


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_create_mcp_server_import_error(self) -> None:
        try:
            from agent_test_suite_generator.mcp_adapter import create_mcp_server

            server = create_mcp_server()
            assert server is not None
        except ImportError:
            with pytest.raises(ImportError, match="FastMCP is required"):
                create_mcp_server()

    def test_mcp_adapter_module_importable(self) -> None:
        import agent_test_suite_generator.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")


# ---------------------------------------------------------------------------
# Local adapter — message dispatch
# ---------------------------------------------------------------------------


class TestLocalAdapter:
    """Tests for local adapter message handling."""

    def test_handle_analyze(
        self, agent: TestSuiteGeneratorAgent, sample_file: str
    ) -> None:
        response = handle_message(
            agent,
            {
                "method": "analyze_code_for_tests",
                "params": {"file_path": sample_file, "language": "python"},
            },
        )
        assert response["status"] == "ok"
        assert len(response["result"]["units"]) > 0

    def test_handle_generate(
        self, agent: TestSuiteGeneratorAgent, sample_file: str
    ) -> None:
        # First analyze
        analyze_resp = handle_message(
            agent,
            {
                "method": "analyze_code_for_tests",
                "params": {"file_path": sample_file},
            },
        )
        analysis_data = analyze_resp["result"]

        response = handle_message(
            agent,
            {"method": "generate_test_cases", "params": {"analysis": analysis_data}},
        )
        assert response["status"] == "ok"
        assert len(response["result"]) > 0

    def test_handle_build(
        self, agent: TestSuiteGeneratorAgent, sample_file: str
    ) -> None:
        analyze_resp = handle_message(
            agent,
            {
                "method": "analyze_code_for_tests",
                "params": {"file_path": sample_file},
            },
        )
        analysis_data = analyze_resp["result"]

        gen_resp = handle_message(
            agent,
            {"method": "generate_test_cases", "params": {"analysis": analysis_data}},
        )
        cases_data = gen_resp["result"]

        response = handle_message(
            agent,
            {
                "method": "build_test_suite",
                "params": {"cases": cases_data, "framework": "pytest"},
            },
        )
        assert response["status"] == "ok"
        assert response["result"]["framework"] == "pytest"

    def test_handle_unknown_method(self, agent: TestSuiteGeneratorAgent) -> None:
        response = handle_message(agent, {"method": "unknown", "params": {}})
        assert response["status"] == "error"
        assert "Unknown method" in response["error"]

    def test_handle_missing_file_path(self, agent: TestSuiteGeneratorAgent) -> None:
        response = handle_message(
            agent, {"method": "analyze_code_for_tests", "params": {}}
        )
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_missing_analysis(self, agent: TestSuiteGeneratorAgent) -> None:
        response = handle_message(
            agent, {"method": "generate_test_cases", "params": {}}
        )
        assert response["status"] == "error"
        assert "Missing" in response["error"]

    def test_handle_missing_cases(self, agent: TestSuiteGeneratorAgent) -> None:
        response = handle_message(
            agent, {"method": "build_test_suite", "params": {}}
        )
        assert response["status"] == "error"
        assert "Missing" in response["error"]
