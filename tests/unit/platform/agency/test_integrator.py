"""Phase F tests: Integrator — merge multi-expert artifacts, detect conflicts, produce unified output."""

import pytest

from agent_nexus.platform.agency.integrator import (
    Artifact,
    ConflictItem,
    IntegratedArtifact,
    Integrator,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def architecture_artifact() -> Artifact:
    return Artifact(
        source_agent="agency.software-architect",
        artifact_type="architecture_plan",
        sections={
            "context": "Integration of agency-agents expert pool",
            "assumptions": ["Agents are persona-only", "Git-based distribution"],
            "proposed_design": "Use generic-expert-agent + profile injection",
            "tradeoffs": ["Flexibility vs complexity"],
            "risks": ["Token cost may increase", "Profile quality varies"],
            "next_steps": ["Implement Phase A", "Run eval"],
        },
    )


@pytest.fixture
def security_artifact() -> Artifact:
    return Artifact(
        source_agent="agency.security-engineer",
        artifact_type="risk_report",
        sections={
            "findings": [
                {"severity": "medium", "description": "Prompt injection in imported MDs"},
                {"severity": "high", "description": "Permission escalation risk"},
            ],
            "severity": "high",
            "affected_components": ["agency-importer", "profile-loader"],
            "mitigation": ["Add content policy checks", "Enforce persona-only mode"],
        },
    )


@pytest.fixture
def security_artifact_conflict() -> Artifact:
    """Conflicts with architecture artifact on risk assessment."""
    return Artifact(
        source_agent="agency.security-engineer",
        artifact_type="risk_report",
        sections={
            "findings": [
                {"severity": "low", "description": "Minor logging concern"},
            ],
            "severity": "low",
            "affected_components": ["runtime"],
            "mitigation": ["Add structured logging"],
        },
    )


# ---------------------------------------------------------------------------
# Merge tests
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestIntegratorMerge:
    """Integrator merges multiple expert artifacts into unified output."""

    def test_merge_two_artifacts(
        self, architecture_artifact: Artifact, security_artifact: Artifact
    ) -> None:
        result = Integrator.merge([architecture_artifact, security_artifact])

        assert isinstance(result, IntegratedArtifact)
        assert result.artifact_type == "integrated_plan"
        assert len(result.source_agents) == 2

    def test_merge_single_artifact(self, architecture_artifact: Artifact) -> None:
        result = Integrator.merge([architecture_artifact])

        assert len(result.source_agents) == 1
        assert "agency.software-architect" in result.source_agents

    def test_merge_preserves_all_section_keys(
        self, architecture_artifact: Artifact, security_artifact: Artifact
    ) -> None:
        result = Integrator.merge([architecture_artifact, security_artifact])

        # All section keys from all artifacts should appear in the merged result
        # (plus the auto-added decision_summary)
        arch_keys = set(architecture_artifact.sections.keys())
        sec_keys = set(security_artifact.sections.keys())
        merged_keys = set(result.merged_sections.keys())
        assert (arch_keys | sec_keys).issubset(merged_keys)

    def test_merge_empty_artifacts_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            Integrator.merge([])

    def test_merge_includes_decision_summary(
        self, architecture_artifact: Artifact, security_artifact: Artifact
    ) -> None:
        result = Integrator.merge([architecture_artifact, security_artifact])

        assert "decision_summary" in result.merged_sections


# ---------------------------------------------------------------------------
# Conflict detection
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestConflictDetection:
    """Integrator detects conflicting viewpoints across artifacts."""

    def test_no_conflicts_single_artifact(self, architecture_artifact: Artifact) -> None:
        result = Integrator.merge([architecture_artifact])
        assert result.conflicts == []

    def test_detects_severity_conflict(self) -> None:
        """Two risk reports with different severity assessments should flag a conflict."""
        artifact_high = Artifact(
            source_agent="agency.security-engineer",
            artifact_type="risk_report",
            sections={
                "findings": [{"severity": "high", "description": "Auth bypass"}],
                "severity": "high",
                "affected_components": ["auth"],
                "mitigation": ["Patch immediately"],
            },
        )
        artifact_low = Artifact(
            source_agent="agency.sre",
            artifact_type="risk_report",
            sections={
                "findings": [{"severity": "low", "description": "Logging noise"}],
                "severity": "low",
                "affected_components": ["logging"],
                "mitigation": ["Add structured logging"],
            },
        )
        result = Integrator.merge([artifact_high, artifact_low])

        # Should detect conflicting severity assessments
        conflict_fields = [c.field for c in result.conflicts]
        assert any("severity" in f.lower() for f in conflict_fields)

    def test_conflict_has_agents_and_description(
        self, architecture_artifact: Artifact, security_artifact: Artifact
    ) -> None:
        result = Integrator.merge([architecture_artifact, security_artifact])

        for conflict in result.conflicts:
            assert isinstance(conflict, ConflictItem)
            assert len(conflict.agents) >= 2
            assert conflict.field
            assert conflict.description


# ---------------------------------------------------------------------------
# Integrated artifact structure
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
class TestIntegratedArtifact:
    """IntegratedArtifact has the correct output structure."""

    def test_has_required_fields(
        self, architecture_artifact: Artifact, security_artifact: Artifact
    ) -> None:
        result = Integrator.merge([architecture_artifact, security_artifact])

        assert result.artifact_type == "integrated_plan"
        assert result.source_agents
        assert result.merged_sections
        assert isinstance(result.conflicts, list)
        assert isinstance(result.risks, list)
        assert isinstance(result.open_questions, list)

    def test_risks_extracted_from_artifacts(
        self, architecture_artifact: Artifact, security_artifact: Artifact
    ) -> None:
        result = Integrator.merge([architecture_artifact, security_artifact])

        # Risks should be collected from all artifacts
        assert len(result.risks) > 0

    def test_source_agents_list(
        self, architecture_artifact: Artifact, security_artifact: Artifact
    ) -> None:
        result = Integrator.merge([architecture_artifact, security_artifact])

        assert set(result.source_agents) == {
            "agency.software-architect",
            "agency.security-engineer",
        }
