"""ErrorAnalyzerAgent — Error pattern analyzer and fix suggestion engine.

Single-phase pipeline:
  analyze_error() — parse error text, categorize, suggest fixes

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_error_analyzer.models import AnalysisReport
from agent_error_analyzer.tools.analyze_error import analyze_error as _analyze


class ErrorAnalyzerAgent:
    """Error pattern analyzer and fix suggestion engine.

    This agent parses error messages and stack traces, categorizes errors,
    extracts context, and suggests remediation based on known patterns.

    Usage:
        agent = ErrorAnalyzerAgent()
        report = agent.analyze_error("TypeError: unsupported operand type(s)")
        print(report.category, report.suggestions)
    """

    def analyze_error(self, error_text: str, language: str = "auto") -> AnalysisReport:
        """Analyze an error message or stack trace.

        Args:
            error_text: Error message or full traceback text.
            language: Language hint (currently only "python" / "auto" supported).

        Returns:
            AnalysisReport with error details and fix suggestions.
        """
        return _analyze(error_text, language)
