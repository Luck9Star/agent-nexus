"""Unit tests for ProcessManager internal methods.

Tests cover _cleanup_dead(), _force_kill_and_reap(), and _build_spawn_env().
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_nexus.platform.orchestration.process_manager import (
    AgentHandle,
    ProcessManager,
)

# ============================================================================
# Helpers
# ============================================================================


def _make_mock_handle(
    name: str,
    alive: bool = True,
    has_drain_task: bool = True,
) -> AgentHandle:
    """Create a mock AgentHandle with controlled is_alive behavior."""
    process = MagicMock(spec=asyncio.subprocess.Process)
    process.returncode = None if alive else 1
    process.pid = 42

    ipc = MagicMock()
    ipc.stream = MagicMock()
    ipc.stream.close_sync = MagicMock()

    handle = AgentHandle(
        name=name,
        process=process,
        ipc=ipc,
    )

    if has_drain_task:
        drain = MagicMock(spec=asyncio.Task)
        drain.done.return_value = False
        handle.drain_task = drain

    return handle


# ============================================================================
# A) _cleanup_dead()
# ============================================================================


# ============================================================================
# B) _force_kill_and_reap()
# ============================================================================


class TestForceKillAndReap:
    """Tests for ProcessManager._force_kill_and_reap()."""

    @pytest.mark.asyncio
    async def test_force_kills_named_agents(self) -> None:
        """All named alive agents are force-killed."""
        pm = ProcessManager()
        h1 = _make_mock_handle("a1", alive=True)
        h2 = _make_mock_handle("a2", alive=True)
        pm._agents = {"a1": h1, "a2": h2}

        await pm._force_kill_and_reap(["a1", "a2"])

        h1.process.kill.assert_called_once()
        h2.process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_removes_killed_agents_from_dict(self) -> None:
        """Force-killed agents are removed from _agents."""
        pm = ProcessManager()
        h1 = _make_mock_handle("a1", alive=True)
        pm._agents = {"a1": h1}

        await pm._force_kill_and_reap(["a1"])

        assert "a1" not in pm._agents

    @pytest.mark.asyncio
    async def test_handles_process_lookup_error(self) -> None:
        """ProcessLookupError during kill is handled gracefully."""
        pm = ProcessManager()
        h1 = _make_mock_handle("a1", alive=True)
        h1.process.kill.side_effect = ProcessLookupError("no such process")
        pm._agents = {"a1": h1}

        # Should not raise
        await pm._force_kill_and_reap(["a1"])

        assert "a1" not in pm._agents

    @pytest.mark.asyncio
    async def test_skips_already_dead_agents_kill(self) -> None:
        """Already-dead agents are not sent kill signal."""
        pm = ProcessManager()
        dead = _make_mock_handle("dead", alive=False)
        pm._agents = {"dead": dead}

        await pm._force_kill_and_reap(["dead"])

        dead.process.kill.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_unknown_agent_names(self) -> None:
        """Names not in _agents are silently skipped."""
        pm = ProcessManager()
        pm._agents = {}

        # Should not raise
        await pm._force_kill_and_reap(["nonexistent"])

    @pytest.mark.asyncio
    async def test_waits_for_process_exit(self) -> None:
        """_force_kill_and_reap calls process.wait() to reap zombies."""
        pm = ProcessManager()
        h1 = _make_mock_handle("a1", alive=True)
        # After kill, process is still alive (returncode None), so wait is called
        h1.process.wait = AsyncMock()
        pm._agents = {"a1": h1}

        await pm._force_kill_and_reap(["a1"])

        h1.process.wait.assert_awaited_once()


# ============================================================================
# C) _build_spawn_env() security
# ============================================================================
