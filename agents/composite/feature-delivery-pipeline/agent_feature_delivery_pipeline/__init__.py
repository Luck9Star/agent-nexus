"""agent-feature-delivery-pipeline -- Requirements-driven parallel delivery pipeline.

Composite Agent that orchestrates requirements-analyzer, api-doc-generator,
test-suite-generator, and code-reviewer in a sequential->parallel DAG pattern.
"""

from agent_nexus.models.composition import (
    Composition,
    CompositionError,
    CompositionTask,
)

from agent_feature_delivery_pipeline.coordinator import (
    FeatureDeliveryCoordinator,
)
from agent_feature_delivery_pipeline.models import (
    PipelineResult,
    PipelineStage,
    StageStatus,
)

__all__ = [
    "Composition",
    "CompositionError",
    "CompositionTask",
    "FeatureDeliveryCoordinator",
    "PipelineResult",
    "PipelineStage",
    "StageStatus",
]
