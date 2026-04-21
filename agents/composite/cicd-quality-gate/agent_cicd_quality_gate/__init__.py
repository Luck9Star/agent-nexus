"""agent-cicd-quality-gate -- CI/CD parallel quality gate.

Composite Agent that orchestrates security-scanner, code-reviewer,
and test-suite-generator in a full-parallel DAG pattern with quality gate decision.
"""

from agent_cicd_quality_gate.coordinator import (
    QualityGateCoordinator,
)
from agent_nexus.models.composition import (
    Composition,
    CompositionError,
    CompositionTask,
)
from agent_cicd_quality_gate.models import (
    GateCheck,
    GateResult,
)

__all__ = [
    "Composition",
    "CompositionError",
    "CompositionTask",
    "GateCheck",
    "GateResult",
    "QualityGateCoordinator",
]
