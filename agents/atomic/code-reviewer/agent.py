"""Top-level entry point for code-reviewer agent."""

from __future__ import annotations


async def run(task: str, _context: dict | None = None) -> str:
    """Execute the code-reviewer agent task.

    Args:
        task: Task description (e.g. path to code file to review).
        _context: Optional context dictionary.

    Returns:
        Task result as string.
    """
    from agent_code_reviewer.agent import CodeReviewerAgent

    agent = CodeReviewerAgent()
    analysis = agent.analyze(task)
    report = agent.review(analysis)
    return report.model_dump_json()
