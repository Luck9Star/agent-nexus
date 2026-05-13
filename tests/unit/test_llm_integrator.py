"""Tests for LLMIntegrator — LLM-powered artifact synthesis."""

import json
from unittest.mock import MagicMock

import pytest

from agent_nexus.platform.agency.integrator import Artifact, IntegratedArtifact
from agent_nexus.platform.agency.llm_client import LLMResponse
from agent_nexus.platform.agency.llm_integrator import LLMIntegrator


def _make_artifacts():
    return [
        Artifact(
            source_agent="agency.expert-a",
            artifact_type="report",
            sections={
                "summary": "Security risk: SQL injection",
                "recommendations": ["Use parameterized queries"],
            },
            metadata={"llm": True},
        ),
        Artifact(
            source_agent="agency.expert-b",
            artifact_type="report",
            sections={
                "summary": "Architecture issue: tight coupling",
                "recommendations": ["Introduce interfaces"],
            },
            metadata={"llm": True},
        ),
    ]


def _llm_response(data: dict) -> MagicMock:
    mock = MagicMock()
    mock.call.return_value = LLMResponse(
        text=json.dumps(data),
        model="test-model",
        provider="test",
    )
    return mock


class TestSynthesizeEdgeCases:
    """Edge cases for synthesize()."""

    def test_empty_artifacts_raises_value_error(self):
        integrator = LLMIntegrator(client=None)
        with pytest.raises(ValueError, match="at least one artifact"):
            integrator.synthesize([], task="anything")

    def test_llm_exception_falls_back_to_rules(self):
        mock_client = MagicMock()
        mock_client.call.side_effect = RuntimeError("LLM service unavailable")

        integrator = LLMIntegrator(client=mock_client)
        result = integrator.synthesize(_make_artifacts(), task="review")

        assert isinstance(result, IntegratedArtifact)
        assert len(result.source_agents) == 2
        assert "summary" in result.merged_sections

    def test_single_artifact_passes_through_sections(self):
        art = Artifact(
            source_agent="agency.expert-a",
            artifact_type="report",
            sections={"finding": "X is broken"},
            metadata={},
        )
        integrator = LLMIntegrator(client=None)
        result = integrator.synthesize([art], task="check")

        assert result.source_agents == ["agency.expert-a"]
        assert result.merged_sections == {"finding": "X is broken"}


class TestParseSynthesis:
    """Tests for _parse_synthesis edge cases via synthesize()."""

    def test_unparseable_json_uses_raw_text(self):
        """When LLM returns non-JSON, raw text goes into 'synthesis' key."""
        mock_client = MagicMock()
        mock_client.call.return_value = LLMResponse(
            text="This is not JSON at all, just plain text.",
            model="test",
            provider="test",
        )

        integrator = LLMIntegrator(client=mock_client)
        result = integrator.synthesize(_make_artifacts(), task="review")

        assert isinstance(result, IntegratedArtifact)
        assert "synthesis" in result.merged_sections
        assert "plain text" in str(result.merged_sections["synthesis"])

    def test_conflict_parsing(self):
        """Conflicts from LLM response are converted to ConflictItem objects."""
        mock_client = _llm_response(
            {
                "summary": "Combined analysis",
                "recommendations": ["Fix all issues"],
                "conflicts": [
                    {
                        "field": "recommendations",
                        "description": "Expert A says fix, Expert B says ignore",
                    },
                    {"field": "severity", "description": "Disagreement on severity"},
                ],
                "gaps": ["Missing performance analysis"],
            }
        )

        integrator = LLMIntegrator(client=mock_client)
        result = integrator.synthesize(_make_artifacts(), task="review")

        assert len(result.conflicts) == 2
        assert result.conflicts[0].field == "recommendations"
        assert result.conflicts[1].field == "severity"

    def test_risks_and_gaps_parsed(self):
        mock_client = _llm_response(
            {
                "summary": "Analysis",
                "recommendations": [],
                "conflicts": [],
                "risks": ["SQL injection risk", "XSS vulnerability"],
                "gaps": ["No performance data", "Missing threat model"],
            }
        )

        integrator = LLMIntegrator(client=mock_client)
        result = integrator.synthesize(_make_artifacts(), task="review")

        assert len(result.risks) == 2
        assert "SQL injection risk" in result.risks
        assert len(result.open_questions) == 2
        assert "No performance data" in result.open_questions

    def test_expert_sections_preserved_as_subkeys(self):
        """Original expert sections preserved as {prefix}.{key} sub-keys."""
        mock_client = _llm_response(
            {
                "summary": "Combined",
                "recommendations": ["Fix things"],
                "conflicts": [],
            }
        )

        integrator = LLMIntegrator(client=mock_client)
        result = integrator.synthesize(_make_artifacts(), task="review")

        assert "expert-a.summary" in result.merged_sections
        assert "expert-b.summary" in result.merged_sections
        assert result.merged_sections["decision_summary"] == "LLM-synthesized 2 expert outputs"

    def test_missing_summary_and_recommendations_keys(self):
        """LLM response with no summary/recommendations still works."""
        mock_client = _llm_response({"conflicts": [], "gaps": []})

        integrator = LLMIntegrator(client=mock_client)
        result = integrator.synthesize(_make_artifacts(), task="review")

        assert isinstance(result, IntegratedArtifact)
        assert "summary" not in result.merged_sections
        assert "recommendations" not in result.merged_sections

    def test_conflict_with_missing_field_defaults_to_unknown(self):
        mock_client = _llm_response(
            {
                "summary": "ok",
                "recommendations": [],
                "conflicts": [{"description": "some conflict"}],
            }
        )

        integrator = LLMIntegrator(client=mock_client)
        result = integrator.synthesize(_make_artifacts(), task="review")

        assert result.conflicts[0].field == "unknown"
