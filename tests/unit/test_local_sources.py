"""Unit tests for SourceManager: manage sources.yaml and resolve agent locations."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
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
        _write_sources(path, [
            {"name": "my-src", "type": "git", "url": "https://example.com/r.git"},
        ])
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
        _write_sources(path, [
            {"name": "private", "type": "git", "url": "https://example.com/r.git"},
        ])
        sm = SourceManager(path)
        assert sm.get_official_source() is None
