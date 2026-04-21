"""Tests for runtime_cmd.py -- start/stop/restart/status/logs/ps."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from agent_nexus.platform.local.cli import app

runner = CliRunner()


class TestStatus:
    def test_status_with_no_agents(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        with patch("agent_nexus.platform.local.cli.runtime_cmd._init_managers") as mock_init:
            mock_lockfile = MagicMock()
            mock_lockfile.load.return_value.agents = {}
            mock_init.return_value = (MagicMock(), mock_lockfile, MagicMock(), config_dir)

            result = runner.invoke(app, ["runtime", "status"])
            assert result.exit_code == 0
            assert "no agents" in result.output.lower() or "not installed" in result.output.lower()


class TestLogs:
    def test_logs_agent_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        with patch("agent_nexus.platform.local.cli.runtime_cmd._init_managers") as mock_init:
            mock_init.return_value = (MagicMock(), MagicMock(), MagicMock(), config_dir)

            result = runner.invoke(app, ["runtime", "logs", "nonexistent-agent"])
            assert "no log" in result.output.lower() or "not" in result.output.lower()


class TestPs:
    def test_ps_is_alias_for_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        with patch("agent_nexus.platform.local.cli.runtime_cmd._init_managers") as mock_init:
            mock_lockfile = MagicMock()
            mock_lockfile.load.return_value.agents = {}
            mock_init.return_value = (MagicMock(), mock_lockfile, MagicMock(), config_dir)

            result = runner.invoke(app, ["runtime", "ps"])
            assert result.exit_code == 0


class TestPathTraversalRejection:
    """Verify that path-traversal agent names are rejected at CLI entry points."""

    TRAVERSAL_NAMES = [
        "../../etc/cron.d/backdoor",
        "../hidden",
        "../../../tmp/evil",
        "/absolute/path",
        ".dotstart",
        "space name",
        "name;rm -rf",
    ]

    @pytest.mark.parametrize("traversal_name", TRAVERSAL_NAMES)
    def test_start_rejects_traversal(self, traversal_name: str) -> None:
        result = runner.invoke(app, ["runtime", "start", traversal_name])
        assert result.exit_code != 0
        assert "invalid" in result.output.lower()

    @pytest.mark.parametrize("traversal_name", TRAVERSAL_NAMES)
    def test_stop_rejects_traversal(self, traversal_name: str) -> None:
        result = runner.invoke(app, ["runtime", "stop", traversal_name])
        assert result.exit_code != 0
        assert "invalid" in result.output.lower()

    @pytest.mark.parametrize("traversal_name", TRAVERSAL_NAMES)
    def test_restart_rejects_traversal(self, traversal_name: str) -> None:
        result = runner.invoke(app, ["runtime", "restart", traversal_name])
        assert result.exit_code != 0
        assert "invalid" in result.output.lower()

    @pytest.mark.parametrize("traversal_name", TRAVERSAL_NAMES)
    def test_logs_rejects_traversal(self, traversal_name: str) -> None:
        result = runner.invoke(app, ["runtime", "logs", traversal_name])
        assert result.exit_code != 0
        assert "invalid" in result.output.lower()

    def test_valid_name_accepted_for_logs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Valid names are not rejected by the validation guard."""
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        with patch("agent_nexus.platform.local.cli.runtime_cmd._init_managers") as mock_init:
            mock_init.return_value = (MagicMock(), MagicMock(), MagicMock(), config_dir)
            # "my-agent" is valid — should pass validation and reach "no log" message
            result = runner.invoke(app, ["runtime", "logs", "my-agent"])
            # Not rejected by the traversal guard
            assert "invalid" not in result.output.lower()
