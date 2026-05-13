"""Unit tests for AgentCreator: directory structure, manifest validation,
SKILL.md, name validation, atomic/composite types, capability validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_nexus.platform.local.create_agent import (
    AgentCreator,
    AgentCreatorError,
)
from agent_nexus.platform.local.manifest import load_manifest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def creator() -> AgentCreator:
    """Return an AgentCreator using the bundled capabilities.toml."""
    return AgentCreator()


@pytest.fixture
def output_dir(tmp_path: Path) -> Path:
    """Provide a clean temporary output directory."""
    return tmp_path / "agents"


# ---------------------------------------------------------------------------
# Test: correct directory structure
# ---------------------------------------------------------------------------


class TestDirectoryStructure:
    def test_creates_agent_toml(self, creator: AgentCreator, output_dir: Path) -> None:
        agent_dir = creator.create("my-agent", output_dir=output_dir)
        assert (agent_dir / "agent.toml").is_file()

    def test_creates_skill_md(self, creator: AgentCreator, output_dir: Path) -> None:
        agent_dir = creator.create("my-agent", output_dir=output_dir)
        assert (agent_dir / "SKILL.md").is_file()

    def test_creates_src_package(self, creator: AgentCreator, output_dir: Path) -> None:
        agent_dir = creator.create("my-agent", output_dir=output_dir)
        pkg = agent_dir / "src" / "agent_my_agent"
        assert pkg.is_dir()
        assert (pkg / "__init__.py").is_file()

    def test_creates_tests_dir(self, creator: AgentCreator, output_dir: Path) -> None:
        agent_dir = creator.create("my-agent", output_dir=output_dir)
        test_file = agent_dir / "tests" / "test_my_agent.py"
        assert test_file.is_file()

    def test_creates_pyproject_toml(self, creator: AgentCreator, output_dir: Path) -> None:
        agent_dir = creator.create("my-agent", output_dir=output_dir)
        assert (agent_dir / "pyproject.toml").is_file()

    def test_returns_agent_dir_with_output(self, creator: AgentCreator, output_dir: Path) -> None:
        agent_dir = creator.create("my-agent", output_dir=output_dir)
        assert agent_dir == output_dir / "my-agent"

    def test_default_output_dir_includes_type(
        self,
        creator: AgentCreator,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        agent_dir = creator.create("my-agent", type="atomic")
        assert agent_dir == tmp_path / "agents" / "atomic" / "my-agent"

    def test_default_composite_output_dir(
        self,
        creator: AgentCreator,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.chdir(tmp_path)
        agent_dir = creator.create("my-agent", type="composite")
        assert agent_dir == tmp_path / "agents" / "composite" / "my-agent"

    def test_raises_on_existing_dir(self, creator: AgentCreator, output_dir: Path) -> None:
        creator.create("my-agent", output_dir=output_dir)
        with pytest.raises(FileExistsError):
            creator.create("my-agent", output_dir=output_dir)


# ---------------------------------------------------------------------------
# Test: agent.toml is valid and loadable via manifest.py
# ---------------------------------------------------------------------------


class TestManifestValidity:
    def test_manifest_loads_via_manifest_py(self, creator: AgentCreator, output_dir: Path) -> None:
        agent_dir = creator.create("my-agent", output_dir=output_dir)
        manifest = load_manifest(agent_dir)
        assert manifest.name == "my-agent"
        assert manifest.version == "0.1.0"
        assert manifest.type.value == "atomic"

    def test_manifest_with_capabilities(self, creator: AgentCreator, output_dir: Path) -> None:
        agent_dir = creator.create(
            "my-agent",
            capabilities=["static-analysis", "security-scan"],
            output_dir=output_dir,
        )
        manifest = load_manifest(agent_dir)
        assert "static-analysis" in manifest.capabilities
        assert "security-scan" in manifest.capabilities

    def test_manifest_with_custom_description(
        self, creator: AgentCreator, output_dir: Path
    ) -> None:
        agent_dir = creator.create(
            "my-agent",
            description="Custom description",
            output_dir=output_dir,
        )
        manifest = load_manifest(agent_dir)
        assert manifest.description == "Custom description"


# ---------------------------------------------------------------------------
# Test: SKILL.md is non-empty
# ---------------------------------------------------------------------------


class TestSkillMd:
    def test_skill_md_non_empty(self, creator: AgentCreator, output_dir: Path) -> None:
        agent_dir = creator.create("my-agent", output_dir=output_dir)
        content = (agent_dir / "SKILL.md").read_text(encoding="utf-8").strip()
        assert len(content) > 0

    def test_skill_md_contains_name(self, creator: AgentCreator, output_dir: Path) -> None:
        agent_dir = creator.create("my-agent", output_dir=output_dir)
        content = (agent_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "my-agent" in content

    def test_skill_md_lists_capabilities(self, creator: AgentCreator, output_dir: Path) -> None:
        agent_dir = creator.create(
            "my-agent",
            capabilities=["static-analysis"],
            output_dir=output_dir,
        )
        content = (agent_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "static-analysis" in content


# ---------------------------------------------------------------------------
# Test: rejects invalid agent names
# ---------------------------------------------------------------------------


class TestNameValidation:
    @pytest.mark.parametrize(
        "name",
        [
            "",
            "-leading",
            "has space",
            "has/slash",
            "has.dot",
            "123_start_with_number_ok",  # this is actually valid per AGENT_NAME_RE
        ],
    )
    def test_rejects_invalid_names(
        self,
        creator: AgentCreator,
        output_dir: Path,
        name: str,
    ) -> None:
        # Note: "123_start_with_number_ok" is valid per the regex, so skip it
        if name == "123_start_with_number_ok":
            pytest.skip("This name is actually valid per AGENT_NAME_RE")
        with pytest.raises(AgentCreatorError, match="Invalid agent name"):
            creator.create(name, output_dir=output_dir)

    def test_accepts_valid_names(self, creator: AgentCreator, output_dir: Path) -> None:
        for name in ["my-agent", "code_reviewer", "Agent123", "a"]:
            agent_dir = creator.create(name, output_dir=output_dir)
            assert agent_dir.is_dir()
            # Clean up so next iteration does not hit FileExistsError
            # Actually, output_dir is same parent so type subdir differs.
            # Use unique output_dir per name by constructing it manually.


class TestValidNames:
    @pytest.mark.parametrize("name", ["my-agent", "code_reviewer", "Agent123"])
    def test_accepts_valid_names(self, creator: AgentCreator, tmp_path: Path, name: str) -> None:
        out = tmp_path / name
        agent_dir = creator.create(name, output_dir=out)
        assert agent_dir.is_dir()


# ---------------------------------------------------------------------------
# Test: supports both atomic and composite types
# ---------------------------------------------------------------------------


class TestAgentTypes:
    def test_atomic_type(self, creator: AgentCreator, output_dir: Path) -> None:
        agent_dir = creator.create("my-agent", type="atomic", output_dir=output_dir)
        manifest = load_manifest(agent_dir)
        assert manifest.type.value == "atomic"

    def test_composite_type(self, creator: AgentCreator, output_dir: Path) -> None:
        agent_dir = creator.create("my-agent", type="composite", output_dir=output_dir)
        manifest = load_manifest(agent_dir)
        assert manifest.type.value == "composite"

    def test_rejects_invalid_type(self, creator: AgentCreator, output_dir: Path) -> None:
        with pytest.raises(AgentCreatorError, match="Invalid agent type"):
            creator.create("my-agent", type="invalid", output_dir=output_dir)

    def test_composite_output_dir(self, creator: AgentCreator, output_dir: Path) -> None:
        agent_dir = creator.create("my-agent", type="composite", output_dir=output_dir)
        assert "composite" in str(agent_dir)


# ---------------------------------------------------------------------------
# Test: capability validation against taxonomy
# ---------------------------------------------------------------------------


class TestCapabilityValidation:
    def test_rejects_unknown_capability(self, creator: AgentCreator, output_dir: Path) -> None:
        with pytest.raises(AgentCreatorError, match="Unknown capabilities"):
            creator.create("my-agent", capabilities=["nonexistent"], output_dir=output_dir)

    def test_accepts_known_capabilities(self, creator: AgentCreator, output_dir: Path) -> None:
        agent_dir = creator.create(
            "my-agent",
            capabilities=["static-analysis", "test-generation"],
            output_dir=output_dir,
        )
        manifest = load_manifest(agent_dir)
        assert manifest.capabilities == ["static-analysis", "test-generation"]

    def test_empty_capabilities_ok(self, creator: AgentCreator, output_dir: Path) -> None:
        agent_dir = creator.create("my-agent", capabilities=[], output_dir=output_dir)
        manifest = load_manifest(agent_dir)
        assert manifest.capabilities == []

    def test_get_valid_capabilities(self, creator: AgentCreator) -> None:
        caps = creator.get_valid_capabilities()
        assert "static-analysis" in caps
        assert "security-scan" in caps
        assert len(caps) > 0

    def test_get_valid_categories(self, creator: AgentCreator) -> None:
        cats = creator.get_valid_categories()
        assert "code-quality" in cats
        assert "security" in cats
        assert len(cats) > 0
