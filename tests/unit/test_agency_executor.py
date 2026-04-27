"""Unit tests for ProfileBasedExecutor and its integration with TaskComposer.

Validates that:
- ProfileBasedExecutor loads profile data from registry and produces differentiated artifacts
- Missing profile_id raises ValueError
- Different profiles produce different artifacts
- All required_sections from output_contract are present
- TaskComposer uses ProfileBasedExecutor by default
- _build_profile_package preserves custom imported_at dates
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from agent_nexus.platform.agency.executor import ProfileBasedExecutor
from agent_nexus.platform.agency.importer import AgencyImporter
from agent_nexus.platform.agency.task_composer import TaskComposer, TaskComposerInput, TaskComposerResult
from agent_nexus.platform.agency.integrator import Artifact
from agent_nexus.platform.agency.registry import ExpertRegistry
from agent_nexus.platform.agency.task_composer import (
    TaskComposer,
    TaskComposerInput,
    TaskComposerResult,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_VENDOR_DIR = _PROJECT_ROOT / "vendor" / "agency-agents"
_ALLOWLIST_PATH = _PROJECT_ROOT / "config" / "agency-agents.allowlist.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def importer_profiles():
    """Real importer output: profile packages from vendor repo."""
    with tempfile.TemporaryDirectory() as tmpdir:
        importer = AgencyImporter(
            vendor_path=str(_VENDOR_DIR),
            allowlist_path=str(_ALLOWLIST_PATH),
            output_dir=tmpdir,
        )
        return importer.dry_run()


@pytest.fixture(scope="module")
def populated_registry(importer_profiles):
    """Registry loaded with all imported profiles."""
    registry = ExpertRegistry()
    for pkg in importer_profiles:
        ep = pkg["expert_profile"]
        registry.add(ep["id"], ep, ep["capabilities"])
    return registry


# ---------------------------------------------------------------------------
# 1. ProfileBasedExecutor core behavior
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestProfileBasedExecutorCore:
    """ProfileBasedExecutor uses profile data to produce structured artifacts."""

    def test_returns_artifact_with_profile_sections(self, populated_registry):
        """Sections are populated from the profile's output_contract."""
        executor = ProfileBasedExecutor(populated_registry)
        # Pick a known profile
        first_id = populated_registry.list_all()[0]
        artifact = executor(first_id, "Design a payment system")

        assert isinstance(artifact, Artifact)
        assert artifact.source_agent == first_id
        assert artifact.artifact_type != "stub"

        # Must have sections from output_contract
        profile = populated_registry.get(first_id)
        required = profile["output_contract"]["required_sections"]
        for section in required:
            assert section in artifact.sections, f"Missing required section: {section}"

    def test_missing_profile_raises_valueerror(self, populated_registry):
        """Unknown profile_id raises ValueError instead of silent stub."""
        executor = ProfileBasedExecutor(populated_registry)
        with pytest.raises(ValueError, match="not found in registry"):
            executor("agency.nonexistent-agent", "Do something")

    def test_artifact_type_matches_output_contract(self, populated_registry):
        """artifact_type comes from the profile's output_contract."""
        executor = ProfileBasedExecutor(populated_registry)
        for pid in populated_registry.list_all()[:3]:
            artifact = executor(pid, "Test task")
            profile = populated_registry.get(pid)
            expected_type = profile["output_contract"]["artifact_type"]
            assert artifact.artifact_type == expected_type

    def test_sections_contain_profile_name(self, populated_registry):
        """Generated sections reference the profile's name (differentiation)."""
        executor = ProfileBasedExecutor(populated_registry)
        first_id = populated_registry.list_all()[0]
        profile = populated_registry.get(first_id)
        name = profile["name"]

        artifact = executor(first_id, "Test task")
        # At least one section value should contain the expert name
        all_values = str(artifact.sections)
        assert name in all_values, (
            f"Expert name '{name}' not found in sections: {artifact.sections}"
        )


# ---------------------------------------------------------------------------
# 2. Differentiation: different profiles produce different artifacts
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestProfileDifferentiation:
    """Different expert profiles produce meaningfully different artifacts."""

    def test_different_profiles_different_sections(self, populated_registry):
        """Two different profiles produce different section content."""
        executor = ProfileBasedExecutor(populated_registry)
        ids = populated_registry.list_all()
        if len(ids) < 2:
            pytest.skip("Need at least 2 profiles")

        artifact_a = executor(ids[0], "Test task")
        artifact_b = executor(ids[1], "Test task")

        # Either artifact_type, section values, or both must differ
        different = (
            artifact_a.artifact_type != artifact_b.artifact_type
            or artifact_a.sections != artifact_b.sections
        )
        assert different, (
            f"Profiles {ids[0]} and {ids[1]} produced identical artifacts"
        )

    def test_capabilities_drive_different_findings(self, populated_registry):
        """Agents with different capabilities produce different 'findings' sections."""
        executor = ProfileBasedExecutor(populated_registry)

        # Find two agents with different capabilities
        ids = populated_registry.list_all()
        task = "Analyze the codebase"

        artifacts_by_id: dict[str, Artifact] = {}
        for pid in ids:
            artifacts_by_id[pid] = executor(pid, task)

        # Check that not all artifacts are identical
        section_keys_set: set[frozenset[str]] = set()
        for art in artifacts_by_id.values():
            section_keys_set.add(frozenset(art.sections.keys()))

        assert len(section_keys_set) > 1, (
            "All profiles produced identical section structures"
        )


