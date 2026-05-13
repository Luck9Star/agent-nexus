"""Tests for unified agent manifest loading (TOML primary, YAML compat).

Covers:
- TOML manifest loading with [agent] section
- YAML backward compatibility with deprecation warning
- Auto-detection by file extension
- Missing fields use defaults
- Invalid files raise appropriate errors
- YAML-to-TOML migration roundtrip
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from agent_nexus.models.agent import AgentManifest
from agent_nexus.platform.local.manifest import (
    TOML_MANIFEST,
    YAML_MANIFEST,
    ManifestError,
    find_manifest,
    load_manifest,
    load_manifest_dict,
    load_manifest_from_file,
    migrate_yaml_to_toml,
)

# ---------------------------------------------------------------------------
# Fixtures: sample manifest data
# ---------------------------------------------------------------------------

ATOMIC_MANIFEST_YAML = """\
name: code-reviewer
version: 1.0.0
type: atomic
description: "Code review expert with SOLID analysis"

model_config:
  recommended: "premium"
  fallback: "standard"

capabilities: [static-analysis, anti-pattern-detection]

permissions:
  mode: default
  allowed_tools: [file_read, file_write]
  denied_tools: [bash]
"""

ATOMIC_MANIFEST_TOML = """\
[agent]
name = "code-reviewer"
version = "1.0.0"
type = "atomic"
description = "Code review expert with SOLID analysis"
capabilities = ["static-analysis", "anti-pattern-detection"]

[agent.model_config]
recommended = "premium"
fallback = "standard"

[agent.permissions]
mode = "default"
allowed_tools = ["file_read", "file_write"]
denied_tools = ["bash"]
"""

COMPOSITE_MANIFEST_TOML = """\
[agent]
name = "feature-delivery-pipeline"
version = "1.0.0"
type = "composite"
description = "Parallel feature delivery"

[agent.dependencies]
atomic_agents = ["doc-filler", "test-suite-generator"]
"""

MINIMAL_MANIFEST_TOML = """\
[agent]
name = "minimal-agent"
version = "0.1.0"
type = "atomic"
description = "A minimal agent"
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(agent_dir: Path, content: str) -> Path:
    """Write a YAML manifest and return its path."""
    path = agent_dir / YAML_MANIFEST
    path.write_text(content, encoding="utf-8")
    return path


def _write_toml(agent_dir: Path, content: str) -> Path:
    """Write a TOML manifest and return its path."""
    path = agent_dir / TOML_MANIFEST
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Tests: find_manifest
# ---------------------------------------------------------------------------


class TestFindManifest:
    def test_finds_toml_first(self, tmp_path: Path) -> None:
        _write_toml(tmp_path, MINIMAL_MANIFEST_TOML)
        _write_yaml(tmp_path, ATOMIC_MANIFEST_YAML)
        found = find_manifest(tmp_path)
        assert found is not None
        assert found.name == TOML_MANIFEST

    def test_finds_yaml_when_no_toml(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path, ATOMIC_MANIFEST_YAML)
        found = find_manifest(tmp_path)
        assert found is not None
        assert found.name == YAML_MANIFEST

    def test_returns_none_when_empty(self, tmp_path: Path) -> None:
        assert find_manifest(tmp_path) is None


# ---------------------------------------------------------------------------
# Tests: load_manifest (high-level)
# ---------------------------------------------------------------------------


class TestLoadManifest:
    def test_load_from_toml(self, tmp_path: Path) -> None:
        _write_toml(tmp_path, ATOMIC_MANIFEST_TOML)
        manifest = load_manifest(tmp_path)
        assert isinstance(manifest, AgentManifest)
        assert manifest.name == "code-reviewer"
        assert manifest.version == "1.0.0"
        assert manifest.type == "atomic"
        assert "static-analysis" in manifest.capabilities
        assert manifest.model_preferences is not None
        assert manifest.model_preferences.recommended == "premium"

    def test_load_from_yaml_with_deprecation(self, tmp_path: Path) -> None:
        _write_yaml(tmp_path, ATOMIC_MANIFEST_YAML)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            manifest = load_manifest(tmp_path)
        assert manifest.name == "code-reviewer"
        deprecation_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecation_warnings) == 1
        assert "deprecated" in str(deprecation_warnings[0].message).lower()

    def test_toml_preferred_over_yaml(self, tmp_path: Path) -> None:
        _write_toml(tmp_path, MINIMAL_MANIFEST_TOML)
        _write_yaml(tmp_path, ATOMIC_MANIFEST_YAML)
        manifest = load_manifest(tmp_path)
        assert manifest.name == "minimal-agent"

    def test_raises_when_no_manifest(self, tmp_path: Path) -> None:
        with pytest.raises(ManifestError, match="No manifest found"):
            load_manifest(tmp_path)

    def test_composite_manifest_toml(self, tmp_path: Path) -> None:
        _write_toml(tmp_path, COMPOSITE_MANIFEST_TOML)
        manifest = load_manifest(tmp_path)
        assert manifest.type == "composite"
        assert "doc-filler" in manifest.dependencies.atomic_agents

    def test_minimal_manifest_defaults(self, tmp_path: Path) -> None:
        _write_toml(tmp_path, MINIMAL_MANIFEST_TOML)
        manifest = load_manifest(tmp_path)
        assert manifest.name == "minimal-agent"
        assert manifest.capabilities == []
        assert manifest.permissions is None
        assert manifest.pip_dependencies == []


