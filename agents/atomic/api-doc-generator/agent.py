"""Top-level entry point for api-doc-generator agent."""

from __future__ import annotations


async def run(task: str, _context: dict | None = None) -> str:
    """Execute the api-doc-generator agent task.

    Args:
        task: Task description (e.g. path to source file to extract API docs from).
        _context: Optional context dictionary.

    Returns:
        Task result as string.
    """
    from agent_api_doc_generator.agent import APIDocGeneratorAgent

    agent = APIDocGeneratorAgent()
    endpoints = agent.extract(task)
    result = agent.generate(endpoints)
    return result.model_dump_json()
