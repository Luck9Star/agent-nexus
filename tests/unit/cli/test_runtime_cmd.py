"""Tests for runtime_cmd.py -- start/stop/restart/status/logs/ps."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from agent_nexus.platform.local.cli import app

runner = CliRunner()


# ── Helpers ──────────────────────────────────────────────────────────


def _setup_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_dir = tmp_path / ".agent-nexus"
    config_dir.mkdir()
    (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
    monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))
    return config_dir


def _mock_supervisor():
    supervisor = AsyncMock()
    pm = MagicMock()
    return supervisor, pm


# ── start ────────────────────────────────────────────────────────────


class TestStart:
    def test_start_no_args_exits_1(self) -> None:
        result = runner.invoke(app, ["runtime", "start"])
        assert result.exit_code == 1
        assert "specify" in result.output.lower()

    def test_start_all_delegates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._start_all",
            new_callable=AsyncMock,
        ) as mock_start_all:
            result = runner.invoke(app, ["runtime", "start", "--all"])
            assert result.exit_code == 0
            mock_start_all.assert_awaited_once()

    def test_start_single_agent_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._start_one",
            new_callable=AsyncMock,
        ) as mock_start_one:
            result = runner.invoke(app, ["runtime", "start", "my-agent"])
            assert result.exit_code == 0
            mock_start_one.assert_awaited_once_with("my-agent")

    def test_start_one_writes_pid_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        supervisor, pm = _mock_supervisor()
        supervisor.start_agent.return_value = True
        mock_handle = MagicMock()
        mock_handle.pid = 12345
        pm.get_agent.return_value = mock_handle

        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._make_supervisor",
            new_callable=AsyncMock,
            return_value=(supervisor, config_dir, pm),
        ):
            import asyncio

            from agent_nexus.platform.local.cli.runtime_cmd import _start_one

            asyncio.run(_start_one("test-agent"))

        pid_file = config_dir / "agents" / "test-agent.pid"
        assert pid_file.exists()
        assert pid_file.read_text() == "12345"

    def test_start_one_failure_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        supervisor, pm = _mock_supervisor()
        supervisor.start_agent.return_value = False

        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._make_supervisor",
            new_callable=AsyncMock,
            return_value=(supervisor, config_dir, pm),
        ):
            import asyncio

            from agent_nexus.platform.local.cli.runtime_cmd import _start_one

            with pytest.raises(BaseException):  # click.exceptions.Exit
                asyncio.run(_start_one("bad-agent"))

    def test_start_all_prints_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        supervisor, pm = _mock_supervisor()
        supervisor.start_all.return_value = ["agent-a", "agent-b"]

        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._make_supervisor",
            new_callable=AsyncMock,
            return_value=(supervisor, config_dir, pm),
        ) as mock_make:
            import asyncio

            from agent_nexus.platform.local.cli.runtime_cmd import _start_all

            asyncio.run(_start_all())
            supervisor.start_all.assert_called_once()

    def test_start_all_no_agents(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        supervisor, pm = _mock_supervisor()
        supervisor.start_all.return_value = []

        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._make_supervisor",
            new_callable=AsyncMock,
            return_value=(supervisor, config_dir, pm),
        ):
            import asyncio

            from agent_nexus.platform.local.cli.runtime_cmd import _start_all

            asyncio.run(_start_all())
            supervisor.start_all.assert_called_once()


# ── stop ─────────────────────────────────────────────────────────────


class TestStop:
    def test_stop_no_args_exits_1(self) -> None:
        result = runner.invoke(app, ["runtime", "stop"])
        assert result.exit_code == 1
        assert "specify" in result.output.lower()

    def test_stop_all_delegates(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._stop_all",
            new_callable=AsyncMock,
        ) as mock_stop_all:
            result = runner.invoke(app, ["runtime", "stop", "--all"])
            assert result.exit_code == 0
            mock_stop_all.assert_awaited_once()

    def test_stop_single_agent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._stop_one",
            new_callable=AsyncMock,
        ) as mock_stop_one:
            result = runner.invoke(app, ["runtime", "stop", "my-agent"])
            assert result.exit_code == 0
            mock_stop_one.assert_awaited_once_with("my-agent")

    def test_stop_one_cleans_pid_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        supervisor, pm = _mock_supervisor()
        supervisor.stop_agent.return_value = True

        pid_dir = config_dir / "agents"
        pid_dir.mkdir(parents=True, exist_ok=True)
        pid_file = pid_dir / "test-agent.pid"
        pid_file.write_text("99999")

        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._make_supervisor",
            new_callable=AsyncMock,
            return_value=(supervisor, config_dir, pm),
        ):
            import asyncio

            from agent_nexus.platform.local.cli.runtime_cmd import _stop_one

            asyncio.run(_stop_one("test-agent"))

        assert not pid_file.exists()

    def test_stop_one_not_running_exits_with_code_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        supervisor, pm = _mock_supervisor()
        supervisor.stop_agent.return_value = False

        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._make_supervisor",
            new_callable=AsyncMock,
            return_value=(supervisor, config_dir, pm),
        ):
            import asyncio

            from agent_nexus.platform.local.cli.runtime_cmd import _stop_one

            with pytest.raises(BaseException):  # click.exceptions.Exit
                asyncio.run(_stop_one("ghost-agent"))

    def test_stop_all_cleans_pid_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        supervisor, pm = _mock_supervisor()
        supervisor.stop_all.return_value = None

        pid_dir = config_dir / "agents"
        pid_dir.mkdir(parents=True, exist_ok=True)
        (pid_dir / "agent-a.pid").write_text("111")
        (pid_dir / "agent-b.pid").write_text("222")

        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._make_supervisor",
            new_callable=AsyncMock,
            return_value=(supervisor, config_dir, pm),
        ):
            import asyncio

            from agent_nexus.platform.local.cli.runtime_cmd import _stop_all

            asyncio.run(_stop_all())

        assert not (pid_dir / "agent-a.pid").exists()
        assert not (pid_dir / "agent-b.pid").exists()


# ── restart ──────────────────────────────────────────────────────────


class TestRestart:
    def test_restart_delegates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._restart_agent",
            new_callable=AsyncMock,
        ) as mock_restart:
            result = runner.invoke(app, ["runtime", "restart", "my-agent"])
            assert result.exit_code == 0
            mock_restart.assert_awaited_once_with("my-agent")

    def test_restart_writes_new_pid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        supervisor, pm = _mock_supervisor()
        supervisor.stop_agent.return_value = True
        supervisor.start_agent.return_value = True
        mock_handle = MagicMock()
        mock_handle.pid = 54321
        pm.get_agent.return_value = mock_handle

        pid_dir = config_dir / "agents"
        pid_dir.mkdir(parents=True, exist_ok=True)
        old_pid = pid_dir / "my-agent.pid"
        old_pid.write_text("11111")

        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._make_supervisor",
            new_callable=AsyncMock,
            return_value=(supervisor, config_dir, pm),
        ):
            import asyncio

            from agent_nexus.platform.local.cli.runtime_cmd import _restart_agent

            asyncio.run(_restart_agent("my-agent"))

        assert old_pid.exists()
        assert old_pid.read_text() == "54321"

    def test_restart_start_failure_exits(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        supervisor, pm = _mock_supervisor()
        supervisor.stop_agent.return_value = False
        supervisor.start_agent.return_value = False

        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._make_supervisor",
            new_callable=AsyncMock,
            return_value=(supervisor, config_dir, pm),
        ):
            import asyncio

            from agent_nexus.platform.local.cli.runtime_cmd import _restart_agent

            with pytest.raises(BaseException):  # click.exceptions.Exit
                asyncio.run(_restart_agent("broken-agent"))

    def test_restart_warns_when_agent_not_running(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stop fails (agent not running) but start succeeds = fresh start."""
        config_dir = _setup_config(tmp_path, monkeypatch)
        supervisor, pm = _mock_supervisor()
        supervisor.stop_agent.return_value = False
        supervisor.start_agent.return_value = True
        mock_handle = MagicMock()
        mock_handle.pid = 11111
        pm.get_agent.return_value = mock_handle

        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._make_supervisor",
            new_callable=AsyncMock,
            return_value=(supervisor, config_dir, pm),
        ):
            import asyncio

            from agent_nexus.platform.local.cli.runtime_cmd import _restart_agent

            asyncio.run(_restart_agent("not-running-agent"))
            supervisor.stop_agent.assert_called_once_with("not-running-agent")
            supervisor.start_agent.assert_called_once_with("not-running-agent")


