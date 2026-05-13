"""G2 tests: Per-agent tool access in allowlist, importer, and validator."""

import tempfile
from pathlib import Path

import pytest
import yaml

from agent_nexus.platform.agency.allowlist import validate_allowlist_entry
from agent_nexus.platform.agency.importer import AgencyImporter

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_VENDOR_DIR = _PROJECT_ROOT / "vendor" / "agency-agents"
_ALLOWLIST_PATH = _PROJECT_ROOT / "config" / "agency-agents.allowlist.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def allowlist_data():
    with open(_ALLOWLIST_PATH) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def importer_profiles():
    with tempfile.TemporaryDirectory() as tmpdir:
        importer = AgencyImporter(
            vendor_path=str(_VENDOR_DIR),
            allowlist_path=str(_ALLOWLIST_PATH),
            output_dir=tmpdir,
        )
        return importer.dry_run()


# ===================================================================
# 1. Allowlist with tools field parses correctly
# ===================================================================


@pytest.mark.timeout(30)
class TestAllowlistToolsField:
    """Allowlist YAML tools field is well-formed for all 12 agents."""

    def test_all_agents_have_tools_field(self, allowlist_data):
        for entry in allowlist_data["agents"]:
            assert "tools" in entry, f"{entry['id']} missing 'tools' field"

    def test_tools_has_allowed_and_denied(self, allowlist_data):
        for entry in allowlist_data["agents"]:
            tools = entry["tools"]
            assert "allowed" in tools, f"{entry['id']} missing tools.allowed"
            assert "denied" in tools, f"{entry['id']} missing tools.denied"

    def test_tools_values_are_string_lists(self, allowlist_data):
        for entry in allowlist_data["agents"]:
            tools = entry["tools"]
            assert isinstance(tools["allowed"], list), f"{entry['id']}: allowed not a list"
            assert isinstance(tools["denied"], list), f"{entry['id']}: denied not a list"
            for t in tools["allowed"]:
                assert isinstance(t, str), f"{entry['id']}: non-string in allowed: {t}"
            for t in tools["denied"]:
                assert isinstance(t, str), f"{entry['id']}: non-string in denied: {t}"

    def test_no_overlap_between_allowed_and_denied(self, allowlist_data):
        for entry in allowlist_data["agents"]:
            tools = entry["tools"]
            overlap = set(tools["allowed"]) & set(tools["denied"])
            assert not overlap, f"{entry['id']}: overlap {overlap}"


# ===================================================================
# 2. Importer respects per-agent tools config
# ===================================================================


@pytest.mark.timeout(30)
class TestImporterPerAgentTools:
    """Importer._build_profile_package() reads tools from allowlist entries."""

    def test_code_reviewer_has_file_read(self, importer_profiles):
        pkg = next(p for p in importer_profiles if p["id"] == "agency.code-reviewer")
        perms = pkg["expert_profile"]["permissions"]
        assert "file_read" in perms["allowed_tools"]
        assert "network" in perms["allowed_tools"]
        assert "bash" in perms["denied_tools"]
        assert "file_write" in perms["denied_tools"]


# ===================================================================
# 3. Agents needing file_read get it
# ===================================================================


@pytest.mark.timeout(30)
class TestFileReadAgents:
    """All agents that need file_read have it in allowed_tools."""

    FILE_READ_AGENTS = [
        "agency.software-architect",
        "agency.backend-architect",
        "agency.ai-engineer",
        "agency.code-reviewer",
        "agency.security-engineer",
        "agency.sre",
        "agency.test-results-analyzer",
        "agency.technical-writer",
        "agency.codebase-onboarding",
        "agency.tool-evaluator",
        "agency.lsp-index-engineer",
    ]

    def test_file_read_agents_get_access(self, importer_profiles):
        profile_map = {p["id"]: p for p in importer_profiles}
        for agent_id in self.FILE_READ_AGENTS:
            pkg = profile_map[agent_id]
            allowed = pkg["expert_profile"]["permissions"]["allowed_tools"]
            assert "file_read" in allowed, (
                f"{agent_id} needs file_read but has allowed_tools={allowed}"
            )


