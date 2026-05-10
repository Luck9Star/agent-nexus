"""Unit tests for GitInstaller: _run_git, _run_git_capture, _run_uv,
_create_venv, _validate_agent_package, _validate_git_url."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.platform.local.installer import (
    GitInstaller,
    InstallationError,
    _validate_git_url,
)


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


def _make_subprocess_mock(
    returncode: int = 0,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> MagicMock:
    """Create a mock asyncio.subprocess.Process."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


# ===========================================================================
# TestRunGit
# ===========================================================================


class TestRunGit:
    """Tests for GitInstaller._run_git (static, async)."""

    @pytest.mark.asyncio
    async def test_success(self, tmp_path: Path) -> None:
        """rc=0 completes without raising."""
        proc = _make_subprocess_mock(returncode=0, stderr=b"")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            await GitInstaller._run_git(["status"], cwd=tmp_path)

        proc.communicate.assert_awaited_once()
        proc.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_failure_raises_installation_error(self, tmp_path: Path) -> None:
        """rc=1 raises InstallationError with stderr content."""
        proc = _make_subprocess_mock(
            returncode=1,
            stderr=b"fatal: not a git repository",
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with pytest.raises(InstallationError, match="rc=1"):
                await GitInstaller._run_git(["pull"], cwd=tmp_path)

    @pytest.mark.asyncio
    async def test_base_exception_kills_process(self, tmp_path: Path) -> None:
        """KeyboardInterrupt during communicate kills the process and re-raises."""
        proc = _make_subprocess_mock()
        proc.communicate = AsyncMock(side_effect=KeyboardInterrupt)

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with pytest.raises(KeyboardInterrupt):
                await GitInstaller._run_git(["clone", "url"], cwd=tmp_path)

        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()


# ===========================================================================
# TestRunGitCapture
# ===========================================================================


class TestRunGitCapture:
    """Tests for GitInstaller._run_git_capture (static, async)."""

    @pytest.mark.asyncio
    async def test_success_returns_stdout(self, tmp_path: Path) -> None:
        """Returns decoded stdout on success."""
        proc = _make_subprocess_mock(
            returncode=0,
            stdout=b"abc123def456\n",
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await GitInstaller._run_git_capture(
                ["rev-parse", "HEAD"], cwd=tmp_path,
            )

        assert result == "abc123def456\n"

    @pytest.mark.asyncio
    async def test_failure_raises_installation_error(self, tmp_path: Path) -> None:
        """rc != 0 raises InstallationError."""
        proc = _make_subprocess_mock(
            returncode=128,
            stderr=b"unknown revision",
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with pytest.raises(InstallationError, match="rc=128"):
                await GitInstaller._run_git_capture(
                    ["rev-parse", "HEAD"], cwd=tmp_path,
                )

    @pytest.mark.asyncio
    async def test_utf8_decode_errors_replaced(self, tmp_path: Path) -> None:
        """Invalid UTF-8 bytes in stdout are replaced instead of crashing."""
        bad_stdout = b"\xff\xfe invalid utf8 \x80"
        proc = _make_subprocess_mock(returncode=0, stdout=bad_stdout)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await GitInstaller._run_git_capture(["log"], cwd=tmp_path)

        # Should not raise; replacement characters should appear
        assert "�" in result or "invalid utf8" in result


# ===========================================================================
# TestRunUv
# ===========================================================================


class TestRunUv:
    """Tests for GitInstaller._run_uv (static, async)."""

    @pytest.mark.asyncio
    async def test_success_returns_stderr(self) -> None:
        """Returns stderr bytes on success."""
        proc = _make_subprocess_mock(
            returncode=0,
            stderr=b"Resolved 10 packages",
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await GitInstaller._run_uv(["venv", ".venv"], "test venv")

        assert result == b"Resolved 10 packages"

    @pytest.mark.asyncio
    async def test_failure_returns_none(self) -> None:
        """rc != 0 returns None (does not raise)."""
        proc = _make_subprocess_mock(
            returncode=1,
            stderr=b"error: no such command",
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await GitInstaller._run_uv(["bad-cmd"], "test")

        assert result is None

    @pytest.mark.asyncio
    async def test_base_exception_kills_process(self) -> None:
        """BaseException during communicate kills process and re-raises."""
        proc = _make_subprocess_mock()
        proc.communicate = AsyncMock(side_effect=RuntimeError("oom"))

        with patch("asyncio.create_subprocess_exec", return_value=proc):
            with pytest.raises(RuntimeError, match="oom"):
                await GitInstaller._run_uv(["pip", "install", "x"], "test")

        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()


# ===========================================================================
# TestValidateGitUrl
# ===========================================================================


class TestValidateGitUrl:
    """Tests for _validate_git_url (module-level function)."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/org/repo.git",
            "http://example.com/repo",
            "git://github.com/org/repo.git",
            "ssh://git@github.com/org/repo.git",
        ],
    )
    def test_valid_urls(self, url: str) -> None:
        """Allowed schemes pass without error."""
        _validate_git_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/repo",
            "/local/path/to/repo",
        ],
    )
    def test_invalid_schemes(self, url: str) -> None:
        """Disallowed schemes raise ValueError."""
        with pytest.raises(ValueError, match="Invalid git URL scheme"):
            _validate_git_url(url)

    def test_http_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        """http:// URLs emit a warning about plaintext credentials."""
        import logging

        with caplog.at_level(logging.WARNING):
            _validate_git_url("http://example.com/repo.git")

        assert any("plaintext HTTP" in r.message for r in caplog.records)


# ===========================================================================
# TestValidateAgentPackage
# ===========================================================================


class TestValidateAgentPackage:
    """Tests for GitInstaller._validate_agent_package."""

    def _write_manifest(self, agent_dir: Path, content: str) -> None:
        manifest = agent_dir / "agent-manifest.yaml"
        manifest.write_text(content, encoding="utf-8")

    def test_valid_package(self, tmp_path: Path) -> None:
        """Valid manifest + SKILL.md yields no issues."""
        installer = _make_installer(tmp_path)
        agent_dir = tmp_path / "my-agent"
        agent_dir.mkdir()

        self._write_manifest(agent_dir, "name: my-agent\nversion: 1.0.0\ntype: atomic\n")
        (agent_dir / "SKILL.md").write_text("# Skill", encoding="utf-8")

        issues, manifest = installer._validate_agent_package(agent_dir)
        assert issues == []
        assert manifest["name"] == "my-agent"

    def test_missing_skill_md(self, tmp_path: Path) -> None:
        """Missing SKILL.md adds an issue but manifest is still parsed."""
        installer = _make_installer(tmp_path)
        agent_dir = tmp_path / "my-agent"
        agent_dir.mkdir()

        self._write_manifest(agent_dir, "name: my-agent\nversion: 1.0.0\ntype: atomic\n")

        issues, manifest = installer._validate_agent_package(agent_dir)
        assert "Missing SKILL.md" in issues
        assert manifest["name"] == "my-agent"

    def test_missing_manifest(self, tmp_path: Path) -> None:
        """Missing agent-manifest.yaml returns issue and empty dict."""
        installer = _make_installer(tmp_path)
        agent_dir = tmp_path / "my-agent"
        agent_dir.mkdir()
        (agent_dir / "SKILL.md").write_text("# Skill", encoding="utf-8")

        issues, manifest = installer._validate_agent_package(agent_dir)
        assert "No manifest found" in issues[0]
        assert manifest == {}

    def test_invalid_manifest_yaml(self, tmp_path: Path) -> None:
        """Malformed YAML in manifest returns parse error issue."""
        installer = _make_installer(tmp_path)
        agent_dir = tmp_path / "my-agent"
        agent_dir.mkdir()

        self._write_manifest(agent_dir, "{{invalid yaml: [}")
        (agent_dir / "SKILL.md").write_text("# Skill", encoding="utf-8")

        issues, _ = installer._validate_agent_package(agent_dir)
        assert any("parse error" in i for i in issues)

    def test_manifest_missing_required_field(self, tmp_path: Path) -> None:
        """Manifest missing 'version' field adds an issue."""
        installer = _make_installer(tmp_path)
        agent_dir = tmp_path / "my-agent"
        agent_dir.mkdir()

        self._write_manifest(agent_dir, "name: my-agent\ntype: atomic\n")
        (agent_dir / "SKILL.md").write_text("# Skill", encoding="utf-8")

        issues, manifest = installer._validate_agent_package(agent_dir)
        assert any("missing required field: version" in i for i in issues)
        assert manifest["name"] == "my-agent"


# ===========================================================================
# TestCreateVenv
# ===========================================================================


class TestCreateVenv:
    """Tests for GitInstaller._create_venv."""

    @pytest.mark.asyncio
    async def test_returns_none_if_no_pyproject(self, tmp_path: Path) -> None:
        """No pyproject.toml means no venv needed."""
        installer = _make_installer(tmp_path)
        agent_dir = tmp_path / "my-agent"
        agent_dir.mkdir()

        result = await installer._create_venv("my-agent", agent_dir)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_uv_failure(self, tmp_path: Path) -> None:
        """Failed _run_uv returns None and cleans up venv dir."""
        installer = _make_installer(tmp_path)
        agent_dir = tmp_path / "my-agent"
        agent_dir.mkdir()
        (agent_dir / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")

        # _run_uv returns None (failure) for both venv and pip calls
        installer._run_uv = AsyncMock(return_value=None)
        installer._validate_venv_path = MagicMock(return_value=True)

        result = await installer._create_venv("my-agent", agent_dir)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_venv_path_on_success(self, tmp_path: Path) -> None:
        """Successful venv creation returns the venv path."""
        installer = _make_installer(tmp_path)
        agent_dir = tmp_path / "my-agent"
        agent_dir.mkdir()
        (agent_dir / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")

        # First call: venv creation; second call: pip install
        installer._run_uv = AsyncMock(return_value=b"ok")
        installer._validate_venv_path = MagicMock(return_value=True)
        installer._has_extra = MagicMock(return_value=False)

        result = await installer._create_venv("my-agent", agent_dir)
        assert result is not None
        assert result.name == "my-agent"
        assert "venvs" in str(result)

    @pytest.mark.asyncio
    async def test_validates_venv_path(self, tmp_path: Path) -> None:
        """_validate_venv_path is called; if it fails, returns None."""
        installer = _make_installer(tmp_path)
        agent_dir = tmp_path / "my-agent"
        agent_dir.mkdir()
        (agent_dir / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")

        installer._validate_venv_path = MagicMock(return_value=False)

        result = await installer._create_venv("my-agent", agent_dir)
        assert result is None
