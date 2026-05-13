"""Tests for sources_cmd.py — Typer CLI for package source management."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from agent_nexus.models.distribution import SourceEntry

runner = CliRunner()


def _mock_managers(sources=None):
    """Create mock managers tuple returned by _init_managers."""
    sources_mock = MagicMock()
    sources_mock.list_sources.return_value = sources or []
    sources_mock.add_source = MagicMock()
    sources_mock.remove_source.return_value = False
    return MagicMock(), MagicMock(), sources_mock, MagicMock()


class TestSourcesList:
    @patch("agent_nexus.platform.local.cli.sources_cmd._init_managers")
    def test_list_empty(self, mock_init):
        """List with no sources prints 'No sources configured.'."""
        mock_init.return_value = _mock_managers(sources=[])
        from agent_nexus.platform.local.cli.sources_cmd import sources_app

        result = runner.invoke(sources_app, ["list"])
        assert result.exit_code == 0
        assert "No sources configured" in result.output

    @patch("agent_nexus.platform.local.cli.sources_cmd._init_managers")
    def test_list_with_sources(self, mock_init):
        """List formats source entries in a table."""
        entries = [
            SourceEntry(name="official", type="git", url="https://github.com/org/repo"),
            SourceEntry(name="private", type="git", url="https://gitlab.com/team/proj"),
        ]
        mock_init.return_value = _mock_managers(sources=entries)
        from agent_nexus.platform.local.cli.sources_cmd import sources_app

        result = runner.invoke(sources_app, ["list"])
        assert result.exit_code == 0
        assert "official" in result.output
        assert "private" in result.output
        assert "https://github.com/org/repo" in result.output
        # Header row present
        assert "Name" in result.output
        assert "Type" in result.output


class TestSourcesAdd:
    @patch("agent_nexus.platform.local.cli.sources_cmd._init_managers")
    @patch("agent_nexus.platform.local.cli.sources_cmd._validate_git_url")
    def test_add_valid_url(self, mock_validate, mock_init):
        """Adding a source with valid URL succeeds."""
        mock_init.return_value = _mock_managers()
        from agent_nexus.platform.local.cli.sources_cmd import sources_app

        result = runner.invoke(
            sources_app, ["add", "--name", "my-src", "--url", "https://github.com/org/repo"]
        )
        assert result.exit_code == 0
        assert "my-src" in result.output
        assert "added" in result.output

    @patch("agent_nexus.platform.local.cli.sources_cmd._validate_git_url")
    def test_add_invalid_url(self, mock_validate):
        """Adding a source with invalid URL exits with code 1."""
        mock_validate.side_effect = ValueError("Invalid git URL scheme: 'file:///bad'")
        from agent_nexus.platform.local.cli.sources_cmd import sources_app

        result = runner.invoke(sources_app, ["add", "--name", "bad", "--url", "file:///bad"])
        assert result.exit_code == 1
        assert "Error" in result.output

    @patch("agent_nexus.platform.local.cli.sources_cmd._init_managers")
    @patch("agent_nexus.platform.local.cli.sources_cmd._validate_git_url")
    def test_add_default_type_is_git(self, mock_validate, mock_init):
        """Default source type is 'git' when --type not specified."""
        _, _, sources_mock, _ = _mock_managers()
        mock_init.return_value = (_, _, sources_mock, _)
        from agent_nexus.platform.local.cli.sources_cmd import sources_app

        result = runner.invoke(
            sources_app, ["add", "--name", "def-src", "--url", "https://github.com/org/r"]
        )
        assert result.exit_code == 0
        call_args = sources_mock.add_source.call_args[0][0]
        assert isinstance(call_args, SourceEntry)
        assert call_args.type == "git"

    @patch("agent_nexus.platform.local.cli.sources_cmd._init_managers")
    @patch("agent_nexus.platform.local.cli.sources_cmd._validate_git_url")
    def test_add_custom_type(self, mock_validate, mock_init):
        """Custom source type is passed through."""
        _, _, sources_mock, _ = _mock_managers()
        mock_init.return_value = (_, _, sources_mock, _)
        from agent_nexus.platform.local.cli.sources_cmd import sources_app

        result = runner.invoke(
            sources_app,
            ["add", "--name", "local-src", "--url", "https://example.com", "--type", "local"],
        )
        assert result.exit_code == 0
        call_args = sources_mock.add_source.call_args[0][0]
        assert call_args.type == "local"


class TestSourcesRemove:
    @patch("agent_nexus.platform.local.cli.sources_cmd._init_managers")
    def test_remove_existing(self, mock_init):
        """Removing an existing source succeeds."""
        _, _, sources_mock, _ = _mock_managers()
        sources_mock.remove_source.return_value = True
        mock_init.return_value = (_, _, sources_mock, _)
        from agent_nexus.platform.local.cli.sources_cmd import sources_app

        result = runner.invoke(sources_app, ["remove", "my-src"])
        assert result.exit_code == 0
        assert "removed" in result.output

    @patch("agent_nexus.platform.local.cli.sources_cmd._init_managers")
    def test_remove_not_found(self, mock_init):
        """Removing a non-existent source exits with code 1."""
        _, _, sources_mock, _ = _mock_managers()
        sources_mock.remove_source.return_value = False
        mock_init.return_value = (_, _, sources_mock, _)
        from agent_nexus.platform.local.cli.sources_cmd import sources_app

        result = runner.invoke(sources_app, ["remove", "ghost"])
        assert result.exit_code == 1
        assert "not found" in result.output
