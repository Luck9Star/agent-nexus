"""Unit tests for SourceManager: manage sources.yaml and resolve agent locations."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import hashlib

import yaml

from agent_nexus.models.distribution import IndexEntry, SourceEntry
from agent_nexus.platform.local.sources import SourceManager


def _make_source(name: str = "test-src", url: str = "https://example.com/repo.git") -> SourceEntry:
    return SourceEntry(name=name, type="git", url=url, branch="main")


def _write_sources(path: Path, sources: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump({"sources": sources}, allow_unicode=True),
        encoding="utf-8",
    )


class TestSourceManagerInit:
    """SourceManager.__init__ and _load"""

    def test_init_creates_default_when_file_missing(self, tmp_path: Path) -> None:
        sm = SourceManager(tmp_path / "sources.yaml")
        sources = sm.list_sources()
        assert len(sources) >= 1
        assert sources[0].name == "official"

    def test_init_loads_valid_file(self, tmp_path: Path) -> None:
        path = tmp_path / "sources.yaml"
        _write_sources(
            path,
            [
                {"name": "my-src", "type": "git", "url": "https://example.com/r.git"},
            ],
        )
        sm = SourceManager(path)
        names = [s.name for s in sm.list_sources()]
        assert "my-src" in names

    def test_init_handles_invalid_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "sources.yaml"
        path.write_text("{{invalid yaml", encoding="utf-8")
        sm = SourceManager(path)
        assert len(sm.list_sources()) >= 1  # falls back to official

    def test_init_handles_missing_sources_key(self, tmp_path: Path) -> None:
        path = tmp_path / "sources.yaml"
        path.write_text(yaml.dump({"other": []}), encoding="utf-8")
        sm = SourceManager(path)
        assert len(sm.list_sources()) >= 1  # falls back to official

    def test_init_handles_non_list_sources(self, tmp_path: Path) -> None:
        path = tmp_path / "sources.yaml"
        path.write_text(yaml.dump({"sources": "not-a-list"}), encoding="utf-8")
        sm = SourceManager(path)
        assert len(sm.list_sources()) >= 1


class TestSourceAddRemove:
    """SourceManager.add_source() and remove_source()"""

    def test_add_source_appends(self, tmp_path: Path) -> None:
        path = tmp_path / "sources.yaml"
        sm = SourceManager(path)
        src = _make_source("new-one")
        sm.add_source(src)
        assert any(s.name == "new-one" for s in sm.list_sources())

    def test_add_source_updates_existing_by_name(self, tmp_path: Path) -> None:
        path = tmp_path / "sources.yaml"
        sm = SourceManager(path)
        sm.add_source(_make_source("x", url="https://old.com/r.git"))
        sm.add_source(_make_source("x", url="https://new.com/r.git"))
        sources = sm.list_sources()
        x_entries = [s for s in sources if s.name == "x"]
        assert len(x_entries) == 1
        assert x_entries[0].url == "https://new.com/r.git"

    def test_remove_source_returns_true(self, tmp_path: Path) -> None:
        path = tmp_path / "sources.yaml"
        sm = SourceManager(path)
        sm.add_source(_make_source("to-remove"))
        assert sm.remove_source("to-remove") is True
        assert not any(s.name == "to-remove" for s in sm.list_sources())

    def test_remove_source_returns_false_for_missing(self, tmp_path: Path) -> None:
        path = tmp_path / "sources.yaml"
        sm = SourceManager(path)
        assert sm.remove_source("ghost") is False


class TestSourceList:
    """SourceManager.list_sources()"""

    def test_official_source_comes_first(self, tmp_path: Path) -> None:
        path = tmp_path / "sources.yaml"
        sm = SourceManager(path)
        sm.add_source(_make_source("zeta"))
        sm.add_source(_make_source("official", url="https://off.com/r.git"))
        sources = sm.list_sources()
        assert sources[0].name == "official"


class TestSourceSave:
    """SourceManager.save()"""

    def test_save_persists_to_disk(self, tmp_path: Path) -> None:
        path = tmp_path / "sources.yaml"
        sm = SourceManager(path)
        sm.add_source(_make_source("persisted"))
        # Reload from disk
        sm2 = SourceManager(path)
        assert any(s.name == "persisted" for s in sm2.list_sources())

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        deep_path = tmp_path / "a" / "b" / "sources.yaml"
        sm = SourceManager(deep_path)
        sm.add_source(_make_source("deep"))
        assert deep_path.exists()


class TestSourceResolve:
    """SourceManager.resolve_agent_source()"""

    def test_resolve_returns_none_when_no_index(self, tmp_path: Path) -> None:
        path = tmp_path / "sources.yaml"
        sm = SourceManager(path)
        sm.add_source(_make_source("no-cache"))
        assert sm.resolve_agent_source("missing-agent") is None

    def test_resolve_finds_matching_entry(self, tmp_path: Path) -> None:
        from agent_nexus.models.agent import AgentType

        path = tmp_path / "sources.yaml"
        sm = SourceManager(path)
        src = _make_source("src-with-index")
        sm.add_source(src)

        # Write a real index.yaml in the expected cache location
        cache_dir = sm._get_cache_path(src)
        cache_dir.mkdir(parents=True, exist_ok=True)
        index_data = {
            "agents": [
                {
                    "name": "my-agent",
                    "version": "1.0.0",
                    "type": "atomic",
                    "path": "packages/my-agent",
                }
            ]
        }
        (cache_dir / "index.yaml").write_text(
            yaml.dump(index_data, allow_unicode=True), encoding="utf-8"
        )

        result = sm.resolve_agent_source("my-agent")
        assert result is not None
        source, rel_path = result
        assert source.name == "src-with-index"
        assert rel_path == "packages/my-agent"


class TestSourceGetOfficial:
    """SourceManager.get_official_source()"""

    def test_get_official_returns_entry(self, tmp_path: Path) -> None:
        sm = SourceManager(tmp_path / "sources.yaml")
        official = sm.get_official_source()
        assert official is not None
        assert official.name == "official"

    def test_get_official_returns_none_when_absent(self, tmp_path: Path) -> None:
        path = tmp_path / "sources.yaml"
        _write_sources(
            path,
            [
                {"name": "private", "type": "git", "url": "https://example.com/r.git"},
            ],
        )
        sm = SourceManager(path)
        assert sm.get_official_source() is None


# ---------------------------------------------------------------------------
# iter102 regression: bare dict subscript → .get() with validation
# ---------------------------------------------------------------------------


class TestSourceEntryValidation:
    """Source entries with missing required fields produce clear errors."""

    def test_source_entry_missing_name_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "sources.yaml"
        _write_sources(path, [{"type": "git", "url": "https://example.com/r.git"}])
        sm = SourceManager(path)
        # Falls back to official when no valid entries
        sm._ensure_loaded()
        assert len(sm._sources) == 1
        assert sm._sources[0].name == "official"

    def _write_index(
        self, tmp_path: Path, url: str, agents: list[dict]
    ) -> tuple[Path, SourceEntry]:
        """Write index.yaml at the correct cache path for a given URL."""
        src = SourceEntry(name="test", type="git", url=url, branch="main")
        digest = hashlib.sha256(url.encode()).hexdigest()[:12]
        cache_dir = tmp_path / "cache" / "repos" / digest
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "index.yaml").write_text(yaml.dump({"agents": agents}))
        return tmp_path, src

    def test_index_entry_missing_name_skipped(self, tmp_path: Path) -> None:
        sm = SourceManager(tmp_path / "sources.yaml")
        _, src = self._write_index(
            tmp_path,
            "https://example.com/r.git",
            [
                {"version": "1.0", "type": "atomic"},
            ],
        )
        entries = sm._load_source_index(src)
        assert entries is not None
        assert len(entries) == 0

    def test_index_entry_missing_version_skipped(self, tmp_path: Path) -> None:
        sm = SourceManager(tmp_path / "sources.yaml")
        _, src = self._write_index(
            tmp_path,
            "https://example.com/r2.git",
            [
                {"name": "test", "type": "atomic"},
            ],
        )
        entries = sm._load_source_index(src)
        assert entries is not None
        assert len(entries) == 0

    def test_index_entry_missing_type_skipped(self, tmp_path: Path) -> None:
        sm = SourceManager(tmp_path / "sources.yaml")
        _, src = self._write_index(
            tmp_path,
            "https://example.com/r3.git",
            [
                {"name": "test", "version": "1.0"},
            ],
        )
        entries = sm._load_source_index(src)
        assert entries is not None
        assert len(entries) == 0


# iter104 regression: search_agents public API coverage


class TestSearchAgents:
    """SourceManager.search_agents() keyword search across source indexes."""

    def _setup_index(
        self, tmp_path: Path, url: str, agents: list[dict]
    ) -> tuple[SourceManager, SourceEntry]:
        sm = SourceManager(tmp_path / "sources.yaml")
        src = SourceEntry(name="test", type="git", url=url, branch="main")
        sm.add_source(src)
        digest = hashlib.sha256(url.encode()).hexdigest()[:12]
        cache_dir = tmp_path / "cache" / "repos" / digest
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / "index.yaml").write_text(yaml.dump({"agents": agents}))
        return sm, src

    def test_search_finds_by_name(self, tmp_path: Path) -> None:
        sm, _ = self._setup_index(
            tmp_path,
            "https://example.com/s1.git",
            [
                {
                    "name": "code-reviewer",
                    "version": "1.0",
                    "type": "atomic",
                    "description": "Reviews code",
                    "tags": ["code", "review"],
                },
            ],
        )
        results = sm.search_agents("code-reviewer")
        assert len(results) == 1
        assert results[0][1].name == "code-reviewer"

    def test_search_finds_by_description(self, tmp_path: Path) -> None:
        sm, _ = self._setup_index(
            tmp_path,
            "https://example.com/s2.git",
            [
                {
                    "name": "my-agent",
                    "version": "1.0",
                    "type": "atomic",
                    "description": "Security vulnerability scanner",
                    "tags": [],
                },
            ],
        )
        results = sm.search_agents("vulnerability")
        assert len(results) == 1

    def test_search_finds_by_tag(self, tmp_path: Path) -> None:
        sm, _ = self._setup_index(
            tmp_path,
            "https://example.com/s3.git",
            [
                {
                    "name": "tool-agent",
                    "version": "1.0",
                    "type": "atomic",
                    "description": "A tool",
                    "tags": ["testing", "automation"],
                },
            ],
        )
        results = sm.search_agents("automation")
        assert len(results) == 1

    def test_search_case_insensitive(self, tmp_path: Path) -> None:
        sm, _ = self._setup_index(
            tmp_path,
            "https://example.com/s4.git",
            [
                {
                    "name": "Doc-Filler",
                    "version": "1.0",
                    "type": "atomic",
                    "description": "Fills documents",
                    "tags": [],
                },
            ],
        )
        results = sm.search_agents("doc-filler")
        assert len(results) == 1

    def test_search_no_match_returns_empty(self, tmp_path: Path) -> None:
        sm, _ = self._setup_index(
            tmp_path,
            "https://example.com/s5.git",
            [
                {
                    "name": "my-agent",
                    "version": "1.0",
                    "type": "atomic",
                    "description": "Does things",
                    "tags": [],
                },
            ],
        )
        results = sm.search_agents("nonexistent-query")
        assert results == []

    def test_search_returns_source_entry(self, tmp_path: Path) -> None:
        sm, src = self._setup_index(
            tmp_path,
            "https://example.com/s6.git",
            [
                {"name": "found-agent", "version": "1.0", "type": "atomic"},
            ],
        )
        results = sm.search_agents("found")
        assert len(results) == 1
        assert results[0][0].name == "test"
