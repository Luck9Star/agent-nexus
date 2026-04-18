"""Unit tests for the platform local module.

Covers LockfileManager, SourceManager, GitInstaller, AgentSupervisor, and CLI
using temp directories, mocked subprocess calls, mocked managers, and Typer's
CliRunner.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from agent_nexus.models.agent import AgentType
from agent_nexus.models.config import ModelConfig, PlatformConfig
from agent_nexus.models.distribution import IndexEntry, Lockfile, LockfileEntry, SourceEntry

from agent_nexus.platform.local.cli import app
from agent_nexus.platform.local.installer import AgentNotFoundError, GitInstaller, InstallationError
from agent_nexus.platform.local.lockfile import LockfileManager
from agent_nexus.platform.local.sources import SourceManager
from agent_nexus.platform.local.supervisor import AgentSupervisor, RestartTracker


# ============================================================================
# Helpers
# ============================================================================


def _make_entry(
    version: str = "1.0.0",
    source: str = "official",
    commit_sha: str = "abc123def456",
    agent_type: AgentType = AgentType.ATOMIC,
    venv_path: str = "",
    dependencies: list[str] | None = None,
) -> LockfileEntry:
    """Create a LockfileEntry with sensible defaults."""
    return LockfileEntry(
        version=version,
        source=source,
        commit_sha=commit_sha,
        agent_type=agent_type,
        installed_at=datetime(2026, 1, 15, 12, 0, 0),
        venv_path=venv_path,
        dependencies=dependencies or [],
    )


def _write_json(path: Path, data: dict) -> None:
    """Write JSON data to a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_yaml(path: Path, data: dict) -> None:
    """Write YAML data to a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")


runner = CliRunner()


# ============================================================================
# LockfileManager Tests
# ============================================================================


class TestLockfileManager:
    """Tests for LockfileManager: load, save, get/add/remove entry, list."""

    def test_load_missing_file_returns_empty_lockfile(self, tmp_path: Path) -> None:
        """When lockfile.json does not exist, load() returns empty Lockfile."""
        mgr = LockfileManager(tmp_path / "lockfile.json")
        lf = mgr.load()
        assert isinstance(lf, Lockfile)
        assert lf.agents == {}
        assert lf.version == 1

    def test_load_unparseable_file_returns_empty(self, tmp_path: Path) -> None:
        """When lockfile.json contains invalid JSON, load() returns empty Lockfile."""
        bad = tmp_path / "lockfile.json"
        bad.write_text("{not valid json!!!", encoding="utf-8")
        mgr = LockfileManager(bad)
        lf = mgr.load()
        assert lf.agents == {}

    def test_load_valid_file(self, tmp_path: Path) -> None:
        """load() parses a valid lockfile with agents."""
        path = tmp_path / "lockfile.json"
        data = {
            "version": 1,
            "agents": {
                "doc-filler": {
                    "version": "1.2.0",
                    "source": "official",
                    "commit_sha": "abc123",
                    "agent_type": "atomic",
                    "installed_at": "2026-01-15T12:00:00",
                    "venv_path": "/venvs/doc-filler",
                    "dependencies": ["pydantic"],
                }
            },
        }
        _write_json(path, data)
        mgr = LockfileManager(path)
        lf = mgr.load()
        assert "doc-filler" in lf.agents
        assert lf.agents["doc-filler"].version == "1.2.0"
        assert lf.agents["doc-filler"].agent_type == AgentType.ATOMIC
        assert lf.agents["doc-filler"].dependencies == ["pydantic"]

    def test_save_creates_valid_json_file(self, tmp_path: Path) -> None:
        """save() writes valid JSON that can be re-loaded."""
        path = tmp_path / "lockfile.json"
        mgr = LockfileManager(path)
        entry = _make_entry()
        lf = Lockfile(agents={"my-agent": entry})
        mgr.save(lf)

        assert path.exists()
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["agents"]["my-agent"]["version"] == "1.0.0"

    def test_save_atomic_write(self, tmp_path: Path) -> None:
        """save() uses atomic write — file content is valid JSON after save."""
        path = tmp_path / "lockfile.json"
        mgr = LockfileManager(path)
        mgr.save(Lockfile())
        # File should be parseable JSON
        content = path.read_text(encoding="utf-8")
        parsed = json.loads(content)
        assert isinstance(parsed, dict)

    def test_save_creates_parent_directory(self, tmp_path: Path) -> None:
        """save() creates parent directories if they don't exist."""
        path = tmp_path / "deep" / "nested" / "lockfile.json"
        mgr = LockfileManager(path)
        mgr.save(Lockfile())
        assert path.exists()

    def test_get_entry_existing(self, tmp_path: Path) -> None:
        """get_entry() returns the entry when agent is in lockfile."""
        path = tmp_path / "lockfile.json"
        entry = _make_entry(version="2.0.0")
        mgr = LockfileManager(path)
        mgr.save(Lockfile(agents={"test-agent": entry}))
        result = mgr.get_entry("test-agent")
        assert result is not None
        assert result.version == "2.0.0"

    def test_get_entry_missing(self, tmp_path: Path) -> None:
        """get_entry() returns None when agent is not in lockfile."""
        mgr = LockfileManager(tmp_path / "lockfile.json")
        assert mgr.get_entry("no-such-agent") is None

    def test_add_entry_raises_not_implemented(self, tmp_path: Path) -> None:
        """add_entry() raises NotImplementedError (legacy method)."""
        mgr = LockfileManager(tmp_path / "lockfile.json")
        entry = _make_entry()
        with pytest.raises(NotImplementedError):
            mgr.add_entry(entry)

    def test_add_entry_by_name_creates_new(self, tmp_path: Path) -> None:
        """add_entry_by_name() adds a new entry and persists it."""
        path = tmp_path / "lockfile.json"
        mgr = LockfileManager(path)
        entry = _make_entry(version="3.0.0")
        mgr.add_entry_by_name("new-agent", entry)
        assert mgr.get_entry("new-agent") is not None
        assert mgr.get_entry("new-agent").version == "3.0.0"

    def test_add_entry_by_name_updates_existing(self, tmp_path: Path) -> None:
        """add_entry_by_name() overwrites an existing entry."""
        path = tmp_path / "lockfile.json"
        mgr = LockfileManager(path)
        mgr.add_entry_by_name("agent-a", _make_entry(version="1.0.0"))
        mgr.add_entry_by_name("agent-a", _make_entry(version="2.0.0"))
        assert mgr.get_entry("agent-a").version == "2.0.0"

    def test_remove_entry_existing(self, tmp_path: Path) -> None:
        """remove_entry() returns True and removes the entry."""
        path = tmp_path / "lockfile.json"
        mgr = LockfileManager(path)
        mgr.add_entry_by_name("to-remove", _make_entry())
        assert mgr.remove_entry("to-remove") is True
        assert mgr.get_entry("to-remove") is None

    def test_remove_entry_missing(self, tmp_path: Path) -> None:
        """remove_entry() returns False when entry doesn't exist."""
        mgr = LockfileManager(tmp_path / "lockfile.json")
        assert mgr.remove_entry("ghost") is False

    def test_list_entries_empty(self, tmp_path: Path) -> None:
        """list_entries() returns empty list when no agents."""
        mgr = LockfileManager(tmp_path / "lockfile.json")
        assert mgr.list_entries() == []

    def test_list_entries_returns_all(self, tmp_path: Path) -> None:
        """list_entries() returns all entries in insertion order."""
        path = tmp_path / "lockfile.json"
        mgr = LockfileManager(path)
        mgr.add_entry_by_name("agent-1", _make_entry(version="1.0.0"))
        mgr.add_entry_by_name("agent-2", _make_entry(version="2.0.0"))
        entries = mgr.list_entries()
        assert len(entries) == 2
        assert entries[0].version == "1.0.0"
        assert entries[1].version == "2.0.0"

    def test_save_and_reload_roundtrip(self, tmp_path: Path) -> None:
        """Data survives a save → load roundtrip unchanged."""
        path = tmp_path / "lockfile.json"
        mgr = LockfileManager(path)
        entry = _make_entry(
            version="1.5.0",
            source="private",
            commit_sha="deadbeef" * 5,
            agent_type=AgentType.COMPOSITE,
            venv_path="/some/venv",
            dependencies=["pydantic>=2", "httpx"],
        )
        mgr.add_entry_by_name("complex-agent", entry)
        # Re-create manager to force re-read from disk
        mgr2 = LockfileManager(path)
        result = mgr2.get_entry("complex-agent")
        assert result is not None
        assert result.version == "1.5.0"
        assert result.source == "private"
        assert result.agent_type == AgentType.COMPOSITE
        assert result.venv_path == "/some/venv"
        assert result.dependencies == ["pydantic>=2", "httpx"]


