"""Unit tests for CLI module.

Covers:
  1. _wait_forever propagates CancelledError (defect fix)
  2. install_app registers install/uninstall/update as separate reachable commands
  3. _install, _uninstall, _update, _list_agents, _info, _sources internal async
     functions tested by mocking _init_managers and deferred imports.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import click.exceptions
import pytest
from typer.testing import CliRunner

from agent_nexus.models.agent import AgentType
from agent_nexus.models.distribution import Lockfile, LockfileEntry, SourceEntry

from agent_nexus.platform.local.cli import (
    _info,
    _install,
    _list_agents,
    _sources,
    _uninstall,
    _update,
    _wait_forever,
    app,
    install_app,
)

runner = CliRunner()


# ============================================================================
# Helpers
# ============================================================================


def _make_entry(
    version: str = "1.0.0",
    source: str = "official",
    commit_sha: str = "a" * 40,
    agent_type: AgentType = AgentType.ATOMIC,
    venv_path: str = "",
    dependencies: list[str] | None = None,
) -> LockfileEntry:
    """Create a LockfileEntry with sensible defaults."""
    return LockfileEntry(
        version=version,
        source=source,
        commit_sha=commit_sha,
        agent_type=agent_type,
        installed_at=datetime(2026, 1, 15, 12, 0, 0),
        venv_path=venv_path,
        dependencies=dependencies or [],
    )


def _mock_managers(lockfile_data: Lockfile | None = None):
    """Build mock (config_loader, lockfile, sources, config_dir) tuple.

    Returns (mocks_tuple, lockfile_mock, sources_mock, config_dir).
    """
    config_loader = MagicMock()
    lockfile = MagicMock()
    if lockfile_data is not None:
        lockfile.load.return_value = lockfile_data
    sources = MagicMock()
    config_dir = Path("/fake/config")
    return (config_loader, lockfile, sources, config_dir), lockfile, sources, config_dir


def _echo_calls(echo_mock: MagicMock) -> list[str]:
    """Extract all string arguments from typer.echo mock calls.

    typer.echo("msg")        -> args[0] = "msg"
    typer.echo("msg", err=True) -> args[0] = "msg"
    """
    result: list[str] = []
    for call in echo_mock.call_args_list:
        if call.args:
            result.append(call.args[0])
        elif "message" in call.kwargs:
            result.append(call.kwargs["message"])
    return result


# ============================================================================
# _wait_forever defect fix
# ============================================================================


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
        await asyncio.sleep(0)
        task.cancel()
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

        asyncio.run(_caller())
        assert caught == ["caller_caught"]


# ============================================================================
# install_app subcommand registration
# ============================================================================


class TestInstallSubcommandsReachable:
    """Verify install, uninstall, update are registered as separate commands."""

    def test_install_help_shows_subcommands(self) -> None:
        """'agent-nexus install --help' must list install, uninstall, update."""
        result = runner.invoke(app, ["install", "--help"])
        assert result.exit_code == 0
        output_lower = result.output.lower()
        assert "install" in output_lower
        assert "uninstall" in output_lower
        assert "update" in output_lower

    def test_uninstall_is_not_interpreted_as_agent_name(self) -> None:
        result = runner.invoke(app, ["install", "uninstall", "--help"])
        assert result.exit_code == 0
        assert "uninstall" in result.output.lower()

    def test_update_is_not_interpreted_as_agent_name(self) -> None:
        result = runner.invoke(app, ["install", "update", "--help"])
        assert result.exit_code == 0
        assert "update" in result.output.lower()

    def test_install_command_is_separate_subcommand(self) -> None:
        result = runner.invoke(app, ["install", "install", "--help"])
        assert result.exit_code == 0
        assert "install" in result.output.lower()

    def test_install_app_has_three_commands(self) -> None:
        commands = install_app.registered_commands
        cmd_names = [cmd.name or cmd.callback.__name__ for cmd in commands]
        assert sorted(cmd_names) == ["install", "uninstall", "update"]


# ============================================================================
# _install tests
# ============================================================================


class TestInstall:
    """Tests for _install internal async function."""

    @pytest.mark.asyncio
    async def test_success(self) -> None:
        """Successful install prints 'Installed name@version'."""
        mocks, _, _, _ = _mock_managers()
        entry = _make_entry(version="2.3.0")

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.installer.GitInstaller") as GitInstallerCls,
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            installer_instance = GitInstallerCls.return_value
            installer_instance.install = AsyncMock(return_value=entry)

            await _install("my-agent", None, None)

        installer_instance.install.assert_awaited_once_with(
            "my-agent", version=None, source_url=None
        )
        calls = _echo_calls(echo_mock)
        assert "Installed my-agent@2.3.0" in calls

    @pytest.mark.asyncio
    async def test_failure_raises_exit(self) -> None:
        """When install() raises, prints error and exits with code 1."""
        mocks, _, _, _ = _mock_managers()

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.installer.GitInstaller") as GitInstallerCls,
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            installer_instance = GitInstallerCls.return_value
            installer_instance.install = AsyncMock(
                side_effect=RuntimeError("clone failed")
            )

            with pytest.raises(click.exceptions.Exit) as exc_info:
                await _install("bad-agent", "1.0.0", "https://example.com/repo")

        assert exc_info.value.exit_code == 1
        calls = _echo_calls(echo_mock)
        assert any("Error: clone failed" in c for c in calls)


# ============================================================================
# _uninstall tests
# ============================================================================


class TestUninstall:
    """Tests for _uninstall internal async function."""

    @pytest.mark.asyncio
    async def test_success_removed(self) -> None:
        """Uninstall returns True -- prints 'Uninstalled name'."""
        mocks, _, _, _ = _mock_managers()

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.installer.GitInstaller") as GitInstallerCls,
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            installer_instance = GitInstallerCls.return_value
            installer_instance.uninstall = AsyncMock(return_value=True)

            await _uninstall("my-agent")

        calls = _echo_calls(echo_mock)
        assert "Uninstalled my-agent" in calls

    @pytest.mark.asyncio
    async def test_not_installed(self) -> None:
        """Uninstall returns False -- prints 'not installed' message."""
        mocks, _, _, _ = _mock_managers()

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.installer.GitInstaller") as GitInstallerCls,
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            installer_instance = GitInstallerCls.return_value
            installer_instance.uninstall = AsyncMock(return_value=False)

            await _uninstall("missing-agent")

        calls = _echo_calls(echo_mock)
        assert any("not installed" in c for c in calls)

    @pytest.mark.asyncio
    async def test_exception_raises_exit(self) -> None:
        """When uninstall() raises, prints error and exits with code 1."""
        mocks, _, _, _ = _mock_managers()

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.installer.GitInstaller") as GitInstallerCls,
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            installer_instance = GitInstallerCls.return_value
            installer_instance.uninstall = AsyncMock(
                side_effect=RuntimeError("disk error")
            )

            with pytest.raises(click.exceptions.Exit) as exc_info:
                await _uninstall("my-agent")

        assert exc_info.value.exit_code == 1
        calls = _echo_calls(echo_mock)
        assert any("Error: disk error" in c for c in calls)


# ============================================================================
# _update tests
# ============================================================================


class TestUpdate:
    """Tests for _update internal async function."""

    @pytest.mark.asyncio
    async def test_update_single_agent(self) -> None:
        """Update a single agent -- prints updated version and count."""
        mocks, _, _, _ = _mock_managers()
        updated_entry = _make_entry(version="2.0.0")

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.installer.GitInstaller") as GitInstallerCls,
            patch("agent_nexus.platform.local.installer.AgentNotFoundError", RuntimeError),
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            installer_instance = GitInstallerCls.return_value
            installer_instance.update = AsyncMock(return_value=updated_entry)

            await _update("my-agent", all_agents=False)

        calls = _echo_calls(echo_mock)
        assert "Updated my-agent@2.0.0" in calls
        assert "Updated 1/1 agent(s)." in calls

    @pytest.mark.asyncio
    async def test_update_single_already_up_to_date(self) -> None:
        """Update returns None -- agent already up to date."""
        mocks, _, _, _ = _mock_managers()

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.installer.GitInstaller") as GitInstallerCls,
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            installer_instance = GitInstallerCls.return_value
            installer_instance.update = AsyncMock(return_value=None)

            await _update("my-agent", all_agents=False)

        calls = _echo_calls(echo_mock)
        assert "my-agent is already up to date." in calls
        assert "Updated 0/1 agent(s)." in calls

    @pytest.mark.asyncio
    async def test_update_all_with_agents(self) -> None:
        """Update --all iterates over all agents in lockfile."""
        agents = {
            "agent-a": _make_entry(version="1.0.0"),
            "agent-b": _make_entry(version="3.0.0"),
        }
        lf = Lockfile(agents=agents)
        mocks, _, _, _ = _mock_managers(lockfile_data=lf)
        updated_entry = _make_entry(version="1.1.0")

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.installer.GitInstaller") as GitInstallerCls,
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            installer_instance = GitInstallerCls.return_value
            installer_instance.update = AsyncMock(return_value=updated_entry)

            await _update(None, all_agents=True)

        assert installer_instance.update.await_count == 2
        calls = _echo_calls(echo_mock)
        assert "Updated 2/2 agent(s)." in calls

    @pytest.mark.asyncio
    async def test_update_all_empty_lockfile(self) -> None:
        """Update --all with empty lockfile prints 'No installed agents'."""
        lf = Lockfile(agents={})
        mocks, _, _, _ = _mock_managers(lockfile_data=lf)

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.installer.GitInstaller"),
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            await _update(None, all_agents=True)

        calls = _echo_calls(echo_mock)
        assert "No installed agents to update." in calls

    @pytest.mark.asyncio
    async def test_update_agent_not_found_error(self) -> None:
        """AgentNotFoundError during update prints appropriate error."""
        mocks, _, _, _ = _mock_managers()

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.installer.GitInstaller") as GitInstallerCls,
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            installer_instance = GitInstallerCls.return_value
            installer_instance.update = AsyncMock(
                side_effect=Exception("AgentNotFoundError")
            )

            # Import the real AgentNotFoundError and use it
            from agent_nexus.platform.local.installer import AgentNotFoundError

            installer_instance.update = AsyncMock(
                side_effect=AgentNotFoundError("missing-agent")
            )

            await _update("missing-agent", all_agents=False)

        calls = _echo_calls(echo_mock)
        assert any("not installed" in c for c in calls)

    @pytest.mark.asyncio
    async def test_update_general_exception(self) -> None:
        """General exception during update prints error with agent name."""
        mocks, _, _, _ = _mock_managers()

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.installer.GitInstaller") as GitInstallerCls,
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            installer_instance = GitInstallerCls.return_value
            installer_instance.update = AsyncMock(
                side_effect=RuntimeError("network timeout")
            )

            await _update("my-agent", all_agents=False)

        calls = _echo_calls(echo_mock)
        assert any("Error updating my-agent: network timeout" in c for c in calls)
        assert "Updated 0/1 agent(s)." in calls


# ============================================================================
# _list_agents tests
# ============================================================================


class TestListAgents:
    """Tests for _list_agents internal async function."""

    @pytest.mark.asyncio
    async def test_empty_lockfile(self) -> None:
        """Empty lockfile prints 'No agents installed'."""
        lf = Lockfile(agents={})
        mocks, _, _, _ = _mock_managers(lockfile_data=lf)

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            await _list_agents()

        calls = _echo_calls(echo_mock)
        assert "No agents installed." in calls

    @pytest.mark.asyncio
    async def test_with_agents(self) -> None:
        """Lockfile with agents prints table header, rows, and count."""
        agents = {
            "doc-filler": _make_entry(
                version="1.2.0",
                source="official",
                agent_type=AgentType.ATOMIC,
            ),
            "feature-pipeline": _make_entry(
                version="0.5.0",
                source="private",
                agent_type=AgentType.COMPOSITE,
            ),
        }
        lf = Lockfile(agents=agents)
        mocks, _, _, _ = _mock_managers(lockfile_data=lf)

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            await _list_agents()

        calls = _echo_calls(echo_mock)
        assert any("Name" in c and "Version" in c for c in calls)
        assert any("---" in c for c in calls)
        assert any("doc-filler" in c and "1.2.0" in c for c in calls)
        assert any("feature-pipeline" in c and "0.5.0" in c for c in calls)
        assert any("2 agent(s) installed" in c for c in calls)


# ============================================================================
# _info tests
# ============================================================================


class TestInfo:
    """Tests for _info internal async function."""

    @pytest.mark.asyncio
    async def test_agent_not_installed(self) -> None:
        """Agent not in lockfile prints error and exits with code 1."""
        mocks, lockfile_mock, _, _ = _mock_managers()
        lockfile_mock.get_entry.return_value = None

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            with pytest.raises(click.exceptions.Exit) as exc_info:
                await _info("missing-agent")

        assert exc_info.value.exit_code == 1
        calls = _echo_calls(echo_mock)
        assert any("not installed" in c for c in calls)

    @pytest.mark.asyncio
    async def test_agent_installed_with_manifest(self) -> None:
        """Agent with a manifest file displays description and run modes."""
        entry = _make_entry(
            version="2.0.0",
            commit_sha="abc123456789" + "d" * 28,
            venv_path="/venvs/my-agent",
            dependencies=["pydantic", "httpx"],
        )
        mocks, lockfile_mock, _, _ = _mock_managers()
        lockfile_mock.get_entry.return_value = entry

        manifest_content = (
            "description: A helpful agent.\n"
            "run_modes:\n"
            "  - mcp\n"
            "  - cli\n"
            "model_tier: standard\n"
        )

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
            patch("pathlib.Path.exists") as exists_mock,
            patch("pathlib.Path.read_text", return_value=manifest_content),
        ):
            # exists() called for manifest_path then skill_path
            exists_mock.side_effect = [True, False]

            await _info("my-agent")

        calls = _echo_calls(echo_mock)
        assert any("Agent: my-agent" in c for c in calls)
        assert any("Version:" in c and "2.0.0" in c for c in calls)
        assert any("Description:" in c and "A helpful agent." in c for c in calls)
        assert any("Run modes:" in c and "mcp" in c for c in calls)
        assert any("Model tier:" in c and "standard" in c for c in calls)

    @pytest.mark.asyncio
    async def test_agent_installed_without_manifest(self) -> None:
        """Agent without manifest skips manifest section."""
        entry = _make_entry(version="1.0.0")
        mocks, lockfile_mock, _, _ = _mock_managers()
        lockfile_mock.get_entry.return_value = entry

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
            patch("pathlib.Path.exists", return_value=False),
        ):
            await _info("my-agent")

        calls = _echo_calls(echo_mock)
        assert any("Agent: my-agent" in c for c in calls)
        assert any("Version:" in c and "1.0.0" in c for c in calls)
        # Should NOT contain manifest-derived fields
        assert not any("Description:" in c for c in calls)


# ============================================================================
# _sources tests
# ============================================================================


class TestSources:
    """Tests for _sources internal async function."""

    @pytest.mark.asyncio
    async def test_list_with_sources(self) -> None:
        """List sources displays table format."""
        mocks, _, sources_mock, _ = _mock_managers()
        source_entries = [
            SourceEntry(name="official", type="git", url="https://example.com/repo"),
            SourceEntry(name="private", type="git", url="https://internal.com/repo"),
        ]
        sources_mock.list_sources.return_value = source_entries

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            await _sources("list", None, None, None)

        calls = _echo_calls(echo_mock)
        assert any("Name" in c and "Type" in c for c in calls)
        assert any("official" in c for c in calls)
        assert any("private" in c for c in calls)

    @pytest.mark.asyncio
    async def test_list_no_sources(self) -> None:
        """List with no sources prints 'No sources configured'."""
        mocks, _, sources_mock, _ = _mock_managers()
        sources_mock.list_sources.return_value = []

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            await _sources("list", None, None, None)

        calls = _echo_calls(echo_mock)
        assert "No sources configured." in calls

    @pytest.mark.asyncio
    async def test_add_without_name(self) -> None:
        """Add without --name prints error and exits."""
        mocks, _, _, _ = _mock_managers()

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            with pytest.raises(click.exceptions.Exit) as exc_info:
                await _sources("add", None, "https://example.com", "git")

        assert exc_info.value.exit_code == 1
        calls = _echo_calls(echo_mock)
        assert any("--name and --url are required" in c for c in calls)

    @pytest.mark.asyncio
    async def test_add_with_valid_params(self) -> None:
        """Add with valid params creates SourceEntry and prints 'added'."""
        mocks, _, sources_mock, _ = _mock_managers()

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.models.distribution.SourceEntry", wraps=SourceEntry),
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            await _sources("add", "my-source", "https://example.com/repo", "git")

        sources_mock.add_source.assert_called_once()
        calls = _echo_calls(echo_mock)
        assert any("added" in c for c in calls)

    @pytest.mark.asyncio
    async def test_add_with_default_type(self) -> None:
        """Add without explicit type defaults to 'git'."""
        mocks, _, sources_mock, _ = _mock_managers()

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.models.distribution.SourceEntry", wraps=SourceEntry),
            patch("agent_nexus.platform.local.cli.typer.echo"),
        ):
            await _sources("add", "my-source", "https://example.com/repo", None)

        # Verify add_source was called with an entry whose type is "git"
        call_args = sources_mock.add_source.call_args
        added_entry = call_args[0][0]
        assert added_entry.type == "git"
        assert added_entry.name == "my-source"
        assert added_entry.url == "https://example.com/repo"

    @pytest.mark.asyncio
    async def test_remove_existing(self) -> None:
        """Remove an existing source prints 'removed'."""
        mocks, _, sources_mock, _ = _mock_managers()
        sources_mock.remove_source.return_value = True

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            await _sources("remove", "my-source", None, None)

        sources_mock.remove_source.assert_called_once_with("my-source")
        calls = _echo_calls(echo_mock)
        assert any("removed" in c for c in calls)

    @pytest.mark.asyncio
    async def test_remove_non_existing(self) -> None:
        """Remove a non-existing source prints 'not found'."""
        mocks, _, sources_mock, _ = _mock_managers()
        sources_mock.remove_source.return_value = False

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            await _sources("remove", "missing-source", None, None)

        calls = _echo_calls(echo_mock)
        assert any("not found" in c for c in calls)

    @pytest.mark.asyncio
    async def test_remove_without_name(self) -> None:
        """Remove without --name prints error and exits."""
        mocks, _, _, _ = _mock_managers()

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            with pytest.raises(click.exceptions.Exit) as exc_info:
                await _sources("remove", None, None, None)

        assert exc_info.value.exit_code == 1
        calls = _echo_calls(echo_mock)
        assert any("--name is required" in c for c in calls)

    @pytest.mark.asyncio
    async def test_unknown_action(self) -> None:
        """Unknown action prints error and exits with code 1."""
        mocks, _, _, _ = _mock_managers()

        with (
            patch("agent_nexus.platform.local.cli._init_managers", return_value=mocks),
            patch("agent_nexus.platform.local.cli.typer.echo") as echo_mock,
        ):
            with pytest.raises(click.exceptions.Exit) as exc_info:
                await _sources("bogus", None, None, None)

        assert exc_info.value.exit_code == 1
        calls = _echo_calls(echo_mock)
        assert any("Unknown action" in c for c in calls)