# ── status ───────────────────────────────────────────────────────────


class TestStatus:
    def test_status_with_no_agents(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._init_managers",
            return_value=(MagicMock(), MagicMock(), MagicMock(), config_dir),
        ):
            mock_lockfile = MagicMock()
            mock_lockfile.load.return_value.agents = {}
            with patch(
                "agent_nexus.platform.local.cli.runtime_cmd._init_managers",
                return_value=(MagicMock(), mock_lockfile, MagicMock(), config_dir),
            ):
                result = runner.invoke(app, ["runtime", "status"])
                assert result.exit_code == 0

    def test_status_with_installed_agents(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        mock_lockfile = MagicMock()
        mock_lockfile.load.return_value.agents = {"agent-a": {}, "agent-b": {}}

        pid_dir = config_dir / "agents"
        pid_dir.mkdir(parents=True, exist_ok=True)
        (pid_dir / "agent-a.pid").write_text("999999999")
        # agent-b has no PID file

        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._init_managers",
            return_value=(MagicMock(), mock_lockfile, MagicMock(), config_dir),
        ):
            result = runner.invoke(app, ["runtime", "status"])
            assert result.exit_code == 0
            assert "agent-a" in result.output
            assert "agent-b" in result.output

    def test_status_cleans_stale_pid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        mock_lockfile = MagicMock()
        mock_lockfile.load.return_value.agents = {"stale-agent": {}}

        pid_dir = config_dir / "agents"
        pid_dir.mkdir(parents=True, exist_ok=True)
        stale_pid = pid_dir / "stale-agent.pid"
        stale_pid.write_text("999999999")  # non-existent PID

        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._init_managers",
            return_value=(MagicMock(), mock_lockfile, MagicMock(), config_dir),
        ):
            result = runner.invoke(app, ["runtime", "status"])
            assert result.exit_code == 0
            # Stale PID file should be cleaned up
            assert not stale_pid.exists()


