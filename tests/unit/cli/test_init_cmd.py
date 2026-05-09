"""Tests for init_cmd.py -- init, doctor, version, env."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_nexus.platform.local.cli import app

runner = CliRunner()


class TestVersion:
    def test_version_outputs_version_string(self) -> None:
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert re.search(r"\d+\.\d+", result.output) or "unknown" in result.output


class TestDoctor:
    def test_doctor_checks_all_items(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        (config_dir / "config.toml").write_text(
            'schema_version = "1.0"\n[runtime]\npython_path = "python3"\n'
        )

        result = runner.invoke(app, ["doctor"])
        output_lower = result.output.lower()
        assert "config" in output_lower
        assert "pass" in output_lower or "fail" in output_lower

    def test_doctor_reports_missing_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        result = runner.invoke(app, ["doctor"])
        # Should show at least one FAIL for missing config
        assert "FAIL" in result.output or "not found" in result.output.lower()


class TestInit:
    def test_init_creates_config_dir_and_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / "fresh-home"
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert config_dir.exists()
        assert (config_dir / "config.toml").exists()

    def test_init_idempotent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / "fresh-home"
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        result1 = runner.invoke(app, ["init"])
        assert result1.exit_code == 0

        result2 = runner.invoke(app, ["init"])
        assert result2.exit_code == 0
        content = (config_dir / "config.toml").read_text()
        assert "schema_version" in content


class TestEnv:
    def test_env_outputs_environment_info(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        result = runner.invoke(app, ["env"])
        assert result.exit_code == 0
        assert "Config dir" in result.output
        assert "Python" in result.output
