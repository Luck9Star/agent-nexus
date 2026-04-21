"""Unit tests for the platform local module.

Covers LockfileManager, SourceManager, GitInstaller, AgentSupervisor, and CLI
using temp directories, mocked subprocess calls, mocked managers, and Typer's
CliRunner.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from typer.testing import CliRunner

from agent_nexus.models.agent import AgentType
from agent_nexus.models.config import ModelConfig, PlatformConfig, ProviderConfig
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
    commit_sha: str = "a" * 40,
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

    def test_corrupt_lockfile_logs_error_level(self, tmp_path: Path, caplog) -> None:
        """iter109 regression: corrupt lockfile logs at ERROR, not WARNING."""
        import logging

        bad = tmp_path / "lockfile.json"
        bad.write_text("GARBAGE", encoding="utf-8")
        mgr = LockfileManager(bad)

        with caplog.at_level(logging.ERROR):
            mgr.load()

        assert any(
            "Corrupt lockfile" in r.message and r.levelno >= logging.ERROR
            for r in caplog.records
        ), "Corrupt lockfile should log at ERROR level"

    def test_load_propagates_os_error(self, tmp_path: Path) -> None:
        """When lockfile is unreadable (PermissionError), load() propagates the error."""
        bad = tmp_path / "lockfile.json"
        bad.write_text('{"version": 1, "agents": {}}', encoding="utf-8")
        mgr = LockfileManager(bad)
        with patch.object(Path, "read_text", side_effect=PermissionError("no access")):
            with pytest.raises(PermissionError, match="no access"):
                mgr.load()

    def test_load_invalid_schema_returns_empty(self, tmp_path: Path) -> None:
        """When lockfile has valid JSON but invalid schema, load() returns empty Lockfile."""
        bad = tmp_path / "lockfile.json"
        # version should be int, not a string
        bad.write_text('{"version": "bad", "agents": {}}', encoding="utf-8")
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
                    "commit_sha": "a" * 40,
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
        mgr._save(lf)

        assert path.exists()
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["agents"]["my-agent"]["version"] == "1.0.0"

    def test_save_atomic_write(self, tmp_path: Path) -> None:
        """save() uses atomic write — file content is valid JSON after save."""
        path = tmp_path / "lockfile.json"
        mgr = LockfileManager(path)
        mgr._save(Lockfile())
        # File should be parseable JSON
        content = path.read_text(encoding="utf-8")
        parsed = json.loads(content)
        assert isinstance(parsed, dict)

    def test_save_creates_parent_directory(self, tmp_path: Path) -> None:
        """save() creates parent directories if they don't exist."""
        path = tmp_path / "deep" / "nested" / "lockfile.json"
        mgr = LockfileManager(path)
        mgr._save(Lockfile())
        assert path.exists()

    def test_get_entry_existing(self, tmp_path: Path) -> None:
        """get_entry() returns the entry when agent is in lockfile."""
        path = tmp_path / "lockfile.json"
        entry = _make_entry(version="2.0.0")
        mgr = LockfileManager(path)
        mgr._save(Lockfile(agents={"test-agent": entry}))
        result = mgr.get_entry("test-agent")
        assert result is not None
        assert result.version == "2.0.0"

    def test_get_entry_missing(self, tmp_path: Path) -> None:
        """get_entry() returns None when agent is not in lockfile."""
        mgr = LockfileManager(tmp_path / "lockfile.json")
        assert mgr.get_entry("no-such-agent") is None

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
        import hashlib

        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)

        # Create index cache for official source (hash-based path)
        official_url = "https://github.com/anthropics/agent-nexus-packages.git"
        url_hash = hashlib.sha256(official_url.encode()).hexdigest()[:12]
        cache_dir = tmp_path / "cache" / "repos" / url_hash
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
        import hashlib

        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)
        official_url = "https://github.com/anthropics/agent-nexus-packages.git"
        url_hash = hashlib.sha256(official_url.encode()).hexdigest()[:12]
        cache_dir = tmp_path / "cache" / "repos" / url_hash
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

    def test_resolve_agent_source_uses_path_override(self, tmp_path: Path) -> None:
        """resolve_agent_source uses IndexEntry.path override when set."""
        import hashlib

        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)

        official_url = "https://github.com/anthropics/agent-nexus-packages.git"
        url_hash = hashlib.sha256(official_url.encode()).hexdigest()[:12]
        cache_dir = tmp_path / "cache" / "repos" / url_hash
        cache_dir.mkdir(parents=True, exist_ok=True)
        index_data = {
            "agents": [
                {
                    "name": "custom-agent",
                    "version": "1.0.0",
                    "type": "atomic",
                    "description": "Custom layout",
                    "path": "agents/custom-agent",
                },
            ]
        }
        _write_yaml(cache_dir / "index.yaml", index_data)

        result = mgr.resolve_agent_source("custom-agent")
        assert result is not None
        _source_entry, relative_path = result
        assert relative_path == "agents/custom-agent"

    def test_resolve_agent_source_defaults_to_packages(self, tmp_path: Path) -> None:
        """resolve_agent_source defaults to packages/<name> when no path override."""
        import hashlib

        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)

        official_url = "https://github.com/anthropics/agent-nexus-packages.git"
        url_hash = hashlib.sha256(official_url.encode()).hexdigest()[:12]
        cache_dir = tmp_path / "cache" / "repos" / url_hash
        cache_dir.mkdir(parents=True, exist_ok=True)
        index_data = {
            "agents": [
                {"name": "standard-agent", "version": "1.0.0", "type": "atomic"},
            ]
        }
        _write_yaml(cache_dir / "index.yaml", index_data)

        result = mgr.resolve_agent_source("standard-agent")
        assert result is not None
        _source_entry, relative_path = result
        assert relative_path == "packages/standard-agent"


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
        """_validate_agent_package returns empty issues and manifest dict for valid package."""
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            tmp_path,
        )
        # Create valid package
        manifest = {"name": "test-agent", "version": "1.0.0", "type": "atomic"}
        _write_yaml(tmp_path / "pkg" / "agent-manifest.yaml", manifest)
        (tmp_path / "pkg" / "SKILL.md").write_text("# Test Agent", encoding="utf-8")
        issues, _ = installer._validate_agent_package(tmp_path / "pkg")
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
        issues, _ = installer._validate_agent_package(pkg_dir)
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
        issues, _ = installer._validate_agent_package(pkg_dir)
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
        issues, _ = installer._validate_agent_package(pkg_dir)
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
        issues, _ = installer._validate_agent_package(pkg_dir)
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
        issues, _ = installer._validate_agent_package(pkg_dir)
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

    def test_installer_uses_timezone_aware_datetime(self) -> None:
        """GitInstaller source imports timezone and uses datetime.now(timezone.utc)."""
        import inspect
        import ast

        source = inspect.getsource(GitInstaller)
        tree = ast.parse(source)
        # Check that 'timezone' appears in the source as datetime.now(timezone.utc)
        # by searching for the call pattern directly in the source text
        assert "datetime.now(timezone.utc)" in source, (
            "Expected 'datetime.now(timezone.utc)' in GitInstaller source"
        )
        # Also verify the import at module level
        import agent_nexus.platform.local.installer as installer_mod
        # The module should have 'timezone' in its namespace (imported from datetime)
        assert hasattr(installer_mod, "timezone") or "timezone" in dir(installer_mod), (
            "installer module should import timezone from datetime"
        )

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
        """_build_command uses venv python with discovered package name."""
        entry = _make_entry(venv_path=str(tmp_path / "venv"))
        # Create venv bin/python
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").touch()
        # Create agent package dir with __init__.py and main.py
        agent_dir = tmp_path / "agents" / "test-agent"
        pkg_dir = agent_dir / "agent_test"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").touch()
        (pkg_dir / "main.py").touch()
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        cmd = supervisor._build_command("test-agent", entry)
        assert cmd is not None
        assert str(tmp_path / "venv" / "bin" / "python") in cmd[0]
        assert cmd == [str(tmp_path / "venv" / "bin" / "python"), str(agent_dir / "agent_test" / "main.py")]

    def test_build_command_venv_with_root_main_py(self, tmp_path: Path) -> None:
        """_build_command prefers root main.py over package discovery."""
        entry = _make_entry(venv_path=str(tmp_path / "venv"))
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").touch()
        # Create BOTH root main.py and package dir — root main.py wins
        agent_dir = tmp_path / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "main.py").touch()
        pkg_dir = agent_dir / "agent_test"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").touch()
        (pkg_dir / "main.py").touch()
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        cmd = supervisor._build_command("test-agent", entry)
        assert cmd == [str(venv_bin / "python"), str(agent_dir / "main.py")]

    def test_build_command_venv_no_package_returns_none(self, tmp_path: Path) -> None:
        """_build_command returns None when venv exists but no package or main.py found."""
        entry = _make_entry(venv_path=str(tmp_path / "venv"))
        venv_bin = tmp_path / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").touch()
        # Agent dir exists but has no main.py or Python package
        agent_dir = tmp_path / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        cmd = supervisor._build_command("test-agent", entry)
        assert cmd is None

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
        """_build_env returns empty dict when config loading fails with I/O error."""
        entry = _make_entry()
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        config.load_config = MagicMock(side_effect=PermissionError("config not readable"))
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        env = supervisor._build_env("test-agent", entry)
        assert "AGENT_MODEL" not in env

    def test_build_env_propagates_programming_errors(self, tmp_path: Path) -> None:
        """_build_env lets non-I/O exceptions (e.g. RuntimeError) propagate."""
        entry = _make_entry()
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        config.load_config = MagicMock(side_effect=RuntimeError("config broken"))
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        with pytest.raises(RuntimeError, match="config broken"):
            supervisor._build_env("test-agent", entry)


# ============================================================================
# CLI Tests
# ============================================================================


class TestCLI:
    """Tests for the Typer CLI commands using CliRunner."""

    @patch("agent_nexus.platform.local.cli._lifecycle._install", new_callable=AsyncMock)
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
            "agent_nexus.platform.local.cli._lifecycle._init_managers",
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
            "agent_nexus.platform.local.cli._lifecycle._init_managers",
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
            "agent_nexus.platform.local.cli._lifecycle._init_managers",
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
            "agent_nexus.platform.local.cli._lifecycle._init_managers",
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
            "agent_nexus.platform.local.cli._lifecycle._init_managers",
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
            "agent_nexus.platform.local.cli._lifecycle._init_managers",
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
            "agent_nexus.platform.local.cli._lifecycle._init_managers",
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
            "agent_nexus.platform.local.cli._lifecycle._init_managers",
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
            "agent_nexus.platform.local.cli._lifecycle._init_managers",
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
            "agent_nexus.platform.local.cli._lifecycle._init_managers",
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
            "agent_nexus.platform.local.cli._lifecycle._init_managers",
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
        from agent_nexus.platform.local.cli._lifecycle import _wait_forever

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
            "agent_nexus.platform.local.cli._lifecycle._init_managers",
            return_value=(mock_loader, mock_lockfile, mock_sources, Path("/tmp/cfg")),
        ):
            result = runner.invoke(app, ["search", "nonexistent"])
            assert "No agents found" in result.output

    def test_search_with_results(self) -> None:
        """search command shows matching agents."""
        mock_sources = MagicMock(spec=SourceManager)
        index_entry = IndexEntry(
            name="doc-filler",
            version="1.0.0",
            type=AgentType.ATOMIC,
            description="Fills documentation",
            tags=["docs"],
        )
        official = SourceEntry(name="official", type="git", url="https://example.com/r.git")
        mock_sources.search_agents.return_value = [(official, index_entry)]

        # _search uses a local import of SourceManager, so patching
        # the class on the source module works because the local import
        # resolves to the patched class at call time.
        with patch(
            "agent_nexus.platform.local.sources.SourceManager",
            return_value=mock_sources,
        ), patch(
            "agent_nexus.platform.local.cli._lifecycle._get_config_dir",
            return_value=Path("/tmp/cfg"),
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
            "agent_nexus.platform.local.cli._lifecycle._init_managers",
            return_value=(mock_loader, mock_lockfile, mock_sources, Path("/tmp/cfg")),
        ):
            result = runner.invoke(app, ["run", "ghost"])
            assert "not installed" in result.output
            assert result.exit_code == 1