# ============================================================================
# SourceManager Tests
# ============================================================================


class TestSourceManager:
    """Tests for SourceManager: load, add/remove source, list, resolve."""

    def test_init_creates_default_with_official(self, tmp_path: Path) -> None:
        """When sources.yaml is absent, SourceManager creates official source."""
        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)
        sources = mgr.list_sources()
        assert len(sources) == 1
        assert sources[0].name == "official"

    def test_init_with_empty_file_uses_defaults(self, tmp_path: Path) -> None:
        """Empty YAML file triggers default official source."""
        path = tmp_path / "sources.yaml"
        path.write_text("", encoding="utf-8")
        mgr = SourceManager(path)
        assert len(mgr.list_sources()) == 1
        assert mgr.list_sources()[0].name == "official"

    def test_init_with_invalid_yaml_uses_defaults(self, tmp_path: Path) -> None:
        """Unparseable YAML triggers default official source."""
        path = tmp_path / "sources.yaml"
        path.write_text("{{{{invalid yaml", encoding="utf-8")
        mgr = SourceManager(path)
        assert mgr.list_sources()[0].name == "official"

    def test_init_with_missing_sources_key(self, tmp_path: Path) -> None:
        """YAML file without 'sources' key triggers default."""
        path = tmp_path / "sources.yaml"
        _write_yaml(path, {"other_key": []})
        mgr = SourceManager(path)
        assert mgr.list_sources()[0].name == "official"

    def test_load_valid_sources_file(self, tmp_path: Path) -> None:
        """Valid sources.yaml with multiple sources loads correctly."""
        path = tmp_path / "sources.yaml"
        data = {
            "sources": [
                {"name": "official", "type": "git", "url": "https://example.com/official.git", "branch": "main"},
                {"name": "my-team", "type": "git", "url": "https://example.com/team.git", "branch": "dev"},
            ]
        }
        _write_yaml(path, data)
        mgr = SourceManager(path)
        sources = mgr.list_sources()
        assert len(sources) == 2

    def test_add_source_new(self, tmp_path: Path) -> None:
        """add_source() adds a new source and persists."""
        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)
        entry = SourceEntry(name="my-src", type="git", url="https://github.com/test/repo.git")
        mgr.add_source(entry)
        # Re-load from disk
        mgr2 = SourceManager(path)
        names = [s.name for s in mgr2.list_sources()]
        assert "my-src" in names

    def test_add_source_updates_existing(self, tmp_path: Path) -> None:
        """add_source() replaces an existing source with the same name."""
        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)
        mgr.add_source(SourceEntry(name="official", type="git", url="https://old-url.git"))
        mgr.add_source(SourceEntry(name="official", type="git", url="https://new-url.git"))
        sources = mgr.list_sources()
        official = [s for s in sources if s.name == "official"]
        assert len(official) == 1
        assert official[0].url == "https://new-url.git"

    def test_remove_source_existing(self, tmp_path: Path) -> None:
        """remove_source() returns True and removes the source."""
        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)
        mgr.add_source(SourceEntry(name="to-remove", type="git", url="https://x.com/r.git"))
        assert mgr.remove_source("to-remove") is True
        assert "to-remove" not in [s.name for s in mgr.list_sources()]

    def test_remove_source_missing(self, tmp_path: Path) -> None:
        """remove_source() returns False when source doesn't exist."""
        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)
        assert mgr.remove_source("nonexistent") is False

    def test_list_sources_official_first(self, tmp_path: Path) -> None:
        """list_sources() returns sources sorted with official first."""
        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)
        mgr.add_source(SourceEntry(name="zeta", type="git", url="https://z.com/r.git"))
        mgr.add_source(SourceEntry(name="alpha", type="git", url="https://a.com/r.git"))
        sources = mgr.list_sources()
        # official should always be first
        assert sources[0].name == "official"

    def test_save_creates_file_with_sources_key(self, tmp_path: Path) -> None:
        """save() writes YAML with top-level 'sources' key."""
        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)
        mgr.save()
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "sources" in raw

    def test_get_official_source_present(self, tmp_path: Path) -> None:
        """get_official_source() returns the official source entry."""
        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)
        official = mgr.get_official_source()
        assert official is not None
        assert official.name == "official"

    def test_get_official_source_absent(self, tmp_path: Path) -> None:
        """get_official_source() returns None when official is removed."""
        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)
        mgr.remove_source("official")
        assert mgr.get_official_source() is None

    def test_resolve_agent_source_found(self, tmp_path: Path) -> None:
        """resolve_agent_source() finds agent in source index."""
        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)

        # Create index cache for official source
        cache_dir = tmp_path / "cache" / "repos" / "official"
        cache_dir.mkdir(parents=True, exist_ok=True)
        index_data = {
            "agents": [
                {"name": "doc-filler", "version": "1.0.0", "type": "atomic", "description": "Fills docs"},
            ]
        }
        _write_yaml(cache_dir / "index.yaml", index_data)

        result = mgr.resolve_agent_source("doc-filler")
        assert result is not None
        source_entry, relative_path = result
        assert source_entry.name == "official"
        assert relative_path == "packages/doc-filler"

    def test_resolve_agent_source_not_found(self, tmp_path: Path) -> None:
        """resolve_agent_source() returns None when agent not in any index."""
        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)
        # No cache/index files exist
        assert mgr.resolve_agent_source("nonexistent-agent") is None

    def test_resolve_agent_source_index_missing_agents_key(self, tmp_path: Path) -> None:
        """resolve_agent_source() returns None when index has no 'agents' key."""
        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)
        cache_dir = tmp_path / "cache" / "repos" / "official"
        cache_dir.mkdir(parents=True, exist_ok=True)
        _write_yaml(cache_dir / "index.yaml", {"other": []})
        assert mgr.resolve_agent_source("any-agent") is None

    def test_source_priority_official_is_zero(self, tmp_path: Path) -> None:
        """_source_priority returns 0 for official sources."""
        official = SourceEntry(name="official", type="git", url="http://x.com")
        assert SourceManager._source_priority(official) == 0

    def test_source_priority_non_official_is_one(self, tmp_path: Path) -> None:
        """_source_priority returns 1 for non-official sources."""
        other = SourceEntry(name="private", type="git", url="http://x.com")
        assert SourceManager._source_priority(other) == 1


