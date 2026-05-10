"""PerformanceProfilerAgent — Performance bottleneck analysis specialist.

Single-phase pipeline:
  1. analyze_performance() — detect performance anti-patterns in source code

Can be used directly or via adapters (MCP, local, CLI).
"""

from __future__ import annotations

from agent_performance_profiler.models import PerformanceReport
from agent_performance_profiler.tools.analyze_performance import (
    analyze_performance as _analyze,
)


class PerformanceProfilerAgent:
    """Performance bottleneck analysis specialist.

    This agent analyzes source code for common performance anti-patterns
    including N+1 queries, inefficient loops, and memory-inefficient operations.

    Usage:
        agent = PerformanceProfilerAgent()
        report = agent.analyze_performance(source_code)
        print(report.critical_count, report.high_count)
    """

    def analyze_performance(self, source_code: str) -> PerformanceReport:
        """Analyze source code for performance anti-patterns.

        Scans for N+1 queries, string concatenation in loops, list
        concatenation in loops, and other common performance issues.

        Args:
            source_code: Python source code to analyze.

        Returns:
            PerformanceReport with all findings, severity counts, and recommendations.
        """
        return _analyze(source_code)