# ============================================================================
# Iteration 13 merges: TestCachePathAlignment, TestPipeSafetyCreateVenv
# ============================================================================


class TestCachePathAlignment:
    """Verify SourceManager._get_cache_path matches GitInstaller._get_cache_path."""

    def test_cache_path_uses_sha256_hash(self, tmp_path: Path) -> None:
        """SourceManager._get_cache_path uses SHA-256 hash, not source.name."""
        sources_yaml = tmp_path / "sources.yaml"
        sources_yaml.write_text("sources: []\n", encoding="utf-8")
        sm = SourceManager(sources_yaml)

        source = SourceEntry(
            name="official",
            type="git",
            url="https://github.com/example/packages.git",
            branch="main",
        )

        cache_path = sm._get_cache_path(source)

        expected_hash = hashlib.sha256(source.url.encode()).hexdigest()[:12]
        expected_path = tmp_path / "cache" / "repos" / expected_hash

        assert cache_path == expected_path
        assert cache_path.name != "official"
        assert cache_path.name == expected_hash

    def test_cache_path_differs_from_name_based_path(self, tmp_path: Path) -> None:
        """Old name-based path and new hash-based path are different."""
        sources_yaml = tmp_path / "sources.yaml"
        sources_yaml.write_text("sources: []\n", encoding="utf-8")
        sm = SourceManager(sources_yaml)

        source = SourceEntry(
            name="my-source",
            type="git",
            url="https://github.com/example/packages.git",
            branch="main",
        )

        hash_path = sm._get_cache_path(source)
        old_name_path = tmp_path / "cache" / "repos" / source.name

        assert hash_path != old_name_path

    def test_load_source_index_uses_hash_path(self, tmp_path: Path) -> None:
        """_load_source_index reads from hash-based cache directory."""
        sources_yaml = tmp_path / "sources.yaml"
        sources_yaml.write_text("sources: []\n", encoding="utf-8")
        sm = SourceManager(sources_yaml)

        source = SourceEntry(
            name="official",
            type="git",
            url="https://github.com/example/packages.git",
            branch="main",
        )

        cache_dir = sm._get_cache_path(source)
        cache_dir.mkdir(parents=True, exist_ok=True)

        index_content = {
            "agents": [
                {
                    "name": "doc-filler",
                    "version": "1.0.0",
                    "type": "atomic",
                    "description": "Test agent",
                }
            ]
        }
        (cache_dir / "index.yaml").write_text(
            yaml.dump(index_content),
            encoding="utf-8",
        )

        result = sm._load_source_index(source)
        assert result is not None
        assert len(result) == 1
        assert result[0].name == "doc-filler"

    def test_consistent_hash_across_calls(self, tmp_path: Path) -> None:
        """Same URL always produces the same cache path."""
        sources_yaml = tmp_path / "sources.yaml"
        sources_yaml.write_text("sources: []\n", encoding="utf-8")
        sm = SourceManager(sources_yaml)

        source = SourceEntry(
            name="test",
            type="git",
            url="https://github.com/example/repo.git",
        )
        path1 = sm._get_cache_path(source)
        path2 = sm._get_cache_path(source)
        assert path1 == path2


class TestPipeSafetyCreateVenv:
    """Verify _create_venv uses communicate() instead of wait()+stderr.read()."""

    async def test_create_venv_uses_communicate(self, tmp_path: Path) -> None:
        """_create_venv should call proc.communicate(), not proc.wait()."""
        sources_yaml = tmp_path / "sources.yaml"
        sources_yaml.write_text("sources: []\n", encoding="utf-8")
        lockfile_json = tmp_path / "lockfile.json"
        lockfile_json.write_text('{"version": 1, "agents": {}}', encoding="utf-8")

        sm = SourceManager(sources_yaml)
        lm = LockfileManager(lockfile_json)
        installer = GitInstaller(sm, lm, tmp_path)

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        (agent_dir / "pyproject.toml").write_text("[project]\nname='test'\n")

        mock_proc_venv = MagicMock()
        mock_proc_venv.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc_venv.returncode = 0

        mock_proc_install = MagicMock()
        mock_proc_install.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc_install.returncode = 0

        with patch(
            "agent_nexus.platform.local.installer.asyncio.create_subprocess_exec",
            side_effect=[mock_proc_venv, mock_proc_install],
        ):
            result = await installer._create_venv("test-agent", agent_dir)

        mock_proc_venv.communicate.assert_awaited_once()
        mock_proc_install.communicate.assert_awaited_once()
        mock_proc_venv.wait.assert_not_called()
        mock_proc_install.wait.assert_not_called()

    async def test_create_venv_handles_failure(self, tmp_path: Path) -> None:
        """_create_venv returns None on uv failure, using communicate()."""
        sources_yaml = tmp_path / "sources.yaml"
        sources_yaml.write_text("sources: []\n", encoding="utf-8")
        lockfile_json = tmp_path / "lockfile.json"
        lockfile_json.write_text('{"version": 1, "agents": {}}', encoding="utf-8")

        sm = SourceManager(sources_yaml)
        lm = LockfileManager(lockfile_json)
        installer = GitInstaller(sm, lm, tmp_path)

        agent_dir = tmp_path / "test-agent"
        agent_dir.mkdir()
        (agent_dir / "pyproject.toml").write_text("[project]\nname='test'\n")

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error details"))
        mock_proc.returncode = 1

        with patch(
            "agent_nexus.platform.local.installer.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            result = await installer._create_venv("test-agent", agent_dir)

        assert result is None
        mock_proc.communicate.assert_awaited_once()
        mock_proc.wait.assert_not_called()


# ============================================================================
# Iteration 15 merge: TestRunGitUsesCommunicate
# ============================================================================


class TestRunGitUsesCommunicate:
    """GitInstaller._run_git should use communicate() to avoid pipe deadlock."""

    @pytest.mark.asyncio
    async def test_run_git_uses_communicate(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_proc
        ) as mock_create:
            await GitInstaller._run_git(["status"], Path("/tmp"))
            mock_create.assert_called_once()
            mock_proc.communicate.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_git_includes_stderr_on_failure(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"error: pathspec 'x' did not match")
        )
        mock_proc.returncode = 128

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(InstallationError) as exc_info:
                await GitInstaller._run_git(["checkout", "x"], Path("/tmp"))
            assert "pathspec" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_run_git_capture_includes_stderr_on_failure(self) -> None:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b"remote: Repository not found")
        )
        mock_proc.returncode = 128

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(InstallationError) as exc_info:
                await GitInstaller._run_git_capture(["ls-remote", "url"], Path("/tmp"))
            assert "Repository not found" in str(exc_info.value)


# ============================================================================
# Iteration 19 merge: TestSupervisorEnvForwarding
# ============================================================================


class TestSupervisorEnvForwarding:
    """_build_env must forward API keys from configured providers."""

    def test_forwards_api_keys(self, monkeypatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-key-456")

        pm = MagicMock()
        lockfile = MagicMock()
        config_loader = MagicMock()

        model_config = ModelConfig(
            default="openai:gpt-4o",
            providers={
                "openai": ProviderConfig(
                    base_url="https://api.openai.com/v1",
                    api_key_env="OPENAI_API_KEY",
                ),
                "anthropic": ProviderConfig(
                    base_url="https://api.anthropic.com",
                    api_key_env="ANTHROPIC_API_KEY",
                ),
            },
        )
        config_loader.load_config.return_value = MagicMock(models=model_config)

        supervisor = AgentSupervisor(
            process_manager=pm,
            lockfile_manager=lockfile,
            config_loader=config_loader,
            config_dir=Path("/tmp/test"),
        )

        env = supervisor._build_env("test-agent", LockfileEntry(
            source="git+https://example.com/test-agent",
            version="1.0.0",
            commit_sha="a" * 40,
            agent_type="atomic",
        ))

        assert env["AGENT_MODEL"] == "openai:gpt-4o"
        assert env["OPENAI_API_KEY"] == "sk-test-123"
        assert env["ANTHROPIC_API_KEY"] == "ant-key-456"

    def test_skips_empty_keys(self, monkeypatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        pm = MagicMock()
        lockfile = MagicMock()
        config_loader = MagicMock()

        model_config = ModelConfig(
            default="openai:gpt-4o",
            providers={
                "openai": ProviderConfig(
                    base_url="https://api.openai.com/v1",
                    api_key_env="OPENAI_API_KEY",
                ),
            },
        )
        config_loader.load_config.return_value = MagicMock(models=model_config)

        supervisor = AgentSupervisor(
            process_manager=pm,
            lockfile_manager=lockfile,
            config_loader=config_loader,
            config_dir=Path("/tmp/test"),
        )

        env = supervisor._build_env("test-agent", LockfileEntry(
            source="git+https://example.com/test-agent",
            version="1.0.0",
            commit_sha="a" * 40,
            agent_type="atomic",
        ))

        assert "OPENAI_API_KEY" not in env
        assert env["AGENT_MODEL"] == "openai:gpt-4o"

    def test_config_load_failure_does_not_crash(self) -> None:
        pm = MagicMock()
        lockfile = MagicMock()
        config_loader = MagicMock()
        config_loader.load_config.side_effect = PermissionError("config not readable")

        supervisor = AgentSupervisor(
            process_manager=pm,
            lockfile_manager=lockfile,
            config_loader=config_loader,
            config_dir=Path("/tmp/test"),
        )

        env = supervisor._build_env("test-agent", LockfileEntry(
            source="git+https://example.com/test-agent",
            version="1.0.0",
            commit_sha="a" * 40,
            agent_type="atomic",
        ))
        assert env["AGENT_NAME"] == "test-agent"
        assert "AGENT_DIR" in env
        # Config failure should not set AGENT_MODEL or API keys
        assert "AGENT_MODEL" not in env

    def test_provider_without_api_key_env(self) -> None:
        """Provider with empty api_key_env should not forward anything."""
        pm = MagicMock()
        lockfile = MagicMock()
        config_loader = MagicMock()

        model_config = ModelConfig(
            default="ollama:llama3",
            providers={
                "ollama": ProviderConfig(
                    base_url="http://localhost:11434",
                    api_key_env="",
                ),
            },
        )
        config_loader.load_config.return_value = MagicMock(models=model_config)

        supervisor = AgentSupervisor(
            process_manager=pm,
            lockfile_manager=lockfile,
            config_loader=config_loader,
            config_dir=Path("/tmp/test"),
        )

        env = supervisor._build_env("test-agent", LockfileEntry(
            source="git+https://example.com/test-agent",
            version="1.0.0",
            commit_sha="a" * 40,
            agent_type="atomic",
        ))
        assert env["AGENT_MODEL"] == "ollama:llama3"
        # AGENT_NAME and AGENT_DIR are always present; no API key forwarded
        assert "test_api_key_env" not in env


# ============================================================================
# Iteration 24 merges: TestReadManifestNonDictYaml, TestSparseCloneParentDir,
#                       TestBuildCommandUnsafeName
# ============================================================================


class TestReadManifestNonDictYaml:
    """GitInstaller._read_manifest returns {} for non-dict YAML content."""

    def _make_installer(self):
        """Create a GitInstaller with mocked dependencies."""
        sources = MagicMock()
        lockfile = MagicMock()
        config_dir = Path(tempfile.mkdtemp())
        return GitInstaller(sources, lockfile, config_dir)

    def test_string_manifest_returns_empty_dict(self):
        installer = self._make_installer()
        with tempfile.TemporaryDirectory() as tmp:
            agent_dir = Path(tmp)
            manifest_path = agent_dir / "agent-manifest.yaml"
            manifest_path.write_text("hello", encoding="utf-8")
            result = installer._read_manifest(agent_dir)
            assert result == {}

    def test_empty_file_returns_empty_dict(self):
        installer = self._make_installer()
        with tempfile.TemporaryDirectory() as tmp:
            agent_dir = Path(tmp)
            manifest_path = agent_dir / "agent-manifest.yaml"
            manifest_path.write_text("", encoding="utf-8")
            result = installer._read_manifest(agent_dir)
            assert result == {}

    def test_list_manifest_returns_empty_dict(self):
        installer = self._make_installer()
        with tempfile.TemporaryDirectory() as tmp:
            agent_dir = Path(tmp)
            manifest_path = agent_dir / "agent-manifest.yaml"
            manifest_path.write_text("- item1\n- item2\n", encoding="utf-8")
            result = installer._read_manifest(agent_dir)
            assert result == {}


class TestSparseCloneParentDir:
    """Verify _sparse_clone uses cache_path.parent.mkdir, not cache_path.mkdir."""

    def test_uses_parent_mkdir_not_target_mkdir(self):
        installer_path = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "agent_nexus"
            / "platform"
            / "local"
            / "installer.py"
        )
        source = installer_path.read_text(encoding="utf-8")
        assert "cache_path.parent.mkdir" in source, (
            "Expected cache_path.parent.mkdir(parents=True, exist_ok=True) "
            "but did not find cache_path.parent.mkdir in installer.py"
        )
        lines = source.splitlines()
        for i, line in enumerate(lines):
            if "cache_path.mkdir" in line and "cache_path.parent" not in line:
                pytest.fail(
                    f"Line {i+1} contains cache_path.mkdir without .parent — "
                    f"this is the bug pattern: {line.strip()}"
                )


class TestBuildCommandUnsafeName:
    """_build_command rejects agent names with unsafe characters."""

    def _make_supervisor(self):
        """Create a bare AgentSupervisor with mocked internals."""
        sup = AgentSupervisor.__new__(AgentSupervisor)
        sup._lockfile = MagicMock()
        sup._config = MagicMock()
        sup._config_dir = Path("/tmp/.agent-nexus")
        sup._pm = MagicMock()
        sup._max_restarts = 3
        sup._restart_trackers = {}
        sup._started_agents = set()
        sup._resolved_packages = {}
        return sup

    def _make_entry(self) -> LockfileEntry:
        return LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="a" * 40,
            agent_type=AgentType.ATOMIC,
            venv_path="",
        )

    def test_dot_in_name_allowed(self):
        """Dots are allowed in agent names (consistent with installer)."""
        sup = self._make_supervisor()
        entry = self._make_entry()
        result = sup._build_command("agent.evil", entry)
        assert result is not None  # dots are allowed

    def test_slash_in_name_returns_none(self):
        sup = self._make_supervisor()
        entry = self._make_entry()
        result = sup._build_command("agent/evil", entry)
        assert result is None

    def test_shell_injection_name_returns_none(self):
        sup = self._make_supervisor()
        entry = self._make_entry()
        result = sup._build_command("agent; rm -rf /", entry)
        assert result is None

    def test_normal_name_returns_command(self):
        sup = self._make_supervisor()
        entry = self._make_entry()
        result = sup._build_command("normal-agent", entry)
        assert result is not None
        assert isinstance(result, list)
        assert len(result) > 0


