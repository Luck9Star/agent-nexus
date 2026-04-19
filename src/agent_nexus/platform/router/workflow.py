"""Workflow types for the Platform Router 4-Phase pattern.

Defines:
- WorkflowPhase: The four phases (research, synthesis, implementation, verification)
- WorkflowContext: Tracks state across phases within a single workflow
- WorkflowResult: Final outcome of a completed workflow
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from agent_nexus.platform.orchestration.task_graph import TaskGraph


class WorkflowPhase(StrEnum):
    """The four phases of a composite agent workflow.

    Phases execute in order:
    1. research    -- parallel workers gather information
    2. synthesis   -- coordinator alone analyzes and plans
    3. implementation -- parallel workers execute the plan
    4. verification   -- fresh worker verifies results
    """

    research = "research"
    synthesis = "synthesis"
    implementation = "implementation"
    verification = "verification"


@dataclass
class WorkflowContext:
    """Track state across workflow phases.

    Created fresh per composite workflow invocation.
    Holds accumulated results, current phase, and the TaskGraph
    instance managing task dependencies for this workflow.
    """

    conversation_id: str
    message: str
    agent_name: str
    phase_results: dict[WorkflowPhase, Any] = field(default_factory=dict)
    current_phase: WorkflowPhase | None = None
    task_graph: TaskGraph | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def close(self) -> None:
        """Release the TaskGraph reference for GC.

        TaskGraph uses ephemeral SQLite connections (per-operation), so
        there is no persistent connection to close.  Setting to ``None``
        drops the reference so the object (and its db_path) can be
        collected.
        """
        self.task_graph = None


@dataclass
class WorkflowResult:
    """Final result from a completed workflow.

    ``success`` is True only when all four phases completed without error.
    ``phase_results`` maps each completed phase to its string output.
    """

    success: bool
    final_output: str
    phase_results: dict[WorkflowPhase, str]
    total_phases: int
    completed_phases: int
    error: str | None = None
    error_type: str | None = None
