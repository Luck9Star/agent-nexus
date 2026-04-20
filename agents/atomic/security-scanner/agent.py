"""Top-level entry point for security-scanner agent."""

from __future__ import annotations


async def run(task: str, _context: dict | None = None) -> str:
    """Execute the security-scanner agent task.

    Args:
        task: Task description (e.g. path to code file to scan).
        _context: Optional context dictionary.

    Returns:
        Task result as string.
    """
    from agent_security_scanner.agent import SecurityScannerAgent

    agent = SecurityScannerAgent()
    result = agent.scan_code(task)
    return result.model_dump_json()