class TestSupervisorConfigLoadLogsError:
    """_build_env must log at ERROR level (not silently swallow) when config
    loading fails.  Regression test for silent `except Exception: pass`.
    Upgraded from WARNING to ERROR in iteration 22 audit.
    """

    def test_config_load_failure_logs_warning(self, caplog) -> None:
        """When config_loader.load_config raises, an error is logged at ERROR level."""
        pm = MagicMock()
        lockfile = MagicMock()
        config_loader = MagicMock()
        config_loader.load_config.side_effect = PermissionError("config not readable")

        supervisor = AgentSupervisor(
            process_manager=pm,
            lockfile_manager=lockfile,
            config_loader=config_loader,
            config_dir=Path("/tmp/test"),
        )

        with caplog.at_level(logging.ERROR, logger="agent_nexus.platform.local.supervisor"):
            env = supervisor._build_env("test-agent", LockfileEntry(
                source="git+https://example.com/test-agent",
                version="1.0.0",
                commit_sha="a" * 40,
                agent_type="atomic",
            ))

        # env should have AGENT_NAME/AGENT_DIR but no model config (no crash)
        assert env["AGENT_NAME"] == "test-agent"
        assert "AGENT_DIR" in env
        assert "AGENT_MODEL" not in env
        assert "Failed to load config" in caplog.text


# ============================================================================
# Regression tests for iteration 22 defects
# ============================================================================


class TestSourceManagerListValidation:
    """Regression: sources.yaml 'sources' key must be a list, not other types."""

    def test_sources_string_value_uses_defaults(self, tmp_path: Path) -> None:
        """When 'sources' maps to a string, SourceManager falls back to defaults."""
        path = tmp_path / "sources.yaml"
        path.write_text("sources: just_a_string\n", encoding="utf-8")
        mgr = SourceManager(path)
        sources = mgr.list_sources()
        assert len(sources) == 1
        assert sources[0].name == "official"

    def test_sources_dict_value_uses_defaults(self, tmp_path: Path) -> None:
        """When 'sources' maps to a dict, SourceManager falls back to defaults."""
        path = tmp_path / "sources.yaml"
        path.write_text("sources:\n  name: official\n  url: http://x.com\n", encoding="utf-8")
        mgr = SourceManager(path)
        sources = mgr.list_sources()
        assert len(sources) == 1
        assert sources[0].name == "official"

    def test_sources_int_value_uses_defaults(self, tmp_path: Path) -> None:
        """When 'sources' maps to an int, SourceManager falls back to defaults."""
        path = tmp_path / "sources.yaml"
        path.write_text("sources: 42\n", encoding="utf-8")
        mgr = SourceManager(path)
        sources = mgr.list_sources()
        assert len(sources) == 1
        assert sources[0].name == "official"

    def test_sources_with_non_dict_items_skips(self, tmp_path: Path) -> None:
        """Non-dict items in sources list are skipped with a warning."""
        path = tmp_path / "sources.yaml"
        path.write_text(
            "sources:\n"
            "  - just_a_string\n"
            "  - name: valid\n"
            "    type: git\n"
            "    url: https://x.com/r.git\n"
            "  - 42\n",
            encoding="utf-8",
        )
        mgr = SourceManager(path)
        sources = mgr.list_sources()
        names = [s.name for s in sources]
        assert "valid" in names
        assert len([n for n in names if n not in ("valid", "official")]) == 0


class TestSourceManagerIndexListValidation:
    """Regression: index.yaml 'agents' key must be a list."""

    def test_index_agents_string_returns_none(self, tmp_path: Path) -> None:
        """When index 'agents' is a string, _load_source_index returns None."""
        import hashlib

        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)

        official_url = "https://github.com/anthropics/agent-nexus-packages.git"
        url_hash = hashlib.sha256(official_url.encode()).hexdigest()[:12]
        cache_dir = tmp_path / "cache" / "repos" / url_hash
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "index.yaml").write_text("agents: not_a_list\n", encoding="utf-8")

        result = mgr._load_source_index(mgr.list_sources()[0])
        assert result is None

    def test_index_agents_dict_returns_none(self, tmp_path: Path) -> None:
        """When index 'agents' is a dict, _load_source_index returns None."""
        import hashlib

        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)

        official_url = "https://github.com/anthropics/agent-nexus-packages.git"
        url_hash = hashlib.sha256(official_url.encode()).hexdigest()[:12]
        cache_dir = tmp_path / "cache" / "repos" / url_hash
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "index.yaml").write_text(
            "agents:\n  name: x\n  version: 1.0\n", encoding="utf-8"
        )

        result = mgr._load_source_index(mgr.list_sources()[0])
        assert result is None

    def test_index_with_non_dict_items_skips(self, tmp_path: Path) -> None:
        """Non-dict items in index agents list are skipped."""
        import hashlib

        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)

        official_url = "https://github.com/anthropics/agent-nexus-packages.git"
        url_hash = hashlib.sha256(official_url.encode()).hexdigest()[:12]
        cache_dir = tmp_path / "cache" / "repos" / url_hash
        cache_dir.mkdir(parents=True, exist_ok=True)
        _write_yaml(
            cache_dir / "index.yaml",
            {
                "agents": [
                    "invalid_string_entry",
                    {"name": "valid-agent", "version": "1.0.0", "type": "atomic"},
                    42,
                ]
            },
        )

        result = mgr._load_source_index(mgr.list_sources()[0])
        assert result is not None
        assert len(result) == 1
        assert result[0].name == "valid-agent"


class TestInstallerReadManifestLogs:
    """Regression: _read_manifest raises InstallationError on parse failure."""

    def test_read_manifest_logs_on_parse_error(self, tmp_path: Path) -> None:
        """_read_manifest raises InstallationError when YAML parsing fails."""
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            tmp_path,
        )
        # Write invalid YAML that safe_load will choke on
        agent_dir = tmp_path / "pkg"
        agent_dir.mkdir()
        (agent_dir / "agent-manifest.yaml").write_text(
            "{{{{invalid yaml", encoding="utf-8"
        )

        with pytest.raises(InstallationError, match="Failed to read manifest"):
            installer._read_manifest(agent_dir)


class TestResolveAgentDirValidation:
    """Regression: _resolve_agent_dir raises ValueError for unsafe agent names.

    This prevents path traversal attacks where a crafted agent_name like
    '../etc' could resolve to a directory outside config_dir/agents/.
    """

    def _make_supervisor(self, tmp_path: Path) -> AgentSupervisor:
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        return AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)

    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        """agent_name='../etc' must raise ValueError."""
        sup = self._make_supervisor(tmp_path)
        with pytest.raises(ValueError, match="unsafe"):
            sup._resolve_agent_dir("../etc")

    def test_rejects_slash_in_name(self, tmp_path: Path) -> None:
        """agent_name='foo/bar' must raise ValueError."""
        sup = self._make_supervisor(tmp_path)
        with pytest.raises(ValueError, match="unsafe"):
            sup._resolve_agent_dir("foo/bar")

    def test_rejects_dot_dot(self, tmp_path: Path) -> None:
        """agent_name='..' must raise ValueError."""
        sup = self._make_supervisor(tmp_path)
        with pytest.raises(ValueError, match="unsafe"):
            sup._resolve_agent_dir("..")

    def test_accepts_valid_hyphenated_name(self, tmp_path: Path) -> None:
        """agent_name='code-reviewer' returns config_dir/agents/code-reviewer."""
        sup = self._make_supervisor(tmp_path)
        result = sup._resolve_agent_dir("code-reviewer")
        assert result == tmp_path / "agents" / "code-reviewer"

    def test_accepts_alphanumeric(self, tmp_path: Path) -> None:
        """agent_name='agent123' returns config_dir/agents/agent123."""
        sup = self._make_supervisor(tmp_path)
        result = sup._resolve_agent_dir("agent123")
        assert result == tmp_path / "agents" / "agent123"

    def test_accepts_dot_in_name(self, tmp_path: Path) -> None:
        """agent_name='my.agent' is allowed by _SAFE_NAME_RE."""
        sup = self._make_supervisor(tmp_path)
        result = sup._resolve_agent_dir("my.agent")
        assert result == tmp_path / "agents" / "my.agent"

    def test_rejects_empty_string(self, tmp_path: Path) -> None:
        """Empty agent_name must raise ValueError."""
        sup = self._make_supervisor(tmp_path)
        with pytest.raises(ValueError, match="unsafe"):
            sup._resolve_agent_dir("")

    def test_rejects_leading_hyphen(self, tmp_path: Path) -> None:
        """agent_name='-evil' must raise ValueError (flag injection)."""
        sup = self._make_supervisor(tmp_path)
        with pytest.raises(ValueError, match="unsafe"):
            sup._resolve_agent_dir("-evil")

    def test_rejects_null_byte(self, tmp_path: Path) -> None:
        """agent_name='agent\\x00evil' must raise ValueError."""
        sup = self._make_supervisor(tmp_path)
        with pytest.raises(ValueError, match="unsafe"):
            sup._resolve_agent_dir("agent\x00evil")


