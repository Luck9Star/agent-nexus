"""Top-level entry point for performance-profiler agent."""

from __future__ import annotations


async def run(task: str, _context: dict | None = None) -> str:
    """Execute the performance-profiler agent task.

    Args:
        task: Source code content to analyze.
        _context: Optional context dictionary.

    Returns:
        Task result as string.
    """
    from agent_performance_profiler.agent import PerformanceProfilerAgent

    agent = PerformanceProfilerAgent()
    result = agent.analyze_performance(task)
    return result.model_dump_json()
