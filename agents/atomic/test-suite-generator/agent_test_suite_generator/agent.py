"""TestSuiteGeneratorAgent — Test suite generation specialist.

Three-phase pipeline:
  1. analyze_code_for_tests() — identify testable units in source code
  2. generate_test_cases()    — generate test cases with edge cases
  3. build_test_suite()       — assemble complete test suite

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_test_suite_generator.models import (
    TestAnalysis,
    TestCase,
    TestSuite,
)
from agent_test_suite_generator.tools.analyze_code import analyze_code_for_tests
from agent_test_suite_generator.tools.build_suite import build_test_suite
from agent_test_suite_generator.tools.generate_cases import generate_test_cases


class TestSuiteGeneratorAgent:
    """Test suite generation specialist.

    This agent provides a three-phase pipeline for test generation:
    Phase 1 (analyze) parses source code to identify testable units.
    Phase 2 (generate) creates test cases with edge cases for each unit.
    Phase 3 (build) assembles everything into a runnable test suite.

    Usage:
        agent = TestSuiteGeneratorAgent()
        analysis = agent.analyze_code_for_tests("source.py", "python")
        cases = agent.generate_test_cases(analysis)
        suite = agent.build_test_suite(cases, "pytest")
    """

    def analyze_code_for_tests(
        self, file_path: str, language: str = "python"
    ) -> TestAnalysis:
        """Phase 1: Analyze source code for testable units.

        Parses the source file and identifies functions, methods, and classes
        that should be tested, including their inputs, expected outputs, and
        edge cases.

        Args:
            file_path: Path to the source code file.
            language: Programming language (currently only "python").

        Returns:
            TestAnalysis with identified testable units and coverage targets.
        """
        return analyze_code_for_tests(file_path, language)

    def generate_test_cases(self, analysis: TestAnalysis) -> list[TestCase]:
        """Phase 2: Generate test cases from analysis.

        Creates test cases for each identified testable unit, including
        normal paths, boundary conditions, and error paths.

        Args:
            analysis: TestAnalysis with identified testable units.

        Returns:
            List of TestCase with setup, actions, assertions, and tags.
        """
        return generate_test_cases(analysis)

    def build_test_suite(
        self, cases: list[TestCase], framework: str = "pytest"
    ) -> TestSuite:
        """Phase 3: Assemble test cases into a complete suite.

        Organizes test cases into a test suite with proper imports,
        fixtures, and framework-specific formatting.

        Args:
            cases: List of TestCase to assemble.
            framework: Test framework ("pytest", "unittest").

        Returns:
            TestSuite ready for code generation.
        """
        return build_test_suite(cases, framework)
