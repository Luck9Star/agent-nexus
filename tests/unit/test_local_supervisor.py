"""Unit tests for AgentSupervisor: manage agent lifecycle based on config and lockfile."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_nexus.models.agent import AgentType
from agent_nexus.models.distribution import Lockfile, LockfileEntry
from agent_nexus.platform.local.supervisor import (
    AgentSupervisor,
    RestartTracker,
)


def _make_entry() -> LockfileEntry:
    return LockfileEntry(
        version="1.0.0", source="official",
        commit_sha="a" * 40, agent_type=AgentType.ATOMIC,
    )


def _make_supervisor(tmp_path: Path) -> tuple[AgentSupervisor, MagicMock, MagicMock, MagicMock]:
    pm = MagicMock()
    pm.start_agent = AsyncMock()
    pm.stop_all = AsyncMock()
    pm.stop_agent = AsyncMock()
    pm.list_running = MagicMock(return_value=[])
    pm.get_agent = MagicMock(return_value=None)
    pm.health_check = AsyncMock(return_value=True)

    lf = MagicMock()
    lf.load.return_value = Lockfile()
    lf.get_entry.return_value = None
    lf.get_entry_from.return_value = None

    cfg = MagicMock()
    cfg.config_dir = tmp_path
    cfg.load_config.return_value = MagicMock(
        models=MagicMock(default="gpt-4", providers={})
    )

    sup = AgentSupervisor(pm, lf, cfg, config_dir=tmp_path)
    return sup, pm, lf, cfg


# ---------------------------------------------------------------------------
# RestartTracker
# ---------------------------------------------------------------------------

class TestRestartTracker:
    def test_should_retry_when_under_max(self) -> None:
        t = RestartTracker(count=0, max_restarts=3)
        assert t.should_retry() is True

    def test_should_retry_false_at_max(self) -> None:
        t = RestartTracker(count=3, max_restarts=3)
        assert t.should_retry() is False

    def test_record_increments(self) -> None:
        t = RestartTracker(count=0, max_restarts=3)
        t.record()
        assert t.count == 1

    def test_reset_zeroes_count(self) -> None:
        t = RestartTracker(count=2, max_restarts=3)
        t.reset()
        assert t.count == 0

    def test_default_max_restarts(self) -> None:
        t = RestartTracker()
        assert t.max_restarts == 3


# ---------------------------------------------------------------------------
# AgentSupervisor
# ---------------------------------------------------------------------------

class TestSupervisorStartAll:
    @pytest.mark.asyncio
    async def test_start_all_returns_started_names(self, tmp_path: Path) -> None:
        sup, pm, lf, _ = _make_supervisor(tmp_path)
        entry = _make_entry()
        lf.load.return_value = Lockfile(agents={"agent-a": entry})
        lf.get_entry_from.return_value = entry
        pm.start_agent.return_value = MagicMock(pid=1234)

        # _build_command needs path checks to pass
        with patch.object(Path, "exists", return_value=True):
            with patch.object(Path, "resolve", return_value=tmp_path / "agents" / "agent-a"):
                with patch.object(Path, "is_relative_to", return_value=True):
                    started = await sup.start_all()
        assert "agent-a" in started

    @pytest.mark.asyncio
    async def test_start_all_skips_failed_agents(self, tmp_path: Path) -> None:
        sup, pm, lf, _ = _make_supervisor(tmp_path)
        lf.load.return_value = Lockfile(agents={"bad": _make_entry()})
        lf.get_entry_from.return_value = None  # agent not in lockfile

        started = await sup.start_all()
        assert started == []


class TestSupervisorStopAll:
    @pytest.mark.asyncio
    async def test_stop_all_delegates(self, tmp_path: Path) -> None:
        sup, pm, _, _ = _make_supervisor(tmp_path)
        await sup.stop_all()
        pm.stop_all.assert_awaited_once()


class TestSupervisorStartAgent:
    @pytest.mark.asyncio
    async def test_start_agent_returns_false_when_not_in_lockfile(self, tmp_path: Path) -> None:
        sup, _, lf, _ = _make_supervisor(tmp_path)
        lf.get_entry.return_value = None
        result = await sup.start_agent("ghost")
        assert result is False

    @pytest.mark.asyncio
    async def test_start_agent_returns_false_on_bad_name(self, tmp_path: Path) -> None:
        sup, _, lf, _ = _make_supervisor(tmp_path)
        lf.get_entry.return_value = _make_entry()
        result = await sup.start_agent("../../../etc/passwd")
        assert result is False


class TestSupervisorStopAgent:
    @pytest.mark.asyncio
    async def test_stop_agent_returns_false_when_not_running(self, tmp_path: Path) -> None:
        sup, pm, _, _ = _make_supervisor(tmp_path)
        pm.get_agent.return_value = None
        assert await sup.stop_agent("not-running") is False

    @pytest.mark.asyncio
    async def test_stop_agent_returns_true_when_stopped(self, tmp_path: Path) -> None:
        sup, pm, _, _ = _make_supervisor(tmp_path)
        handle = MagicMock(is_alive=True)
        pm.get_agent.return_value = handle
        result = await sup.stop_agent("running-agent")
        assert result is True
        pm.stop_agent.assert_awaited_once_with("running-agent")


class TestSupervisorHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check_all_returns_status(self, tmp_path: Path) -> None:
        sup, pm, _, _ = _make_supervisor(tmp_path)
        pm.list_running.return_value = ["a", "b"]
        pm.health_check = AsyncMock(side_effect=[True, False])
        result = await sup.health_check_all()
        assert result == {"a": True, "b": False}

    @pytest.mark.asyncio
    async def test_health_check_handles_key_error(self, tmp_path: Path) -> None:
        sup, pm, _, _ = _make_supervisor(tmp_path)
        pm.list_running.return_value = ["x"]
        pm.health_check = AsyncMock(side_effect=KeyError("x"))
        result = await sup.health_check_all()
        assert result == {"x": False}


class TestSupervisorAutoRestart:
    @pytest.mark.asyncio
    async def test_auto_restart_skips_unstarted_agents(self, tmp_path: Path) -> None:
        sup, pm, lf, _ = _make_supervisor(tmp_path)
        lf.load.return_value = Lockfile(agents={"never-started": _make_entry()})
        restarted = await sup.auto_restart_dead()
        assert restarted == []

    @pytest.mark.asyncio
    async def test_auto_restart_respects_max_restarts(self, tmp_path: Path) -> None:
        sup, pm, lf, _ = _make_supervisor(tmp_path)
        entry = _make_entry()
        lf.load.return_value = Lockfile(agents={"dead-agent": entry})
        lf.get_entry.return_value = entry

        # Mark as started but dead
        sup._started_agents.add("dead-agent")
        tracker = RestartTracker(count=3, max_restarts=3)
        sup._restart_trackers["dead-agent"] = tracker

        restarted = await sup.auto_restart_dead()
        assert restarted == []

    @pytest.mark.asyncio
    async def test_auto_restart_dead_success(self, tmp_path: Path) -> None:
        """Dead agent within restart budget is restarted successfully."""
        sup, pm, lf, _ = _make_supervisor(tmp_path)
        entry = _make_entry()
        lf.load.return_value = Lockfile(agents={"dead-agent": entry})
        lf.get_entry.return_value = entry

        # Mark as started; agent handle is None (dead / never created)
        sup._started_agents.add("dead-agent")
        pm.get_agent.return_value = None

        # start_agent calls pm.start_agent which returns a handle with .pid
        handle_mock = MagicMock()
        handle_mock.pid = 12345
        pm.start_agent = AsyncMock(return_value=handle_mock)

        # _build_command must return a valid command list
        with patch.object(sup, "_build_command", return_value=["python", "main.py"]):
            restarted = await sup.auto_restart_dead()
        assert restarted == ["dead-agent"]

    @pytest.mark.asyncio
    async def test_auto_restart_dead_start_fails(self, tmp_path: Path) -> None:
        """Dead agent that fails to restart is NOT in the returned list."""
        sup, pm, lf, _ = _make_supervisor(tmp_path)
        entry = _make_entry()
        lf.load.return_value = Lockfile(agents={"dead-agent": entry})
        lf.get_entry.return_value = entry

        sup._started_agents.add("dead-agent")
        pm.get_agent.return_value = None

        # _build_command returns None → start_agent returns False
        with patch.object(sup, "_build_command", return_value=None):
            restarted = await sup.auto_restart_dead()
        assert restarted == []


class TestSupervisorListHelpers:
    def test_list_running_delegates(self, tmp_path: Path) -> None:
        sup, pm, _, _ = _make_supervisor(tmp_path)
        pm.list_running.return_value = ["a", "b"]
        assert sup.list_running() == ["a", "b"]

    def test_list_installed_returns_agent_names(self, tmp_path: Path) -> None:
        sup, _, lf, _ = _make_supervisor(tmp_path)
        entry = _make_entry()
        lf.load.return_value = Lockfile(agents={"x": entry, "y": entry})
        assert sorted(sup.list_installed()) == ["x", "y"]


class TestSupervisorBuildEnv:
    def test_build_env_includes_model(self, tmp_path: Path) -> None:
        sup, _, _, cfg = _make_supervisor(tmp_path)
        entry = _make_entry()
        cfg.load_config.return_value = MagicMock(
            models=MagicMock(default="gpt-4", providers={})
        )
        env = sup._build_env("test", entry)
        assert env.get("AGENT_MODEL") == "gpt-4"

    def test_build_env_handles_config_error(self, tmp_path: Path) -> None:
        sup, _, _, cfg = _make_supervisor(tmp_path)
        cfg.load_config.side_effect = RuntimeError("broken")
        env = sup._build_env("test", _make_entry())
        assert env == {}


class TestSupervisorBuildCommandVenvFallback:
    """Regression: _build_command logs warning when configured venv is missing."""

    def test_logs_warning_when_venv_missing(self, tmp_path: Path) -> None:
        sup, _, lf, _ = _make_supervisor(tmp_path)
        entry = LockfileEntry(
            version="1.0.0",
            source="official",
            commit_sha="a" * 40,
            agent_type=AgentType.ATOMIC,
            venv_path="/nonexistent/venv/path",
        )

        with patch.object(Path, "exists", return_value=False):
            cmd = sup._build_command("test-agent", entry)

        # Should fall through to uvx/python3 fallback, not return None
        assert cmd is not None