# ============================================================================
# GitInstaller Tests
# ============================================================================


class TestGitInstaller:
    """Tests for GitInstaller: install, uninstall, update, validation."""

    @pytest.fixture
    def installer_env(self, tmp_path: Path):
        """Set up a GitInstaller with mocked managers."""
        sources_path = tmp_path / "sources.yaml"
        lockfile_path = tmp_path / "lockfile.json"
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        source_mgr = SourceManager(sources_path)
        lockfile_mgr = LockfileManager(lockfile_path)
        installer = GitInstaller(source_mgr, lockfile_mgr, config_dir)
        return installer, source_mgr, lockfile_mgr, config_dir

    def test_url_to_source_name_basic(self) -> None:
        """_url_to_source_name extracts last path component."""
        from agent_nexus.platform.local.installer import _url_to_source_name
        assert _url_to_source_name("https://github.com/user/my-repo.git") == "my-repo"

    def test_url_to_source_name_without_git_suffix(self) -> None:
        """_url_to_source_name strips .git suffix."""
        from agent_nexus.platform.local.installer import _url_to_source_name
        assert _url_to_source_name("https://github.com/user/my-repo") == "my-repo"

    def test_url_to_source_name_trailing_slash(self) -> None:
        """_url_to_source_name handles trailing slash."""
        from agent_nexus.platform.local.installer import _url_to_source_name
        assert _url_to_source_name("https://github.com/user/my-repo/") == "my-repo"

    def test_url_to_source_name_empty_returns_direct(self) -> None:
        """_url_to_source_name returns 'direct' for empty string edge case."""
        from agent_nexus.platform.local.installer import _url_to_source_name
        # rsplit on empty gives empty
        assert _url_to_source_name("") == "direct"

    def test_validate_agent_package_valid(self, tmp_path: Path) -> None:
        """_validate_agent_package returns empty list for valid package."""
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            tmp_path,
        )
        # Create valid package
        manifest = {"name": "test-agent", "version": "1.0.0", "type": "atomic"}
        _write_yaml(tmp_path / "pkg" / "agent-manifest.yaml", manifest)
        (tmp_path / "pkg" / "SKILL.md").write_text("# Test Agent", encoding="utf-8")
        issues = installer._validate_agent_package(tmp_path / "pkg")
        assert issues == []

    def test_validate_agent_package_missing_manifest(self, tmp_path: Path) -> None:
        """_validate_agent_package reports missing agent-manifest.yaml."""
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            tmp_path,
        )
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        (pkg_dir / "SKILL.md").write_text("# Test", encoding="utf-8")
        issues = installer._validate_agent_package(pkg_dir)
        assert "Missing agent-manifest.yaml" in issues

    def test_validate_agent_package_missing_skill_md(self, tmp_path: Path) -> None:
        """_validate_agent_package reports missing SKILL.md."""
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            tmp_path,
        )
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        manifest = {"name": "a", "version": "1.0.0", "type": "atomic"}
        _write_yaml(pkg_dir / "agent-manifest.yaml", manifest)
        issues = installer._validate_agent_package(pkg_dir)
        assert "Missing SKILL.md" in issues

    def test_validate_agent_package_missing_required_fields(self, tmp_path: Path) -> None:
        """_validate_agent_package reports missing required fields in manifest."""
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            tmp_path,
        )
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        _write_yaml(pkg_dir / "agent-manifest.yaml", {"name": "a"})  # missing version, type
        (pkg_dir / "SKILL.md").write_text("# X", encoding="utf-8")
        issues = installer._validate_agent_package(pkg_dir)
        assert any("version" in i for i in issues)
        assert any("type" in i for i in issues)

    def test_validate_agent_package_invalid_type(self, tmp_path: Path) -> None:
        """_validate_agent_package reports invalid agent type."""
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            tmp_path,
        )
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        manifest = {"name": "a", "version": "1.0.0", "type": "hybrid"}
        _write_yaml(pkg_dir / "agent-manifest.yaml", manifest)
        (pkg_dir / "SKILL.md").write_text("# X", encoding="utf-8")
        issues = installer._validate_agent_package(pkg_dir)
        assert any("Invalid agent type" in i for i in issues)

    def test_validate_agent_package_non_mapping_manifest(self, tmp_path: Path) -> None:
        """_validate_agent_package reports when manifest is not a YAML mapping."""
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            tmp_path,
        )
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        (pkg_dir / "agent-manifest.yaml").write_text("- list\n- not\n- dict", encoding="utf-8")
        (pkg_dir / "SKILL.md").write_text("# X", encoding="utf-8")
        issues = installer._validate_agent_package(pkg_dir)
        assert any("not a valid mapping" in i for i in issues)

    def test_read_manifest_valid(self, tmp_path: Path) -> None:
        """_read_manifest returns dict for valid manifest."""
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            tmp_path,
        )
        manifest = {"name": "x", "version": "2.0.0", "type": "composite"}
        _write_yaml(tmp_path / "pkg" / "agent-manifest.yaml", manifest)
        result = installer._read_manifest(tmp_path / "pkg")
        assert result["name"] == "x"
        assert result["type"] == "composite"

    def test_read_manifest_missing_returns_empty(self, tmp_path: Path) -> None:
        """_read_manifest returns empty dict when manifest absent."""
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            tmp_path,
        )
        result = installer._read_manifest(tmp_path / "nonexistent")
        assert result == {}

    async def test_uninstall_agent_exists(self, tmp_path: Path) -> None:
        """uninstall() removes files, venv, and lockfile entry."""
        lockfile_path = tmp_path / "lockfile.json"
        sources_path = tmp_path / "sources.yaml"
        config_dir = tmp_path / "config"
        agents_dir = config_dir / "agents"
        venvs_dir = config_dir / "venvs"
        agents_dir.mkdir(parents=True)
        venvs_dir.mkdir(parents=True)

        lockfile_mgr = LockfileManager(lockfile_path)
        source_mgr = SourceManager(sources_path)

        # Simulate installed agent
        agent_dir = agents_dir / "my-agent"
        agent_dir.mkdir()
        (agent_dir / "main.py").write_text("print('hi')", encoding="utf-8")
        venv_path = venvs_dir / "my-agent"
        venv_path.mkdir()

        lockfile_mgr.add_entry_by_name("my-agent", _make_entry(venv_path=str(venv_path)))

        installer = GitInstaller(source_mgr, lockfile_mgr, config_dir)
        result = await installer.uninstall("my-agent")

        assert result is True
        assert not agent_dir.exists()
        assert not venv_path.exists()
        assert lockfile_mgr.get_entry("my-agent") is None

    async def test_uninstall_agent_not_installed(self, tmp_path: Path) -> None:
        """uninstall() returns False when agent is not installed."""
        lockfile_path = tmp_path / "lockfile.json"
        lockfile_mgr = LockfileManager(lockfile_path)
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            lockfile_mgr,
            tmp_path / "config",
        )
        result = await installer.uninstall("no-such-agent")
        assert result is False

    async def test_update_raises_not_installed(self, tmp_path: Path) -> None:
        """update() raises AgentNotFoundError for uninstalled agent."""
        lockfile_mgr = LockfileManager(tmp_path / "lockfile.json")
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            lockfile_mgr,
            tmp_path / "config",
        )
        with pytest.raises(AgentNotFoundError):
            await installer.update("no-such-agent")

    def test_get_installed_version_present(self, tmp_path: Path) -> None:
        """get_installed_version() returns version for installed agent."""
        lockfile_path = tmp_path / "lockfile.json"
        lockfile_mgr = LockfileManager(lockfile_path)
        lockfile_mgr.add_entry_by_name("agent-x", _make_entry(version="4.2.0"))
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            lockfile_mgr,
            tmp_path / "config",
        )
        assert installer.get_installed_version("agent-x") == "4.2.0"

    def test_get_installed_version_absent(self, tmp_path: Path) -> None:
        """get_installed_version() returns None for missing agent."""
        lockfile_mgr = LockfileManager(tmp_path / "lockfile.json")
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            lockfile_mgr,
            tmp_path / "config",
        )
        assert installer.get_installed_version("ghost") is None

    async def test_install_agent_not_found(self, tmp_path: Path) -> None:
        """install() raises AgentNotFoundError when agent not in any source."""
        lockfile_path = tmp_path / "lockfile.json"
        sources_path = tmp_path / "sources.yaml"
        lockfile_mgr = LockfileManager(lockfile_path)
        source_mgr = SourceManager(sources_path)
        installer = GitInstaller(source_mgr, lockfile_mgr, tmp_path / "config")
        with pytest.raises(AgentNotFoundError, match="not found in any configured source"):
            await installer.install("nonexistent-agent")

    def test_get_cache_path_deterministic(self, tmp_path: Path) -> None:
        """_get_cache_path returns the same path for the same URL."""
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            tmp_path / "config",
        )
        url = "https://github.com/user/repo.git"
        path1 = installer._get_cache_path(url)
        path2 = installer._get_cache_path(url)
        assert path1 == path2

    def test_get_cache_path_different_urls(self, tmp_path: Path) -> None:
        """_get_cache_path returns different paths for different URLs."""
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            tmp_path / "config",
        )
        path1 = installer._get_cache_path("https://github.com/user/repo-a.git")
        path2 = installer._get_cache_path("https://github.com/user/repo-b.git")
        assert path1 != path2


