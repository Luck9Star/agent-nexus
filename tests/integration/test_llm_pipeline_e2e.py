"""Integration test: LLM pipeline end-to-end through TaskComposer."""

from unittest.mock import MagicMock, patch

from agent_nexus.platform.agency.integrator import IntegratedArtifact
from agent_nexus.platform.agency.llm_client import LLMResponse
from agent_nexus.platform.agency.llm_planner import PlannerOutput
from agent_nexus.platform.agency.qa_gate import QAGateResult
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
def test_llm_pipeline_e2e_with_mocked_llm(mock_llm_client):
    """Full pipeline with mocked LLM at every stage."""
    # Mock LLMClient for executor (2 experts = 2 calls)
    mock_exec_client = MagicMock()
    mock_exec_client.call.side_effect = [
        LLMResponse(text="## summary\nCode looks good", model="test", provider="test"),
        LLMResponse(text="## summary\nNo security issues", model="test", provider="test"),
    ]
    mock_exec_client.model_name = "test-model"
    mock_llm_client.return_value = mock_exec_client

    registry = _registry_with_experts()

    # Mock planner
    mock_planner = MagicMock()
    mock_planner.analyze_task.return_value = PlannerOutput(
        capabilities=["code_review", "security_review"],
    )

    # Mock integrator
    mock_integrator = MagicMock()
    mock_integrator.synthesize.return_value = IntegratedArtifact(
        source_agents=["agency.reviewer", "agency.security"],
        merged_sections={"summary": "Synthesized result"},
    )

    # Mock QA gate
    mock_qa = MagicMock()
    mock_qa.evaluate.return_value = QAGateResult(
        passed=True,
        contract_result=MagicMock(passed=True, missing_sections=[]),
        gitnexus_result=MagicMock(passed=True, skipped=True, failed_checks=[]),
        failures=[],
    )

    composer = TaskComposer(registry)
    result = composer.run(
        TaskComposerInput(task="review code for security", mode="review"),
        llm_planner=mock_planner,
        llm_integrator=mock_integrator,
        llm_qa_gate=mock_qa,
    )

    # Verify pipeline executed all stages
    mock_planner.analyze_task.assert_called_once()
    mock_integrator.synthesize.assert_called_once()
    mock_qa.evaluate.assert_called_once()
    assert result.qa_passed is True
    assert result.integrated is not None
    assert len(result.selected_agents) > 0


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
    assert result.qa_passed is not None