class TestPs:
    def test_ps_is_alias_for_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        mock_lockfile = MagicMock()
        mock_lockfile.load.return_value.agents = {}

        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._init_managers",
            return_value=(MagicMock(), mock_lockfile, MagicMock(), config_dir),
        ):
            result = runner.invoke(app, ["runtime", "ps"])
            assert result.exit_code == 0


# ── logs ─────────────────────────────────────────────────────────────


class TestLogs:
    def test_logs_agent_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._init_managers",
            return_value=(MagicMock(), MagicMock(), MagicMock(), config_dir),
        ):
            result = runner.invoke(app, ["runtime", "logs", "nonexistent-agent"])
            assert "no log" in result.output.lower() or "not" in result.output.lower()

    def test_show_logs_reads_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        log_dir = config_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "my-agent.log").write_text("line1\nline2\nline3\n")

        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._init_managers",
            return_value=(MagicMock(), MagicMock(), MagicMock(), config_dir),
        ):
            result = runner.invoke(app, ["runtime", "logs", "my-agent"])
            assert result.exit_code == 0
            assert "line1" in result.output
            assert "line3" in result.output

    def test_show_logs_with_line_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        log_dir = config_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        lines = [f"line-{i}" for i in range(100)]
        (log_dir / "agent.log").write_text("\n".join(lines) + "\n")

        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._init_managers",
            return_value=(MagicMock(), MagicMock(), MagicMock(), config_dir),
        ):
            result = runner.invoke(app, ["runtime", "logs", "agent", "--lines", "5"])
            assert result.exit_code == 0
            assert "line-99" in result.output
            assert "line-0" not in result.output

    def test_resolve_log_path_exit_code_0_when_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._init_managers",
            return_value=(MagicMock(), MagicMock(), MagicMock(), config_dir),
        ):
            result = runner.invoke(app, ["runtime", "logs", "no-logs-agent"])
            assert result.exit_code == 0  # intentional UX: not an error


# ── Path traversal ───────────────────────────────────────────────────


class TestPathTraversalRejection:
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

    def test_valid_name_accepted_for_logs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = _setup_config(tmp_path, monkeypatch)
        with patch(
            "agent_nexus.platform.local.cli.runtime_cmd._init_managers",
            return_value=(MagicMock(), MagicMock(), MagicMock(), config_dir),
        ):
            result = runner.invoke(app, ["runtime", "logs", "my-agent"])
            assert "invalid" not in result.output.lower()
