"""Tests for config_cmd.py -- show/get/edit/validate/providers/path."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from agent_nexus.platform.local.cli import app

runner = CliRunner()


def _make_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_dir = tmp_path / ".agent-nexus"
    config_dir.mkdir()
    monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))
    return config_dir


class TestConfigShow:
    def test_show_outputs_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = _make_config_dir(tmp_path, monkeypatch)
        (config_dir / "config.toml").write_text(
            'schema_version = "1.0"\n[runtime]\npython_path = "python3"\n[models]\ndefault = "openai:gpt-4o"\n'
        )

        with patch("agent_nexus.platform.local.cli._shared._init_managers") as mock_init:
            mock_config = MagicMock()
            mock_config.models.default = "openai:gpt-4o"
            mock_config.models.providers.keys.return_value = ["openai"]
            mock_config.runtime.python_path = "python3"
            mock_config.runtime.uv_path = "uv"
            mock_loader = MagicMock()
            mock_loader.config_dir = config_dir
            mock_loader.load_config.return_value = mock_config
            mock_init.return_value = (mock_loader, MagicMock(), MagicMock(), config_dir)

            result = runner.invoke(app, ["config", "show"])
            assert result.exit_code == 0
            assert "gpt-4o" in result.output


class TestConfigGet:
    def test_get_returns_value(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = _make_config_dir(tmp_path, monkeypatch)

        with patch("agent_nexus.platform.local.cli._shared._init_managers") as mock_init:
            mock_config = MagicMock()
            mock_config.model_dump.return_value = {
                "models": {"default": "openai:gpt-4o"},
                "runtime": {"python_path": "python3", "uv_path": "uv"},
            }
            mock_loader = MagicMock()
            mock_loader.load_config.return_value = mock_config
            mock_init.return_value = (mock_loader, MagicMock(), MagicMock(), config_dir)

            result = runner.invoke(app, ["config", "get", "models.default"])
            assert result.exit_code == 0
            assert "gpt-4o" in result.output


class TestConfigValidate:
    def test_validate_valid_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = _make_config_dir(tmp_path, monkeypatch)
        (config_dir / "config.toml").write_text(
            'schema_version = "1.0"\n[runtime]\npython_path = "python3"\n'
        )

        with patch("agent_nexus.platform.local.cli._shared._init_managers") as mock_init:
            mock_loader = MagicMock()
            mock_loader.load_config.return_value = MagicMock()
            mock_init.return_value = (mock_loader, MagicMock(), MagicMock(), config_dir)

            result = runner.invoke(app, ["config", "validate"])
            assert result.exit_code == 0
            assert "valid" in result.output.lower()


class TestConfigPath:
    def test_path_outputs_config_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = _make_config_dir(tmp_path, monkeypatch)

        result = runner.invoke(app, ["config", "path"])
        assert result.exit_code == 0
        assert str(config_dir) in result.output


class TestConfigProviders:
    def test_providers_lists_providers(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = _make_config_dir(tmp_path, monkeypatch)

        with patch("agent_nexus.platform.local.cli._shared._init_managers") as mock_init:
            mock_provider = MagicMock()
            mock_provider.api_key_env = "OPENAI_API_KEY"
            mock_provider.base_url = None
            mock_config = MagicMock()
            mock_config.models.providers = {"openai": mock_provider}
            mock_loader = MagicMock()
            mock_loader.load_config.return_value = mock_config
            mock_init.return_value = (mock_loader, MagicMock(), MagicMock(), config_dir)

            result = runner.invoke(app, ["config", "providers"])
            assert result.exit_code == 0
            assert "openai" in result.output.lower()