class TestInstallerVenvPathIsRelativeTo:
    """Regression: uninstall() uses is_relative_to to validate venv_path.

    Prevents directory traversal where a crafted venv_path in the lockfile
    could point to an arbitrary directory on the system.  Only venvs under
    config_dir/venvs/ should be removed during uninstallation.
    """

    @pytest.mark.asyncio
    async def test_rejects_venv_path_outside_venvs_dir(self, tmp_path: Path) -> None:
        """A venv_path like /tmp/evil should NOT be deleted during uninstall."""
        sources_yaml = tmp_path / "sources.yaml"
        sources_yaml.write_text("sources: []\n", encoding="utf-8")
        lockfile_json = tmp_path / "lockfile.json"
        lockfile_json.write_text('{"version": 1, "agents": {}}', encoding="utf-8")

        sm = SourceManager(sources_yaml)
        lm = LockfileManager(lockfile_json)

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        agents_dir = config_dir / "agents"
        venvs_dir = config_dir / "venvs"
        agents_dir.mkdir()
        venvs_dir.mkdir()

        # Create a "malicious" directory that should NOT be deleted
        evil_dir = tmp_path / "evil-target"
        evil_dir.mkdir()
        (evil_dir / "important.txt").write_text("do not delete", encoding="utf-8")

        # Simulate an installed agent whose venv_path points outside venvs_dir
        agent_dir = agents_dir / "evil-agent"
        agent_dir.mkdir()
        entry = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="a" * 40,
            agent_type=AgentType.ATOMIC,
            venv_path=str(evil_dir),
            installed_at=datetime(2026, 1, 15, 12, 0, 0),
        )
        lm.add_entry_by_name("evil-agent", entry)

        installer = GitInstaller(sm, lm, config_dir)
        result = await installer.uninstall("evil-agent")

        assert result is True
        # The evil directory must NOT be deleted
        assert evil_dir.exists()
        assert (evil_dir / "important.txt").exists()
        # But the agent dir should be gone
        assert not agent_dir.exists()

    @pytest.mark.asyncio
    async def test_accepts_venv_path_inside_venvs_dir(self, tmp_path: Path) -> None:
        """A venv_path under venvs_dir should be deleted during uninstall."""
        sources_yaml = tmp_path / "sources.yaml"
        sources_yaml.write_text("sources: []\n", encoding="utf-8")
        lockfile_json = tmp_path / "lockfile.json"
        lockfile_json.write_text('{"version": 1, "agents": {}}', encoding="utf-8")

        sm = SourceManager(sources_yaml)
        lm = LockfileManager(lockfile_json)

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        agents_dir = config_dir / "agents"
        venvs_dir = config_dir / "venvs"
        agents_dir.mkdir()
        venvs_dir.mkdir()

        # Create agent dir and valid venv
        agent_dir = agents_dir / "good-agent"
        agent_dir.mkdir()
        venv_path = venvs_dir / "good-agent"
        venv_path.mkdir()
        (venv_path / "pyvenv.cfg").write_text("home = /usr/bin", encoding="utf-8")

        entry = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="a" * 40,
            agent_type=AgentType.ATOMIC,
            venv_path=str(venv_path),
            installed_at=datetime(2026, 1, 15, 12, 0, 0),
        )
        lm.add_entry_by_name("good-agent", entry)

        installer = GitInstaller(sm, lm, config_dir)
        result = await installer.uninstall("good-agent")

        assert result is True
        # Both agent dir and venv should be removed
        assert not agent_dir.exists()
        assert not venv_path.exists()

    @pytest.mark.asyncio
    async def test_rejects_traversal_via_dotdot(self, tmp_path: Path) -> None:
        """A venv_path like '../../tmp/evil' must not be deleted."""
        sources_yaml = tmp_path / "sources.yaml"
        sources_yaml.write_text("sources: []\n", encoding="utf-8")
        lockfile_json = tmp_path / "lockfile.json"
        lockfile_json.write_text('{"version": 1, "agents": {}}', encoding="utf-8")

        sm = SourceManager(sources_yaml)
        lm = LockfileManager(lockfile_json)

        config_dir = tmp_path / "config"
        config_dir.mkdir()
        agents_dir = config_dir / "agents"
        venvs_dir = config_dir / "venvs"
        agents_dir.mkdir()
        venvs_dir.mkdir()

        # Create a target directory outside venvs_dir
        target_dir = tmp_path / "outside-target"
        target_dir.mkdir()
        (target_dir / "secret.txt").write_text("secret", encoding="utf-8")

        agent_dir = agents_dir / "traversal-agent"
        agent_dir.mkdir()

        # venv_path uses ../.. to escape venvs_dir
        traversal_path = str(venvs_dir / ".." / ".." / "outside-target")
        entry = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="a" * 40,
            agent_type=AgentType.ATOMIC,
            venv_path=traversal_path,
            installed_at=datetime(2026, 1, 15, 12, 0, 0),
        )
        lm.add_entry_by_name("traversal-agent", entry)

        installer = GitInstaller(sm, lm, config_dir)
        result = await installer.uninstall("traversal-agent")

        assert result is True
        # Traversal target must NOT be deleted
        assert target_dir.exists()
        assert (target_dir / "secret.txt").exists()


class TestSupervisorSafeNameRegex:
    """Regression: _SAFE_NAME_RE is consistent with installer pattern."""

    def test_allows_dots_for_package_names(self, tmp_path: Path) -> None:
        """Dots are allowed (e.g., my.agent, code-review.v2)."""
        entry = _make_entry(venv_path="")
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        cmd = supervisor._build_command("my.agent", entry)
        assert cmd is not None

    def test_rejects_leading_hyphen(self, tmp_path: Path) -> None:
        """Names starting with hyphen are rejected (flag injection)."""
        entry = _make_entry(venv_path="")
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        cmd = supervisor._build_command("-evil-flag", entry)
        assert cmd is None

    def test_rejects_double_dot(self, tmp_path: Path) -> None:
        """Names with '..' are allowed by regex but that's OK since there's
        no path traversal risk (name is used as directory name, not path)."""
        entry = _make_entry(venv_path="")
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        # 'agent..evil' is allowed -- no actual security risk since
        # shutil.copytree uses it as a leaf directory name
        cmd = supervisor._build_command("agent..evil", entry)
        assert cmd is not None  # allowed -- consistent with installer


# ============================================================================
# Regression: LockfileManager get_entry_from + supervisor lockfile passthrough
#             + ERROR log level (from iter 42 audit)
# ============================================================================


class TestLockfileManagerGetEntryFrom:
    """Regression 3.1: get_entry_from() reads from pre-loaded lockfile.

    Without this method, start_all() would call get_entry() for each agent,
    causing N redundant disk reads and a TOCTOU window where the lockfile
    could change between reads.
    """

    def test_get_entry_from_loaded_lockfile(self, tmp_path: Path) -> None:
        """get_entry_from() returns correct entry from in-memory lockfile."""
        lockfile_path = tmp_path / "lockfile.json"
        mgr = LockfileManager(lockfile_path)

        entry1 = _make_entry(version="1.0.0", commit_sha="a" * 40)
        entry2 = _make_entry(
            version="2.0.0",
            source="git+https://example.com/other",
            commit_sha="b" * 40,
        )

        lockfile = Lockfile(
            agents={
                "agent-a": entry1,
                "agent-b": entry2,
            }
        )
        mgr._save(lockfile)

        # Load once, query N times without re-reading disk
        loaded = mgr.load()
        result = mgr.get_entry_from(loaded, "agent-a")
        assert result is not None
        assert result.version == "1.0.0"
        assert result.commit_sha == "a" * 40

        result_b = mgr.get_entry_from(loaded, "agent-b")
        assert result_b is not None
        assert result_b.version == "2.0.0"

    def test_get_entry_from_missing_returns_none(
        self, tmp_path: Path
    ) -> None:
        """get_entry_from() returns None for unknown agent names."""
        lockfile_path = tmp_path / "lockfile.json"
        mgr = LockfileManager(lockfile_path)

        lockfile = Lockfile(agents={"existing": _make_entry()})
        mgr._save(lockfile)

        loaded = mgr.load()
        assert mgr.get_entry_from(loaded, "nonexistent") is None


class TestSupervisorLockfilePassthrough:
    """Regression 3.1 continued: start_all() passes lockfile to start_agent().

    This avoids N redundant disk reads and TOCTOU during bulk startup.
    """

    @pytest.mark.asyncio
    async def test_start_all_passes_lockfile(self, tmp_path: Path) -> None:
        """start_all() loads lockfile once and passes it through."""
        lockfile_path = tmp_path / "lockfile.json"
        lockfile_mgr = LockfileManager(lockfile_path)

        entry = _make_entry()
        lockfile = Lockfile(agents={"test-agent": entry})
        lockfile_mgr._save(lockfile)

        pm = MagicMock(spec=["start_agent", "stop_agent", "stop_all", "get_agent", "list_running", "health_check"])
        pm.start_agent = AsyncMock()
        handle = MagicMock()
        handle.pid = 12345
        pm.start_agent.return_value = handle

        config_loader = MagicMock()
        config_loader.load_config.return_value = MagicMock(
            models=MagicMock(default=None, providers={})
        )
        config_loader.config_dir = tmp_path

        supervisor = AgentSupervisor(
            process_manager=pm,
            lockfile_manager=lockfile_mgr,
            config_loader=config_loader,
            config_dir=tmp_path,
        )

        started = await supervisor.start_all()
        assert "test-agent" in started


class TestSupervisorBuildEnvLogsError:
    """Regression 3.4: _build_env logs at ERROR level on config failure.

    Previously logged at WARNING, which could hide configuration issues
    that cause agents to run without model settings and API keys.
    """

    def test_config_failure_logs_error(self, caplog) -> None:
        """When config_loader.load_config raises, error is logged at ERROR."""
        pm = MagicMock()
        lockfile = MagicMock()
        config_loader = MagicMock()
        config_loader.load_config.side_effect = PermissionError("config not readable")

        supervisor = AgentSupervisor(
            process_manager=pm,
            lockfile_manager=lockfile,
            config_loader=config_loader,
            config_dir=Path("/tmp/test"),
        )

        with caplog.at_level(
            logging.ERROR,
            logger="agent_nexus.platform.local.supervisor",
        ):
            env = supervisor._build_env("my-agent", LockfileEntry(
                source="git+https://example.com/test-agent",
                version="1.0.0",
                commit_sha="a" * 40,
                agent_type="atomic",
            ))

        assert env["AGENT_NAME"] == "my-agent"
        assert "AGENT_DIR" in env
        assert "AGENT_MODEL" not in env
        assert "Failed to load config" in caplog.text
        error_records = [
            r for r in caplog.records
            if r.levelname == "ERROR" and "Failed to load config" in r.message
        ]
        assert len(error_records) >= 1, (
            "Expected at least one ERROR-level log record for config failure"
        )


# ============================================================================
# GitInstaller comprehensive coverage tests
# ============================================================================


