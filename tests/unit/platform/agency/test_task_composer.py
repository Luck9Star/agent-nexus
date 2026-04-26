"""Tests for TaskComposer — end-to-end DAG orchestration."""

from pathlib import Path

import pytest

from agent_nexus.platform.agency.importer import AgencyImporter
from agent_nexus.platform.agency.registry import ExpertRegistry
from agent_nexus.platform.agency.selector import SelectionRequest, SpecialistSelector
from agent_nexus.platform.agency.integrator import Artifact, Integrator
from agent_nexus.platform.agency.qa_gate import QAGate, QAGateInput
from agent_nexus.platform.agency.task_composer import (
    TaskComposer,
    TaskComposerInput,
    TaskComposerResult,
)

# Paths
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_VENDOR_DIR = _PROJECT_ROOT / "vendor" / "agency-agents"
_ALLOWLIST_PATH = _PROJECT_ROOT / "config" / "agency-agents.allowlist.yaml"


def _build_composer() -> TaskComposer:
    """Create a TaskComposer with real importer-loaded registry."""
    importer = AgencyImporter(
        vendor_path=str(_VENDOR_DIR),
        allowlist_path=str(_ALLOWLIST_PATH),
        output_dir="/tmp/agency-test-composer",
    )
    profiles = importer.dry_run()

    registry = ExpertRegistry()
    for pkg in profiles:
        ep = pkg["expert_profile"]
        registry.add(ep["id"], ep, ep["capabilities"])

    return TaskComposer(registry=registry)


class TestTaskComposerInput:
    """TaskComposerInput dataclass validates fields."""

    def test_input_fields(self) -> None:
        inp = TaskComposerInput(
            task="Design integration architecture",
            mode="plan",
            max_parallel=3,
        )
        assert inp.task == "Design integration architecture"
        assert inp.mode == "plan"
        assert inp.max_parallel == 3


class TestTaskComposerSelect:
    """TaskComposer selects specialists based on task."""

    def test_select_returns_specialists(self) -> None:
        composer = _build_composer()
        inp = TaskComposerInput(
            task="Design a system architecture",
            mode="plan",
        )
        result = composer.run(inp)
        assert isinstance(result, TaskComposerResult)
        assert len(result.selected_agents) > 0


class TestTaskComposerDAG:
    """TaskComposer generates a valid DAG."""

    def test_dag_has_integrate_and_validate(self) -> None:
        composer = _build_composer()
        inp = TaskComposerInput(
            task="Review code quality",
            mode="plan",
        )
        result = composer.run(inp)
        assert result.dag is not None
        task_ids = [t.id for t in result.dag.tasks]
        assert "integrate" in task_ids
        assert "validate" in task_ids


class TestTaskComposerFullRun:
    """TaskComposer runs the full pipeline with mock expert execution."""

    def test_full_run_with_mock_experts(self) -> None:
        composer = _build_composer()
        inp = TaskComposerInput(
            task="Design a system architecture",
            mode="plan",
        )

        # Provide mock expert executor
        def mock_executor(profile_id: str, task: str) -> Artifact:
            return Artifact(
                source_agent=profile_id,
                artifact_type="architecture_plan",
                sections={
                    "context": task,
                    "recommendation": f"Recommendation from {profile_id}",
                    "risks": ["Token cost may increase"],
                },
            )

        result = composer.run(inp, expert_executor=mock_executor)
        assert isinstance(result, TaskComposerResult)
        assert result.integrated is not None
        assert result.qa_passed is not None


class TestTaskComposerNoMatch:
    """TaskComposer handles case where no specialist matches."""

    def test_no_matching_specialist(self) -> None:
        composer = _build_composer()
        inp = TaskComposerInput(
            task="Design a rocket engine",  # no expert has this capability
            mode="plan",
        )
        result = composer.run(inp)
        assert isinstance(result, TaskComposerResult)
        # Should still succeed — may select best available or return empty
        # The key is it doesn't crash
