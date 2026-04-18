"""Self-Evolution Engine models: SkillRecord, EvolutionType, SkillOrigin, SkillLineage, EvolutionContext, EvolutionMetrics."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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
    generation: int = 0
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

    id: str
    name: str
    version: str = "1.0.0"
    lineage: SkillLineage = Field(default_factory=SkillLineage)
    directory: str = ""
    is_active: bool = True
    total_selections: int = 0
    total_applied: int = 0
    total_completions: int = 0
    total_fallbacks: int = 0
    first_seen: datetime = Field(default_factory=datetime.now)
    last_updated: datetime = Field(default_factory=datetime.now)


class EvolutionMetrics(BaseModel):
    """Aggregated evolution quality metrics for an Agent.

    Used in L0 context injection (~30 tokens).
    """

    model_config = ConfigDict(frozen=True)

    total_selections: int = 0
    total_applied: int = 0
    total_completions: int = 0
    total_fallbacks: int = 0


class EvolutionContext(BaseModel):
    """Context passed to the Evolution Engine when triggering evolution.

    Contains the information needed for the LLM analyzer to decide
    whether and how to evolve a Skill.
    """

    model_config = ConfigDict(frozen=True)

    agent_id: str
    task_id: str
    task_description: str = ""
    task_completed: bool = False
    skill_ids_used: list[str] = Field(default_factory=list)
    skills_applied: list[str] = Field(default_factory=list)
    skills_fell_back: list[str] = Field(default_factory=list)
    execution_output: str | None = None
    execution_error: str | None = None