# ============================================================================
# RestartTracker Tests
# ============================================================================


class TestRestartTracker:
    """Tests for RestartTracker: should_retry, record, reset."""

    def test_initial_state_allows_retry(self) -> None:
        """Fresh tracker allows retries (count=0 < max=3)."""
        tracker = RestartTracker()
        assert tracker.should_retry() is True

    def test_record_increments_count(self) -> None:
        """record() increments the restart counter."""
        tracker = RestartTracker()
        tracker.record()
        assert tracker.count == 1
        tracker.record()
        assert tracker.count == 2

    def test_should_retry_false_at_max(self) -> None:
        """should_retry() returns False after max_restarts reached."""
        tracker = RestartTracker(max_restarts=2)
        tracker.record()
        tracker.record()
        assert tracker.should_retry() is False

    def test_should_retry_true_below_max(self) -> None:
        """should_retry() returns True below max_restarts."""
        tracker = RestartTracker(max_restarts=3)
        tracker.record()
        assert tracker.count == 1
        assert tracker.should_retry() is True
        tracker.record()
        assert tracker.count == 2
        assert tracker.should_retry() is True

    def test_reset_clears_count(self) -> None:
        """reset() sets count back to 0."""
        tracker = RestartTracker()
        tracker.record()
        tracker.record()
        tracker.reset()
        assert tracker.count == 0
        assert tracker.should_retry() is True

    def test_custom_max_restarts(self) -> None:
        """RestartTracker respects custom max_restarts value."""
        tracker = RestartTracker(max_restarts=1)
        tracker.record()
        assert tracker.should_retry() is False

    def test_default_max_restarts(self) -> None:
        """Default max_restarts is 3."""
        tracker = RestartTracker()
        assert tracker.max_restarts == 3


