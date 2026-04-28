"""CodeReviewerAgent -- 代码质量审查专家。

Three-phase pipeline:
  1. analyze()   -- static code analysis with language-specific rules
  2. check()     -- anti-pattern detection across categories
  3. review()    -- compile findings into a structured review report

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_code_reviewer.models import (
    CodeAnalysis,
    PatternMatch,
    ReviewReport,
)
from agent_code_reviewer.tools.analyze_code import analyze_code
from agent_code_reviewer.tools.check_patterns import check_patterns
from agent_code_reviewer.tools.generate_review import generate_review


class CodeReviewerAgent:
    """代码质量审查专家。

    This agent provides a three-phase pipeline for code review:
    Phase 1 (analyze) performs static analysis with language-specific rules.
    Phase 2 (check) detects anti-patterns including security vulnerabilities
    and performance issues. Phase 3 (review) compiles findings into a
    structured report with severity counts and suggestions.

    Usage:
        agent = CodeReviewerAgent()
        analysis = agent.analyze("/path/to/main.py")
        patterns = agent.check(code, "python")
        report = agent.review(analysis, patterns)
        print(report.summary, report.overall_score)
    """

    def analyze(self, file_path: str, language: str = "") -> CodeAnalysis:
        """Phase 1: Perform static code analysis.

        Args:
            file_path: Path to the code file to analyze.
            language: Programming language hint. If empty, auto-detected.

        Returns:
            CodeAnalysis with issues and metrics.
        """
        return analyze_code(file_path, language)

    def check(self, code: str, language: str = "") -> list[PatternMatch]:
        """Phase 2: Detect anti-patterns in code.

        Args:
            code: The source code to scan.
            language: Programming language hint.

        Returns:
            List of PatternMatch objects for each detected pattern.
        """
        return check_patterns(code, language)

    def review(
        self,
        analysis: CodeAnalysis,
        patterns: list[PatternMatch] | None = None,
    ) -> ReviewReport:
        """Phase 3: Compile findings into a structured review report.

        Args:
            analysis: The CodeAnalysis from phase 1.
            patterns: PatternMatch results from phase 2. If None,
                only analysis issues are included.

        Returns:
            ReviewReport with summary, findings, suggestions, and score.
        """
        return generate_review(analysis, patterns)
