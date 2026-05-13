"""Unit tests for ScoreManager: agent quality scoring and rating management."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from agent_nexus.models.distribution import AgentScore
from agent_nexus.platform.local.scoring import ScoreManager


class TestAgentScoreModel:
    """AgentScore Pydantic model defaults and validation."""

    def test_all_fields(self) -> None:
        now = datetime.now(UTC)
        score = AgentScore(
            quality_gate_score=0.92,
            download_count=42,
            average_rating=4.5,
            rating_count=10,
            last_updated=now,
        )
        assert score.quality_gate_score == 0.92
        assert score.download_count == 42
        assert score.average_rating == 4.5
        assert score.rating_count == 10
        assert score.last_updated == now


class TestScoreManagerInit:
    """ScoreManager.__init__ and _load."""

    def test_init_with_missing_file(self, tmp_path: object) -> None:
        from pathlib import Path

        path = Path(str(tmp_path)) / "scores.json"
        sm = ScoreManager(path)
        assert sm.list_scores() == {}

    def test_init_loads_valid_file(self, tmp_path: object) -> None:
        from pathlib import Path

        path = Path(str(tmp_path)) / "scores.json"
        data = {
            "my-agent": {
                "quality_gate_score": 0.85,
                "download_count": 5,
                "average_rating": None,
                "rating_count": 0,
                "last_updated": "2026-05-11T12:00:00+00:00",
            }
        }
        path.write_text(json.dumps(data), encoding="utf-8")
        sm = ScoreManager(path)
        scores = sm.list_scores()
        assert "my-agent" in scores
        assert scores["my-agent"].download_count == 5

    def test_init_handles_invalid_json(self, tmp_path: object) -> None:
        from pathlib import Path

        path = Path(str(tmp_path)) / "scores.json"
        path.write_text("{bad json", encoding="utf-8")
        sm = ScoreManager(path)
        assert sm.list_scores() == {}


class TestGetScore:
    """ScoreManager.get_score()"""

    def test_returns_empty_score_for_unknown(self, tmp_path: object) -> None:
        from pathlib import Path

        sm = ScoreManager(Path(str(tmp_path)) / "scores.json")
        score = sm.get_score("nonexistent")
        assert score == AgentScore()

    def test_returns_recorded_score(self, tmp_path: object) -> None:
        from pathlib import Path

        sm = ScoreManager(Path(str(tmp_path)) / "scores.json")
        sm.record_download("my-agent")
        score = sm.get_score("my-agent")
        assert score.download_count == 1


class TestRecordDownload:
    """ScoreManager.record_download()"""

    def test_increments_count(self, tmp_path: object) -> None:
        from pathlib import Path

        sm = ScoreManager(Path(str(tmp_path)) / "scores.json")
        score = sm.record_download("agent-a")
        assert score.download_count == 1
        assert score.last_updated is not None

    def test_increments_from_existing(self, tmp_path: object) -> None:
        from pathlib import Path

        sm = ScoreManager(Path(str(tmp_path)) / "scores.json")
        sm.record_download("agent-a")
        score = sm.record_download("agent-a")
        assert score.download_count == 2

    def test_preserves_other_fields(self, tmp_path: object) -> None:
        from pathlib import Path

        sm = ScoreManager(Path(str(tmp_path)) / "scores.json")
        sm.record_quality_score("agent-a", 0.9)
        sm.record_download("agent-a")
        score = sm.get_score("agent-a")
        assert score.quality_gate_score == 0.9
        assert score.download_count == 1


class TestRecordQualityScore:
    """ScoreManager.record_quality_score()"""

    def test_stores_score(self, tmp_path: object) -> None:
        from pathlib import Path

        sm = ScoreManager(Path(str(tmp_path)) / "scores.json")
        score = sm.record_quality_score("agent-a", 0.85)
        assert score.quality_gate_score == 0.85
        assert score.last_updated is not None

    def test_overwrites_previous(self, tmp_path: object) -> None:
        from pathlib import Path

        sm = ScoreManager(Path(str(tmp_path)) / "scores.json")
        sm.record_quality_score("agent-a", 0.5)
        score = sm.record_quality_score("agent-a", 0.95)
        assert score.quality_gate_score == 0.95


class TestRecordUserRating:
    """ScoreManager.record_user_rating()"""

    def test_records_first_rating(self, tmp_path: object) -> None:
        from pathlib import Path

        sm = ScoreManager(Path(str(tmp_path)) / "scores.json")
        score = sm.record_user_rating("agent-a", 4.0)
        assert score.average_rating == 4.0
        assert score.rating_count == 1

    def test_computes_running_average(self, tmp_path: object) -> None:
        from pathlib import Path

        sm = ScoreManager(Path(str(tmp_path)) / "scores.json")
        sm.record_user_rating("agent-a", 5.0)
        score = sm.record_user_rating("agent-a", 3.0)
        assert score.average_rating == 4.0
        assert score.rating_count == 2

    def test_rejects_below_range(self, tmp_path: object) -> None:
        from pathlib import Path

        sm = ScoreManager(Path(str(tmp_path)) / "scores.json")
        with pytest.raises(ValueError, match="between 1.0 and 5.0"):
            sm.record_user_rating("agent-a", 0.5)

    def test_rejects_above_range(self, tmp_path: object) -> None:
        from pathlib import Path

        sm = ScoreManager(Path(str(tmp_path)) / "scores.json")
        with pytest.raises(ValueError, match="between 1.0 and 5.0"):
            sm.record_user_rating("agent-a", 5.5)

    def test_accepts_boundary_values(self, tmp_path: object) -> None:
        from pathlib import Path

        sm = ScoreManager(Path(str(tmp_path)) / "scores.json")
        score = sm.record_user_rating("agent-a", 1.0)
        assert score.average_rating == 1.0
        score = sm.record_user_rating("agent-a", 5.0)
        assert score.average_rating == 3.0

    def test_preserves_other_fields(self, tmp_path: object) -> None:
        from pathlib import Path

        sm = ScoreManager(Path(str(tmp_path)) / "scores.json")
        sm.record_quality_score("agent-a", 0.88)
        sm.record_download("agent-a")
        sm.record_user_rating("agent-a", 4.0)
        score = sm.get_score("agent-a")
        assert score.quality_gate_score == 0.88
        assert score.download_count == 1
        assert score.average_rating == 4.0


class TestListScores:
    """ScoreManager.list_scores()"""

    def test_empty_when_no_scores(self, tmp_path: object) -> None:
        from pathlib import Path

        sm = ScoreManager(Path(str(tmp_path)) / "scores.json")
        assert sm.list_scores() == {}

    def test_returns_all_recorded(self, tmp_path: object) -> None:
        from pathlib import Path

        sm = ScoreManager(Path(str(tmp_path)) / "scores.json")
        sm.record_download("agent-a")
        sm.record_download("agent-b")
        scores = sm.list_scores()
        assert len(scores) == 2
        assert "agent-a" in scores
        assert "agent-b" in scores


class TestPersistence:
    """Score data persists across ScoreManager instances."""

    def test_save_and_reload(self, tmp_path: object) -> None:
        from pathlib import Path

        path = Path(str(tmp_path)) / "scores.json"
        sm1 = ScoreManager(path)
        sm1.record_quality_score("persist-agent", 0.77)
        sm1.record_download("persist-agent")
        sm1.record_user_rating("persist-agent", 4.5)

        sm2 = ScoreManager(path)
        score = sm2.get_score("persist-agent")
        assert score.quality_gate_score == 0.77
        assert score.download_count == 1
        assert score.average_rating == 4.5
        assert score.rating_count == 1

    def test_datetime_round_trip(self, tmp_path: object) -> None:
        from pathlib import Path

        path = Path(str(tmp_path)) / "scores.json"
        sm1 = ScoreManager(path)
        sm1.record_download("dt-agent")

        sm2 = ScoreManager(path)
        score = sm2.get_score("dt-agent")
        assert score.last_updated is not None
        assert isinstance(score.last_updated, datetime)
