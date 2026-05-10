"""EvolutionExperimenter -- A/B testing and rollback for evolved skills.

Design decisions (from docs/roadmap/p2-5-evolution.md):
  D23: Evolution mode: configurable, default A/B test
  D24: Rollback: previous version only (no arbitrary version history)
"""

from __future__ import annotations

import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from agent_nexus.models.evolution import SkillRecord
from agent_nexus.platform.evolution.store import EvolutionStore

logger = logging.getLogger(__name__)


class ExperimentStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    REVERTED = "reverted"


@dataclass(frozen=True)
class Experiment:
    experiment_id: str
    parent_skill_id: str
    evolved_skill_id: str
    status: ExperimentStatus = ExperimentStatus.RUNNING
    created_at: float = field(default_factory=time.time)
    min_samples: int = 30
    confidence_level: float = 0.95
    parent_successes: int = 0
    parent_total: int = 0
    evolved_successes: int = 0
    evolved_total: int = 0


@dataclass(frozen=True)
class ExperimentResult:
    parent_performance: float
    evolved_performance: float
    confidence: float
    recommendation: str  # "promote" | "revert" | "continue"
    samples_remaining: int


class EvolutionExperimenter:
    """A/B testing for evolved skills.

    Usage::

        ex = EvolutionExperimenter(store)
        exp = ex.create_experiment(parent, evolved)
        assigned = ex.assign(exp)  # random A or B
        ex.record_outcome(exp.experiment_id, assigned.id, success=True)
        result = ex.evaluate(exp.experiment_id)
        if result.recommendation == "revert":
            ex.rollback(exp.experiment_id)
    """

    def __init__(self, store: EvolutionStore) -> None:
        self._store = store
        self._experiments: dict[str, Experiment] = {}

    def create_experiment(
        self,
        parent: SkillRecord,
        evolved: SkillRecord,
        min_samples: int = 30,
        confidence_level: float = 0.95,
    ) -> Experiment:
        exp = Experiment(
            experiment_id=str(uuid.uuid4()),
            parent_skill_id=parent.id,
            evolved_skill_id=evolved.id,
            min_samples=min_samples,
            confidence_level=confidence_level,
        )
        self._experiments[exp.experiment_id] = exp
        logger.info(
            "Created experiment %s: parent=%s evolved=%s",
            exp.experiment_id[:8],
            parent.id,
            evolved.id,
        )
        return exp

    def assign(self, experiment: Experiment) -> SkillRecord:
        """Randomly assign parent or evolved version."""
        is_evolved = random.random() < 0.5
        skill_id = experiment.evolved_skill_id if is_evolved else experiment.parent_skill_id
        record = self._store.get_skill_record(skill_id)
        if record is None:
            raise KeyError(f"Skill not found: {skill_id}")
        return record

    def record_outcome(
        self,
        experiment_id: str,
        skill_id: str,
        success: bool,
    ) -> None:
        exp = self._experiments.get(experiment_id)
        if exp is None:
            raise KeyError(f"Experiment not found: {experiment_id}")

        if skill_id == exp.parent_skill_id:
            total = exp.parent_total + 1
            successes = exp.parent_successes + (1 if success else 0)
            updated = Experiment(
                experiment_id=exp.experiment_id,
                parent_skill_id=exp.parent_skill_id,
                evolved_skill_id=exp.evolved_skill_id,
                status=exp.status,
                created_at=exp.created_at,
                min_samples=exp.min_samples,
                confidence_level=exp.confidence_level,
                parent_successes=successes,
                parent_total=total,
                evolved_successes=exp.evolved_successes,
                evolved_total=exp.evolved_total,
            )
        elif skill_id == exp.evolved_skill_id:
            total = exp.evolved_total + 1
            successes = exp.evolved_successes + (1 if success else 0)
            updated = Experiment(
                experiment_id=exp.experiment_id,
                parent_skill_id=exp.parent_skill_id,
                evolved_skill_id=exp.evolved_skill_id,
                status=exp.status,
                created_at=exp.created_at,
                min_samples=exp.min_samples,
                confidence_level=exp.confidence_level,
                parent_successes=exp.parent_successes,
                parent_total=exp.parent_total,
                evolved_successes=successes,
                evolved_total=total,
            )
        else:
            raise ValueError(f"Skill {skill_id} is not part of experiment {experiment_id}")

        self._experiments[experiment_id] = updated

    def evaluate(self, experiment_id: str) -> ExperimentResult:
        exp = self._experiments.get(experiment_id)
        if exp is None:
            raise KeyError(f"Experiment not found: {experiment_id}")

        parent_perf = exp.parent_successes / exp.parent_total if exp.parent_total > 0 else 0.0
        evolved_perf = exp.evolved_successes / exp.evolved_total if exp.evolved_total > 0 else 0.0

        total_samples = exp.parent_total + exp.evolved_total
        samples_remaining = max(0, exp.min_samples * 2 - total_samples)

        # Simple confidence: minimum of parent/evolved sample counts / min_samples
        min_ratio = min(exp.parent_total, exp.evolved_total) / max(exp.min_samples, 1)
        confidence = min(1.0, min_ratio)

        if samples_remaining > 0:
            recommendation = "continue"
        elif evolved_perf > parent_perf and confidence >= 0.8:
            recommendation = "promote"
        elif evolved_perf < parent_perf and confidence >= 0.8:
            recommendation = "revert"
        else:
            recommendation = "continue"

        return ExperimentResult(
            parent_performance=round(parent_perf, 3),
            evolved_performance=round(evolved_perf, 3),
            confidence=round(confidence, 3),
            recommendation=recommendation,
            samples_remaining=samples_remaining,
        )

    def rollback(self, experiment_id: str) -> SkillRecord:
        """Rollback: deactivate evolved, reactivate parent.

        D24: Rollback to previous version only.
        """
        exp = self._experiments.get(experiment_id)
        if exp is None:
            raise KeyError(f"Experiment not found: {experiment_id}")

        # Deactivate evolved skill
        evolved = self._store.get_skill_record(exp.evolved_skill_id)
        if evolved is not None and evolved.is_active:
            self._store.deactivate_skill(exp.evolved_skill_id)

        # Ensure parent is active
        parent = self._store.get_skill_record(exp.parent_skill_id)
        if parent is not None and not parent.is_active:
            self._store.reactivate_skill(exp.parent_skill_id)

        # Mark experiment as reverted
        updated = Experiment(
            experiment_id=exp.experiment_id,
            parent_skill_id=exp.parent_skill_id,
            evolved_skill_id=exp.evolved_skill_id,
            status=ExperimentStatus.REVERTED,
            created_at=exp.created_at,
            min_samples=exp.min_samples,
            confidence_level=exp.confidence_level,
            parent_successes=exp.parent_successes,
            parent_total=exp.parent_total,
            evolved_successes=exp.evolved_successes,
            evolved_total=exp.evolved_total,
        )
        self._experiments[experiment_id] = updated

        if parent is None:
            raise KeyError(f"Parent skill not found: {exp.parent_skill_id}")
        logger.info("Rolled back experiment %s: reactivated %s", experiment_id[:8], parent.id)
        return parent

    def get_experiment(self, experiment_id: str) -> Experiment | None:
        return self._experiments.get(experiment_id)

    def list_active(self) -> list[Experiment]:
        return [e for e in self._experiments.values() if e.status == ExperimentStatus.RUNNING]
