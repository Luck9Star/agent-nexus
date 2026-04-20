"""Top-level entry point for doc-filler agent."""

from __future__ import annotations


async def run(task: str, _context: dict | None = None) -> str:
    """Execute the doc-filler agent task.

    Args:
        task: Task description (e.g. path to .docx template to analyze).
        _context: Optional context dictionary.

    Returns:
        Task result as string.
    """
    from agent_doc_filler.agent import DocFillerAgent

    agent = DocFillerAgent()
    result = agent.analyze(task)
    return result.model_dump_json()
