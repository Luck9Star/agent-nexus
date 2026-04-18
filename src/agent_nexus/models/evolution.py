"""Self-Evolution Engine models: SkillRecord, EvolutionType, SkillOrigin, SkillLineage, EvolutionContext, EvolutionMetrics."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

_utc_now = lambda: datetime.now(timezone.utc)


class EvolutionType(StrEnum):
    """How a Skill was produced by the Evolution Engine.

    - FIX: A broken Skill was repaired (1 parent).
    - DERIVED: A new Skill derived from 1+ existing Skills (enhancement/merge).
    - CAPTURED: A new Skill extracted from a successful task with no parent Skill (0 parents).
    """

    FIX = "fix"
    DERIVED = "derived"
    CAPTURED = "captured"


class SkillOrigin(StrEnum):
    """Where a Skill originally came from.

    - IMPORTED: Shipped with the Agent Package.
    - CAPTURED: Created by the Evolution Engine from a successful task.
    - DERIVED: Created by enhancing/merging existing Skills.
    - FIXED: Created by repairing a broken Skill.
    """

    IMPORTED = "imported"
    CAPTURED = "captured"
    DERIVED = "derived"
    FIXED = "fixed"


class SkillLineage(BaseModel):
    """DAG version history of a Skill.

    Tracks the origin, generation number, parent Skills (for FIX/DERIVED),
    content diff/snapshot for auditing.
    """

    model_config = ConfigDict(frozen=True)

    origin: SkillOrigin = SkillOrigin.IMPORTED
    generation: int = Field(default=0, ge=0)
    parent_skill_ids: list[str] = Field(default_factory=list)
    content_diff: str | None = None
    content_snapshot: dict[str, str] | None = None


class SkillRecord(BaseModel):
    """A Skill in the Evolution Engine's registry.

    Each SkillRecord tracks quality counters used by health diagnostics
    and evolution triggers:
        applied_rate      = total_applied / total_selections
        completion_rate   = total_completions / total_applied
        effective_rate    = total_completions / total_selections
        fallback_rate     = total_fallbacks / total_selections
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: str = "1.0.0"
    lineage: SkillLineage = Field(default_factory=SkillLineage)
    directory: str = ""
    is_active: bool = True
    total_selections: int = Field(default=0, ge=0)
    total_applied: int = Field(default=0, ge=0)
    total_completions: int = Field(default=0, ge=0)
    total_fallbacks: int = Field(default=0, ge=0)
    first_seen: datetime = Field(default_factory=_utc_now)
    last_updated: datetime = Field(default_factory=_utc_now)

    @model_validator(mode='after')
    def _validate_counters(self) -> 'SkillRecord':
        if self.total_selections == 0:
            if self.total_applied != 0 or self.total_fallbacks != 0:
                raise ValueError(
                    "counter invariant violated: zero selections requires zero applied and zero fallbacks"
                )
        if self.total_applied > self.total_selections:
            raise ValueError("total_applied cannot exceed total_selections")
        if self.total_completions > self.total_applied:
            raise ValueError("total_completions cannot exceed total_applied")
        if self.total_fallbacks > self.total_applied:
            raise ValueError("total_fallbacks cannot exceed total_applied")
        return self


class EvolutionMetrics(BaseModel):
    """Aggregated evolution quality metrics for an Agent.

    Used in L0 context injection (~30 tokens).
    """

    model_config = ConfigDict(frozen=True)

    total_selections: int = Field(default=0, ge=0)
    total_applied: int = Field(default=0, ge=0)
    total_completions: int = Field(default=0, ge=0)
    total_fallbacks: int = Field(default=0, ge=0)

    @model_validator(mode='after')
    def _validate_counters(self) -> 'EvolutionMetrics':
        if self.total_applied > self.total_selections:
            raise ValueError("total_applied cannot exceed total_selections")
        if self.total_completions > self.total_applied:
            raise ValueError("total_completions cannot exceed total_applied")
        if self.total_fallbacks > self.total_applied:
            raise ValueError("total_fallbacks cannot exceed total_applied")
        return self


class EvolutionContext(BaseModel):
    """Context passed to the Evolution Engine when triggering evolution.

    Contains the information needed for the LLM analyzer to decide
    whether and how to evolve a Skill.
    """

    model_config = ConfigDict(frozen=True)

    agent_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    task_description: str = ""
    task_completed: bool = False
    skill_ids_used: list[str] = Field(default_factory=list)
    skills_applied: list[str] = Field(default_factory=list)
    skills_fell_back: list[str] = Field(default_factory=list)
    execution_output: str | None = None
    execution_error: str | None = None