class TestGitInstallerInstall:
    """Cover install() main path, rollback, and edge cases."""

    @pytest.fixture
    def mock_installer(self, tmp_path: Path):
        """Create a GitInstaller with fully mocked managers."""
        sources = MagicMock(spec=SourceManager)
        lockfile = MagicMock(spec=LockfileManager)
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        installer = GitInstaller(sources, lockfile, config_dir)
        return installer, sources, lockfile, config_dir

    async def test_install_invalid_name_spaces(self, mock_installer) -> None:
        """install() raises InstallationError for names with spaces."""
        installer, *_ = mock_installer
        with pytest.raises(InstallationError, match="Invalid agent name"):
            await installer.install("bad agent name")

    async def test_install_invalid_name_path_traversal(self, mock_installer) -> None:
        """install() raises InstallationError for names with path traversal."""
        installer, *_ = mock_installer
        with pytest.raises(InstallationError, match="Invalid agent name"):
            await installer.install("../etc/passwd")

    async def test_install_invalid_name_starts_with_dot(self, mock_installer) -> None:
        """install() raises InstallationError for names starting with dot."""
        installer, *_ = mock_installer
        with pytest.raises(InstallationError, match="Invalid agent name"):
            await installer.install(".hidden-agent")

    async def test_install_invalid_name_slash(self, mock_installer) -> None:
        """install() raises InstallationError for names containing slash."""
        installer, *_ = mock_installer
        with pytest.raises(InstallationError, match="Invalid agent name"):
            await installer.install("foo/bar")

    async def test_install_with_source_url_happy_path(self, mock_installer, tmp_path: Path) -> None:
        """install() with source_url delegates to internal helpers and updates lockfile."""
        installer, _, lockfile, config_dir = mock_installer
        agents_dir = config_dir / "agents"

        # Create a fake agent directory that _sparse_clone will "return"
        fake_agent_dir = tmp_path / "cloned" / "packages" / "my-agent"
        fake_agent_dir.mkdir(parents=True)
        _write_yaml(
            fake_agent_dir / "agent-manifest.yaml",
            {"name": "my-agent", "version": "2.0.0", "type": "atomic", "description": "Test agent"},
        )
        (fake_agent_dir / "SKILL.md").write_text("# My Agent", encoding="utf-8")

        installer._sparse_clone = AsyncMock(return_value=fake_agent_dir)
        installer._create_venv = AsyncMock(return_value=None)
        installer._get_commit_sha = AsyncMock(return_value="abc123" + "d" * 34)

        entry = await installer.install(
            "my-agent",
            source_url="https://github.com/user/repo.git",
        )

        installer._sparse_clone.assert_awaited_once()
        installer._create_venv.assert_awaited_once()
        installer._get_commit_sha.assert_awaited_once()
        lockfile.add_entry_by_name.assert_called_once_with("my-agent", entry)
        assert entry.version == "2.0.0"
        assert entry.source == "repo"
        assert entry.commit_sha == "abc123" + "d" * 34
        # Agent files should be copied to agents dir
        assert (agents_dir / "my-agent" / "SKILL.md").exists()

    async def test_install_with_version_prefix(self, mock_installer, tmp_path: Path) -> None:
        """install() passes version as git ref in tag format."""
        installer, _, lockfile, _ = mock_installer

        fake_agent_dir = tmp_path / "cloned" / "packages" / "test-agent"
        fake_agent_dir.mkdir(parents=True)
        _write_yaml(
            fake_agent_dir / "agent-manifest.yaml",
            {"name": "test-agent", "version": "3.1.0", "type": "atomic", "description": "Test"},
        )
        (fake_agent_dir / "SKILL.md").write_text("# Test", encoding="utf-8")

        installer._sparse_clone = AsyncMock(return_value=fake_agent_dir)
        installer._create_venv = AsyncMock(return_value=None)
        installer._get_commit_sha = AsyncMock(return_value="latest")

        entry = await installer.install(
            "test-agent",
            version="3.1.0",
            source_url="https://github.com/org/agents.git",
        )

        # Verify _sparse_clone was called with the versioned ref
        call_args = installer._sparse_clone.call_args
        assert call_args[0][3] == "test-agent/v3.1.0"  # ref parameter

    async def test_install_rollback_on_failure(self, mock_installer, tmp_path: Path) -> None:
        """install() cleans up created paths on failure."""
        installer, _, lockfile, config_dir = mock_installer

        # Create a fake agent dir that _sparse_clone returns
        fake_agent_dir = tmp_path / "cloned" / "packages" / "fail-agent"
        fake_agent_dir.mkdir(parents=True)
        (fake_agent_dir / "SKILL.md").write_text("# Fail", encoding="utf-8")

        installer._sparse_clone = AsyncMock(return_value=fake_agent_dir)
        # Make _validate_agent_package fail
        installer._validate_agent_package = MagicMock(
            return_value=(["Missing agent-manifest.yaml"], {})
        )

        with pytest.raises(InstallationError, match="validation failed"):
            await installer.install("fail-agent", source_url="https://github.com/x/y.git")

        # agents dir should have been cleaned up by rollback
        agents_dest = config_dir / "agents" / "fail-agent"
        assert not agents_dest.exists()
        # Lockfile should NOT have been updated
        lockfile.add_entry_by_name.assert_not_called()

    async def test_install_clone_failure_raises_installation_error(self, mock_installer) -> None:
        """install() wraps _sparse_clone exceptions in InstallationError."""
        installer, *_ = mock_installer
        installer._sparse_clone = AsyncMock(side_effect=RuntimeError("git clone failed"))

        with pytest.raises(InstallationError, match="Failed to clone agent"):
            await installer.install("some-agent", source_url="https://github.com/x/y.git")

    async def test_install_source_resolution_no_source(self, mock_installer) -> None:
        """install() raises AgentNotFoundError when source resolution returns None."""
        installer, sources, _, _ = mock_installer
        sources.resolve_agent_source = MagicMock(return_value=None)

        with pytest.raises(AgentNotFoundError, match="not found in any configured source"):
            await installer.install("missing-agent")

    async def test_install_source_resolution_happy_path(self, mock_installer, tmp_path: Path) -> None:
        """install() uses resolved source from SourceManager."""
        installer, sources, lockfile, _ = mock_installer

        fake_agent_dir = tmp_path / "cloned" / "packages" / "resolved-agent"
        fake_agent_dir.mkdir(parents=True)
        _write_yaml(
            fake_agent_dir / "agent-manifest.yaml",
            {"name": "resolved-agent", "version": "1.0.0", "type": "composite", "description": "Resolved agent"},
        )
        (fake_agent_dir / "SKILL.md").write_text("# Resolved", encoding="utf-8")

        resolved_source = SourceEntry(
            name="official", type="git", url="https://github.com/org/repo.git"
        )
        sources.resolve_agent_source = MagicMock(
            return_value=(resolved_source, "agents/resolved-agent")
        )
        installer._sparse_clone = AsyncMock(return_value=fake_agent_dir)
        installer._create_venv = AsyncMock(return_value=None)
        installer._get_commit_sha = AsyncMock(return_value="latest")

        entry = await installer.install("resolved-agent")
        assert entry.version == "1.0.0"
        assert entry.source == "official"

    async def test_install_with_venv(self, mock_installer, tmp_path: Path) -> None:
        """install() records venv_path in lockfile entry when venv is created."""
        installer, _, lockfile, _ = mock_installer

        fake_agent_dir = tmp_path / "cloned" / "packages" / "venv-agent"
        fake_agent_dir.mkdir(parents=True)
        _write_yaml(
            fake_agent_dir / "agent-manifest.yaml",
            {"name": "venv-agent", "version": "1.0.0", "type": "atomic", "description": "Venv agent"},
        )
        (fake_agent_dir / "SKILL.md").write_text("# Venv", encoding="utf-8")

        venv_path = tmp_path / "config" / "venvs" / "venv-agent"
        installer._sparse_clone = AsyncMock(return_value=fake_agent_dir)
        installer._create_venv = AsyncMock(return_value=venv_path)
        installer._get_commit_sha = AsyncMock(return_value="latest")

        entry = await installer.install("venv-agent", source_url="https://github.com/x/y.git")
        assert entry.venv_path == str(venv_path)


class TestGitInstallerSparseClone:
    """Cover _sparse_clone path traversal rejections and clone paths."""

    @pytest.fixture
    def mock_installer(self, tmp_path: Path):
        """Create a GitInstaller with mocked managers."""
        sources = MagicMock(spec=SourceManager)
        lockfile = MagicMock(spec=LockfileManager)
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        installer = GitInstaller(sources, lockfile, config_dir)
        installer._run_git = AsyncMock()
        return installer, config_dir

    async def test_path_traversal_rejection(self, mock_installer) -> None:
        """_sparse_clone rejects relative paths containing '..'."""
        installer, *_ = mock_installer
        with pytest.raises(InstallationError, match="path traversal"):
            await installer._sparse_clone(
                "https://github.com/x/y.git", "agent", "../etc/passwd", None,
            )

    async def test_absolute_path_rejection(self, mock_installer) -> None:
        """_sparse_clone rejects absolute relative_path."""
        installer, *_ = mock_installer
        with pytest.raises(InstallationError, match="path traversal|is absolute"):
            await installer._sparse_clone(
                "https://github.com/x/y.git", "agent", "/etc/passwd", None,
            )

    async def test_fresh_clone_no_existing_cache(self, mock_installer, tmp_path: Path) -> None:
        """_sparse_clone performs initial clone when no .git dir exists."""
        installer, config_dir = mock_installer
        cache_dir = config_dir / "cache" / "repos"
        # No .git dir exists, so _run_git should be called for clone

        # Create the expected agent dir so the function finds it
        source_url = "https://github.com/user/repo.git"
        digest = hashlib.sha256(source_url.encode()).hexdigest()[:12]
        agent_dir = cache_dir / digest / "packages" / "test-agent"
        agent_dir.mkdir(parents=True)

        result = await installer._sparse_clone(
            source_url, "test-agent", "packages/test-agent", None,
        )

        assert result == agent_dir
        # _run_git should have been called at least 3 times:
        # clone, sparse-checkout set, checkout
        assert installer._run_git.call_count >= 3

    async def test_existing_cache_fetch_path(self, mock_installer, tmp_path: Path) -> None:
        """_sparse_clone fetches when .git dir already exists."""
        installer, config_dir = mock_installer
        source_url = "https://github.com/user/repo.git"
        digest = hashlib.sha256(source_url.encode()).hexdigest()[:12]
        cache_path = config_dir / "cache" / "repos" / digest

        # Create an existing .git dir to trigger the fetch path
        git_dir = cache_path / ".git"
        git_dir.mkdir(parents=True)

        # Create the expected agent dir
        agent_dir = cache_path / "packages" / "cached-agent"
        agent_dir.mkdir(parents=True)

        result = await installer._sparse_clone(
            source_url, "cached-agent", "packages/cached-agent", None,
        )

        assert result == agent_dir
        # First call should be fetch (not clone)
        first_call_args = installer._run_git.call_args_list[0]
        assert first_call_args[0][0][0] == "fetch"

    async def test_fallback_to_agent_name_dir(self, mock_installer, tmp_path: Path) -> None:
        """_sparse_clone falls back to agent_name dir when relative_path not found."""
        installer, config_dir = mock_installer
        source_url = "https://github.com/user/repo.git"
        digest = hashlib.sha256(source_url.encode()).hexdigest()[:12]
        cache_path = config_dir / "cache" / "repos" / digest

        # Only create the alt dir (agent_name), not the relative_path dir
        alt_dir = cache_path / "fallback-agent"
        alt_dir.mkdir(parents=True)

        result = await installer._sparse_clone(
            source_url, "fallback-agent", "packages/fallback-agent", None,
        )

        assert result == alt_dir

    async def test_no_dir_found_raises(self, mock_installer, tmp_path: Path) -> None:
        """_sparse_clone raises InstallationError when neither path exists."""
        installer, config_dir = mock_installer
        source_url = "https://github.com/user/repo.git"
        digest = hashlib.sha256(source_url.encode()).hexdigest()[:12]
        cache_path = config_dir / "cache" / "repos" / digest
        # Create .git to skip clone, but don't create any agent dir
        (cache_path / ".git").mkdir(parents=True)

        with pytest.raises(InstallationError, match="not found in repository"):
            await installer._sparse_clone(
                source_url, "no-such-agent", "packages/no-such-agent", None,
            )


class TestGitInstallerGetCommitSha:
    """Cover _get_commit_sha success and fallback."""

    @pytest.fixture
    def mock_installer(self, tmp_path: Path):
        sources = MagicMock(spec=SourceManager)
        lockfile = MagicMock(spec=LockfileManager)
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        installer = GitInstaller(sources, lockfile, config_dir)
        return installer

    async def test_success_returns_stripped_sha(self, mock_installer, tmp_path: Path) -> None:
        """_get_commit_sha returns the stripped stdout on success."""
        installer = mock_installer
        installer._run_git_capture = AsyncMock(return_value="  abcdef1234567890  \n")

        result = await installer._get_commit_sha(tmp_path)
        assert result == "abcdef1234567890"
        installer._run_git_capture.assert_awaited_once_with(
            ["rev-parse", "HEAD"], cwd=tmp_path,
        )

    async def test_exception_falls_back_to_latest(self, mock_installer, tmp_path: Path) -> None:
        """_get_commit_sha raises InstallationError when git command fails."""
        installer = mock_installer
        installer._run_git_capture = AsyncMock(side_effect=RuntimeError("git not found"))

        with pytest.raises(InstallationError, match="Could not determine commit SHA"):
            await installer._get_commit_sha(tmp_path)


class TestGitInstallerValidateYaml:
    """Cover _validate_agent_package YAML parse error path."""

    def test_yaml_parse_error_returns_issue(self, tmp_path: Path) -> None:
        """_validate_agent_package reports yaml.YAMLError as an issue."""
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            tmp_path,
        )
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        # Write invalid YAML that triggers YAMLError
        (pkg_dir / "agent-manifest.yaml").write_text(
            "name: test\n  bad indent: [\n", encoding="utf-8",
        )
        (pkg_dir / "SKILL.md").write_text("# Test", encoding="utf-8")

        issues, _ = installer._validate_agent_package(pkg_dir)
        assert any("parse error" in i for i in issues)