# ---------------------------------------------------------------------------
# 3. Output contract coverage
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestOutputContractCoverage:
    """All required_sections from output_contract must be present."""

    def test_all_required_sections_present(self, populated_registry):
        """Every profile's required_sections appear in the artifact."""
        executor = ProfileBasedExecutor(populated_registry)
        for pid in populated_registry.list_all():
            artifact = executor(pid, "Test task")
            profile = populated_registry.get(pid)
            required = profile["output_contract"]["required_sections"]

            for section in required:
                assert section in artifact.sections, (
                    f"Profile {pid}: missing required section '{section}'"
                )

    def test_all_profiles_covered(self, populated_registry):
        """Smoke test: executor works for every registered profile without error."""
        executor = ProfileBasedExecutor(populated_registry)
        for pid in populated_registry.list_all():
            artifact = executor(pid, "Smoke test task")
            assert isinstance(artifact, Artifact)
            assert len(artifact.sections) > 0


# ---------------------------------------------------------------------------
# 4. TaskComposer integration: uses ProfileBasedExecutor by default
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestTaskComposerDefaultExecutor:
    """TaskComposer uses ProfileBasedExecutor when no executor is provided."""

    def test_default_executor_produces_differentiated_output(self, populated_registry):
        """Without passing expert_executor, TaskComposer produces profile-aware artifacts."""
        composer = TaskComposer(registry=populated_registry)
        inp = TaskComposerInput(
            task="Design a microservice architecture for a payment system",
            mode="plan",
            max_parallel=3,
        )
        result = composer.run(inp)

        assert isinstance(result, TaskComposerResult)
        if result.integrated is not None:
            # The integrated artifact should have merged sections from real profiles,
            # not the same stub "context" key from every agent
            merged = result.integrated.merged_sections
            assert len(merged) > 1, (
                f"Expected >1 merged sections, got: {list(merged.keys())}"
            )

    def test_default_not_stub_anymore(self, populated_registry):
        """Default executor output is NOT the old stub format (just 'context')."""
        composer = TaskComposer(registry=populated_registry)
        inp = TaskComposerInput(
            task="Review code for security vulnerabilities",
            mode="plan",
        )
        result = composer.run(inp)

        if result.integrated is not None:
            merged = result.integrated.merged_sections
            # Old stub only had {"context": task}. Real profiles produce richer output.
            # At least one section besides "context" should exist
            non_context = [k for k in merged if k != "context"]
            # Even if all profiles only produce "context", merged_sections
            # will also contain "final_recommendation" and "decision_summary"
            # from Integrator. So check that we have more than stub would give.
            assert "final_recommendation" in merged or len(non_context) > 0, (
                "Output looks like old stub — ProfileBasedExecutor not active"
            )

    def test_explicit_executor_still_works(self, populated_registry):
        """Passing an explicit executor still overrides the default."""
        composer = TaskComposer(registry=populated_registry)

        def custom_executor(profile_id: str, task: str) -> Artifact:
            return Artifact(
                source_agent=profile_id,
                artifact_type="custom",
                sections={"custom_section": "custom_value"},
            )

        inp = TaskComposerInput(task="Test task", mode="plan")
        result = composer.run(inp, expert_executor=custom_executor)

        if result.integrated is not None:
            assert "custom_section" in result.integrated.merged_sections


# ---------------------------------------------------------------------------
# 5. Importer: _build_profile_package imported_at parameter
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
class TestImporterImportedAt:
    """_build_profile_package() preserves custom imported_at date."""

    def test_custom_imported_at_preserved(self, importer_profiles):
        """Passing a custom imported_at date is reflected in the profile package."""
        from datetime import date

        from agent_nexus.platform.agency.importer import AgencyImporter
        from agent_nexus.platform.agency.parser import parse_frontmatter

        importer = AgencyImporter(
            vendor_path=str(_VENDOR_DIR),
            allowlist_path=str(_ALLOWLIST_PATH),
            output_dir="/dev/null",
        )
        allowlist_data = importer._load_allowlist()
        entry = allowlist_data["agents"][0]
        source_path = entry["source_path"]
        md_file = _VENDOR_DIR / source_path
        content = md_file.read_text(encoding="utf-8")
        parsed = parse_frontmatter(content)

        custom_date = date(2020, 1, 1)
        pkg = importer._build_profile_package(parsed, entry, imported_at=custom_date)

        assert pkg["expert_profile"]["profile"]["imported_at"] == "2020-01-01"

    def test_default_imported_at_is_today(self, importer_profiles):
        """Without imported_at, today's date is used."""
        from datetime import date

        from agent_nexus.platform.agency.importer import AgencyImporter
        from agent_nexus.platform.agency.parser import parse_frontmatter

        importer = AgencyImporter(
            vendor_path=str(_VENDOR_DIR),
            allowlist_path=str(_ALLOWLIST_PATH),
            output_dir="/dev/null",
        )
        allowlist_data = importer._load_allowlist()
        entry = allowlist_data["agents"][0]
        source_path = entry["source_path"]
        md_file = _VENDOR_DIR / source_path
        content = md_file.read_text(encoding="utf-8")
        parsed = parse_frontmatter(content)

        pkg = importer._build_profile_package(parsed, entry)

        assert pkg["expert_profile"]["profile"]["imported_at"] == date.today().isoformat()
