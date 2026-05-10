"""Top-level entry point for data-pipeline-validator agent."""

from __future__ import annotations


async def run(task: str, _context: dict | None = None) -> str:
    """Execute the data-pipeline-validator agent task.

    Args:
        task: Pipeline configuration as JSON string.
        _context: Optional context dictionary.

    Returns:
        Task result as string.
    """
    from agent_data_pipeline_validator.agent import DataPipelineValidatorAgent

    agent = DataPipelineValidatorAgent()
    result = agent.validate_pipeline(task)
    return result.model_dump_json()