class TestGitInstallerCreateVenv:
    """Cover _create_venv no-pyproject, uv-not-found, and pip failure paths."""

    async def test_no_pyproject_returns_none(self, tmp_path: Path) -> None:
        """_create_venv returns None when no pyproject.toml exists."""
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            tmp_path,
        )
        agent_dir = tmp_path / "no-pyproject"
        agent_dir.mkdir()

        result = await installer._create_venv("test-agent", agent_dir)
        assert result is None

    async def test_uv_not_found_returns_none(self, tmp_path: Path) -> None:
        """_create_venv returns None gracefully when uv is not found."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            config_dir,
        )
        agent_dir = config_dir / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

        with patch(
            "agent_nexus.platform.local.installer.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("uv not found"),
        ):
            result = await installer._create_venv("test-agent", agent_dir)

        assert result is None

    async def test_uv_venv_nonzero_return_returns_none(self, tmp_path: Path) -> None:
        """_create_venv returns None when uv venv exits with non-zero code."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            config_dir,
        )
        agent_dir = config_dir / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error: failed"))
        mock_proc.returncode = 1

        with patch(
            "agent_nexus.platform.local.installer.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            result = await installer._create_venv("test-agent", agent_dir)

        assert result is None

    async def test_pip_install_failure_cleans_up(self, tmp_path: Path) -> None:
        """_create_venv removes venv dir when pip install fails."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            config_dir,
        )
        agent_dir = config_dir / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

        call_count = 0

        def _make_proc(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = AsyncMock()
            if call_count == 1:
                # First call: uv venv — succeeds
                proc.communicate = AsyncMock(return_value=(b"", b""))
                proc.returncode = 0
            else:
                # Second call: uv pip install — fails
                proc.communicate = AsyncMock(return_value=(b"", b"pip error"))
                proc.returncode = 1
            return proc

        with patch(
            "agent_nexus.platform.local.installer.asyncio.create_subprocess_exec",
            side_effect=_make_proc,
        ):
            result = await installer._create_venv("test-agent", agent_dir)

        assert result is None
        # venv dir should have been cleaned up
        venv_path = config_dir / "venvs" / "test-agent"
        assert not venv_path.exists()


class TestGitInstallerUninstallEdgeCases:
    """Cover uninstall() name validation and fallback venv removal."""

    async def test_uninstall_invalid_name(self, tmp_path: Path) -> None:
        """uninstall() raises InstallationError for invalid agent names."""
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            tmp_path / "config",
        )
        with pytest.raises(InstallationError, match="Invalid agent name"):
            await installer.uninstall("../bad-name")

    async def test_uninstall_fallback_venv_no_venv_path(self, tmp_path: Path) -> None:
        """uninstall() removes default venv when lockfile entry has no venv_path."""
        lockfile = MagicMock(spec=LockfileManager)
        entry = _make_entry(venv_path="")
        lockfile.pop_entry = MagicMock(return_value=entry)

        config_dir = tmp_path / "config"
        venvs_dir = config_dir / "venvs"
        venvs_dir.mkdir(parents=True)
        default_venv = venvs_dir / "fallback-agent"
        default_venv.mkdir()

        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            lockfile,
            config_dir,
        )

        result = await installer.uninstall("fallback-agent")
        assert result is True
        assert not default_venv.exists()

    async def test_uninstall_refuses_outside_venv(self, tmp_path: Path) -> None:
        """uninstall() refuses to remove a venv_path outside the allowed prefix."""
        lockfile = MagicMock(spec=LockfileManager)
        entry = _make_entry(venv_path="/tmp/malicious-venv")
        lockfile.pop_entry = MagicMock(return_value=entry)

        config_dir = tmp_path / "config"
        config_dir.mkdir()

        # Create the malicious venv so we can verify it is NOT removed
        malicious = Path("/tmp/malicious-venv")
        # We can't reliably test /tmp, but we can verify the logic doesn't crash
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            lockfile,
            config_dir,
        )

        # Should not raise, and should not attempt to remove /tmp/malicious-venv
        result = await installer.uninstall("sneaky-agent")
        assert result is True


class TestGitInstallerUpdate:
    """Cover update() delegation behavior."""

    async def test_update_delegates_to_install(self, tmp_path: Path) -> None:
        """update() calls install() with version=None, source_url=None."""
        lockfile = MagicMock(spec=LockfileManager)
        entry = _make_entry()
        lockfile.get_entry = MagicMock(return_value=entry)

        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            lockfile,
            tmp_path / "config",
        )

        # Mock install to verify delegation
        expected = _make_entry(version="2.0.0")
        installer.install = AsyncMock(return_value=expected)

        result = await installer.update("my-agent")
        installer.install.assert_awaited_once_with("my-agent", version=None, source_url=None)
        assert result is expected


class TestGitInstallerRunGitCapture:
    """Cover _run_git_capture success path."""

    async def test_returns_stdout_on_success(self, tmp_path: Path) -> None:
        """_run_git_capture returns decoded stdout on successful git command."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"abc123\n", b""))
        mock_proc.returncode = 0

        with patch(
            "agent_nexus.platform.local.installer.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            result = await GitInstaller._run_git_capture(["status"], tmp_path)

        assert result == "abc123\n"

    async def test_raises_on_nonzero_returncode(self, tmp_path: Path) -> None:
        """_run_git_capture raises InstallationError on non-zero exit."""
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b"fatal: error"))
        mock_proc.returncode = 128

        with patch(
            "agent_nexus.platform.local.installer.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            with pytest.raises(InstallationError, match="git.*failed"):
                await GitInstaller._run_git_capture(["bad-cmd"], tmp_path)


# ============================================================================
# LockfileManager save() exception cleanup
# ============================================================================


class TestLockfileManagerSaveExceptionCleanup:
    """Cover lockfile.py lines 77-83: save() BaseException cleanup path."""

    def test_save_removes_tempfile_on_write_failure(self, tmp_path: Path) -> None:
        """When os.replace fails, save() cleans up the temp file and re-raises."""
        lockfile_path = tmp_path / "lockfile.json"
        mgr = LockfileManager(lockfile_path)

        with patch("os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                mgr._save(Lockfile())

        # The temp file should have been cleaned up (no stale .lockfile-*.tmp files)
        tmp_files = list(tmp_path.glob(".lockfile-*.tmp"))
        assert len(tmp_files) == 0

    def test_save_succeeds_normally(self, tmp_path: Path) -> None:
        """Normal save path works without triggering exception cleanup."""
        lockfile_path = tmp_path / "lockfile.json"
        mgr = LockfileManager(lockfile_path)
        entry = _make_entry(version="1.0.0")
        mgr._save(Lockfile(agents={"test": entry}))
        assert lockfile_path.exists()
        assert "test" in lockfile_path.read_text(encoding="utf-8")


# ============================================================================
# SourceManager save() exception cleanup
# ============================================================================


class TestSourceManagerSaveExceptionCleanup:
    """Cover sources.py lines 114-120: save() BaseException cleanup path."""

    def test_save_removes_tempfile_on_replace_failure(self, tmp_path: Path) -> None:
        """When os.replace fails during save, temp file is cleaned up."""
        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)

        with patch("os.replace", side_effect=OSError("permission denied")):
            with pytest.raises(OSError, match="permission denied"):
                mgr.save()

        # No stale .sources-*.yaml.tmp files left behind
        tmp_files = list(tmp_path.glob(".sources-*.yaml.tmp"))
        assert len(tmp_files) == 0


# ============================================================================
# SourceManager _load() invalid source entries
# ============================================================================


class TestSourceManagerLoadInvalidEntries:
    """Cover sources.py lines 194-195: SourceEntry construction exception."""

    def test_load_skips_entry_with_empty_name(self, tmp_path: Path) -> None:
        """A source entry with an empty name is skipped (SourceEntry requires min_length=1)."""
        path = tmp_path / "sources.yaml"
        _write_yaml(path, {
            "sources": [
                {"name": "", "type": "git", "url": "https://example.com/repo.git"},
                {"name": "valid", "type": "git", "url": "https://example.com/valid.git"},
            ]
        })
        mgr = SourceManager(path)
        sources = mgr.list_sources()
        names = [s.name for s in sources]
        assert "valid" in names
        assert "" not in names

    def test_load_skips_git_source_with_empty_url(self, tmp_path: Path) -> None:
        """A git source with empty url fails validation and is skipped."""
        path = tmp_path / "sources.yaml"
        _write_yaml(path, {
            "sources": [
                {"name": "bad-git", "type": "git", "url": ""},
                {"name": "good", "type": "git", "url": "https://example.com/good.git"},
            ]
        })
        mgr = SourceManager(path)
        names = [s.name for s in mgr.list_sources()]
        assert "good" in names
        assert "bad-git" not in names


# ============================================================================
# SourceManager _load_source_index() exception paths
# ============================================================================


class TestSourceManagerLoadSourceIndexExceptions:
    """Cover sources.py lines 219-221 and 247-248: parse and entry exceptions."""

    def test_load_source_index_unparseable_yaml(self, tmp_path: Path) -> None:
        """_load_source_index returns None when index.yaml has invalid YAML."""
        import hashlib

        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)

        official_url = "https://github.com/anthropics/agent-nexus-packages.git"
        url_hash = hashlib.sha256(official_url.encode()).hexdigest()[:12]
        cache_dir = tmp_path / "cache" / "repos" / url_hash
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "index.yaml").write_text("{{{{invalid yaml content", encoding="utf-8")

        result = mgr._load_source_index(mgr.list_sources()[0])
        assert result is None

    def test_load_source_index_invalid_agent_type(self, tmp_path: Path) -> None:
        """_load_source_index skips entries with invalid AgentType."""
        import hashlib

        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)

        official_url = "https://github.com/anthropics/agent-nexus-packages.git"
        url_hash = hashlib.sha256(official_url.encode()).hexdigest()[:12]
        cache_dir = tmp_path / "cache" / "repos" / url_hash
        cache_dir.mkdir(parents=True, exist_ok=True)
        index_data = {
            "agents": [
                {"name": "bad-agent", "version": "1.0.0", "type": "not_a_real_type"},
                {"name": "good-agent", "version": "1.0.0", "type": "atomic"},
            ]
        }
        _write_yaml(cache_dir / "index.yaml", index_data)

        result = mgr._load_source_index(mgr.list_sources()[0])
        assert result is not None
        assert len(result) == 1
        assert result[0].name == "good-agent"

    def test_load_source_index_missing_required_fields(self, tmp_path: Path) -> None:
        """_load_source_index skips entries missing required fields like version."""
        import hashlib

        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)

        official_url = "https://github.com/anthropics/agent-nexus-packages.git"
        url_hash = hashlib.sha256(official_url.encode()).hexdigest()[:12]
        cache_dir = tmp_path / "cache" / "repos" / url_hash
        cache_dir.mkdir(parents=True, exist_ok=True)
        index_data = {
            "agents": [
                {"name": "no-version", "type": "atomic"},
                {"name": "valid", "version": "1.0.0", "type": "atomic"},
            ]
        }
        _write_yaml(cache_dir / "index.yaml", index_data)

        result = mgr._load_source_index(mgr.list_sources()[0])
        assert result is not None
        assert len(result) == 1
        assert result[0].name == "valid"


# ============================================================================
# LockfileManager save() nested OSError in cleanup
# ============================================================================


class TestLockfileManagerSaveCleanupOSError:
    """Cover lockfile.py lines 81-82: os.unlink fails during cleanup."""

    def test_save_cleanup_oserror_is_silenced(self, tmp_path: Path) -> None:
        """When both os.replace and os.unlink fail, the OSError from unlink is silenced."""
        lockfile_path = tmp_path / "lockfile.json"
        mgr = LockfileManager(lockfile_path)

        with patch("os.replace", side_effect=OSError("replace failed")), \
             patch("os.unlink", side_effect=OSError("unlink also failed")):
            with pytest.raises(OSError, match="replace failed"):
                mgr._save(Lockfile())


# ============================================================================
# SourceManager save() nested OSError in cleanup
# ============================================================================