# ============================================================================
# AgentSupervisor Tests
# ============================================================================


def _make_mock_pm() -> MagicMock:
    """Create a mock ProcessManager."""
    pm = MagicMock(spec=["start_agent", "stop_agent", "stop_all", "get_agent", "list_running", "health_check"])
    pm.start_agent = AsyncMock()
    pm.stop_agent = AsyncMock()
    pm.stop_all = AsyncMock()
    pm.get_agent = MagicMock(return_value=None)
    pm.list_running = MagicMock(return_value=[])
    pm.health_check = AsyncMock(return_value=True)
    return pm


def _make_mock_lockfile_mgr(agents: dict[str, LockfileEntry] | None = None) -> MagicMock:
    """Create a mock LockfileManager."""
    mgr = MagicMock(spec=LockfileManager)
    lf = Lockfile(agents=agents or {})
    mgr.load = MagicMock(return_value=lf)
    mgr.get_entry = MagicMock(return_value=None)
    if agents:
        def get_entry_fn(name):
            return agents.get(name)
        mgr.get_entry = MagicMock(side_effect=get_entry_fn)
    return mgr


def _make_mock_config_loader() -> MagicMock:
    """Create a mock ConfigLoader."""
    loader = MagicMock()
    loader.config_dir = Path("/tmp/.agent-nexus")
    config = PlatformConfig(models=ModelConfig(default="openai:gpt-4o"))
    loader.load_config = MagicMock(return_value=config)
    return loader


