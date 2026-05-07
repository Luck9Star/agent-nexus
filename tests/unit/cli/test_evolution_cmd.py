"""Tests for evolution_cmd.py -- status/health/list/history/metrics/fix/promote."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from agent_nexus.platform.local.cli import app

runner = CliRunner()


def _make_mock_engine():
    """Create a mock (engine, store) pair matching _get_engine() return type."""
    engine = MagicMock()

    engine.health_checker.get_health_summary.return_value = {
        "total_skills": 5,
        "healthy": 4,
        "unhealthy": 1,
        "suggestions": 0,
    }
    engine.health_checker.diagnose_all.return_value = {}

    mock_store = MagicMock()
    mock_store.get_active_skills.return_value = []
    mock_store.get_all_skills.return_value = []
    mock_store.get_ancestry.return_value = []
    from agent_nexus.models.evolution import EvolutionMetrics

    mock_store.get_metrics.return_value = EvolutionMetrics(
        total_selections=100,
        total_applied=80,
        total_completions=70,
        total_fallbacks=10,
    )
    engine.store = mock_store

    return engine, mock_store


class TestEvolutionStatus:
    def test_status_shows_summary(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine = _make_mock_engine()
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=engine):
            result = runner.invoke(app, ["evolution", "status"])
            assert result.exit_code == 0
            assert "5" in result.output


class TestEvolutionList:
    def test_list_with_no_skills(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine = _make_mock_engine()
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=engine):
            result = runner.invoke(app, ["evolution", "list"])
            assert result.exit_code == 0


class TestEvolutionMetrics:
    def test_metrics_shows_aggregate(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine = _make_mock_engine()
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=engine):
            result = runner.invoke(app, ["evolution", "metrics"])
            assert result.exit_code == 0
            assert "100" in result.output


class TestPromotePathTraversalRejection:
    """Verify that path-traversal skill IDs are rejected in evolution promote."""

    TRAVERSAL_IDS = [
        "../../etc/cron.d/backdoor",
        "../hidden",
        "/absolute/path",
        "name;rm -rf",
    ]

    @pytest.mark.parametrize("traversal_id", TRAVERSAL_IDS)
    def test_promote_rejects_traversal(self, traversal_id: str) -> None:
        result = runner.invoke(app, ["evolution", "promote", traversal_id])
        assert result.exit_code != 0
        assert "invalid" in result.output.lower()

    def test_promote_accepts_valid_skill_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Valid skill IDs pass the traversal guard."""
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine = _make_mock_engine()
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=engine):
            # "my-skill" is valid — should pass validation
            result = runner.invoke(app, ["evolution", "promote", "my-skill"])
            assert "invalid" not in result.output.lower()
