"""agent-performance-profiler — Performance bottleneck analysis agent.

Detects N+1 queries, inefficient loops, memory-inefficient operations,
and other common performance anti-patterns in Python source code.
"""

from agent_performance_profiler.agent import PerformanceProfilerAgent
from agent_performance_profiler.models import (
    PerformanceFinding,
    PerformanceReport,
)

__all__ = [
    "PerformanceProfilerAgent",
    "PerformanceFinding",
    "PerformanceReport",
]
