"""Top-level entry point for localization-specialist agent."""

from __future__ import annotations


async def run(task: str, _context: dict | None = None) -> str:
    """Execute the localization-specialist agent task.

    Args:
        task: Task description (e.g. text to localize).
        _context: Optional context dictionary.

    Returns:
        Task result as string.
    """
    from agent_localization_specialist.agent import LocalizationSpecialistAgent

    agent = LocalizationSpecialistAgent()
    result = agent.analyze_text(task)
    return result.model_dump_json()
