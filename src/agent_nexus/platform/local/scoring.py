"""Agent scoring and rating management."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from agent_nexus.models.distribution import AgentScore

logger = logging.getLogger(__name__)


class ScoreManager:
    """Manages agent quality scores and download counts.

    Stores scores in a JSON file in the config directory.
    """

    def __init__(self, scores_path: Path) -> None:
        self._path = scores_path
        self._scores: dict[str, AgentScore] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for agent_name, score_data in data.items():
                    if isinstance(score_data, dict):
                        if "last_updated" in score_data and isinstance(
                            score_data["last_updated"], str
                        ):
                            score_data["last_updated"] = datetime.fromisoformat(
                                score_data["last_updated"]
                            )
                        self._scores[agent_name] = AgentScore.model_validate(score_data)
            except (json.JSONDecodeError, Exception) as e:
                logger.warning("Failed to load scores from %s: %s", self._path, e)

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {}
        for name, score in self._scores.items():
            d = score.model_dump(mode="json")
            data[name] = d
        self._path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def get_score(self, agent_name: str) -> AgentScore:
        return self._scores.get(agent_name, AgentScore())

    def record_download(self, agent_name: str) -> AgentScore:
        current = self.get_score(agent_name)
        updated = AgentScore(
            quality_gate_score=current.quality_gate_score,
            download_count=current.download_count + 1,
            average_rating=current.average_rating,
            rating_count=current.rating_count,
            last_updated=datetime.now(UTC),
        )
        self._scores[agent_name] = updated
        self._save()
        return updated

    def record_quality_score(self, agent_name: str, score: float) -> AgentScore:
        current = self.get_score(agent_name)
        updated = AgentScore(
            quality_gate_score=score,
            download_count=current.download_count,
            average_rating=current.average_rating,
            rating_count=current.rating_count,
            last_updated=datetime.now(UTC),
        )
        self._scores[agent_name] = updated
        self._save()
        return updated

    def record_user_rating(self, agent_name: str, rating: float) -> AgentScore:
        """Record a user rating (1-5). Updates running average."""
        if not 1.0 <= rating <= 5.0:
            raise ValueError(f"Rating must be between 1.0 and 5.0, got {rating}")
        current = self.get_score(agent_name)
        total = (current.average_rating or 0.0) * current.rating_count + rating
        new_count = current.rating_count + 1
        new_avg = round(total / new_count, 2)
        updated = AgentScore(
            quality_gate_score=current.quality_gate_score,
            download_count=current.download_count,
            average_rating=new_avg,
            rating_count=new_count,
            last_updated=datetime.now(UTC),
        )
        self._scores[agent_name] = updated
        self._save()
        return updated

    def list_scores(self) -> dict[str, AgentScore]:
        return dict(self._scores)
