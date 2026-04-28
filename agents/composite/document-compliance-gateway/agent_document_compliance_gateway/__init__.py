"""agent-document-compliance-gateway -- Cross-dimension document compliance checking.

Composite Agent that orchestrates contract-analyzer, accessibility-auditor,
and localization-specialist in a full-parallel DAG pattern with conflict detection.
"""

from agent_document_compliance_gateway.coordinator import (
    ComplianceCoordinator,
)
from agent_nexus.models.composition import (
    Composition,
    CompositionError,
    CompositionTask,
)
from agent_document_compliance_gateway.models import (
    CheckStatus,
    ComplianceCheck,
    ComplianceResult,
    ConflictItem,
)

__all__ = [
    "CheckStatus",
    "ComplianceCheck",
    "ComplianceCoordinator",
    "ComplianceResult",
    "Composition",
    "CompositionError",
    "CompositionTask",
    "ConflictItem",
]
