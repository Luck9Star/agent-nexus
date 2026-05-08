"""Integration test: LLM pipeline end-to-end through TaskComposer."""

from unittest.mock import MagicMock, patch

from agent_nexus.platform.agency.llm_client import LLMResponse
from agent_nexus.platform.agency.registry import ExpertRegistry
from agent_nexus.platform.agency.task_composer import TaskComposer, TaskComposerInput


def _registry_with_experts():
    registry = ExpertRegistry()
    registry.add(
        "agency.reviewer",
        {
            "id": "agency.reviewer",
            "name": "Code Reviewer",
            "capabilities": ["code_review"],
            "permissions": {"mode": "plan"},
            "output_contract": {"artifact_type": "report", "required_sections": ["summary"]},
            "profile": {"body": "You review code."},
        },
        ["code_review"],
    )
    registry.add(
        "agency.security",
        {
            "id": "agency.security",
            "name": "Security Expert",
            "capabilities": ["security_review"],
            "permissions": {"mode": "plan"},
            "output_contract": {"artifact_type": "report", "required_sections": ["summary"]},
            "profile": {"body": "You review security."},
        },
        ["security_review"],
    )
    return registry


@patch("agent_nexus.platform.agency.executor.LLMClient")
def test_llm_pipeline_fallback_without_llm(mock_llm_client):
    """Without LLM components, pipeline uses rule-based fallback."""
    mock_client = MagicMock()
    mock_client.call.return_value = LLMResponse(
        text="## summary\nTest output",
        model="test",
        provider="test",
    )
    mock_client.model_name = "test"
    mock_llm_client.return_value = mock_client

    registry = _registry_with_experts()
    composer = TaskComposer(registry)
    result = composer.run(
        TaskComposerInput(task="review code", mode="review"),
    )

    # Pipeline should complete with rule-based components
    assert result.integrated is not None
    assert isinstance(result.integrated.merged_sections, dict)
    assert len(result.integrated.merged_sections) > 0
    assert isinstance(result.qa_passed, bool)
