"""Unit tests for GitInstaller: install/uninstall/update agents from git repos."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.models.agent import AgentType
from agent_nexus.models.distribution import LockfileEntry, SourceEntry
from agent_nexus.platform.local.installer import (
    AgentNotFoundError,
    GitInstaller,
    InstallationError,
    _url_to_source_name,
)


def _make_entry() -> LockfileEntry:
    return LockfileEntry(
        version="1.0.0", source="official",
        commit_sha="a" * 40, agent_type=AgentType.ATOMIC,
    )


def _make_installer(tmp_path: Path) -> tuple[GitInstaller, MagicMock, MagicMock]:
    sources = MagicMock()
    sources.resolve_agent_source.return_value = None
    lf = MagicMock()
    lf.get_entry.return_value = None
    lf.add_entry_by_name = MagicMock()
    lf.remove_entry = MagicMock()
    installer = GitInstaller(sources, lf, tmp_path)
    return installer, sources, lf


# ---------------------------------------------------------------------------
# _url_to_source_name helper
# ---------------------------------------------------------------------------

class TestUrlToSourceName:
    def test_extracts_repo_name(self) -> None:
        assert _url_to_source_name("https://github.com/user/my-repo.git") == "my-repo"

    def test_strips_trailing_slash(self) -> None:
        assert _url_to_source_name("https://github.com/user/repo/") == "repo"

    def test_handles_no_git_suffix(self) -> None:
        assert _url_to_source_name("https://example.com/pkg") == "pkg"

    def test_returns_direct_for_empty(self) -> None:
        # rsplit on "/" gives "" when URL is just ""
        result = _url_to_source_name("")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------

class TestInstallerInstall:
    @pytest.mark.asyncio
    async def test_install_rejects_invalid_name(self, tmp_path: Path) -> None:
        inst, _, _ = _make_installer(tmp_path)
        with pytest.raises(InstallationError, match="Invalid agent name"):
            await inst.install("../../../etc/passwd")

    @pytest.mark.asyncio
    async def test_install_raises_not_found_when_no_source(self, tmp_path: Path) -> None:
        inst, sources, _ = _make_installer(tmp_path)
        sources.resolve_agent_source.return_value = None
        with pytest.raises(AgentNotFoundError, match="not found"):
            await inst.install("missing-agent")

    @pytest.mark.asyncio
    async def test_install_with_direct_source_url(self, tmp_path: Path) -> None:
        inst, _, lf = _make_installer(tmp_path)
        # Patch internals to avoid actual git/venv operations
        with patch.object(inst, "_sparse_clone", new_callable=AsyncMock) as mock_clone, \
             patch.object(inst, "_validate_agent_package", return_value=[]), \
             patch.object(inst, "_create_venv", new_callable=AsyncMock, return_value=None), \
             patch.object(inst, "_get_commit_sha", new_callable=AsyncMock, return_value="a" * 40), \
             patch("shutil.copytree"), \
             patch("shutil.rmtree"), \
             patch.object(Path, "exists", return_value=True):

            agent_dir = tmp_path / "cache" / "repos" / "abc" / "packages" / "test-agent"
            agent_dir.mkdir(parents=True, exist_ok=True)
            (agent_dir / "agent-manifest.yaml").write_text(
                "name: test-agent\nversion: 1.0.0\ntype: atomic\n", encoding="utf-8"
            )
            (agent_dir / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
            mock_clone.return_value = agent_dir

            entry = await inst.install(
                "test-agent", source_url="https://example.com/repo.git"
            )
            assert entry is not None
            lf.add_entry_by_name.assert_called_once()

    @pytest.mark.asyncio
    async def test_install_rollback_on_venv_failure(self, tmp_path: Path) -> None:
        """When _create_venv fails after files are copied, rollback cleans up."""
        inst, _, _ = _make_installer(tmp_path)
        agent_dir = tmp_path / "cache" / "repos" / "abc" / "packages" / "test-agent"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "agent-manifest.yaml").write_text(
            "name: test-agent\nversion: 1.0.0\ntype: atomic\n", encoding="utf-8"
        )
        (agent_dir / "SKILL.md").write_text("# Skill\n", encoding="utf-8")

        dest = inst._agents_dir / "test-agent"

        with patch.object(inst, "_sparse_clone", new_callable=AsyncMock, return_value=agent_dir), \
             patch.object(inst, "_validate_agent_package", return_value=[]), \
             patch.object(inst, "_read_manifest", return_value=None), \
             patch.object(inst, "_create_venv", new_callable=AsyncMock, side_effect=OSError("venv boom")), \
             patch.object(inst, "_get_commit_sha", new_callable=AsyncMock, return_value="a" * 40):

            with pytest.raises(OSError, match="venv boom"):
                await inst.install("test-agent", source_url="https://example.com/repo.git")

        # Rollback should have cleaned up the copied directory
        assert not dest.exists(), "Rollback should remove copied agent directory"

    @pytest.mark.asyncio
    async def test_install_rollback_on_validation_failure(self, tmp_path: Path) -> None:
        """When validation fails before copy, no files are left behind."""
        inst, _, _ = _make_installer(tmp_path)
        agent_dir = tmp_path / "cache" / "repos" / "abc" / "packages" / "test-agent"
        agent_dir.mkdir(parents=True, exist_ok=True)

        dest = inst._agents_dir / "test-agent"

        with patch.object(inst, "_sparse_clone", new_callable=AsyncMock, return_value=agent_dir), \
             patch.object(inst, "_validate_agent_package", return_value=["missing SKILL.md"]):

            with pytest.raises(InstallationError, match="validation failed"):
                await inst.install("test-agent", source_url="https://example.com/repo.git")

        # Validation fails before copytree, so dest should not exist
        assert not dest.exists()


# ---------------------------------------------------------------------------
# uninstall
# ---------------------------------------------------------------------------

class TestInstallerUninstall:
    @pytest.mark.asyncio
    async def test_uninstall_rejects_invalid_name(self, tmp_path: Path) -> None:
        inst, _, _ = _make_installer(tmp_path)
        with pytest.raises(InstallationError, match="Invalid agent name"):
            await inst.uninstall("../../etc")

    @pytest.mark.asyncio
    async def test_uninstall_returns_false_when_not_installed(self, tmp_path: Path) -> None:
        inst, _, lf = _make_installer(tmp_path)
        lf.get_entry.return_value = None
        assert await inst.uninstall("ghost") is False

    @pytest.mark.asyncio
    async def test_uninstall_removes_lockfile_entry_first(self, tmp_path: Path) -> None:
        inst, _, lf = _make_installer(tmp_path)
        entry = _make_entry()
        lf.get_entry.return_value = entry

        agent_dir = tmp_path / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "main.py").write_text("print('hi')", encoding="utf-8")

        with patch("shutil.rmtree"):
            result = await inst.uninstall("test-agent")
        assert result is True
        lf.remove_entry.assert_called_once_with("test-agent")

    @pytest.mark.asyncio
    async def test_uninstall_refuses_venv_outside_allowed_dir(self, tmp_path: Path) -> None:
        inst, _, lf = _make_installer(tmp_path)
        entry = LockfileEntry(
            version="1.0.0", source="official",
            commit_sha="a" * 40, agent_type=AgentType.ATOMIC,
            venv_path="/tmp/malicious/path",
        )
        lf.get_entry.return_value = entry
        agent_dir = tmp_path / "agents" / "agent-x"
        agent_dir.mkdir(parents=True)

        with patch("shutil.rmtree"):
            result = await inst.uninstall("agent-x")
        assert result is True


# ---------------------------------------------------------------------------
# update / get_installed_version
# ---------------------------------------------------------------------------

class TestInstallerUpdate:
    @pytest.mark.asyncio
    async def test_update_raises_when_not_installed(self, tmp_path: Path) -> None:
        inst, _, lf = _make_installer(tmp_path)
        lf.get_entry.return_value = None
        with pytest.raises(AgentNotFoundError, match="not installed"):
            await inst.update("ghost")


class TestInstallerGetVersion:
    def test_get_installed_version_returns_version(self, tmp_path: Path) -> None:
        inst, _, lf = _make_installer(tmp_path)
        entry = _make_entry()
        lf.get_entry.return_value = entry
        assert inst.get_installed_version("test-agent") == "1.0.0"

    def test_get_installed_version_returns_none(self, tmp_path: Path) -> None:
        inst, _, lf = _make_installer(tmp_path)
        lf.get_entry.return_value = None
        assert inst.get_installed_version("ghost") is None


# ---------------------------------------------------------------------------
# install — manifest validation
# ---------------------------------------------------------------------------

class TestInstallerManifestValidation:
    """Regression: invalid manifest data raises InstallationError, not ValidationError."""

    @pytest.mark.asyncio
    async def test_invalid_manifest_raises_installation_error(self, tmp_path: Path) -> None:
        inst, _, lf = _make_installer(tmp_path)

        agent_dir = tmp_path / "cache" / "repos" / "abc" / "packages" / "bad-manifest"
        agent_dir.mkdir(parents=True)
        # Valid YAML structure but invalid name (contains spaces)
        (agent_dir / "agent-manifest.yaml").write_text(
            "name: 'has spaces'\nversion: 1.0.0\ntype: atomic\ndescription: test\n",
            encoding="utf-8",
        )
        (agent_dir / "SKILL.md").write_text("# Skill\n", encoding="utf-8")

        # Also write the manifest to the agents dir (where _read_manifest reads from)
        dest = tmp_path / "agents" / "bad-manifest"
        dest.mkdir(parents=True)
        (dest / "agent-manifest.yaml").write_text(
            "name: 'has spaces'\nversion: 1.0.0\ntype: atomic\ndescription: test\n",
            encoding="utf-8",
        )

        with patch.object(inst, "_sparse_clone", new_callable=AsyncMock, return_value=agent_dir), \
             patch.object(inst, "_validate_agent_package", return_value=[]), \
             patch.object(inst, "_create_venv", new_callable=AsyncMock, return_value=None), \
             patch.object(inst, "_get_commit_sha", new_callable=AsyncMock, return_value="a" * 40), \
             patch("shutil.copytree"), \
             patch("shutil.rmtree"):

            # _read_manifest reads from dest, returns dict with name="has spaces"
            # AgentManifest(**dict) fails validation, wrapped in InstallationError
            with pytest.raises(InstallationError, match="invalid manifest data"):
                await inst.install(
                    "bad-manifest",
                    source_url="https://example.com/repo.git",
                )


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

class TestInstallerValidation:
    def test_validate_rejects_missing_manifest(self, tmp_path: Path) -> None:
        inst, _, _ = _make_installer(tmp_path)
        agent_dir = tmp_path / "pkg"
        agent_dir.mkdir()
        (agent_dir / "SKILL.md").write_text("# x\n", encoding="utf-8")
        issues = inst._validate_agent_package(agent_dir)
        assert any("manifest" in i.lower() for i in issues)

    def test_validate_rejects_missing_skill_md(self, tmp_path: Path) -> None:
        inst, _, _ = _make_installer(tmp_path)
        agent_dir = tmp_path / "pkg"
        agent_dir.mkdir()
        (agent_dir / "agent-manifest.yaml").write_text(
            "name: x\nversion: 1.0.0\ntype: atomic\n", encoding="utf-8"
        )
        issues = inst._validate_agent_package(agent_dir)
        assert any("SKILL" in i for i in issues)

    def test_validate_passes_valid_package(self, tmp_path: Path) -> None:
        inst, _, _ = _make_installer(tmp_path)
        agent_dir = tmp_path / "pkg"
        agent_dir.mkdir()
        (agent_dir / "agent-manifest.yaml").write_text(
            "name: x\nversion: 1.0.0\ntype: atomic\n", encoding="utf-8"
        )
        (agent_dir / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
        issues = inst._validate_agent_package(agent_dir)
        assert issues == []

    def test_validate_rejects_invalid_type(self, tmp_path: Path) -> None:
        inst, _, _ = _make_installer(tmp_path)
        agent_dir = tmp_path / "pkg"
        agent_dir.mkdir()
        (agent_dir / "agent-manifest.yaml").write_text(
            "name: x\nversion: 1.0.0\ntype: super-duper\n", encoding="utf-8"
        )
        (agent_dir / "SKILL.md").write_text("# Skill\n", encoding="utf-8")
        issues = inst._validate_agent_package(agent_dir)
        assert any("Invalid agent type" in i for i in issues)


# ---------------------------------------------------------------------------
# cache path
# ---------------------------------------------------------------------------

class TestInstallerCachePath:
    def test_cache_path_is_deterministic(self, tmp_path: Path) -> None:
        inst, _, _ = _make_installer(tmp_path)
        url = "https://example.com/repo.git"
        p1 = inst._get_cache_path(url)
        p2 = inst._get_cache_path(url)
        assert p1 == p2

    def test_cache_path_differs_for_different_urls(self, tmp_path: Path) -> None:
        inst, _, _ = _make_installer(tmp_path)
        p1 = inst._get_cache_path("https://a.com/r.git")
        p2 = inst._get_cache_path("https://b.com/r.git")
        assert p1 != p2


class TestCreateVenvBroadExcept:
    """_create_venv catches all exceptions, not just FileNotFoundError."""

    @pytest.mark.asyncio
    async def test_permission_error_returns_none(self, tmp_path: Path) -> None:
        """PermissionError during venv creation returns None instead of crashing."""
        inst, sources, lockfile = _make_installer(tmp_path)
        agent_dir = tmp_path / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "pyproject.toml").write_text("[project]\nname='t'\nversion='0'\n")

        with patch("asyncio.create_subprocess_exec", side_effect=PermissionError("denied")):
            result = await inst._create_venv("test-agent", agent_dir)

        assert result is None


class TestCreateVenvCleanupOnFailure:
    """Regression: _create_venv cleans up orphan venv directory on all failure paths."""

    @pytest.mark.asyncio
    async def test_venv_cleanup_on_uv_venv_failure(self, tmp_path: Path) -> None:
        """Orphan venv directory is cleaned up when 'uv venv' returns non-zero."""
        inst, _, _ = _make_installer(tmp_path)
        agent_dir = tmp_path / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "pyproject.toml").write_text("[project]\nname='t'\nversion='0'\n")

        venv_path = tmp_path / "venvs" / "test-agent"

        proc_mock = MagicMock()
        proc_mock.returncode = 1
        proc_mock.communicate = AsyncMock(return_value=(b"", b"error"))

        with patch("asyncio.create_subprocess_exec", return_value=proc_mock):
            result = await inst._create_venv("test-agent", agent_dir)

        assert result is None
        assert not venv_path.exists()

    @pytest.mark.asyncio
    async def test_venv_cleanup_on_file_not_found(self, tmp_path: Path) -> None:
        """Orphan venv directory is cleaned up when uv is not found."""
        inst, _, _ = _make_installer(tmp_path)
        agent_dir = tmp_path / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "pyproject.toml").write_text("[project]\nname='t'\nversion='0'\n")

        venv_path = tmp_path / "venvs" / "test-agent"
        # Simulate uv creating a partial venv directory before the FileNotFoundError
        venv_path.mkdir(parents=True)

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("no uv")):
            result = await inst._create_venv("test-agent", agent_dir)

        assert result is None
        assert not venv_path.exists()


# ---------------------------------------------------------------------------
# Regression: symlink directory escape in _create_venv
# ---------------------------------------------------------------------------


class TestSymlinkEscapeFix:
    """When venv_path is a symlink pointing outside _venvs_dir, _create_venv
    must abort (return None) instead of creating a venv at the symlink target.

    Before the fix, the code logged a warning but continued to create the
    venv at the symlink path, which resolved to an arbitrary external
    directory — a directory escape vulnerability.
    """

    @pytest.mark.asyncio
    async def test_symlink_escape_returns_none(self, tmp_path: Path) -> None:
        """Symlink pointing outside _venvs_dir causes _create_venv to abort."""
        inst, _, _ = _make_installer(tmp_path)
        agent_dir = tmp_path / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "pyproject.toml").write_text("[project]\nname='t'\nversion='0'\n")

        # Create a symlink in venvs_dir pointing to an external directory
        external = tmp_path / "external-target"
        external.mkdir()
        venvs_dir = tmp_path / "venvs"
        venvs_dir.mkdir()
        symlink = venvs_dir / "test-agent"
        symlink.symlink_to(external)

        # _create_venv should detect the escape and return None
        result = await inst._create_venv("test-agent", agent_dir)

        assert result is None
        # External target should NOT have venv contents
        assert not (external / "bin").exists()
        assert not (external / "pyvenv.cfg").exists()


# ---------------------------------------------------------------------------
# Regression: subprocess FD cleanup on proc.communicate() exception
# ---------------------------------------------------------------------------


class TestSubprocessFDLeakFix:
    """proc.communicate() can raise (CancelledError, transport error).
    Without cleanup, the subprocess is orphaned (FD leak).

    Fix: wrap communicate() in try/except with proc.kill() + proc.wait().
    """

    @pytest.mark.asyncio
    async def test_run_git_kills_proc_on_communicate_error(self) -> None:
        """_run_git kills and waits for the subprocess if communicate() raises."""
        proc_mock = MagicMock()
        proc_mock.communicate = AsyncMock(side_effect=asyncio.CancelledError())
        proc_mock.kill = MagicMock()
        proc_mock.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=proc_mock):
            with pytest.raises(asyncio.CancelledError):
                await GitInstaller._run_git(["status"], Path("/tmp"))

        proc_mock.kill.assert_called_once()
        proc_mock.wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_git_capture_kills_proc_on_communicate_error(self) -> None:
        """_run_git_capture kills and waits for the subprocess if communicate() raises."""
        proc_mock = MagicMock()
        proc_mock.communicate = AsyncMock(side_effect=OSError("transport"))
        proc_mock.kill = MagicMock()
        proc_mock.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=proc_mock):
            with pytest.raises(OSError, match="transport"):
                await GitInstaller._run_git_capture(["rev-parse", "HEAD"], Path("/tmp"))

        proc_mock.kill.assert_called_once()
        proc_mock.wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_venv_kills_proc_on_communicate_error(self, tmp_path: Path) -> None:
        """_create_venv kills and waits for the subprocess if communicate() raises.

        CancelledError is BaseException (not Exception) so it bypasses the
        outer except Exception in _create_venv and propagates up — but the
        inner handler still kills the proc before re-raising.
        """
        inst, _, _ = _make_installer(tmp_path)
        agent_dir = tmp_path / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "pyproject.toml").write_text("[project]\nname='t'\nversion='0'\n")

        proc_mock = MagicMock()
        proc_mock.communicate = AsyncMock(side_effect=asyncio.CancelledError())
        proc_mock.kill = MagicMock()
        proc_mock.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", return_value=proc_mock):
            # CancelledError bypasses outer except Exception, propagates up
            with pytest.raises(asyncio.CancelledError):
                await inst._create_venv("test-agent", agent_dir)

        proc_mock.kill.assert_called_once()
        proc_mock.wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_venv_kills_proc_on_pip_install_communicate_error(
        self, tmp_path: Path,
    ) -> None:
        """Second subprocess (uv pip install) communicate() error kills proc."""
        inst, _, _ = _make_installer(tmp_path)
        agent_dir = tmp_path / "agents" / "test-agent"
        agent_dir.mkdir(parents=True)
        (agent_dir / "pyproject.toml").write_text("[project]\nname='t'\nversion='0'\n")

        # First proc (uv venv) succeeds
        ok_proc = MagicMock()
        ok_proc.communicate = AsyncMock(return_value=(b"", b""))
        ok_proc.returncode = 0

        # Second proc (uv pip install) fails on communicate
        fail_proc = MagicMock()
        fail_proc.communicate = AsyncMock(side_effect=asyncio.CancelledError())
        fail_proc.kill = MagicMock()
        fail_proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", side_effect=[ok_proc, fail_proc]):
            with pytest.raises(asyncio.CancelledError):
                await inst._create_venv("test-agent", agent_dir)

        fail_proc.kill.assert_called_once()
        fail_proc.wait.assert_called_once()
