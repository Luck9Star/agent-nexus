"""Unit tests for CLI module defect fixes.

Covers:
  1. _wait_forever propagates CancelledError (defect fix: no longer swallows it)
  2. install_app registers install/uninstall/update as separate reachable commands
"""

from __future__ import annotations

import asyncio

import pytest
from typer.testing import CliRunner

from agent_nexus.platform.local.cli import _wait_forever, app, install_app

runner = CliRunner()


# ------------------------------------------------------------------
# Defect 1: _wait_forever must propagate CancelledError
# ------------------------------------------------------------------


class TestWaitForeverPropagatesCancelledError:
    """Verify that _wait_forever does NOT catch CancelledError."""

    def test_cancelled_error_propagates(self) -> None:
        """CancelledError raised by task.cancel() must propagate out of _wait_forever."""
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(self._cancel_wait_forever())

    @staticmethod
    async def _cancel_wait_forever() -> None:
        """Start _wait_forever, then cancel it after a tick."""
        task = asyncio.create_task(_wait_forever())
        await asyncio.sleep(0)  # let the task start and enter sleep
        task.cancel()
        # await the task without return_exceptions -- CancelledError must propagate
        await task

    def test_caller_can_catch_cancelled_error(self) -> None:
        """Caller's except clause fires when _wait_forever is cancelled."""
        caught: list[str] = []

        async def _caller() -> None:
            try:
                task = asyncio.create_task(_wait_forever())
                await asyncio.sleep(0)
                task.cancel()
                await task
            except asyncio.CancelledError:
                caught.append("caller_caught")
                # Simulate cleanup (like supervisor.stop_agent)

        asyncio.run(_caller())
        assert caught == ["caller_caught"], (
            "Caller's except CancelledError clause must fire when _wait_forever is cancelled"
        )


# ------------------------------------------------------------------
# Defect 2: install/uninstall/update are reachable subcommands
# ------------------------------------------------------------------


class TestInstallSubcommandsReachable:
    """Verify install, uninstall, update are registered as separate commands."""

    def test_install_help_shows_subcommands(self) -> None:
        """'agent-nexus install --help' must list install, uninstall, update."""
        result = runner.invoke(app, ["install", "--help"])
        assert result.exit_code == 0, f"Exit code {result.exit_code}: {result.output}"
        output_lower = result.output.lower()
        assert "install" in output_lower, (
            f"'install' subcommand not listed in help output:\n{result.output}"
        )
        assert "uninstall" in output_lower, (
            f"'uninstall' subcommand not listed in help output:\n{result.output}"
        )
        assert "update" in output_lower, (
            f"'update' subcommand not listed in help output:\n{result.output}"
        )

    def test_uninstall_is_not_interpreted_as_agent_name(self) -> None:
        """'agent-nexus install uninstall --help' invokes uninstall command."""
        result = runner.invoke(app, ["install", "uninstall", "--help"])
        assert result.exit_code == 0, (
            f"'install uninstall --help' failed with exit {result.exit_code}:\n{result.output}"
        )
        assert "uninstall" in result.output.lower()

    def test_update_is_not_interpreted_as_agent_name(self) -> None:
        """'agent-nexus install update --help' invokes update command."""
        result = runner.invoke(app, ["install", "update", "--help"])
        assert result.exit_code == 0, (
            f"'install update --help' failed with exit {result.exit_code}:\n{result.output}"
        )
        assert "update" in result.output.lower()

    def test_install_command_is_separate_subcommand(self) -> None:
        """'agent-nexus install install --help' invokes install subcommand."""
        result = runner.invoke(app, ["install", "install", "--help"])
        assert result.exit_code == 0, (
            f"'install install --help' failed with exit {result.exit_code}:\n{result.output}"
        )
        assert "install" in result.output.lower()

    def test_install_app_has_three_commands(self) -> None:
        """install_app Typer group must register exactly 3 commands."""
        commands = install_app.registered_commands
        cmd_names = [cmd.name or cmd.callback.__name__ for cmd in commands]
        assert sorted(cmd_names) == ["install", "uninstall", "update"], (
            f"Expected ['install', 'uninstall', 'update'], got {sorted(cmd_names)}"
        )
