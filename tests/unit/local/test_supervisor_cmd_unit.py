"""Unit tests for AgentSupervisor: _resolve_package_name, _build_command,
_find_package_with_main, _find_package_with_init, _read_hatch_packages,
RestartTracker."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock


from agent_nexus.models.distribution import AgentType, LockfileEntry
from agent_nexus.platform.local.supervisor import AgentSupervisor, RestartTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_supervisor(tmp_path: Path) -> AgentSupervisor:
    """Build an AgentSupervisor with mock dependencies."""
    return AgentSupervisor(
        process_manager=MagicMock(),
        lockfile_manager=MagicMock(),
        config_loader=MagicMock(),
        config_dir=tmp_path / "config",
    )


def _make_entry(
    version: str = "1.0.0",
    source: str = "test",
    commit_sha: str = "a" * 40,
    venv_path: str = "",
) -> LockfileEntry:
    return LockfileEntry(
        version=version,
        source=source,
        commit_sha=commit_sha,
        agent_type=AgentType.ATOMIC,
        venv_path=venv_path,
    )


# ===========================================================================
# TestResolvePackageName
# ===========================================================================


class TestResolvePackageName:
    """Tests for AgentSupervisor._resolve_package_name."""

    def test_no_dir_returns_none(self, tmp_path: Path) -> None:
        """Non-existent directory returns None."""
        sup = _make_supervisor(tmp_path)
        result = sup._resolve_package_name("my-agent", tmp_path / "nonexistent")
        assert result is None

    def test_cached_result(self, tmp_path: Path) -> None:
        """Second call returns cached result without re-scanning filesystem."""
        sup = _make_supervisor(tmp_path)
        sup._resolved_packages["my-agent"] = "my_pkg"

        result1 = sup._resolve_package_name("my-agent", tmp_path)
        result2 = sup._resolve_package_name("my-agent", tmp_path)

        assert result1 == "my_pkg"
        assert result2 == "my_pkg"

    def test_finds_package_with_main(self, tmp_path: Path) -> None:
        """Subdirectory with __init__.py + main.py is found."""
        sup = _make_supervisor(tmp_path)
        agent_dir = tmp_path / "my-agent"
        pkg_dir = agent_dir / "my_pkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
        (pkg_dir / "main.py").write_text("pass", encoding="utf-8")

        result = sup._resolve_package_name("my-agent", agent_dir)
        assert result == "my_pkg"

    def test_finds_package_with_init_only(self, tmp_path: Path) -> None:
        """Subdirectory with only __init__.py (no main.py) is found."""
        sup = _make_supervisor(tmp_path)
        agent_dir = tmp_path / "my-agent"
        pkg_dir = agent_dir / "my_pkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("", encoding="utf-8")

        result = sup._resolve_package_name("my-agent", agent_dir)
        assert result == "my_pkg"

    def test_finds_via_hatch_config(self, tmp_path: Path) -> None:
        """pyproject.toml with hatch.build.targets.wheel.packages is used."""
        sup = _make_supervisor(tmp_path)
        agent_dir = tmp_path / "my-agent"
        agent_dir.mkdir()
        pyproject = agent_dir / "pyproject.toml"
        pyproject.write_text(
            '[tool.hatch.build.targets.wheel]\npackages = ["special_pkg"]\n',
            encoding="utf-8",
        )

        result = sup._resolve_package_name("my-agent", agent_dir)
        assert result == "special_pkg"

    def test_no_package_returns_none(self, tmp_path: Path) -> None:
        """Empty directory with no packages returns None."""
        sup = _make_supervisor(tmp_path)
        agent_dir = tmp_path / "my-agent"
        agent_dir.mkdir()

        result = sup._resolve_package_name("my-agent", agent_dir)
        assert result is None

    def test_skips_dot_and_underscore_dirs(self, tmp_path: Path) -> None:
        """Directories starting with '.' or '_' are ignored."""
        sup = _make_supervisor(tmp_path)
        agent_dir = tmp_path / "my-agent"
        agent_dir.mkdir()

        # Hidden/private dirs with valid structure
        for name in (".hidden", "_private"):
            d = agent_dir / name
            d.mkdir()
            (d / "__init__.py").write_text("", encoding="utf-8")
            (d / "main.py").write_text("pass", encoding="utf-8")

        result = sup._resolve_package_name("my-agent", agent_dir)
        assert result is None

    def test_result_is_cached(self, tmp_path: Path) -> None:
        """After first call, result is stored in _resolved_packages."""
        sup = _make_supervisor(tmp_path)
        agent_dir = tmp_path / "my-agent"
        pkg_dir = agent_dir / "my_pkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
        (pkg_dir / "main.py").write_text("pass", encoding="utf-8")

        sup._resolve_package_name("my-agent", agent_dir)
        assert "my-agent" in sup._resolved_packages
        assert sup._resolved_packages["my-agent"] == "my_pkg"


# ===========================================================================
# TestBuildCommand
# ===========================================================================


class TestBuildCommand:
    """Tests for AgentSupervisor._build_command."""

    def test_unsafe_name_returns_none(self, tmp_path: Path) -> None:
        """Agent name with path traversal characters returns None."""
        sup = _make_supervisor(tmp_path)
        entry = _make_entry()
        result = sup._build_command("../../../etc", entry)
        assert result is None

    def test_venv_strategy(self, tmp_path: Path) -> None:
        """Strategy 1: venv python + main.py when venv_path is set."""
        sup = _make_supervisor(tmp_path)
        config_dir = tmp_path / "config"
        agents_dir = config_dir / "agents" / "my-agent"
        agents_dir.mkdir(parents=True)

        # Create a main.py in agent dir
        (agents_dir / "main.py").write_text("pass", encoding="utf-8")

        # Create venv structure inside config_dir (safe path)
        venv_dir = config_dir / "venvs" / "my-agent" / "bin"
        venv_dir.mkdir(parents=True)
        python_bin = venv_dir / "python"
        python_bin.write_text("#!/bin/python", encoding="utf-8")

        entry = _make_entry(venv_path=str(config_dir / "venvs" / "my-agent"))
        result = sup._build_command("my-agent", entry)

        assert result is not None
        assert str(python_bin) in result[0]
        assert "main.py" in result[1]

    def test_system_python_strategy(self, tmp_path: Path) -> None:
        """Strategy 2: system python3 + main.py when no venv."""
        sup = _make_supervisor(tmp_path)
        config_dir = tmp_path / "config"
        agents_dir = config_dir / "agents" / "my-agent"
        agents_dir.mkdir(parents=True)
        (agents_dir / "main.py").write_text("pass", encoding="utf-8")

        entry = _make_entry(venv_path="")
        result = sup._build_command("my-agent", entry)

        assert result is not None
        assert result[0] == "python3"
        assert "main.py" in result[1]

    def test_uvx_fallback(self, tmp_path: Path) -> None:
        """Strategy 3: uvx fallback when no venv and no main.py."""
        sup = _make_supervisor(tmp_path)
        config_dir = tmp_path / "config"
        agents_dir = config_dir / "agents" / "my-agent"
        agents_dir.mkdir(parents=True)
        # No main.py, no venv

        entry = _make_entry(venv_path="")
        result = sup._build_command("my-agent", entry)

        assert result is not None
        assert result[0] == "uvx"
        assert result[1] == "my-agent"

    def test_venv_outside_config_dir_returns_none(self, tmp_path: Path) -> None:
        """Venv path outside config_dir is rejected (security)."""
        sup = _make_supervisor(tmp_path)
        config_dir = tmp_path / "config"
        agents_dir = config_dir / "agents" / "my-agent"
        agents_dir.mkdir(parents=True)
        (agents_dir / "main.py").write_text("pass", encoding="utf-8")

        # Venv outside config_dir
        outside_venv = tmp_path / "evil-venv"
        outside_venv.mkdir()
        bin_dir = outside_venv / "bin"
        bin_dir.mkdir()
        (bin_dir / "python").write_text("#!/bin/python", encoding="utf-8")

        entry = _make_entry(venv_path=str(outside_venv))
        result = sup._build_command("my-agent", entry)

        # Should fall through to system strategy since venv is outside config
        # But the venv_python exists, so it returns None (security block)
        assert result is None


# ===========================================================================
# TestRestartTracker (supplementary to existing tests)
# ===========================================================================


class TestRestartTrackerUnit:
    """P0-gap tests for RestartTracker: initial state, max enforcement, reset."""

    def test_initial_state_allows_retry(self) -> None:
        """Fresh tracker (count=0) allows retries."""
        tracker = RestartTracker()
        assert tracker.should_retry() is True

    def test_after_max_stops_retrying(self) -> None:
        """After recording max_restarts attempts, should_retry returns False."""
        tracker = RestartTracker(max_restarts=2)
        tracker.record()  # count=1
        tracker.record()  # count=2
        assert tracker.should_retry() is False

    def test_reset_allows_retry_again(self) -> None:
        """After reset, a maxed-out tracker allows retries again."""
        tracker = RestartTracker(max_restarts=1)
        tracker.record()
        assert tracker.should_retry() is False
        tracker.reset()
        assert tracker.should_retry() is True
        assert tracker.count == 0