class TestAgentSupervisor:
    """Tests for AgentSupervisor: start/stop agents, health check, auto-restart."""

    async def test_start_all_empty_lockfile(self, tmp_path: Path) -> None:
        """start_all() returns empty list when no agents installed."""
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        started = await supervisor.start_all()
        assert started == []

    async def test_start_all_starts_agents(self, tmp_path: Path) -> None:
        """start_all() starts all agents in lockfile."""
        entry = _make_entry(venv_path="")
        pm = _make_mock_pm()
        mock_handle = MagicMock()
        mock_handle.pid = 42
        pm.start_agent = AsyncMock(return_value=mock_handle)
        lockfile = _make_mock_lockfile_mgr({"agent-1": entry})
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        started = await supervisor.start_all()
        assert "agent-1" in started

    async def test_start_agent_not_installed(self, tmp_path: Path) -> None:
        """start_agent() returns False when agent not in lockfile."""
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        assert await supervisor.start_agent("ghost") is False

    async def test_start_agent_success(self, tmp_path: Path) -> None:
        """start_agent() returns True when agent starts successfully."""
        entry = _make_entry(venv_path="")
        pm = _make_mock_pm()
        mock_handle = MagicMock()
        mock_handle.pid = 99
        pm.start_agent = AsyncMock(return_value=mock_handle)
        lockfile = _make_mock_lockfile_mgr({"test-agent": entry})
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        assert await supervisor.start_agent("test-agent") is True
        pm.start_agent.assert_called_once()

    async def test_start_agent_failure(self, tmp_path: Path) -> None:
        """start_agent() returns False when ProcessManager raises."""
        entry = _make_entry(venv_path="")
        pm = _make_mock_pm()
        pm.start_agent = AsyncMock(side_effect=RuntimeError("spawn failed"))
        lockfile = _make_mock_lockfile_mgr({"bad-agent": entry})
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        assert await supervisor.start_agent("bad-agent") is False

    async def test_stop_agent_running(self, tmp_path: Path) -> None:
        """stop_agent() returns True when agent is alive and stopped."""
        pm = _make_mock_pm()
        mock_handle = MagicMock()
        mock_handle.is_alive = True
        pm.get_agent = MagicMock(return_value=mock_handle)
        pm.stop_agent = AsyncMock()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        assert await supervisor.stop_agent("running-agent") is True

    async def test_stop_agent_not_running(self, tmp_path: Path) -> None:
        """stop_agent() returns False when agent is not alive."""
        pm = _make_mock_pm()
        mock_handle = MagicMock()
        mock_handle.is_alive = False
        pm.get_agent = MagicMock(return_value=mock_handle)
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        assert await supervisor.stop_agent("dead-agent") is False

    async def test_stop_agent_no_handle(self, tmp_path: Path) -> None:
        """stop_agent() returns False when agent has no handle."""
        pm = _make_mock_pm()
        pm.get_agent = MagicMock(return_value=None)
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        assert await supervisor.stop_agent("no-handle") is False

    async def test_stop_agent_key_error(self, tmp_path: Path) -> None:
        """stop_agent() returns False when PM raises KeyError."""
        pm = _make_mock_pm()
        mock_handle = MagicMock()
        mock_handle.is_alive = True
        pm.get_agent = MagicMock(return_value=mock_handle)
        pm.stop_agent = AsyncMock(side_effect=KeyError("not found"))
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        assert await supervisor.stop_agent("missing-agent") is False

    async def test_stop_all(self, tmp_path: Path) -> None:
        """stop_all() delegates to ProcessManager.stop_all()."""
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        await supervisor.stop_all()
        pm.stop_all.assert_called_once()

    async def test_health_check_all(self, tmp_path: Path) -> None:
        """health_check_all() returns health status for running agents."""
        pm = _make_mock_pm()
        pm.list_running = MagicMock(return_value=["agent-a", "agent-b"])
        pm.health_check = AsyncMock(return_value=True)
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        results = await supervisor.health_check_all()
        assert results == {"agent-a": True, "agent-b": True}

    async def test_health_check_all_with_dead(self, tmp_path: Path) -> None:
        """health_check_all() returns False for agents that fail health check."""
        pm = _make_mock_pm()
        pm.list_running = MagicMock(return_value=["agent-a", "agent-b"])
        pm.health_check = AsyncMock(side_effect=[True, KeyError("not found")])
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        results = await supervisor.health_check_all()
        assert results["agent-a"] is True
        assert results["agent-b"] is False

    async def test_auto_restart_dead_restarts_dead_agents(self, tmp_path: Path) -> None:
        """auto_restart_dead() restarts agents that are no longer alive."""
        entry = _make_entry(venv_path="")
        pm = _make_mock_pm()
        pm.get_agent = MagicMock(return_value=None)  # no handle = dead
        mock_handle = MagicMock()
        mock_handle.pid = 77
        pm.start_agent = AsyncMock(return_value=mock_handle)
        lockfile = _make_mock_lockfile_mgr({"agent-1": entry})
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path, max_restarts=3)
        supervisor._started_agents.add("agent-1")
        restarted = await supervisor.auto_restart_dead()
        assert "agent-1" in restarted

    async def test_auto_restart_dead_skips_alive_agents(self, tmp_path: Path) -> None:
        """auto_restart_dead() skips agents that are still alive."""
        entry = _make_entry(venv_path="")
        pm = _make_mock_pm()
        mock_handle = MagicMock()
        mock_handle.is_alive = True
        pm.get_agent = MagicMock(return_value=mock_handle)
        lockfile = _make_mock_lockfile_mgr({"agent-1": entry})
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        restarted = await supervisor.auto_restart_dead()
        assert restarted == []

    async def test_auto_restart_dead_respects_max_restarts(self, tmp_path: Path) -> None:
        """auto_restart_dead() stops restarting after max_restarts exceeded."""
        entry = _make_entry(venv_path="")
        pm = _make_mock_pm()
        pm.get_agent = MagicMock(return_value=None)
        # Make start_agent fail so tracker does NOT reset on success
        pm.start_agent = AsyncMock(side_effect=RuntimeError("spawn failed"))
        lockfile = _make_mock_lockfile_mgr({"agent-1": entry})
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path, max_restarts=1)
        supervisor._started_agents.add("agent-1")

        # First attempt: records count=1, start fails -> not in restarted
        r1 = await supervisor.auto_restart_dead()
        assert "agent-1" not in r1  # start_agent failed

        # Tracker now at count=1, max=1. Second call: should_retry()=False
        r2 = await supervisor.auto_restart_dead()
        assert "agent-1" not in r2

    def test_list_running(self, tmp_path: Path) -> None:
        """list_running() delegates to ProcessManager."""
        pm = _make_mock_pm()
        pm.list_running = MagicMock(return_value=["a", "b"])
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        assert supervisor.list_running() == ["a", "b"]

    def test_list_installed(self, tmp_path: Path) -> None:
        """list_installed() returns agent names from lockfile."""
        entry = _make_entry()
        lockfile = _make_mock_lockfile_mgr({"agent-x": entry})
        pm = _make_mock_pm()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        assert supervisor.list_installed() == ["agent-x"]

    def test_build_command_venv_strategy(self, tmp_path: Path) -> None:
        """_build_command uses venv python when venv_path exists."""
        entry = _make_entry(venv_path=str(tmp_path / "venv"))
        # Create venv bin/python
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").touch()
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        cmd = supervisor._build_command("test-agent", entry)
        assert cmd is not None
        assert str(tmp_path / "venv" / "bin" / "python") in cmd[0]

    def test_build_command_uvx_fallback(self, tmp_path: Path) -> None:
        """_build_command falls back to uvx when no venv."""
        entry = _make_entry(venv_path="")
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        cmd = supervisor._build_command("my-agent", entry)
        assert cmd == ["uvx", "my-agent"]

    def test_resolve_agent_dir(self, tmp_path: Path) -> None:
        """_resolve_agent_dir returns config_dir/agents/agent_name."""
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        result = supervisor._resolve_agent_dir("my-agent")
        assert result == tmp_path / "agents" / "my-agent"

    def test_build_env_includes_agent_model(self, tmp_path: Path) -> None:
        """_build_env includes AGENT_MODEL from config."""
        entry = _make_entry()
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        env = supervisor._build_env("test-agent", entry)
        assert env["AGENT_MODEL"] == "openai:gpt-4o"

    def test_build_env_handles_config_failure(self, tmp_path: Path) -> None:
        """_build_env returns empty dict when config loading fails."""
        entry = _make_entry()
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        config.load_config = MagicMock(side_effect=RuntimeError("config broken"))
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        env = supervisor._build_env("test-agent", entry)
        assert "AGENT_MODEL" not in env


