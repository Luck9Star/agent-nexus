"""Tests for TaskComposer with LLM component wiring."""

from unittest.mock import MagicMock

from agent_nexus.platform.agency.integrator import IntegratedArtifact
from agent_nexus.platform.agency.llm_planner import PlannerOutput
from agent_nexus.platform.agency.registry import ExpertRegistry
from agent_nexus.platform.agency.task_composer import TaskComposer, TaskComposerInput


def _registry_with_two_experts():
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


def test_task_composer_with_llm_planner():
    """TaskComposer uses LLMPlanner when provided."""
    registry = _registry_with_two_experts()
    composer = TaskComposer(registry)

    # Mock LLMPlanner
    mock_planner = MagicMock()
    mock_planner.analyze_task.return_value = PlannerOutput(
        capabilities=["code_review", "security_review"],
    )

    input_ = TaskComposerInput(task="review code for security issues", mode="review")
    result = composer.run(input_, llm_planner=mock_planner)

    mock_planner.analyze_task.assert_called_once_with("review code for security issues")
    assert result.selected_agents  # Should select experts


def test_task_composer_with_llm_integrator():
    """TaskComposer uses LLMIntegrator when provided."""
    registry = _registry_with_two_experts()
    composer = TaskComposer(registry)

    mock_integrator = MagicMock()
    mock_integrator.synthesize.return_value = IntegratedArtifact(
        source_agents=["agency.reviewer", "agency.security"],
        merged_sections={"summary": "LLM-synthesized output"},
    )

    input_ = TaskComposerInput(task="review code", mode="review")
    result = composer.run(input_, llm_integrator=mock_integrator)

    assert result.integrated is not None
    assert "summary" in result.integrated.merged_sections
