"""Unit tests for GitInstaller: install_local and update happy-path flows."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.platform.local.installer import (
    AgentNotFoundError,
    GitInstaller,
    InstallationError,
)
from agent_nexus.models.distribution import LockfileEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_installer(tmp_path: Path) -> GitInstaller:
    """Build a GitInstaller with mock dependencies."""
    return GitInstaller(
        source_manager=MagicMock(),
        lockfile_manager=MagicMock(),
        config_dir=tmp_path / "config",
    )


def _make_entry(**overrides) -> MagicMock:
    """Build a mock LockfileEntry."""
    entry = MagicMock(spec=LockfileEntry)
    entry.version = overrides.get("version", "1.0.0")
    return entry


# ===========================================================================
# TestInstallLocal
# ===========================================================================


@pytest.mark.timeout(10)
class TestInstallLocal:
    """Tests for GitInstaller.install_local."""

    @pytest.mark.asyncio
    async def test_invalid_name_raises(self, tmp_path: Path) -> None:
        """agent_name with special chars raises InstallationError."""
        installer = _make_installer(tmp_path)
        local_path = tmp_path / "agent"
        local_path.mkdir()

        with pytest.raises(InstallationError, match="Invalid agent name"):
            await installer.install_local("bad!name", local_path)

    @pytest.mark.asyncio
    async def test_nonexistent_path_raises(self, tmp_path: Path) -> None:
        """local_path that does not exist raises InstallationError."""
        installer = _make_installer(tmp_path)
        missing = tmp_path / "no-such-dir"

        with pytest.raises(InstallationError, match="Local agent path does not exist"):
            await installer.install_local("valid-name", missing)

    @pytest.mark.asyncio
    async def test_invalid_package_raises(self, tmp_path: Path) -> None:
        """_validate_agent_package returns issues -> InstallationError."""
        installer = _make_installer(tmp_path)
        local_path = tmp_path / "agent"
        local_path.mkdir()

        with patch.object(installer, "_validate_agent_package", return_value=(["Missing SKILL.md"], {})):
            with pytest.raises(InstallationError, match="validation failed"):
                await installer.install_local("valid-name", local_path)

    @pytest.mark.asyncio
    async def test_successful_install(self, tmp_path: Path) -> None:
        """All steps succeed -> returns entry, calls lockfile.add_entry_by_name."""
        installer = _make_installer(tmp_path)
        local_path = tmp_path / "agent"
        local_path.mkdir()

        dest = tmp_path / "config" / "agents" / "valid-name"
        entry = _make_entry()

        with (
            patch.object(installer, "_validate_agent_package", return_value=([], {"name": "x"})),
            patch.object(installer, "_copy_to_agents_dir", return_value=dest),
            patch.object(installer, "_parse_manifest_safe", return_value=MagicMock()),
            patch.object(installer, "_create_venv", new_callable=AsyncMock, return_value=None),
            patch.object(installer, "_get_local_commit_sha", new_callable=AsyncMock, return_value="abc123"),
            patch.object(installer, "_build_lockfile_entry", return_value=entry),
        ):
            result = await installer.install_local("valid-name", local_path)

        assert result is entry
        installer._lockfile.add_entry_by_name.assert_called_once_with("valid-name", entry)  # type: ignore[union-attr]

    @pytest.mark.asyncio
    async def test_rollback_on_copy_failure(self, tmp_path: Path) -> None:
        """_copy_to_agents_dir raises -> _rollback_paths called, exception re-raised."""
        installer = _make_installer(tmp_path)
        local_path = tmp_path / "agent"
        local_path.mkdir()

        with (
            patch.object(installer, "_validate_agent_package", return_value=([], {})),
            patch.object(installer, "_copy_to_agents_dir", side_effect=OSError("disk full")),
            patch.object(installer, "_rollback_paths") as mock_rollback,
        ):
            with pytest.raises(OSError, match="disk full"):
                await installer.install_local("valid-name", local_path)

        mock_rollback.assert_called_once()
        args = mock_rollback.call_args[0]
        assert args[1] == "valid-name"
        assert args[2] == "Local install"

    @pytest.mark.asyncio
    async def test_rollback_on_venv_failure(self, tmp_path: Path) -> None:
        """_create_venv raises -> _rollback_paths called with both dest and venv paths."""
        installer = _make_installer(tmp_path)
        local_path = tmp_path / "agent"
        local_path.mkdir()

        dest = tmp_path / "config" / "agents" / "valid-name"

        with (
            patch.object(installer, "_validate_agent_package", return_value=([], {})),
            patch.object(installer, "_copy_to_agents_dir", return_value=dest),
            patch.object(installer, "_parse_manifest_safe", return_value=MagicMock()),
            patch.object(installer, "_create_venv", new_callable=AsyncMock, side_effect=RuntimeError("venv boom")),
            patch.object(installer, "_rollback_paths") as mock_rollback,
        ):
            with pytest.raises(RuntimeError, match="venv boom"):
                await installer.install_local("valid-name", local_path)

        # _created_paths should contain dest (copy succeeded before venv)
        rolled_back_paths = mock_rollback.call_args[0][0]
        assert dest in rolled_back_paths

    @pytest.mark.asyncio
    async def test_venv_path_recorded(self, tmp_path: Path) -> None:
        """_create_venv returns a path -> added to _created_paths, passed to _build_lockfile_entry."""
        installer = _make_installer(tmp_path)
        local_path = tmp_path / "agent"
        local_path.mkdir()

        dest = tmp_path / "config" / "agents" / "valid-name"
        venv = tmp_path / "config" / "venvs" / "valid-name"
        entry = _make_entry()

        with (
            patch.object(installer, "_validate_agent_package", return_value=([], {"name": "x"})),
            patch.object(installer, "_copy_to_agents_dir", return_value=dest),
            patch.object(installer, "_parse_manifest_safe", return_value=MagicMock()),
            patch.object(installer, "_create_venv", new_callable=AsyncMock, return_value=venv),
            patch.object(installer, "_get_local_commit_sha", new_callable=AsyncMock, return_value="abc123"),
            patch.object(GitInstaller, "_build_lockfile_entry", return_value=entry) as mock_build,
            patch.object(installer, "_rollback_paths"),
        ):
            result = await installer.install_local("valid-name", local_path)

        assert result is entry
        assert mock_build.call_args.kwargs["venv_path"] == venv

    @pytest.mark.asyncio
    async def test_commit_sha_from_local(self, tmp_path: Path) -> None:
        """_get_local_commit_sha result is forwarded to _build_lockfile_entry."""
        installer = _make_installer(tmp_path)
        local_path = tmp_path / "agent"
        local_path.mkdir()

        dest = tmp_path / "config" / "agents" / "valid-name"
        entry = _make_entry()

        with (
            patch.object(installer, "_validate_agent_package", return_value=([], {"name": "x"})),
            patch.object(installer, "_copy_to_agents_dir", return_value=dest),
            patch.object(installer, "_parse_manifest_safe", return_value=MagicMock()),
            patch.object(installer, "_create_venv", new_callable=AsyncMock, return_value=None),
            patch.object(installer, "_get_local_commit_sha", new_callable=AsyncMock, return_value="deadbeef" * 5),
            patch.object(GitInstaller, "_build_lockfile_entry", return_value=entry) as mock_build,
        ):
            await installer.install_local("valid-name", local_path)

        assert mock_build.call_args.kwargs["commit_sha"] == "deadbeef" * 5


# ===========================================================================
# TestUpdateHappyPath
# ===========================================================================


@pytest.mark.timeout(10)
class TestUpdateHappyPath:
    """Tests for GitInstaller.update."""

    @pytest.mark.asyncio
    async def test_agent_not_installed_raises(self, tmp_path: Path) -> None:
        """lockfile.get_entry returns None -> AgentNotFoundError."""
        installer = _make_installer(tmp_path)
        installer._lockfile.get_entry = MagicMock(return_value=None)

        with pytest.raises(AgentNotFoundError, match="is not installed"):
            await installer.update("missing-agent")

    @pytest.mark.asyncio
    async def test_delegates_to_install(self, tmp_path: Path) -> None:
        """get_entry returns existing -> calls self.install with version=None, source_url=None."""
        installer = _make_installer(tmp_path)
        existing = _make_entry()
        installer._lockfile.get_entry = MagicMock(return_value=existing)

        entry = _make_entry(version="2.0.0")
        installer.install = AsyncMock(return_value=entry)

        await installer.update("my-agent")

        installer.install.assert_awaited_once_with("my-agent", version=None, source_url=None)

    @pytest.mark.asyncio
    async def test_returns_install_result(self, tmp_path: Path) -> None:
        """update returns the same LockfileEntry that install returns."""
        installer = _make_installer(tmp_path)
        installer._lockfile.get_entry = MagicMock(return_value=_make_entry())

        entry = _make_entry(version="3.0.0")
        installer.install = AsyncMock(return_value=entry)

        result = await installer.update("my-agent")

        assert result is entry

    @pytest.mark.asyncio
    async def test_install_receives_correct_args(self, tmp_path: Path) -> None:
        """install is called with the correct agent_name."""
        installer = _make_installer(tmp_path)
        installer._lockfile.get_entry = MagicMock(return_value=_make_entry())
        installer.install = AsyncMock(return_value=_make_entry())

        await installer.update("feature-agent")

        installer.install.assert_awaited_once()
        call_args = installer.install.call_args
        assert call_args.args[0] == "feature-agent"

    @pytest.mark.asyncio
    async def test_propagates_install_error(self, tmp_path: Path) -> None:
        """install raises InstallationError -> propagated through update."""
        installer = _make_installer(tmp_path)
        installer._lockfile.get_entry = MagicMock(return_value=_make_entry())
        installer.install = AsyncMock(side_effect=InstallationError("clone failed"))

        with pytest.raises(InstallationError, match="clone failed"):
            await installer.update("my-agent")
