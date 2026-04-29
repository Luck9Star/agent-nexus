"""Agency Pipeline x CLI mode — expert orchestration validation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.capabilities.contracts.agency import AGENCY_PIPELINE

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def contract():
    return AGENCY_PIPELINE


@pytest.fixture
def struct_validator():
    from tests.capabilities.validators.structure import StructureValidator

    return StructureValidator()


@pytest.fixture
def orch_validator():
    from tests.capabilities.validators.orchestration import OrchestrationValidator

    return OrchestrationValidator()


class TestAgencyCLI:
    """Agency Pipeline x CLI mode — CI layer structure validation."""

    def test_agency_cli_help(self):
        result = subprocess.run(
            [
                "uv",
                "run",
                "python",
                "-m",
                "agent_nexus.platform.agency.cli",
                "run-composition",
                "--help",
            ],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            timeout=30,
        )
        assert result.returncode == 0
        assert "message" in result.stdout.lower() or "task" in result.stdout.lower()

    def test_agency_cli_no_llm_runs(self, contract):
        result = subprocess.run(
            [
                "uv",
                "run",
                "python",
                "-m",
                "agent_nexus.platform.agency.cli",
                "run-composition",
                "--message",
                contract.required_inputs["task"].examples[0],
                "--timeout",
                "60",
            ],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            timeout=90,
        )
        assert result.returncode == 0, f"Agency CLI failed: {result.stderr[:500]}"

        output = result.stdout.strip()
        assert len(output) > contract.quality_thresholds.min_output_length, (
            f"Output too short: {len(output)} chars"
        )

    def test_agency_composer_produces_plan(self):
        from agent_nexus.platform.agency.registry import ExpertRegistry
        from agent_nexus.platform.agency.task_composer import (
            TaskComposer,
            TaskComposerInput,
        )

        registry = ExpertRegistry()
        registry.add(
            "security-expert",
            {"name": "Security Expert", "capabilities": ["security_analysis"]},
            ["security_analysis"],
        )

        composer = TaskComposer(registry=registry)
        composer_input = TaskComposerInput(
            task="Analyze code security",
            max_parallel=3,
        )
        result = composer.run(composer_input)
        assert result is not None
        assert len(result.selected_agents) > 0
