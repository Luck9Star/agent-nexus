"""Top-level entry point for accessibility-auditor agent."""

from __future__ import annotations


async def run(task: str, _context: dict | None = None) -> str:
    """Execute the accessibility-auditor agent task.

    Args:
        task: Task description (e.g. path to HTML file to audit).
        _context: Optional context dictionary.

    Returns:
        Task result as string.
    """
    from agent_accessibility_auditor.agent import AccessibilityAuditorAgent

    agent = AccessibilityAuditorAgent()
    result = agent.audit_content(task)
    return result.model_dump_json()
