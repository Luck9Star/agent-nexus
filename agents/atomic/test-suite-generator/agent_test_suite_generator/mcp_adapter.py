"""MCP adapter — expose test-suite-generator as an MCP Server using FastMCP.

Provides three MCP tools:
- analyze_code_for_tests: Identify testable units in source code.
- generate_test_cases: Generate test cases from analysis.
- build_test_suite: Assemble complete test suite.
"""

from __future__ import annotations

from agent_test_suite_generator.models import (
    TestAnalysis,
    TestCase,
)
from agent_test_suite_generator.tools.analyze_code import (
    analyze_code_for_tests as _analyze,
)
from agent_test_suite_generator.tools.build_suite import build_test_suite as _build
from agent_test_suite_generator.tools.generate_cases import (
    generate_test_cases as _generate,
)


def create_mcp_server() -> object:
    """Create and return a FastMCP server for test-suite-generator.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-test-suite-generator[full]

    Returns:
        A FastMCP server instance with analyze_code_for_tests,
        generate_test_cases, and build_test_suite tools.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-test-suite-generator[full]"
        ) from None

    mcp = FastMCP("test-suite-generator")

    @mcp.tool()
    def analyze_code_for_tests(file_path: str, language: str = "python") -> dict:
        """Analyze source code to identify testable units."""
        result = _analyze(file_path, language)
        return result.model_dump()

    @mcp.tool()
    def generate_test_cases(analysis: dict) -> list[dict]:
        """Generate test cases from code analysis."""
        analysis_obj = TestAnalysis.model_validate(analysis)
        cases = _generate(analysis_obj)
        return [c.model_dump() for c in cases]

    @mcp.tool()
    def build_test_suite(cases: list[dict], framework: str = "pytest") -> dict:
        """Assemble test cases into a complete suite."""
        case_objects = [TestCase.model_validate(c) for c in cases]
        result = _build(case_objects, framework)
        return result.model_dump()

    return mcp