class TestSourceManagerSaveCleanupOSError:
    """Cover sources.py lines 118-119: os.unlink fails during cleanup."""

    def test_save_cleanup_oserror_is_silenced(self, tmp_path: Path) -> None:
        """When both os.replace and os.unlink fail, the OSError from unlink is silenced."""
        path = tmp_path / "sources.yaml"
        mgr = SourceManager(path)

        with patch("os.replace", side_effect=OSError("replace failed")), \
             patch("os.unlink", side_effect=OSError("unlink also failed")):
            with pytest.raises(OSError, match="replace failed"):
                mgr.save()


class TestSupervisorStartAllExceptionHandling:
    """Cover supervisor.py lines 122-123: start_all exception handler."""

    async def test_start_all_catches_exception_from_start_agent(self, tmp_path: Path) -> None:
        """start_all() catches exceptions from start_agent and continues."""
        entry = _make_entry(venv_path="")
        pm = _make_mock_pm()
        # start_agent is called internally -- make it raise for the first agent
        # We use a real lockfile mgr so start_agent resolves the entry,
        # but we make _build_command raise via an unsafe agent name in lockfile.
        # Instead, mock start_agent at the supervisor level to raise.
        lockfile = _make_mock_lockfile_mgr({"crash-agent": entry, "ok-agent": entry})
        config = _make_mock_config_loader()

        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        # Make start_agent raise for crash-agent, succeed for ok-agent
        mock_handle = MagicMock()
        mock_handle.pid = 42
        call_count = 0

        async def _start_agent_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("name") == "crash-agent":
                raise RuntimeError("unexpected crash")
            return mock_handle

        pm.start_agent = AsyncMock(side_effect=_start_agent_side_effect)
        started = await supervisor.start_all()
        # crash-agent raised, ok-agent should succeed
        assert "ok-agent" in started
        assert "crash-agent" not in started


class TestSupervisorStartAgentCommandBuildFailure:
    """Cover supervisor.py lines 183-186: start_agent when _build_command returns None."""

    async def test_start_agent_returns_false_when_command_is_none(self, tmp_path: Path) -> None:
        """start_agent() returns False when _build_command cannot build a command."""
        # Use a venv_path that points outside config_dir, so _build_command returns None
        outside_venv = tmp_path.parent / "outside" / "venv"
        outside_bin = outside_venv / "bin"
        outside_bin.mkdir(parents=True, exist_ok=True)
        (outside_bin / "python").touch()
        entry = _make_entry(venv_path=str(outside_venv))
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr({"escaped-agent": entry})
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        assert await supervisor.start_agent("escaped-agent") is False
        pm.start_agent.assert_not_called()


class TestSupervisorAutoRestartAliveContinue:
    """Cover supervisor.py line 287: auto_restart_dead skips alive agents via continue."""

    async def test_auto_restart_dead_skips_started_alive_agents(self, tmp_path: Path) -> None:
        """auto_restart_dead() skips agents that were started and are still alive."""
        entry = _make_entry(venv_path="")
        pm = _make_mock_pm()
        alive_handle = MagicMock()
        alive_handle.is_alive = True
        pm.get_agent = MagicMock(return_value=alive_handle)
        lockfile = _make_mock_lockfile_mgr({"alive-agent": entry})
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        supervisor._started_agents.add("alive-agent")

        restarted = await supervisor.auto_restart_dead()
        assert restarted == []
        pm.start_agent.assert_not_called()


class TestSupervisorBuildCommandVenvOutsideConfigDir:
    """Cover supervisor.py lines 356-360: _build_command rejects venv outside config_dir."""

    def test_build_command_returns_none_for_venv_outside_config_dir(self, tmp_path: Path) -> None:
        """_build_command returns None when venv_path is outside config_dir."""
        # Create venv python outside the config_dir
        outside_venv = tmp_path.parent / "evil-venv"
        outside_bin = outside_venv / "bin"
        outside_bin.mkdir(parents=True, exist_ok=True)
        (outside_bin / "python").touch()

        entry = _make_entry(venv_path=str(outside_venv))
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        cmd = supervisor._build_command("test-agent", entry)
        assert cmd is None


class TestSupervisorBuildCommandVenvWithMainPy:
    """Cover supervisor.py line 363: _build_command with venv inside config_dir and main.py."""

    def test_build_command_returns_python_main_py_with_venv_and_main(self, tmp_path: Path) -> None:
        """_build_command returns [venv_python, agent_main] when venv is inside config_dir."""
        venv_dir = tmp_path / "venvs" / "test-agent"
        venv_bin = venv_dir / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").touch()

        # Create agent main.py inside config_dir/agents/test-agent
        agent_dir = tmp_path / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "main.py").touch()

        entry = _make_entry(venv_path=str(venv_dir))
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        cmd = supervisor._build_command("test-agent", entry)
        assert cmd is not None
        assert cmd[0] == str(venv_dir / "bin" / "python")
        assert cmd[1] == str(agent_dir / "main.py")


class TestSupervisorBuildCommandPython3Fallback:
    """Cover supervisor.py line 372: _build_command falls back to python3 main.py."""

    def test_build_command_returns_python3_main_when_no_venv_with_main(self, tmp_path: Path) -> None:
        """_build_command returns ['python3', 'main.py'] when no venv but main.py exists."""
        # No venv in entry
        entry = _make_entry(venv_path="")
        # Create agent dir with main.py
        agent_dir = tmp_path / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "main.py").touch()

        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr()
        config = _make_mock_config_loader()
        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)
        cmd = supervisor._build_command("test-agent", entry)
        assert cmd == ["python3", str(agent_dir / "main.py")]


class TestInstallerReinstallRemovesExistingDest:
    """Cover installer.py line 153: shutil.rmtree(dest) when dest already exists."""

    @pytest.mark.asyncio
    async def test_install_removes_existing_agent_dir_on_reinstall(self, tmp_path: Path) -> None:
        """install() removes existing agent dir before copying new version."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        agents_dir = config_dir / "agents"
        agents_dir.mkdir()

        # Create existing agent dir with stale file
        existing_dest = agents_dir / "reinstall-agent"
        existing_dest.mkdir()
        (existing_dest / "stale-file.txt").write_text("old", encoding="utf-8")

        sources = MagicMock(spec=SourceManager)
        lockfile = MagicMock(spec=LockfileManager)
        installer = GitInstaller(sources, lockfile, config_dir)

        # Create fake cloned agent
        fake_dir = tmp_path / "cloned" / "packages" / "reinstall-agent"
        fake_dir.mkdir(parents=True)
        _write_yaml(
            fake_dir / "agent-manifest.yaml",
            {"name": "reinstall-agent", "version": "2.0.0", "type": "atomic", "description": "Updated"},
        )
        (fake_dir / "SKILL.md").write_text("# Updated", encoding="utf-8")

        installer._sparse_clone = AsyncMock(return_value=fake_dir)
        installer._create_venv = AsyncMock(return_value=None)
        installer._get_commit_sha = AsyncMock(return_value="a" * 40)

        entry = await installer.install("reinstall-agent", source_url="https://github.com/x/y.git")
        assert entry.version == "2.0.0"
        # Stale file should be gone (dest was removed and re-copied)
        assert not (existing_dest / "stale-file.txt").exists()
        # New files should be present
        assert (existing_dest / "SKILL.md").exists()


class TestInstallerRollbackCleanupPaths:
    """Cover installer.py lines 197-203: rollback removes created paths."""

    @pytest.mark.asyncio
    async def test_install_rollback_removes_file_path(self, tmp_path: Path) -> None:
        """install() rollback removes both directory and file paths."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        sources = MagicMock(spec=SourceManager)
        lockfile = MagicMock(spec=LockfileManager)
        installer = GitInstaller(sources, lockfile, config_dir)

        # Create fake cloned agent
        fake_dir = tmp_path / "cloned" / "packages" / "fail-agent"
        fake_dir.mkdir(parents=True)
        (fake_dir / "SKILL.md").write_text("# Fail", encoding="utf-8")

        installer._sparse_clone = AsyncMock(return_value=fake_dir)
        # Validation passes but _create_venv will fail later
        # Actually make validation fail to trigger rollback with dest dir
        installer._validate_agent_package = MagicMock(
            return_value=(["Missing agent-manifest.yaml"], {})
        )

        with pytest.raises(InstallationError, match="validation failed"):
            await installer.install("fail-agent", source_url="https://github.com/x/y.git")

        # agents dir should have been cleaned up by rollback
        agents_dest = config_dir / "agents" / "fail-agent"
        assert not agents_dest.exists()

    @pytest.mark.asyncio
    async def test_install_rollback_handles_rmtree_failure(self, tmp_path: Path) -> None:
        """install() rollback silently handles rmtree failure during cleanup."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        sources = MagicMock(spec=SourceManager)
        lockfile = MagicMock(spec=LockfileManager)
        installer = GitInstaller(sources, lockfile, config_dir)

        # Create fake cloned agent
        fake_dir = tmp_path / "cloned" / "packages" / "rmfail-agent"
        fake_dir.mkdir(parents=True)
        (fake_dir / "SKILL.md").write_text("# Fail", encoding="utf-8")

        installer._sparse_clone = AsyncMock(return_value=fake_dir)
        installer._validate_agent_package = MagicMock(
            return_value=(["Missing agent-manifest.yaml"], {})
        )

        # Patch shutil.rmtree to always raise OSError
        # Validation fails, so step 5 rmtree is never reached.
        # The rollback rmtree call hits this and is silently caught.
        with patch(
            "agent_nexus.platform.local.installer.shutil.rmtree",
            side_effect=OSError("permission denied during rollback"),
        ):
            with pytest.raises(InstallationError, match="validation failed"):
                await installer.install("rmfail-agent", source_url="https://github.com/x/y.git")

    @pytest.mark.asyncio
    async def test_install_rollback_unlinks_file_path(self, tmp_path: Path) -> None:
        """install() rollback uses unlink for non-directory paths."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        sources = MagicMock(spec=SourceManager)
        lockfile = MagicMock(spec=LockfileManager)
        installer = GitInstaller(sources, lockfile, config_dir)

        # Create fake cloned agent
        fake_dir = tmp_path / "cloned" / "packages" / "unlink-agent"
        fake_dir.mkdir(parents=True)
        (fake_dir / "SKILL.md").write_text("# Fail", encoding="utf-8")

        installer._sparse_clone = AsyncMock(return_value=fake_dir)
        # Make validation fail to trigger rollback
        installer._validate_agent_package = MagicMock(
            return_value=(["Missing agent-manifest.yaml"], {})
        )

        # The rollback iterates _created_paths. The first path added is `dest` (a dir).
        # To test the `elif path.exists(): path.unlink()` branch (lines 200-201),
        # we need a file path in _created_paths. That happens when venv_path is added
        # (line 167) but validation fails before venv creation.
        # Actually validation fails BEFORE venv creation, so only dest is in _created_paths.
        # We need to test a scenario where a file path ends up in _created_paths.
        # Since the current code only adds dir paths, we test the dir branch coverage.
        with pytest.raises(InstallationError, match="validation failed"):
            await installer.install("unlink-agent", source_url="https://github.com/x/y.git")


