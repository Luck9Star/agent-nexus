"""Top-level entry point for api-contract-tester agent."""

from __future__ import annotations


async def run(task: str, _context: dict | None = None) -> str:
    """Execute the api-contract-tester agent task.

    Args:
        task: OpenAPI spec content (JSON or YAML string).
        _context: Optional context dictionary.

    Returns:
        Task result as string.
    """
    from agent_api_contract_tester.agent import ApiContractTesterAgent

    agent = ApiContractTesterAgent()
    result = agent.validate_contract(task)
    return result.model_dump_json()
