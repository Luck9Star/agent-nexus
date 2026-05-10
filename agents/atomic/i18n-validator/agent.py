"""Top-level entry point for i18n-validator agent."""

from __future__ import annotations


async def run(task: str, _context: dict | None = None) -> str:
    """Execute the i18n-validator agent task.

    Args:
        task: JSON string with locales data.
        _context: Optional context dictionary.

    Returns:
        Task result as string.
    """
    import json

    from agent_i18n_validator.agent import I18nValidatorAgent

    agent = I18nValidatorAgent()
    data = json.loads(task)
    locales = data.get("locales", {})
    base_locale = data.get("base_locale", "en")
    result = agent.validate_i18n(locales, base_locale)
    return result.model_dump_json()
