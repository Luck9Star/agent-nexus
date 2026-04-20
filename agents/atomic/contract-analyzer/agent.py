"""Top-level entry point for contract-analyzer agent."""

from __future__ import annotations


async def run(task: str, _context: dict | None = None) -> str:
    """Execute the contract-analyzer agent task.

    Args:
        task: Task description (e.g. contract text to analyze).
        _context: Optional context dictionary.

    Returns:
        Task result as string.
    """
    from agent_contract_analyzer.agent import ContractAnalyzerAgent

    agent = ContractAnalyzerAgent()
    result = agent.extract_clauses(task)
    return str([r.model_dump() for r in result])
