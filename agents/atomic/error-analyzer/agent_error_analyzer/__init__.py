"""agent-error-analyzer — Error pattern analyzer and fix suggestion engine.

Parses error messages and stack traces, categorizes error types,
extracts context, and suggests remediation based on known patterns.
"""

from agent_error_analyzer.agent import ErrorAnalyzerAgent
from agent_error_analyzer.models import AnalysisReport, FixSuggestion

__all__ = [
    "ErrorAnalyzerAgent",
    "FixSuggestion",
    "AnalysisReport",
]