# ---------------------------------------------------------------------------
# Tests: load_manifest_from_file (explicit path)
# ---------------------------------------------------------------------------


class TestLoadManifestFromFile:
    def test_toml_file(self, tmp_path: Path) -> None:
        path = _write_toml(tmp_path, ATOMIC_MANIFEST_TOML)
        manifest = load_manifest_from_file(path)
        assert manifest.name == "code-reviewer"

    def test_yaml_file(self, tmp_path: Path) -> None:
        path = _write_yaml(tmp_path, ATOMIC_MANIFEST_YAML)
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            manifest = load_manifest_from_file(path)
        assert manifest.name == "code-reviewer"

    def test_empty_toml_raises(self, tmp_path: Path) -> None:
        path = tmp_path / TOML_MANIFEST
        path.write_text("", encoding="utf-8")
        with pytest.raises(ManifestError, match="Empty or unparseable"):
            load_manifest_from_file(path)

    def test_invalid_toml_syntax_raises(self, tmp_path: Path) -> None:
        path = tmp_path / TOML_MANIFEST
        path.write_text("[agent\nname = invalid\n", encoding="utf-8")
        with pytest.raises(ManifestError):
            load_manifest_from_file(path)

    def test_invalid_yaml_content_raises(self, tmp_path: Path) -> None:
        path = tmp_path / YAML_MANIFEST
        path.write_text("just a string not a dict", encoding="utf-8")
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            with pytest.raises(ManifestError, match="Empty or unparseable"):
                load_manifest_from_file(path)

    def test_unsupported_extension_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "manifest.json"
        path.write_text("{}", encoding="utf-8")
        with pytest.raises(ManifestError, match="Unsupported manifest format"):
            load_manifest_from_file(path)


# ---------------------------------------------------------------------------
# Tests: load_manifest_dict (validation + raw dict)
# ---------------------------------------------------------------------------


class TestLoadManifestDict:
    def test_valid_toml_returns_empty_issues(self, tmp_path: Path) -> None:
        _write_toml(tmp_path, ATOMIC_MANIFEST_TOML)
        issues, data = load_manifest_dict(tmp_path)
        assert issues == []
        assert data["name"] == "code-reviewer"

    def test_missing_name_field(self, tmp_path: Path) -> None:
        toml_content = """\
[agent]
version = "1.0.0"
type = "atomic"
description = "Missing name"
"""
        _write_toml(tmp_path, toml_content)
        issues, _ = load_manifest_dict(tmp_path)
        assert any("missing required field: name" in i for i in issues)

    def test_invalid_agent_type(self, tmp_path: Path) -> None:
        toml_content = """\
[agent]
name = "test"
version = "1.0.0"
type = "invalid_type"
description = "Bad type"
"""
        _write_toml(tmp_path, toml_content)
        issues, _ = load_manifest_dict(tmp_path)
        assert any("Invalid agent type" in i for i in issues)

    def test_no_manifest_returns_issues(self, tmp_path: Path) -> None:
        issues, data = load_manifest_dict(tmp_path)
        assert len(issues) > 0
        assert data == {}


# ---------------------------------------------------------------------------
# Tests: migrate_yaml_to_toml
# ---------------------------------------------------------------------------