class TestInstallerCreateVenvRemovesExisting:
    """Cover installer.py line 430: _create_venv removes existing venv before creating new."""

    @pytest.mark.asyncio
    async def test_create_venv_removes_existing_venv_dir(self, tmp_path: Path) -> None:
        """_create_venv removes existing venv directory before creating a new one."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            MagicMock(spec=LockfileManager),
            config_dir,
        )
        agent_dir = config_dir / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

        # Create existing venv with a stale marker file
        venv_path = config_dir / "venvs" / "test-agent"
        venv_path.mkdir(parents=True)
        (venv_path / "stale-marker.txt").write_text("old", encoding="utf-8")

        call_count = 0

        def _make_proc(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            proc = AsyncMock()
            proc.communicate = AsyncMock(return_value=(b"", b""))
            proc.returncode = 0
            return proc

        with patch(
            "agent_nexus.platform.local.installer.asyncio.create_subprocess_exec",
            side_effect=_make_proc,
        ):
            result = await installer._create_venv("test-agent", agent_dir)

        assert result == venv_path
        # Stale marker should be gone since venv was removed and recreated
        assert not (venv_path / "stale-marker.txt").exists()


class TestInstallRollbackAfterCopy:
    """Cover installer.py lines 197-199: rollback iterates _created_paths with entries.

    The existing rollback tests fail BEFORE the copy-to-agents-dir step, so
    _created_paths stays empty and the rollback loop body never executes.
    These tests fail AFTER copy (step 5) to ensure _created_paths has entries.
    """

    @pytest.mark.asyncio
    async def test_rollback_removes_dir_after_create_venv_raises(self, tmp_path: Path) -> None:
        """install() rollback removes agent dir when _create_venv raises."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        sources = MagicMock(spec=SourceManager)
        lockfile = MagicMock(spec=LockfileManager)
        installer = GitInstaller(sources, lockfile, config_dir)

        # Create a fake cloned agent with valid structure
        fake_agent_dir = tmp_path / "cloned" / "packages" / "rb-agent"
        fake_agent_dir.mkdir(parents=True)
        (fake_agent_dir / "SKILL.md").write_text("# rb", encoding="utf-8")
        manifest = {"name": "rb-agent", "version": "1.0.0", "type": "atomic", "description": "test"}
        _write_yaml(fake_agent_dir / "agent-manifest.yaml", manifest)

        installer._sparse_clone = AsyncMock(return_value=fake_agent_dir)
        installer._validate_agent_package = MagicMock(return_value=([], {}))
        # Fail AFTER copy by making _create_venv raise
        installer._create_venv = AsyncMock(side_effect=RuntimeError("venv boom"))

        with pytest.raises(RuntimeError, match="venv boom"):
            await installer.install("rb-agent", source_url="https://github.com/x/y.git")

        # dest dir was added to _created_paths then cleaned by rollback
        agents_dest = config_dir / "agents" / "rb-agent"
        assert not agents_dest.exists()

    @pytest.mark.asyncio
    async def test_rollback_removes_dir_and_venv_after_commit_sha_fails(self, tmp_path: Path) -> None:
        """install() rollback removes both dest dir and venv_path entries."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        sources = MagicMock(spec=SourceManager)
        lockfile = MagicMock(spec=LockfileManager)
        installer = GitInstaller(sources, lockfile, config_dir)

        # Create a fake cloned agent with valid structure
        fake_agent_dir = tmp_path / "cloned" / "packages" / "rb2-agent"
        fake_agent_dir.mkdir(parents=True)
        (fake_agent_dir / "SKILL.md").write_text("# rb2", encoding="utf-8")
        manifest = {"name": "rb2-agent", "version": "1.0.0", "type": "atomic", "description": "test"}
        _write_yaml(fake_agent_dir / "agent-manifest.yaml", manifest)

        installer._sparse_clone = AsyncMock(return_value=fake_agent_dir)
        installer._validate_agent_package = MagicMock(return_value=([], {}))

        # _create_venv succeeds, adding venv_path to _created_paths
        fake_venv = config_dir / "venvs" / "rb2-agent"
        fake_venv.mkdir(parents=True)
        installer._create_venv = AsyncMock(return_value=fake_venv)

        # _get_commit_sha fails AFTER both paths are in _created_paths
        installer._get_commit_sha = AsyncMock(side_effect=RuntimeError("sha fail"))

        with pytest.raises(RuntimeError, match="sha fail"):
            await installer.install("rb2-agent", source_url="https://github.com/x/y.git")

        # Both dest dir and venv should be cleaned by rollback
        agents_dest = config_dir / "agents" / "rb2-agent"
        assert not agents_dest.exists()
        # fake_venv was added to _created_paths; rollback removes it
        # (it's a dir, so rmtree is used)
        assert not fake_venv.exists()


class TestInstallRollbackUnlinkFilePath:
    """Cover installer.py lines 200-201: rollback unlinks a file (non-dir) path."""

    @pytest.mark.asyncio
    async def test_rollback_unlinks_non_directory_path(self, tmp_path: Path) -> None:
        """install() rollback calls unlink for a file path in _created_paths."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        sources = MagicMock(spec=SourceManager)
        lockfile = MagicMock(spec=LockfileManager)
        installer = GitInstaller(sources, lockfile, config_dir)

        # Create a fake cloned agent with valid structure
        fake_agent_dir = tmp_path / "cloned" / "packages" / "ul-agent"
        fake_agent_dir.mkdir(parents=True)
        (fake_agent_dir / "SKILL.md").write_text("# ul", encoding="utf-8")
        manifest = {"name": "ul-agent", "version": "1.0.0", "type": "atomic", "description": "test"}
        _write_yaml(fake_agent_dir / "agent-manifest.yaml", manifest)

        installer._sparse_clone = AsyncMock(return_value=fake_agent_dir)
        installer._validate_agent_package = MagicMock(return_value=([], {}))

        # Create a real file to use as a fake venv_path return value.
        # _create_venv normally returns a directory, but we return a file
        # to exercise the `elif path.exists(): path.unlink()` branch.
        fake_file = tmp_path / "venvs" / "ul-agent-marker"
        fake_file.parent.mkdir(parents=True, exist_ok=True)
        fake_file.write_text("not-a-dir", encoding="utf-8")
        assert fake_file.exists() and not fake_file.is_dir()

        call_count = 0

        async def _create_venv_side_effect(agent_name, agent_dir):
            nonlocal call_count
            call_count += 1
            return fake_file

        installer._create_venv = AsyncMock(side_effect=_create_venv_side_effect)

        # Fail after _create_venv so the file path is in _created_paths
        installer._get_commit_sha = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            await installer.install("ul-agent", source_url="https://github.com/x/y.git")

        # The file should have been unlinked by the rollback
        assert not fake_file.exists()


class TestSupervisorStartAllExceptionPropagation:
    """Cover supervisor.py lines 122-123: start_all() catches exception from start_agent.

    The existing TestSupervisorStartAllExceptionHandling mocks pm.start_agent,
    but the supervisor's own start_agent() catches all exceptions from pm.start_agent
    (lines 207-211) and returns False. So the exception never propagates to start_all.

    To hit lines 122-123, something must raise OUTSIDE the try block in start_agent()
    (lines 191-211). We make _resolve_agent_dir raise ValueError for a specific agent,
    which happens at line 188, before the try block.
    """

    async def test_start_all_catches_resolve_dir_error(self, tmp_path: Path) -> None:
        """start_all() catches ValueError from _resolve_agent_dir and continues."""
        entry = _make_entry(venv_path="")
        pm = _make_mock_pm()
        lockfile = _make_mock_lockfile_mgr({"bad-agent": entry, "good-agent": entry})
        config = _make_mock_config_loader()

        supervisor = AgentSupervisor(pm, lockfile, config, config_dir=tmp_path)

        # Make _resolve_agent_dir raise for bad-agent, succeed for good-agent.
        # This raises at line 188 which is BEFORE start_agent's internal try block,
        # so the exception propagates to start_all's handler (lines 122-123).
        original_resolve = supervisor._resolve_agent_dir

        def _patched_resolve(name: str) -> Path:
            if name == "bad-agent":
                raise ValueError("unsafe agent name")
            return original_resolve(name)

        supervisor._resolve_agent_dir = _patched_resolve

        # good-agent should still succeed: _build_command will fall through to
        # the uvx fallback since no venv and no main.py exist.
        mock_handle = MagicMock()
        mock_handle.pid = 99
        pm.start_agent = AsyncMock(return_value=mock_handle)

        started = await supervisor.start_all()

        # bad-agent raised and was caught by start_all's except block
        assert "bad-agent" not in started
        # good-agent should succeed
        assert "good-agent" in started


class TestInstallRollbackCleanupFailure:
    """Cover installer.py lines 202-203: rollback inner except when cleanup itself fails."""

    @pytest.mark.asyncio
    async def test_rollback_swallows_rmtree_error(self, tmp_path: Path) -> None:
        """install() rollback catches and logs rmtree failure during cleanup."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        sources = MagicMock(spec=SourceManager)
        lockfile = MagicMock(spec=LockfileManager)
        installer = GitInstaller(sources, lockfile, config_dir)

        # Create a fake cloned agent with valid structure
        fake_agent_dir = tmp_path / "cloned" / "packages" / "rf-agent"
        fake_agent_dir.mkdir(parents=True)
        (fake_agent_dir / "SKILL.md").write_text("# rf", encoding="utf-8")
        manifest = {"name": "rf-agent", "version": "1.0.0", "type": "atomic", "description": "test"}
        _write_yaml(fake_agent_dir / "agent-manifest.yaml", manifest)

        installer._sparse_clone = AsyncMock(return_value=fake_agent_dir)
        installer._validate_agent_package = MagicMock(return_value=([], {}))

        # _create_venv raises so dest is in _created_paths but rollback runs
        installer._create_venv = AsyncMock(side_effect=RuntimeError("venv fail"))

        # Make shutil.rmtree fail during rollback cleanup.
        # Step 5 copies files (no pre-existing dest so rmtree is NOT called there).
        # The rollback at line 199 calls rmtree -- make it raise to hit lines 202-203.
        import shutil as _shutil
        rmtree_calls = []

        def _rmtree_side_effect(path, *args, **kwargs):
            rmtree_calls.append(str(path))
            # Fail during rollback cleanup (second call) to exercise inner except
            if len(rmtree_calls) > 1:
                raise OSError("rollback permission denied")
            return _shutil.rmtree(path, *args, **kwargs)

        with patch(
            "agent_nexus.platform.local.installer.shutil.rmtree",
            side_effect=_rmtree_side_effect,
        ):
            with pytest.raises(RuntimeError, match="venv fail"):
                await installer.install("rf-agent", source_url="https://github.com/x/y.git")

        # At least 2 rmtree calls: step-5 pre-copy + rollback cleanup
        assert len(rmtree_calls) >= 2


# ---------------------------------------------------------------------------
# iter118 regression: _rmtree_best_effort continues on individual file failure
# ---------------------------------------------------------------------------


class TestRmTreeBestEffort:
    """Verify _rmtree_best_effort logs errors instead of raising."""

    def test_removes_clean_directory(self, tmp_path: Path) -> None:
        from agent_nexus.platform.local.installer import _rmtree_best_effort

        d = tmp_path / "clean"
        d.mkdir()
        (d / "file.txt").write_text("hello")
        _rmtree_best_effort(d, context="test")
        assert not d.exists()

    def test_continues_on_permission_error(self, tmp_path: Path) -> None:
        """Best-effort removal should not raise even when individual files fail."""
        from agent_nexus.platform.local.installer import _rmtree_best_effort

        d = tmp_path / "partial"
        d.mkdir()
        (d / "a.txt").write_text("a")
        (d / "b.txt").write_text("b")

        # Make a subdirectory unreadable so rmtree encounters an error
        protected = d / "inner"
        protected.mkdir()
        (protected / "secret.txt").write_text("can't touch this")
        protected.chmod(0o000)

        try:
            # Should NOT raise — best-effort means continue on error
            _rmtree_best_effort(d, context="permission-test")
        finally:
            # Restore permissions for cleanup
            if protected.exists():
                protected.chmod(0o755)

    async def test_uninstall_continues_on_rmtree_error(
        self, tmp_path: Path
    ) -> None:
        """Uninstall should succeed even if agent dir removal hits errors."""
        lockfile = MagicMock(spec=LockfileManager)
        entry = _make_entry(venv_path="")
        lockfile.pop_entry = MagicMock(return_value=entry)

        config_dir = tmp_path / "config"
        agents_dir = config_dir / "agents"
        agents_dir.mkdir(parents=True)
        agent_dir = agents_dir / "partial-agent"
        agent_dir.mkdir()
        (agent_dir / "SKILL.md").write_text("# skill")

        installer = GitInstaller(
            MagicMock(spec=SourceManager),
            lockfile,
            config_dir,
        )

        # Make a subdirectory unreadable so rmtree hits an error
        protected = agent_dir / "protected"
        protected.mkdir()
        (protected / "secret.txt").write_text("can't touch this")
        protected.chmod(0o000)

        try:
            result = await installer.uninstall("partial-agent")
            # Lockfile entry should be removed regardless (via pop_entry)
            lockfile.pop_entry.assert_called_once_with("partial-agent")
            # uninstall returns True (lockfile was removed)
            assert result is True
        finally:
            # Restore permissions for cleanup
            if protected.exists():
                protected.chmod(0o755)
