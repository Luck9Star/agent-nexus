"""Top-level entry point for requirements-analyzer agent."""

from __future__ import annotations


async def run(task: str, _context: dict | None = None) -> str:
    """Execute the requirements-analyzer agent task.

    Args:
        task: Task description (e.g. requirement text to analyze).
        _context: Optional context dictionary.

    Returns:
        Task result as string.
    """
    from agent_requirements_analyzer.agent import RequirementsAnalyzerAgent

    agent = RequirementsAnalyzerAgent()
    result = agent.analyze(task)
    return result.model_dump_json()