class TestMigrateYamlToToml:
    def test_basic_migration(self, tmp_path: Path) -> None:
        yaml_path = _write_yaml(tmp_path, ATOMIC_MANIFEST_YAML)
        toml_path = migrate_yaml_to_toml(yaml_path)
        assert toml_path.exists()
        assert toml_path.name == TOML_MANIFEST
        # Verify the migrated file can be loaded back
        manifest = load_manifest(tmp_path)
        assert manifest.name == "code-reviewer"
        assert manifest.version == "1.0.0"
        assert manifest.type == "atomic"

    def test_custom_output_path(self, tmp_path: Path) -> None:
        yaml_path = _write_yaml(tmp_path, ATOMIC_MANIFEST_YAML)
        output = tmp_path / "custom-agent.toml"
        result = migrate_yaml_to_toml(yaml_path, output)
        assert result == output
        assert output.exists()

    def test_migration_preserves_capabilities(self, tmp_path: Path) -> None:
        yaml_path = _write_yaml(tmp_path, ATOMIC_MANIFEST_YAML)
        migrate_yaml_to_toml(yaml_path)
        manifest = load_manifest(tmp_path)
        assert "static-analysis" in manifest.capabilities
        assert "anti-pattern-detection" in manifest.capabilities

    def test_nonexistent_yaml_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ManifestError, match="not found"):
            migrate_yaml_to_toml(tmp_path / "nonexistent.yaml")

    def test_roundtrip_yaml_to_toml_fields(self, tmp_path: Path) -> None:
        """Full roundtrip: YAML → TOML → load → verify all fields match."""
        yaml_content = """\
name: test-agent
version: "2.5.0"
type: atomic
description: "Roundtrip test agent"
capabilities: [cap-a, cap-b]
"""
        yaml_path = _write_yaml(tmp_path, yaml_content)
        toml_path = migrate_yaml_to_toml(yaml_path)

        # Load original YAML
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            yaml_manifest = load_manifest_from_file(yaml_path)

        # Load migrated TOML
        toml_manifest = load_manifest_from_file(toml_path)

        assert yaml_manifest.name == toml_manifest.name
        assert yaml_manifest.version == toml_manifest.version
        assert yaml_manifest.type == toml_manifest.type
        assert yaml_manifest.description == toml_manifest.description
        assert yaml_manifest.capabilities == toml_manifest.capabilities

    def test_migration_composite_with_dependencies(self, tmp_path: Path) -> None:
        """Migrate a composite YAML manifest with dependencies."""
        yaml_content = """\
name: pipeline-agent
version: "1.0.0"
type: composite
description: "A composite agent"
dependencies:
  atomic_agents:
    - doc-filler
    - code-reviewer
"""
        yaml_path = _write_yaml(tmp_path, yaml_content)
        toml_path = migrate_yaml_to_toml(yaml_path)
        manifest = load_manifest_from_file(toml_path)
        assert manifest.type == "composite"
        assert "doc-filler" in manifest.dependencies.atomic_agents
        assert "code-reviewer" in manifest.dependencies.atomic_agents


# ---------------------------------------------------------------------------
# Tests: TOML format specifics
# ---------------------------------------------------------------------------


class TestTomlFormat:
    def test_toml_with_all_optional_fields(self, tmp_path: Path) -> None:
        toml_content = """\
[agent]
name = "full-agent"
version = "3.0.0"
type = "atomic"
description = "Agent with all fields"
capabilities = ["cap-1", "cap-2"]
tools = ["tool-a", "tool-b"]
denied_tools = ["bash"]
pip_dependencies = ["requests>=2.0"]
effort = "high"
max_turns = 20
memory_scope = "session"
color = "#FF0000"
background = true
initial_prompt = "Hello, start working"

[agent.model_config]
recommended = "anthropic:claude-sonnet-4-20250514"
fallback = "ollama:qwen2.5-coder:7b"

[agent.permissions]
mode = "default"
allowed_tools = ["file_read"]
denied_tools = ["bash"]

[agent.dependencies]
atomic_agents = ["dep-a"]
"""
        _write_toml(tmp_path, toml_content)
        manifest = load_manifest(tmp_path)
        assert manifest.name == "full-agent"
        assert manifest.capabilities == ["cap-1", "cap-2"]
        assert manifest.tools == ["tool-a", "tool-b"]
        assert manifest.pip_dependencies == ["requests>=2.0"]
        assert manifest.effort == "high"
        assert manifest.max_turns == 20
        assert manifest.memory_scope == "session"
        assert manifest.color == "#FF0000"
        assert manifest.background is True
        assert manifest.initial_prompt == "Hello, start working"
        assert manifest.dependencies.atomic_agents == ["dep-a"]

    def test_toml_no_agent_section_treated_as_flat(self, tmp_path: Path) -> None:
        """A TOML without [agent] section is treated as flat (backward compat
        with simple key=value format)."""
        toml_content = """\
name = "flat-agent"
version = "1.0.0"
type = "atomic"
description = "Flat TOML without agent section"
"""
        _write_toml(tmp_path, toml_content)
        manifest = load_manifest(tmp_path)
        assert manifest.name == "flat-agent"
