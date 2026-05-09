"""Tests for evolution_cmd.py -- status/health/list/history/metrics/fix/promote."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from agent_nexus.models.evolution import EvolutionMetrics, SkillLineage, SkillRecord
from agent_nexus.platform.local.cli import app

runner = CliRunner()


# ── Helpers ──────────────────────────────────────────────────────────


def _make_mock_engine():
    """Create a mock engine with a mock store attached.

    Returns (engine, store) tuple matching _get_engine() return type.
    """
    mock_store = MagicMock()
    mock_store.get_active_skills.return_value = []
    mock_store.get_all_skills.return_value = []
    mock_store.get_ancestry.return_value = []
    mock_store.get_metrics.return_value = EvolutionMetrics(
        total_selections=100,
        total_applied=80,
        total_completions=70,
        total_fallbacks=10,
    )
    mock_store.close.return_value = None

    engine = MagicMock()
    engine.health_checker.get_health_summary.return_value = {
        "total_skills": 5,
        "healthy": 4,
        "unhealthy": 1,
        "suggestions": 0,
    }
    engine.store = mock_store

    return engine, mock_store


def _make_skill_record(
    name: str = "test-skill",
    id: str = "skill-001",
    is_active: bool = True,
    lineage: SkillLineage | None = None,
) -> SkillRecord:
    return SkillRecord(
        id=id,
        name=name,
        version="1.0.0",
        lineage=lineage or SkillLineage(generation=1),
        is_active=is_active,
        total_selections=10,
        total_applied=8,
        total_completions=7,
        total_fallbacks=1,
        first_seen=datetime(2026, 1, 1),
        last_updated=datetime(2026, 5, 1),
    )


# ── status ───────────────────────────────────────────────────────────


class TestEvolutionStatus:
    def test_status_shows_summary(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "status"])
            assert result.exit_code == 0
            assert "5" in result.output
            assert "4" in result.output  # healthy
            assert "1" in result.output  # unhealthy

    def test_status_zero_skills(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        engine.health_checker.get_health_summary.return_value = {
            "total_skills": 0,
            "healthy": 0,
            "unhealthy": 0,
            "suggestions": 0,
        }
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "status"])
            assert result.exit_code == 0
            assert "0" in result.output


# ── health ───────────────────────────────────────────────────────────


class TestEvolutionHealth:
    def test_health_specific_skill_healthy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        engine.check_health.return_value = []  # no suggestions = healthy

        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "health", "my-skill"])
            assert result.exit_code == 0
            assert "HEALTHY" in result.output

    def test_health_specific_skill_unhealthy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_nexus.platform.evolution.thresholds import EvolutionSuggestion
        from agent_nexus.models.evolution import EvolutionType

        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        engine.check_health.return_value = [
            EvolutionSuggestion(
                evolution_type=EvolutionType.DERIVED,
                direction="improve error handling",
            ),
        ]

        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "health", "unhealthy-skill"])
            assert result.exit_code == 0
            assert "UNHEALTHY" in result.output
            assert "improve error handling" in result.output

    def test_health_skill_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        engine.check_health.side_effect = ValueError("Skill not found")

        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "health", "missing-skill"])
            assert result.exit_code == 1

    def test_health_all_skills_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        engine.diagnose_all.return_value = {}

        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "health"])
            assert result.exit_code == 0
            assert "no skills" in result.output.lower()

    def test_health_all_skills_with_reports(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from agent_nexus.platform.evolution.health import HealthReport

        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        reports = {
            "skill-a": HealthReport(
                skill_id="id-a",
                skill_name="skill-a",
                is_healthy=True,
                suggestions=[],
                metrics={"applied_rate": 0.8, "completion_rate": 0.7, "fallback_rate": 0.1},
            ),
            "skill-b": HealthReport(
                skill_id="id-b",
                skill_name="skill-b",
                is_healthy=False,
                suggestions=[],
                metrics={"applied_rate": 0.3, "completion_rate": 0.2, "fallback_rate": 0.5},
            ),
        }
        engine.diagnose_all.return_value = reports

        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "health"])
            assert result.exit_code == 0
            assert "skill-a" in result.output
            assert "skill-b" in result.output
            assert "HEALTHY" in result.output
            assert "UNHEALTHY" in result.output


# ── list ─────────────────────────────────────────────────────────────


class TestEvolutionList:
    def test_list_with_no_skills(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "list"])
            assert result.exit_code == 0
            assert "no skills" in result.output.lower()

    def test_list_active_skills(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        engine.store.get_active_skills.return_value = [
            _make_skill_record("active-skill"),
        ]

        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "list"])
            assert result.exit_code == 0
            assert "active-skill" in result.output

    def test_list_all_includes_inactive(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        engine.store.get_all_skills.return_value = [
            _make_skill_record("active-skill", is_active=True),
            _make_skill_record("old-skill", id="skill-002", is_active=False),
        ]

        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "list", "--all"])
            assert result.exit_code == 0
            assert "active-skill" in result.output
            assert "old-skill" in result.output
            assert "inactive" in result.output.lower()


# ── history ──────────────────────────────────────────────────────────


class TestEvolutionHistory:
    def test_history_by_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        skill = _make_skill_record("my-skill", id="uuid-1234")
        engine.store.get_skill_record.return_value = skill
        engine.store.get_ancestry.return_value = [
            _make_skill_record("my-skill", id="uuid-1234", lineage=SkillLineage(generation=2)),
            _make_skill_record("my-skill", id="uuid-parent", lineage=SkillLineage(generation=1)),
        ]

        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "history", "uuid-1234"])
            assert result.exit_code == 0
            assert "my-skill" in result.output
            assert "gen 2" in result.output
            assert "gen 1" in result.output

    def test_history_by_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        engine.store.get_skill_record.return_value = None  # not found by ID
        engine.store.get_versions.return_value = [
            _make_skill_record("named-skill", id="v2-id", is_active=True, lineage=SkillLineage(generation=2)),
        ]
        engine.store.get_ancestry.return_value = [
            _make_skill_record("named-skill", id="v2-id", lineage=SkillLineage(generation=2)),
        ]

        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "history", "named-skill"])
            assert result.exit_code == 0
            assert "named-skill" in result.output

    def test_history_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        engine.store.get_skill_record.return_value = None
        engine.store.get_versions.return_value = []

        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "history", "nonexistent"])
            assert result.exit_code == 1
            assert "not found" in result.output.lower()

    def test_history_no_ancestry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        skill = _make_skill_record("solo-skill")
        engine.store.get_skill_record.return_value = skill
        engine.store.get_ancestry.return_value = []

        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "history", "solo-skill"])
            assert result.exit_code == 0
            assert "no ancestry" in result.output.lower()


# ── metrics ──────────────────────────────────────────────────────────


class TestEvolutionMetrics:
    def test_metrics_shows_aggregate(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "metrics"])
            assert result.exit_code == 0
            assert "100" in result.output  # total_selections
            assert "70" in result.output  # total_completions

    def test_metrics_with_zero_selections(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        engine.store.get_metrics.return_value = EvolutionMetrics()
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "metrics"])
            assert result.exit_code == 0
            # Zero selections means no rate lines
            assert "success rate" not in result.output.lower()


# ── fix ──────────────────────────────────────────────────────────────


class TestEvolutionFix:
    def test_fix_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        engine.evolve.return_value = []
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "fix", "my-skill"])
            assert result.exit_code == 0
            assert "triggered" in result.output.lower()

    def test_fix_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        engine.evolve.side_effect = RuntimeError("evolution engine crashed")
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "fix", "my-skill"])
            assert result.exit_code == 1
            assert "failed" in result.output.lower()


# ── promote ──────────────────────────────────────────────────────────


class TestEvolutionPromote:
    def test_promote_success(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent_nexus.platform.evolution.promotion import PromotionResult

        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        engine.promote_candidate.return_value = PromotionResult(
            success=True,
            agent_name="promoted-skill",
        )
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "promote", "my-skill"])
            assert "promoted" in result.output.lower()

    def test_promote_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from agent_nexus.platform.evolution.promotion import PromotionResult

        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        engine.promote_candidate.return_value = PromotionResult(
            success=False,
            error="quality gate failed",
        )
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "promote", "my-skill"])
            assert "not completed" in result.output.lower()

    def test_promote_exception(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        engine.promote_candidate.side_effect = RuntimeError("store error")
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "promote", "my-skill"])
            assert result.exit_code == 1
            assert "failed" in result.output.lower()


# ── path traversal ───────────────────────────────────────────────────


class TestPromotePathTraversalRejection:
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

    @pytest.mark.parametrize("traversal_id", TRAVERSAL_IDS)
    def test_fix_rejects_traversal(self, traversal_id: str) -> None:
        result = runner.invoke(app, ["evolution", "fix", traversal_id])
        assert result.exit_code != 0
        assert "invalid" in result.output.lower()

    def test_promote_accepts_valid_skill_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config_dir = tmp_path / ".agent-nexus"
        config_dir.mkdir()
        (config_dir / "config.toml").write_text('schema_version = "1.0"\n')
        monkeypatch.setenv("AGENT_NEXUS_HOME", str(config_dir))

        engine, _store = _make_mock_engine()
        with patch("agent_nexus.platform.local.cli.evolution_cmd._get_engine", return_value=(engine, _store)):
            result = runner.invoke(app, ["evolution", "promote", "my-skill"])
            assert "invalid" not in result.output.lower()
