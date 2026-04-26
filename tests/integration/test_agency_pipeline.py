"""Integration test: full agency pipeline from importer to QA gate.

Uses real importer output — no manually constructed fixtures.
Validates that all modules chain correctly across module boundaries.
"""

import importlib.util
import sys
import tempfile
from pathlib import Path

import pytest

from agent_nexus.platform.agency.importer import AgencyImporter
from agent_nexus.platform.agency.registry import ExpertRegistry
from agent_nexus.platform.agency.selector import SelectionRequest, SpecialistSelector
from agent_nexus.platform.agency.planner import DynamicCompositePlanner, generate_toml
from agent_nexus.platform.agency.integrator import Artifact, Integrator
from agent_nexus.platform.agency.qa_gate import QAGate, QAGateInput

# Load profile_loader from the generic-expert-agent package (not installed in project venv)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_VENDOR_DIR = _PROJECT_ROOT / "vendor" / "agency-agents"
_ALLOWLIST_PATH = _PROJECT_ROOT / "config" / "agency-agents.allowlist.yaml"
_PROFILE_LOADER_PATH = (
    _PROJECT_ROOT
    / "agents"
    / "atomic"
    / "generic-expert-agent"
    / "agent_generic_expert_agent"
    / "profile_loader.py"
)

_spec = importlib.util.spec_from_file_location(
    "agent_generic_expert_agent.profile_loader",
    _PROFILE_LOADER_PATH,
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["agent_generic_expert_agent.profile_loader"] = _mod
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
assemble_prompt = _mod.assemble_prompt

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_VENDOR_DIR = _PROJECT_ROOT / "vendor" / "agency-agents"
_ALLOWLIST_PATH = _PROJECT_ROOT / "config" / "agency-agents.allowlist.yaml"


@pytest.fixture(scope="module")
def importer_profiles():
    """Real importer output: 12 profile packages from vendor repo."""
    with tempfile.TemporaryDirectory() as tmpdir:
        importer = AgencyImporter(
            vendor_path=str(_VENDOR_DIR),
            allowlist_path=str(_ALLOWLIST_PATH),
            output_dir=tmpdir,
        )
        return importer.dry_run()


@pytest.fixture(scope="module")
def populated_registry(importer_profiles):
    """ExpertRegistry loaded with all 12 imported profiles."""
    registry = ExpertRegistry()
    for pkg in importer_profiles:
        ep = pkg["expert_profile"]
        registry.add(ep["id"], ep, ep["capabilities"])
    return registry


# ---------------------------------------------------------------------------
# Step 1: Importer produces valid output
# ---------------------------------------------------------------------------


class TestImporterOutput:
    """Importer dry_run produces valid profile packages."""

    def test_importer_produces_12_profiles(self, importer_profiles):
        assert len(importer_profiles) == 12

    def test_each_profile_has_required_keys(self, importer_profiles):
        required = {"id", "expert_profile", "normalized_prompt", "source_md", "output_contract"}
        for pkg in importer_profiles:
            assert required.issubset(pkg.keys()), f"Missing keys in {pkg['id']}: {required - set(pkg.keys())}"


# ---------------------------------------------------------------------------
# Step 2: Cross-module boundary: importer → profile_loader
# ---------------------------------------------------------------------------


class TestImporterToProfileLoader:
    """importer._build_profile_package() output must cover profile_loader.assemble_prompt() fields."""

    def test_profile_loader_assembles_nonempty_prompt(self, importer_profiles):
        """assemble_prompt() must return a non-empty string (len > 50) using importer output."""
        for pkg in importer_profiles:
            ep = pkg["expert_profile"]
            prompt = assemble_prompt(ep)
            assert isinstance(prompt, str), f"{ep['id']}: prompt is not a string"
            assert len(prompt) > 50, f"{ep['id']}: prompt too short ({len(prompt)} chars)"

    def test_profile_has_body_and_vibe(self, importer_profiles):
        """expert_profile.profile section must have non-empty body and vibe fields."""
        for pkg in importer_profiles:
            ep = pkg["expert_profile"]
            profile_section = ep.get("profile", {})
            assert "body" in profile_section, f"{ep['id']}: missing 'body' in profile section"
            assert "vibe" in profile_section, f"{ep['id']}: missing 'vibe' in profile section"
            assert len(profile_section["body"]) > 0, f"{ep['id']}: empty body"
            assert isinstance(profile_section["vibe"], str), f"{ep['id']}: vibe is not a string"

    def test_importer_keys_cover_profile_loader_reads(self, importer_profiles):
        """All fields that assemble_prompt reads must be present in importer output."""
        for pkg in importer_profiles:
            ep = pkg["expert_profile"]
            # assemble_prompt reads: name, profile.vibe, profile.body, profile.description
            assert "name" in ep, f"Missing 'name' in {ep['id']}"
            assert "profile" in ep, f"Missing 'profile' in {ep['id']}"
            for field in ("vibe", "body", "description"):
                assert field in ep["profile"], f"Missing 'profile.{field}' in {ep['id']}"


# ---------------------------------------------------------------------------
# Step 3: Selector selects specialists from registry
# ---------------------------------------------------------------------------


class TestSelectorFromRegistry:
    """Selector picks specialists from importer-populated registry."""

    def test_select_for_architecture_task(self, populated_registry):
        selector = SpecialistSelector(populated_registry)
        results = selector.select(
            SelectionRequest(
                task_type="architecture",
                required_capabilities=["system_design"],
                optional_capabilities=["security_review"],
                max_agents=3,
                permissions="plan",
            )
        )
        assert len(results) > 0
        ids = [r.agent_id for r in results]
        assert "agency.software-architect" in ids

    def test_select_for_security_task(self, populated_registry):
        selector = SpecialistSelector(populated_registry)
        results = selector.select(
            SelectionRequest(
                task_type="security",
                required_capabilities=["security_review"],
                optional_capabilities=[],
                max_agents=3,
                permissions="plan",
            )
        )
        assert len(results) > 0
        ids = [r.agent_id for r in results]
        assert "agency.security-engineer" in ids


# ---------------------------------------------------------------------------
# Step 4: Planner generates TOML DAG
# ---------------------------------------------------------------------------


class TestPlannerFromSelection:
    """Planner generates TOML from selected specialists."""

    def test_plan_and_generate_toml(self, populated_registry):
        selector = SpecialistSelector(populated_registry)
        results = selector.select(
            SelectionRequest(
                task_type="architecture",
                required_capabilities=["system_design"],
                optional_capabilities=["security_review"],
                max_agents=3,
                permissions="plan",
            )
        )
        assert len(results) > 0

        from agent_nexus.platform.agency.planner import SubtaskDef

        subtasks = []
        for sel in results:
            profile = populated_registry.get(sel.agent_id)
            artifact_type = (
                profile.get("output_contract", {}).get("artifact_type", "report")
                if profile
                else "report"
            )
            subtasks.append(
                SubtaskDef(
                    id=sel.agent_id.replace("agency.", ""),
                    goal="Design integration architecture",
                    needed_capabilities=["system_design", "security_review"],
                    output_contract=artifact_type,
                    assigned_agent=sel.agent_id,
                )
            )

        planner = DynamicCompositePlanner()
        dag = planner.resolve_dependencies(subtasks, composition_name="integration-test")
        toml_str = generate_toml(dag)

        assert "[composition]" in toml_str
        assert "integrate" in toml_str
        assert "validate" in toml_str

        # Verify TOML is parseable
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib

        parsed = tomllib.loads(toml_str)
        assert len(parsed["tasks"]) >= 3  # specialists + integrate + validate


# ---------------------------------------------------------------------------
# Step 5: Integrator merges artifacts
# ---------------------------------------------------------------------------


class TestIntegratorMerge:
    """Integrator merges artifacts from specialists into unified output."""

    def test_merge_two_expert_artifacts(self, populated_registry):
        selector = SpecialistSelector(populated_registry)
        results = selector.select(
            SelectionRequest(
                task_type="architecture",
                required_capabilities=["security_review"],
                optional_capabilities=[],
                max_agents=3,
                permissions="plan",
            )
        )
        assert len(results) >= 2

        # Simulate expert outputs
        artifacts = []
        for sel in results:
            profile = populated_registry.get(sel.agent_id)
            name = profile.get("name", sel.agent_id) if profile else sel.agent_id
            artifacts.append(
                Artifact(
                    source_agent=sel.agent_id,
                    artifact_type="report",
                    sections={
                        "context": f"Analysis from {name}",
                        "recommendation": f"Recommendation from {name}",
                        "risks": [f"Risk identified by {name}"],
                    },
                )
            )

        result = Integrator.merge(artifacts)

        assert result.artifact_type == "integrated_plan"
        assert len(result.source_agents) >= 2
        assert "final_recommendation" in result.merged_sections
        assert "decision_summary" in result.merged_sections
        assert len(result.risks) > 0


# ---------------------------------------------------------------------------
# Step 6: QA Gate validates integrated output
# ---------------------------------------------------------------------------


class TestQAGateValidation:
    """QA Gate validates the integrated output."""

    def test_qa_gate_passes_with_complete_output(self, populated_registry):
        # Simulate complete integrated output
        integrated_output = {
            "sections": {
                "context": "Integration plan",
                "assumptions": ["Experts are persona-only"],
                "proposed_design": "Generic expert agent + profile injection",
                "tradeoffs": ["Flexibility vs complexity"],
                "risks": ["Token cost may increase"],
                "next_steps": ["Implement Phase A"],
            }
        }
        required_sections = [
            "context", "assumptions", "proposed_design",
            "tradeoffs", "risks", "next_steps",
        ]

        gate_input = QAGateInput(
            output=integrated_output,
            required_sections=required_sections,
            task_type="architecture_review",
        )
        result = QAGate.run(gate_input)
        assert result.passed is True
        assert result.contract_result.passed is True

    def test_qa_gate_fails_with_missing_sections(self):
        incomplete_output = {
            "sections": {
                "context": "Plan",
            }
        }
        gate_input = QAGateInput(
            output=incomplete_output,
            required_sections=["context", "risks", "next_steps"],
            task_type="architecture_review",
        )
        result = QAGate.run(gate_input)
        assert result.passed is False
        assert "risks" in result.contract_result.missing_sections


# ---------------------------------------------------------------------------
# Full pipeline: importer → profile_loader → selector → planner → integrator → qa_gate
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """End-to-end pipeline using real importer output, no manual fixtures."""

    def test_full_pipeline(self, importer_profiles, populated_registry):
        """Chain: importer → profile_loader → selector → planner → integrator → qa_gate."""

        # 1. Importer already produced profiles (fixture)
        assert len(importer_profiles) == 12

        # 2. Profile loader produces non-empty prompts
        for pkg in importer_profiles:
            prompt = assemble_prompt(pkg["expert_profile"])
            assert len(prompt) > 50, f"Prompt for {pkg['id']} too short"

        # 3. Selector picks specialists
        selector = SpecialistSelector(populated_registry)
        selected = selector.select(
            SelectionRequest(
                task_type="plan",
                required_capabilities=["security_review"],
                optional_capabilities=["system_design", "reliability_review"],
                max_agents=3,
                permissions="plan",
            )
        )
        assert len(selected) >= 2, f"Expected >= 2 specialists, got {len(selected)}"

        # 4. Planner generates DAG
        from agent_nexus.platform.agency.planner import SubtaskDef

        subtasks = []
        for sel in selected:
            profile = populated_registry.get(sel.agent_id)
            artifact_type = (
                profile.get("output_contract", {}).get("artifact_type", "report")
                if profile
                else "report"
            )
            subtasks.append(
                SubtaskDef(
                    id=sel.agent_id.replace("agency.", ""),
                    goal="Design integration architecture",
                    needed_capabilities=["system_design", "security_review"],
                    output_contract=artifact_type,
                    assigned_agent=sel.agent_id,
                )
            )

        planner = DynamicCompositePlanner()
        dag = planner.resolve_dependencies(subtasks, composition_name="e2e-pipeline")
        toml_str = generate_toml(dag)
        assert "integrate" in toml_str

        # 5. Integrator merges expert artifacts
        artifacts = []
        for sel in selected:
            profile = populated_registry.get(sel.agent_id)
            name = profile.get("name", sel.agent_id) if profile else sel.agent_id
            artifacts.append(
                Artifact(
                    source_agent=sel.agent_id,
                    artifact_type="report",
                    sections={
                        "context": f"Analysis from {name}",
                        "recommendation": f"Recommendation from {name}",
                        "risks": [f"Risk by {name}"],
                        "next_steps": ["Proceed with implementation"],
                    },
                )
            )

        integrated = Integrator.merge(artifacts)
        assert integrated.artifact_type == "integrated_plan"
        assert "final_recommendation" in integrated.merged_sections

        # 6. QA Gate validates
        gate_input = QAGateInput(
            output={"sections": integrated.merged_sections},
            required_sections=["context", "risks", "next_steps"],
            task_type="architecture_review",
        )
        qa_result = QAGate.run(gate_input)
        assert qa_result.passed is True, (
            f"QA gate failed: missing={qa_result.contract_result.missing_sections}"
        )
