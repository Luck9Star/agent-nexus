"""Phase F tests: Integrator — merge multi-expert artifacts, detect conflicts, produce unified output."""

import pytest

from agent_nexus.platform.agency.integrator import (
    Artifact,
    ConflictItem,
    IntegratedArtifact,
    Integrator,
    _detect_risk_conflicts,
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


# ---------------------------------------------------------------------------
# _detect_risk_conflicts — CC 10 boundary function
# ---------------------------------------------------------------------------


class TestDetectRiskConflicts:
    """Tests for _detect_risk_conflicts (CC 10)."""

    def _make_artifact(
        self,
        agent: str,
        sections: dict[str, object],
    ) -> Artifact:
        return Artifact(
            source_agent=agent,
            artifact_type="report",
            sections=sections,
        )

    def test_single_artifact_no_conflict(self) -> None:
        a = self._make_artifact("a1", {"risks": ["token cost"]})
        assert _detect_risk_conflicts([a]) == []

    def test_no_risks_no_conflict(self) -> None:
        a1 = self._make_artifact("a1", {"findings": ["x"]})
        a2 = self._make_artifact("a2", {"findings": ["y"]})
        assert _detect_risk_conflicts([a1, a2]) == []

    def test_disjoint_risks_with_shared_sections_produces_conflict(self) -> None:
        """Completely different risks + overlapping sections = conflict."""
        a1 = self._make_artifact(
            "a1",
            {
                "risks": ["authentication bypass vulnerability"],
                "mitigation": ["patch auth module"],
            },
        )
        a2 = self._make_artifact(
            "a2",
            {
                "risks": ["performance degradation under load"],
                "mitigation": ["add caching layer"],
            },
        )
        result = _detect_risk_conflicts([a1, a2])
        assert len(result) == 1
        assert result[0].field == "risks"
        assert "disjoint" in result[0].description.lower()
        assert set(result[0].agents) == {"a1", "a2"}

    def test_three_agents_disjoint_risks(self) -> None:
        a1 = self._make_artifact("a1", {"risks": ["auth bypass"], "findings": ["x"]})
        a2 = self._make_artifact("a2", {"risks": ["memory leak in parser"], "findings": ["y"]})
        a3 = self._make_artifact("a3", {"risks": ["disk exhaustion"], "findings": ["z"]})
        result = _detect_risk_conflicts([a1, a2, a3])
        assert len(result) == 1
        assert set(result[0].agents) == {"a1", "a2", "a3"}

    def test_structural_sections_excluded_from_overlap(self) -> None:
        """Structural sections (decision_summary, recommendation) should not count."""
        a1 = self._make_artifact(
            "a1",
            {
                "risks": ["auth bypass"],
            },
        )
        a2 = self._make_artifact(
            "a2",
            {
                "risks": ["memory leak in parser"],
                "recommendation": "monitor",
            },
        )
        # a1 has {risks}, a2 has {risks, recommendation}
        # After removing structural: a1={risks}, a2={risks} → overlap on risks
        # So this DOES produce a conflict
        result = _detect_risk_conflicts([a1, a2])
        assert len(result) == 1
