"""Unit tests for AgencyImporter — profile import, policy checks, and output generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_nexus.platform.agency.importer import (
    AgencyImporter,
    ContentPolicyError,
    ContentPolicyViolation,
    _dump_yaml,
    _yaml_quote,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_ALLOWLIST: dict[str, Any] = {
    "source": {"repo": "https://example.com/agents.git", "ref": "main"},
    "agents": [
        {
            "id": "agency.test-agent",
            "source_path": "agents/test.md",
            "capabilities": ["code-review", "bug-analysis"],
            "output_contract": "review_report",
            "tools": {"allowed": ["file_read"], "denied": ["bash"]},
        },
    ],
}

SAMPLE_MD = """\
---
name: Test Agent
description: A test agent for unit tests
vibe: professional
---
You are a helpful test agent.
"""


@pytest.fixture()
def vendor_dir(tmp_path: Path) -> Path:
    """Create a vendor directory with a sample MD file."""
    agents_dir = tmp_path / "vendor" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "test.md").write_text(SAMPLE_MD, encoding="utf-8")
    return tmp_path / "vendor"


@pytest.fixture()
def allowlist_file(tmp_path: Path) -> Path:
    """Create a sample allowlist YAML file."""
    import yaml

    path = tmp_path / "allowlist.yaml"
    path.write_text(yaml.dump(SAMPLE_ALLOWLIST, default_flow_style=False), encoding="utf-8")
    return path


@pytest.fixture()
def output_dir(tmp_path: Path) -> Path:
    return tmp_path / "output"


@pytest.fixture()
def importer(vendor_dir: Path, allowlist_file: Path, output_dir: Path) -> AgencyImporter:
    return AgencyImporter(
        vendor_path=str(vendor_dir),
        allowlist_path=str(allowlist_file),
        output_dir=str(output_dir),
    )


# ============================================================================
# dry_run
# ============================================================================


class TestDryRun:
    def test_dry_run_returns_profiles(self, importer: AgencyImporter) -> None:
        profiles = importer.dry_run()
        assert len(profiles) == 1
        pkg = profiles[0]
        assert pkg["id"] == "agency.test-agent"
        assert "expert_profile" in pkg
        assert "normalized_prompt" in pkg
        assert pkg["expert_profile"]["name"] == "Test Agent"

    def test_dry_run_profile_structure(self, importer: AgencyImporter) -> None:
        profiles = importer.dry_run()
        ep = profiles[0]["expert_profile"]
        assert ep["id"] == "agency.test-agent"
        assert ep["source"]["kind"] == "git"
        assert ep["source"]["repo"] == "https://example.com/agents.git"
        assert ep["profile"]["category"] == "agents"  # _derive_category from "agents/test.md"
        assert ep["capabilities"] == ["code-review", "bug-analysis"]
        assert ep["output_contract"]["artifact_type"] == "review_report"
        assert ep["permissions"]["mode"] == "plan"

    def test_dry_run_normalized_prompt(self, importer: AgencyImporter) -> None:
        profiles = importer.dry_run()
        prompt = profiles[0]["normalized_prompt"]
        assert "# Test Agent" in prompt
        assert "You are a helpful test agent." in prompt

    def test_dry_run_file_not_found(
        self, vendor_dir: Path, allowlist_file: Path, output_dir: Path
    ) -> None:
        import yaml

        bad = {
            "source": {"repo": "https://example.com/agents.git", "ref": "main"},
            "agents": [
                {
                    "id": "agency.missing",
                    "source_path": "agents/missing.md",
                    "capabilities": ["doc-gen"],
                    "output_contract": "documentation",
                },
            ],
        }
        allowlist_file.write_text(yaml.dump(bad, default_flow_style=False), encoding="utf-8")
        imp = AgencyImporter(str(vendor_dir), str(allowlist_file), str(output_dir))
        with pytest.raises(FileNotFoundError, match="Vendor file not found"):
            imp.dry_run()

    def test_dry_run_directory_traversal_blocked(
        self, vendor_dir: Path, allowlist_file: Path, output_dir: Path
    ) -> None:
        import yaml

        bad = {
            "source": {"repo": "https://example.com/agents.git", "ref": "main"},
            "agents": [
                {
                    "id": "agency.traversal",
                    "source_path": "../../etc/passwd.md",
                    "capabilities": ["doc-gen"],
                    "output_contract": "documentation",
                },
            ],
        }
        allowlist_file.write_text(yaml.dump(bad, default_flow_style=False), encoding="utf-8")
        imp = AgencyImporter(str(vendor_dir), str(allowlist_file), str(output_dir))
        with pytest.raises(ValueError, match="source_path"):
            imp.dry_run()


# ============================================================================
# import_all
# ============================================================================


class TestImportAll:
    def test_import_all_creates_files(self, importer: AgencyImporter, output_dir: Path) -> None:
        importer.import_all()
        # Check profile JSON
        profile_path = output_dir / "agency.test-agent.json"
        assert profile_path.is_file()
        data = json.loads(profile_path.read_text(encoding="utf-8"))
        assert data["id"] == "agency.test-agent"

        # Check normalized prompt
        prompt_path = output_dir / "normalized" / "agency.test-agent.md"
        assert prompt_path.is_file()
        content = prompt_path.read_text(encoding="utf-8")
        assert "Test Agent" in content

        # Check index.yaml and source.lock.yaml
        assert (output_dir / "index.yaml").is_file()
        assert (output_dir / "source.lock.yaml").is_file()

    def test_import_all_index_content(self, importer: AgencyImporter, output_dir: Path) -> None:
        import yaml

        importer.import_all()
        index = yaml.safe_load((output_dir / "index.yaml").read_text(encoding="utf-8"))
        assert index["version"] == 1
        assert len(index["agents"]) == 1
        assert index["agents"][0]["id"] == "agency.test-agent"

    def test_import_all_source_lock(self, importer: AgencyImporter, output_dir: Path) -> None:
        import yaml

        importer.import_all()
        lock = yaml.safe_load((output_dir / "source.lock.yaml").read_text(encoding="utf-8"))
        assert lock["version"] == 1
        assert lock["source"]["repo"] == "https://example.com/agents.git"

    def test_import_all_empty_agents(
        self, vendor_dir: Path, allowlist_file: Path, output_dir: Path
    ) -> None:
        import yaml

        empty = {"source": {"repo": "x", "ref": "main"}, "agents": []}
        allowlist_file.write_text(yaml.dump(empty, default_flow_style=False), encoding="utf-8")
        imp = AgencyImporter(str(vendor_dir), str(allowlist_file), str(output_dir))
        imp.import_all()
        index = yaml.safe_load((output_dir / "index.yaml").read_text(encoding="utf-8"))
        assert index.get("agents") in ([], None)


# ============================================================================
# ContentPolicyViolation
# ============================================================================


class TestContentPolicyViolation:
    def test_content_policy_violation_is_exception(self) -> None:
        with pytest.raises(ContentPolicyError):
            raise ContentPolicyViolation("test violation")

    def test_content_policy_violation_message(self) -> None:
        err = ContentPolicyViolation("unsafe content detected")
        assert "unsafe content" in str(err)


# ============================================================================
# YAML helpers
# ============================================================================


class TestYamlHelpers:
    def test_yaml_quote_special_chars(self) -> None:
        assert _yaml_quote("hello: world").startswith('"')
        assert _yaml_quote("normal text") == "normal text"

    def test_yaml_quote_empty_string(self) -> None:
        assert _yaml_quote("") == '""'

    def test_dump_yaml_produces_valid_yaml(self, tmp_path: Path) -> None:
        import yaml

        data = {"key": "value", "nested": {"a": 1}}
        out = tmp_path / "test.yaml"
        with out.open("w") as f:
            _dump_yaml(data, f)
        parsed = yaml.safe_load(out.read_text())
        assert parsed == data


# ============================================================================
# _build_profile_package edge cases
# ============================================================================


class TestBuildProfilePackage:
    def test_default_denied_tools_when_not_specified(
        self, vendor_dir: Path, allowlist_file: Path, output_dir: Path
    ) -> None:
        import yaml

        minimal = {
            "source": {"repo": "r", "ref": "main"},
            "agents": [
                {
                    "id": "agency.min-agent",
                    "source_path": "agents/test.md",
                    "capabilities": ["doc-gen"],
                    "output_contract": "documentation",
                },
            ],
        }
        allowlist_file.write_text(yaml.dump(minimal, default_flow_style=False), encoding="utf-8")
        imp = AgencyImporter(str(vendor_dir), str(allowlist_file), str(output_dir))
        profiles = imp.dry_run()
        perms = profiles[0]["expert_profile"]["permissions"]
        assert "bash" in perms["denied_tools"]
        assert "file_write" in perms["denied_tools"]

    def test_custom_tools_propagated(
        self, vendor_dir: Path, allowlist_file: Path, output_dir: Path
    ) -> None:
        import yaml

        custom = {
            "source": {"repo": "r", "ref": "main"},
            "agents": [
                {
                    "id": "agency.custom-agent",
                    "source_path": "agents/test.md",
                    "capabilities": ["test"],
                    "output_contract": "documentation",
                    "tools": {"allowed": ["bash", "network"], "denied": []},
                },
            ],
        }
        allowlist_file.write_text(yaml.dump(custom, default_flow_style=False), encoding="utf-8")
        imp = AgencyImporter(str(vendor_dir), str(allowlist_file), str(output_dir))
        profiles = imp.dry_run()
        perms = profiles[0]["expert_profile"]["permissions"]
        assert "bash" in perms["allowed_tools"]
        assert perms["denied_tools"] == []
