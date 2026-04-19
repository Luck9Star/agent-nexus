"""Unit tests for LockfileManager: read/write lockfile.json atomically."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent_nexus.models.agent import AgentType
from agent_nexus.models.distribution import Lockfile, LockfileEntry
from agent_nexus.platform.local.lockfile import LockfileManager


def _make_entry(name: str = "test-agent") -> LockfileEntry:
    return LockfileEntry(
        version="1.0.0",
        source="official",
        commit_sha="a" * 40,
        agent_type=AgentType.ATOMIC,
    )


class TestLockfileLoad:
    """LockfileManager.load()"""

    def test_load_missing_file_returns_empty(self, tmp_path: Path) -> None:
        lf = LockfileManager(tmp_path / "lockfile.json")
        result = lf.load()
        assert isinstance(result, Lockfile)
        assert len(result.agents) == 0

    @patch.object(Path, "read_text")
    def test_load_invalid_json_returns_empty(self, mock_read: MagicMock, tmp_path: Path) -> None:
        lockfile_path = tmp_path / "lockfile.json"
        lockfile_path.write_text("not json")
        mock_read.side_effect = lambda **kw: "not json"
        lf = LockfileManager(lockfile_path)
        result = lf.load()
        assert isinstance(result, Lockfile)
        assert len(result.agents) == 0

    def test_load_valid_lockfile(self, tmp_path: Path) -> None:
        lockfile_path = tmp_path / "lockfile.json"
        entry = _make_entry()
        data = Lockfile(agents={"test-agent": entry}).model_dump(mode="json")
        lockfile_path.write_text(json.dumps(data), encoding="utf-8")

        lf = LockfileManager(lockfile_path)
        result = lf.load()
        assert "test-agent" in result.agents
        assert result.agents["test-agent"].version == "1.0.0"


class TestLockfileSave:
    """LockfileManager.save()"""

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        lockfile_path = tmp_path / "deep" / "nested" / "lockfile.json"
        lf = LockfileManager(lockfile_path)
        lf.save(Lockfile())
        assert lockfile_path.exists()

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        lockfile_path = tmp_path / "lockfile.json"
        lf = LockfileManager(lockfile_path)
        entry = _make_entry()
        lf.save(Lockfile(agents={"agent-a": entry}))

        loaded = lf.load()
        assert "agent-a" in loaded.agents
        assert loaded.agents["agent-a"].version == "1.0.0"

    def test_save_atomic_no_corrupt_on_failure(self, tmp_path: Path) -> None:
        lockfile_path = tmp_path / "lockfile.json"
        lockfile_path.write_text('{"version": 1, "agents": {}}', encoding="utf-8")
        lf = LockfileManager(lockfile_path)

        with patch("os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                lf.save(Lockfile())

        # Original file should still be intact
        raw = json.loads(lockfile_path.read_text(encoding="utf-8"))
        assert raw["version"] == 1


class TestLockfileGetEntry:
    """LockfileManager.get_entry() and get_entry_from()"""

    def test_get_entry_returns_entry(self, tmp_path: Path) -> None:
        lockfile_path = tmp_path / "lockfile.json"
        entry = _make_entry()
        data = Lockfile(agents={"my-agent": entry}).model_dump(mode="json")
        lockfile_path.write_text(json.dumps(data), encoding="utf-8")

        lf = LockfileManager(lockfile_path)
        result = lf.get_entry("my-agent")
        assert result is not None
        assert result.version == "1.0.0"

    def test_get_entry_returns_none_for_missing(self, tmp_path: Path) -> None:
        lockfile_path = tmp_path / "lockfile.json"
        lockfile_path.write_text(json.dumps(Lockfile().model_dump(mode="json")), encoding="utf-8")
        lf = LockfileManager(lockfile_path)
        assert lf.get_entry("nonexistent") is None

    def test_get_entry_from_preloaded_lockfile(self, tmp_path: Path) -> None:
        lf = LockfileManager(tmp_path / "lockfile.json")
        entry = _make_entry()
        lockfile = Lockfile(agents={"preloaded": entry})
        result = lf.get_entry_from(lockfile, "preloaded")
        assert result is not None
        assert result.source == "official"

    def test_get_entry_from_returns_none_for_missing(self, tmp_path: Path) -> None:
        lf = LockfileManager(tmp_path / "lockfile.json")
        lockfile = Lockfile()
        assert lf.get_entry_from(lockfile, "ghost") is None


class TestLockfileAddRemove:
    """LockfileManager.add_entry_by_name() and remove_entry()"""

    def test_add_entry_creates_new(self, tmp_path: Path) -> None:
        lockfile_path = tmp_path / "lockfile.json"
        lf = LockfileManager(lockfile_path)
        entry = _make_entry("new-agent")
        lf.add_entry_by_name("new-agent", entry)

        loaded = lf.load()
        assert "new-agent" in loaded.agents

    def test_add_entry_updates_existing(self, tmp_path: Path) -> None:
        lockfile_path = tmp_path / "lockfile.json"
        lf = LockfileManager(lockfile_path)
        entry_v1 = _make_entry("agent-x")
        lf.add_entry_by_name("agent-x", entry_v1)

        entry_v2 = LockfileEntry(
            version="2.0.0", source="official",
            commit_sha="b" * 40, agent_type=AgentType.ATOMIC,
        )
        lf.add_entry_by_name("agent-x", entry_v2)

        loaded = lf.load()
        assert loaded.agents["agent-x"].version == "2.0.0"

    def test_remove_entry_returns_true(self, tmp_path: Path) -> None:
        lockfile_path = tmp_path / "lockfile.json"
        lf = LockfileManager(lockfile_path)
        lf.add_entry_by_name("remove-me", _make_entry("remove-me"))
        assert lf.remove_entry("remove-me") is True
        assert lf.get_entry("remove-me") is None

    def test_remove_entry_returns_false_for_missing(self, tmp_path: Path) -> None:
        lockfile_path = tmp_path / "lockfile.json"
        lf = LockfileManager(lockfile_path)
        assert lf.remove_entry("ghost") is False


class TestLockfileListEntries:
    """LockfileManager.list_entries()"""

    def test_list_entries_returns_all(self, tmp_path: Path) -> None:
        lockfile_path = tmp_path / "lockfile.json"
        lf = LockfileManager(lockfile_path)
        lf.add_entry_by_name("a", _make_entry("a"))
        lf.add_entry_by_name("b", _make_entry("b"))
        entries = lf.list_entries()
        assert len(entries) == 2

    def test_list_entries_empty_lockfile(self, tmp_path: Path) -> None:
        lockfile_path = tmp_path / "lockfile.json"
        lf = LockfileManager(lockfile_path)
        assert lf.list_entries() == []
