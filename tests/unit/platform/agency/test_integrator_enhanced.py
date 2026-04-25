"""Tests for enhanced Integrator — final_recommendation, viewpoint conflicts, open_questions."""

import pytest

from agent_nexus.platform.agency.integrator import (
    Artifact,
    Integrator,
)


class TestFinalRecommendation:
    """Integrator outputs a final_recommendation field."""

    def test_single_artifact_recommendation(self) -> None:
        artifact = Artifact(
            source_agent="agency.software-architect",
            artifact_type="architecture_plan",
            sections={
                "context": "test",
                "recommendation": "Use microservices",
            },
        )
        result = Integrator.merge([artifact])
        assert "final_recommendation" in result.merged_sections

    def test_multiple_artifacts_recommendation_merged(self) -> None:
        a1 = Artifact(
            source_agent="agency.software-architect",
            artifact_type="architecture_plan",
            sections={"recommendation": "Use microservices"},
        )
        a2 = Artifact(
            source_agent="agency.security-engineer",
            artifact_type="risk_report",
            sections={"recommendation": "Add auth middleware"},
        )
        result = Integrator.merge([a1, a2])
        rec = result.merged_sections["final_recommendation"]
        assert isinstance(rec, str)
        assert len(rec) > 0


class TestViewpointConflict:
    """Integrator detects viewpoint conflicts beyond severity."""

    def test_conflicting_severity_viewpoints(self) -> None:
        a1 = Artifact(
            source_agent="agency.security-engineer",
            artifact_type="risk_report",
            sections={
                "findings": [{"severity": "high", "description": "Auth bypass"}],
                "severity": "high",
                "mitigation": ["Patch now"],
            },
        )
        a2 = Artifact(
            source_agent="agency.sre",
            artifact_type="risk_report",
            sections={
                "findings": [{"severity": "low", "description": "Minor issue"}],
                "severity": "low",
                "mitigation": ["Monitor"],
            },
        )
        result = Integrator.merge([a1, a2])
        conflict_fields = [c.field for c in result.conflicts]
        assert any("severity" in f for f in conflict_fields)

    def test_conflicting_recommendations(self) -> None:
        """Directly conflicting recommendations should be detected."""
        a1 = Artifact(
            source_agent="agency.software-architect",
            artifact_type="architecture_plan",
            sections={
                "recommendation": "Use monolith architecture",
                "context": "test",
            },
        )
        a2 = Artifact(
            source_agent="agency.backend-architect",
            artifact_type="architecture_plan",
            sections={
                "recommendation": "Use microservices architecture",
                "context": "test",
            },
        )
        result = Integrator.merge([a1, a2])
        conflict_fields = [c.field for c in result.conflicts]
        assert any("recommendation" in f for f in conflict_fields)


class TestOpenQuestions:
    """Integrator reports missing content in open_questions."""

    def test_missing_contract_sections_reported(self) -> None:
        """If no artifact provides certain expected sections, open_questions flags them."""
        a1 = Artifact(
            source_agent="agency.software-architect",
            artifact_type="architecture_plan",
            sections={"context": "test"},
        )
        expected_sections = ["context", "risks", "next_steps"]
        result = Integrator.merge([a1], expected_sections=expected_sections)
        assert len(result.open_questions) > 0
        missing_names = [q for q in result.open_questions if "risks" in q.lower() or "next_steps" in q.lower()]
        assert len(missing_names) > 0

    def test_no_open_questions_when_complete(self) -> None:
        """When all expected sections are present, no open questions about missing content."""
        a1 = Artifact(
            source_agent="agency.software-architect",
            artifact_type="architecture_plan",
            sections={"context": "test", "risks": ["r1"], "next_steps": ["s1"]},
        )
        result = Integrator.merge([a1], expected_sections=["context", "risks", "next_steps"])
        missing_qs = [q for q in result.open_questions if "missing" in q.lower()]
        assert len(missing_qs) == 0
