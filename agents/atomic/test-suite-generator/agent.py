"""Top-level entry point for test-suite-generator agent."""

from __future__ import annotations


async def run(task: str, _context: dict | None = None) -> str:
    """Execute the test-suite-generator agent task.

    Args:
        task: Task description (e.g. path to source file to generate tests for).
        _context: Optional context dictionary.

    Returns:
        Task result as string.
    """
    from agent_test_suite_generator.agent import TestSuiteGeneratorAgent

    agent = TestSuiteGeneratorAgent()
    result = agent.analyze_code_for_tests(task)
    return result.model_dump_json()