# ===================================================================
# 4. Validator rejects invalid tools configs
# ===================================================================


@pytest.mark.timeout(30)
class TestToolsValidation:
    """validate_allowlist_entry catches invalid tools fields."""

    def _make_entry(self, **overrides):
        base = {
            "source_path": "engineering/test.md",
            "id": "agency.test-agent",
            "capabilities": ["testing"],
            "output_contract": "test_report",
        }
        base.update(overrides)
        return base

    def test_valid_tools_passes(self):
        entry = self._make_entry(tools={"allowed": ["file_read"], "denied": ["bash"]})
        errors = validate_allowlist_entry(entry)
        # Only check for tools-related errors
        tools_errors = [e for e in errors if "tools" in e.lower()]
        assert tools_errors == []

    def test_tools_not_dict_fails(self):
        entry = self._make_entry(tools="bad")
        errors = validate_allowlist_entry(entry)
        assert any("'tools' must be a mapping" in e for e in errors)

    def test_allowed_not_list_fails(self):
        entry = self._make_entry(tools={"allowed": "file_read", "denied": ["bash"]})
        errors = validate_allowlist_entry(entry)
        assert any("'tools.allowed' must be a list" in e for e in errors)

    def test_denied_not_list_fails(self):
        entry = self._make_entry(tools={"allowed": ["file_read"], "denied": "bash"})
        errors = validate_allowlist_entry(entry)
        assert any("'tools.denied' must be a list" in e for e in errors)

    def test_allowed_non_strings_fails(self):
        entry = self._make_entry(tools={"allowed": [123], "denied": ["bash"]})
        errors = validate_allowlist_entry(entry)
        assert any("'tools.allowed' must be a list of strings" in e for e in errors)

    def test_overlap_fails(self):
        entry = self._make_entry(
            tools={"allowed": ["file_read", "bash"], "denied": ["bash", "network"]}
        )
        errors = validate_allowlist_entry(entry)
        assert any("both allowed and denied" in e for e in errors)


# ===================================================================
# 5. Backward compatibility: no tools field → defaults
# ===================================================================


@pytest.mark.timeout(30)
class TestBackwardCompat:
    """Agents without tools field get default permissions."""

    def test_entry_without_tools_gets_defaults(self):
        """Importer falls back to empty allowed and full denied when no tools field."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a minimal allowlist without tools field
            allowlist_content = yaml.dump(
                {
                    "source": {
                        "repo": "https://github.com/example/test",
                        "ref": "abc123",
                    },
                    "agents": [
                        {
                            "source_path": "engineering/engineering-software-architect.md",
                            "id": "agency.test-default",
                            "capabilities": ["system_design"],
                            "output_contract": "architecture_plan",
                        },
                    ],
                }
            )
            allowlist_file = Path(tmpdir) / "test-allowlist.yaml"
            allowlist_file.write_text(allowlist_content)

            importer = AgencyImporter(
                vendor_path=str(_VENDOR_DIR),
                allowlist_path=str(allowlist_file),
                output_dir=tmpdir,
            )
            profiles = importer.dry_run()
            assert len(profiles) == 1
            perms = profiles[0]["expert_profile"]["permissions"]
            assert perms["allowed_tools"] == []
            assert perms["denied_tools"] == ["bash", "file_write", "network"]

    def test_entry_without_tools_validates(self):
        """validate_allowlist_entry accepts entries without tools field."""
        entry = {
            "source_path": "test/test.md",
            "id": "agency.test-agent",
            "capabilities": ["testing"],
            "output_contract": "test_report",
        }
        errors = validate_allowlist_entry(entry)
        assert errors == []
