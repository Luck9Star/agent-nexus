"""Agency Pipeline x API mode — full LLM-powered orchestration validation."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.capabilities.contracts.agency import AGENCY_PIPELINE

_REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def contract():
    return AGENCY_PIPELINE


@pytest.mark.requires_api
@pytest.mark.capability_release
class TestAgencyAPI:
    """Agency Pipeline x API mode — full pipeline with real LLM."""

    def test_agency_pipeline_with_llm(self, contract):
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
                "--use-llm",
                "--timeout",
                "180",
            ],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            timeout=300,
        )
        assert result.returncode == 0, f"Pipeline failed: {result.stderr[:500]}"
        output = result.stdout.strip()
        assert len(output) > contract.quality_thresholds.min_output_length, (
            f"Output too short: {len(output)} chars"
        )

    def test_agency_pipeline_qa_score(self, contract):
        result = subprocess.run(
            [
                "uv",
                "run",
                "python",
                "-m",
                "agent_nexus.platform.agency.cli",
                "run-composition",
                "--message",
                "Review code quality of the agency module",
                "--use-llm",
                "--timeout",
                "180",
            ],
            capture_output=True,
            text=True,
            cwd=str(_REPO_ROOT),
            timeout=300,
        )
        assert result.returncode == 0, f"Pipeline failed: {result.stderr[:500]}"