# ============================================================================
# CLI Tests
# ============================================================================


class TestCLI:
    """Tests for the Typer CLI commands using CliRunner."""

    @patch("agent_nexus.platform.local.cli._install", new_callable=AsyncMock)
    def test_install_agent_success(self, mock_install) -> None:
        """install command invokes _install with the agent name."""
        result = runner.invoke(app, ["install", "doc-filler"])
        assert mock_install.called
        call_args = mock_install.call_args[0]
        assert call_args[0] == "doc-filler"

    def test_list_agents_empty(self) -> None:
        """list command shows 'No agents installed' when lockfile is empty."""
        mock_lockfile = MagicMock(spec=LockfileManager)
        mock_lockfile.load.return_value = Lockfile()
        mock_sources = MagicMock(spec=SourceManager)
        mock_loader = MagicMock()

        with patch(
            "agent_nexus.platform.local.cli._init_managers",
            return_value=(mock_loader, mock_lockfile, mock_sources, Path("/tmp/cfg")),
        ):
            result = runner.invoke(app, ["list"])
            assert "No agents installed" in result.output

    def test_list_agents_with_entries(self) -> None:
        """list command shows agent table when agents are installed."""
        entry = _make_entry(version="2.0.0", source="official")
        mock_lockfile = MagicMock(spec=LockfileManager)
        mock_lockfile.load.return_value = Lockfile(agents={"doc-filler": entry})
        mock_sources = MagicMock(spec=SourceManager)
        mock_loader = MagicMock()

        with patch(
            "agent_nexus.platform.local.cli._init_managers",
            return_value=(mock_loader, mock_lockfile, mock_sources, Path("/tmp/cfg")),
        ):
            result = runner.invoke(app, ["list"])
            assert "doc-filler" in result.output
            assert "2.0.0" in result.output
            assert "1 agent(s) installed" in result.output

    def test_update_no_args_shows_error(self) -> None:
        """update command with no args and no --all shows error and exits with code 1."""
        result = runner.invoke(app, ["install", "update"])
        # When no name arg given and --all=False, the callback prints message + exit(1)
        # Typer may surface this as exit code 1
        assert result.exit_code != 0

    def test_info_agent_not_installed(self) -> None:
        """info command shows error for uninstalled agent."""
        mock_lockfile = MagicMock(spec=LockfileManager)
        mock_lockfile.get_entry.return_value = None
        mock_sources = MagicMock(spec=SourceManager)
        mock_loader = MagicMock()

        with patch(
            "agent_nexus.platform.local.cli._init_managers",
            return_value=(mock_loader, mock_lockfile, mock_sources, Path("/tmp/cfg")),
        ):
            result = runner.invoke(app, ["info", "nonexistent"])
            assert "not installed" in result.output
            assert result.exit_code == 1

    def test_info_agent_installed(self) -> None:
        """info command shows agent details when installed."""
        entry = _make_entry(version="1.2.0", commit_sha="a" * 40)
        mock_lockfile = MagicMock(spec=LockfileManager)
        mock_lockfile.get_entry.return_value = entry
        mock_sources = MagicMock(spec=SourceManager)
        mock_loader = MagicMock()
        config_dir = Path("/tmp/cfg")

        with patch(
            "agent_nexus.platform.local.cli._init_managers",
            return_value=(mock_loader, mock_lockfile, mock_sources, config_dir),
        ):
            result = runner.invoke(app, ["info", "doc-filler"])
            assert "doc-filler" in result.output
            assert "1.2.0" in result.output

    def test_sources_list(self) -> None:
        """sources list command shows configured sources."""
        mock_lockfile = MagicMock(spec=LockfileManager)
        mock_sources = MagicMock(spec=SourceManager)
        mock_sources.list_sources.return_value = [
            SourceEntry(name="official", type="git", url="https://example.com/repo.git", branch="main"),
        ]
        mock_loader = MagicMock()

        with patch(
            "agent_nexus.platform.local.cli._init_managers",
            return_value=(mock_loader, mock_lockfile, mock_sources, Path("/tmp/cfg")),
        ):
            result = runner.invoke(app, ["sources", "list"])
            assert "official" in result.output
            assert "https://example.com/repo.git" in result.output

    def test_sources_add_missing_url(self) -> None:
        """sources add without --url shows error."""
        mock_lockfile = MagicMock(spec=LockfileManager)
        mock_sources = MagicMock(spec=SourceManager)
        mock_loader = MagicMock()

        with patch(
            "agent_nexus.platform.local.cli._init_managers",
            return_value=(mock_loader, mock_lockfile, mock_sources, Path("/tmp/cfg")),
        ):
            result = runner.invoke(app, ["sources", "add", "--name", "my-src"])
            assert result.exit_code == 1
            assert "required" in result.output.lower() or "url" in result.output.lower()

    def test_sources_add_success(self) -> None:
        """sources add with --name and --url adds the source."""
        mock_lockfile = MagicMock(spec=LockfileManager)
        mock_sources = MagicMock(spec=SourceManager)
        mock_loader = MagicMock()

        with patch(
            "agent_nexus.platform.local.cli._init_managers",
            return_value=(mock_loader, mock_lockfile, mock_sources, Path("/tmp/cfg")),
        ):
            result = runner.invoke(app, ["sources", "add", "--name", "my-src", "--url", "https://x.com/r.git"])
            assert "added" in result.output.lower()
            mock_sources.add_source.assert_called_once()

    def test_sources_remove_existing(self) -> None:
        """sources remove with existing source removes it."""
        mock_lockfile = MagicMock(spec=LockfileManager)
        mock_sources = MagicMock(spec=SourceManager)
        mock_sources.remove_source.return_value = True
        mock_loader = MagicMock()

        with patch(
            "agent_nexus.platform.local.cli._init_managers",
            return_value=(mock_loader, mock_lockfile, mock_sources, Path("/tmp/cfg")),
        ):
            result = runner.invoke(app, ["sources", "remove", "--name", "my-src"])
            assert "removed" in result.output.lower()

    def test_sources_remove_missing(self) -> None:
        """sources remove with nonexistent source shows not found."""
        mock_lockfile = MagicMock(spec=LockfileManager)
        mock_sources = MagicMock(spec=SourceManager)
        mock_sources.remove_source.return_value = False
        mock_loader = MagicMock()

        with patch(
            "agent_nexus.platform.local.cli._init_managers",
            return_value=(mock_loader, mock_lockfile, mock_sources, Path("/tmp/cfg")),
        ):
            result = runner.invoke(app, ["sources", "remove", "--name", "nope"])
            assert "not found" in result.output.lower()

    def test_sources_remove_missing_name(self) -> None:
        """sources remove without --name shows error."""
        mock_lockfile = MagicMock(spec=LockfileManager)
        mock_sources = MagicMock(spec=SourceManager)
        mock_loader = MagicMock()

        with patch(
            "agent_nexus.platform.local.cli._init_managers",
            return_value=(mock_loader, mock_lockfile, mock_sources, Path("/tmp/cfg")),
        ):
            result = runner.invoke(app, ["sources", "remove"])
            assert result.exit_code == 1

    def test_sources_unknown_action(self) -> None:
        """sources with unknown action shows error."""
        mock_lockfile = MagicMock(spec=LockfileManager)
        mock_sources = MagicMock(spec=SourceManager)
        mock_loader = MagicMock()

        with patch(
            "agent_nexus.platform.local.cli._init_managers",
            return_value=(mock_loader, mock_lockfile, mock_sources, Path("/tmp/cfg")),
        ):
            result = runner.invoke(app, ["sources", "explode"])
            assert "Unknown action" in result.output
            assert result.exit_code == 1

    def test_app_no_args_shows_help(self) -> None:
        """Running app with no args shows help (no_args_is_help=True)."""
        result = runner.invoke(app, [])
        # Typer/click returns exit code 0 or 2 for help display
        assert result.exit_code in (0, 2)
        assert "Usage" in result.output or "agent-nexus" in result.output.lower()

    def test_wait_forever_is_cancellable(self) -> None:
        """_wait_forever can be cancelled."""
        from agent_nexus.platform.local.cli import _wait_forever

        async def _run():
            task = asyncio.create_task(_wait_forever())
            await asyncio.sleep(0)  # let task start
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        asyncio.run(_run())  # should not raise

    def test_search_no_results(self) -> None:
        """search command with no matches shows 'No agents found'."""
        mock_lockfile = MagicMock(spec=LockfileManager)
        mock_sources = MagicMock(spec=SourceManager)
        mock_sources.list_sources.return_value = []
        mock_loader = MagicMock()

        with patch(
            "agent_nexus.platform.local.cli._init_managers",
            return_value=(mock_loader, mock_lockfile, mock_sources, Path("/tmp/cfg")),
        ):
            result = runner.invoke(app, ["search", "nonexistent"])
            assert "No agents found" in result.output

    def test_search_with_results(self) -> None:
        """search command shows matching agents."""
        mock_lockfile = MagicMock(spec=LockfileManager)
        mock_sources = MagicMock(spec=SourceManager)
        index_entry = IndexEntry(
            name="doc-filler",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="Fills documentation",
            tags=["docs"],
        )
        official = SourceEntry(name="official", type="git", url="https://example.com/r.git")
        mock_sources.list_sources.return_value = [official]
        mock_sources._load_source_index.return_value = [index_entry]
        mock_loader = MagicMock()

        with patch(
            "agent_nexus.platform.local.cli._init_managers",
            return_value=(mock_loader, mock_lockfile, mock_sources, Path("/tmp/cfg")),
        ):
            result = runner.invoke(app, ["search", "doc"])
            assert "doc-filler" in result.output
            assert "1 result(s)" in result.output

    def test_run_agent_not_installed(self) -> None:
        """run command shows error when agent is not installed."""
        mock_lockfile = MagicMock(spec=LockfileManager)
        mock_lockfile.get_entry.return_value = None
        mock_sources = MagicMock(spec=SourceManager)
        mock_loader = MagicMock()

        with patch(
            "agent_nexus.platform.local.cli._init_managers",
            return_value=(mock_loader, mock_lockfile, mock_sources, Path("/tmp/cfg")),
        ):
            result = runner.invoke(app, ["run", "ghost"])
            assert "not installed" in result.output
            assert result.exit_code == 1
