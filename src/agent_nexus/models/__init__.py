"""Agent Nexus Foundation Layer: shared data models.

Every other module in the platform depends on the types defined here.
All models are pure data (Pydantic BaseModel or frozen dataclass) with no
business logic.

Usage:
    from agent_nexus.models import AgentManifest, TaskItem, SkillRecord
"""

# capability.py — Model capability registry
# agent.py — Agent system
from agent_nexus.models.agent import (
    AgentDefinition,
    AgentManifest,
    AgentModelConfig,
    AgentPackage,
    AgentRole,
    AgentType,
    CommandDef,
    HookDef,
    McpServerConfig,
    ModelTier,
    RunMode,
    SkillDefinition,
)
from agent_nexus.models.capability import (
    PROVIDER_DEFAULTS,
    ModelCapability,
    ModelCapabilityRegistry,
)

# composition.py — Shared composition data models
from agent_nexus.models.composition import (
    Composition,
    CompositionError,
    CompositionTask,
)

# config.py — Configuration
from agent_nexus.models.config import (
    ModelConfig,
    PlatformConfig,
    ProviderApiType,
    ProviderConfig,
    RuntimeConfig,
)

# context.py — Context budget & tiered loading
from agent_nexus.models.context import (
    ContextBudget,
    ContextBudgetLogEntry,
    ContextLevel,
    TokenUsage,
)

# distribution.py — Git-based distribution
from agent_nexus.models.distribution import (
    IndexEntry,
    InstallationStatus,
    Lockfile,
    LockfileEntry,
    PackageSource,
    SourceEntry,
    SourceType,
)

# errors.py — Platform-wide base exception
from agent_nexus.models.errors import AgentNexusError

# evolution.py — Self-Evolution Engine
from agent_nexus.models.evolution import (
    EvolutionContext,
    EvolutionMetrics,
    EvolutionType,
    SkillLineage,
    SkillOrigin,
    SkillRecord,
)

# hooks.py — Lifecycle hooks
from agent_nexus.models.hooks import (
    AggregatedHookResult,
    HookDefinition,
    HookEvent,
    HookExecution,
    HookType,
)

# ipc.py — Inter-process communication
from agent_nexus.models.ipc import (
    AgentToPlatform,
    AgentToPlatformType,
    IPCMessage,
    MessageDirection,
    PlatformToAgent,
    PlatformToAgentType,
)

# permission.py — Permission system
from agent_nexus.models.permission import (
    PathAccess,
    PathRule,
    PermissionConfig,
    PermissionDecision,
    PermissionMode,
)

# runtime.py — Python Runtime
from agent_nexus.models.runtime import (
    ExecutionResult,
    Function,
    RuntimeType,
    SecurityViolation,
    Variable,
)

# task.py — Task graph
from agent_nexus.models.task import (
    TaskGraphSnapshot,
    TaskItem,
    TaskState,
)

__all__ = [
    # capability.py
    "PROVIDER_DEFAULTS",
    # agent.py
    "AgentDefinition",
    "AgentManifest",
    "AgentModelConfig",
    "AgentNexusError",
    "AgentPackage",
    "AgentRole",
    # ipc.py
    "AgentToPlatform",
    "AgentToPlatformType",
    "AgentType",
    # hooks.py
    "AggregatedHookResult",
    "CommandDef",
    # composition.py
    "Composition",
    "CompositionError",
    "CompositionTask",
    # context.py
    "ContextBudget",
    "ContextBudgetLogEntry",
    "ContextLevel",
    # evolution.py
    "EvolutionContext",
    "EvolutionMetrics",
    "EvolutionType",
    # runtime.py
    "ExecutionResult",
    "Function",
    "HookDef",
    "HookDefinition",
    "HookEvent",
    "HookExecution",
    "HookType",
    "IPCMessage",
    # distribution.py
    "IndexEntry",
    "InstallationStatus",
    "Lockfile",
    "LockfileEntry",
    "McpServerConfig",
    "MessageDirection",
    "ModelCapability",
    "ModelCapabilityRegistry",
    # config.py
    "ModelConfig",
    "ModelTier",
    "PackageSource",
    # permission.py
    "PathAccess",
    "PathRule",
    "PermissionConfig",
    "PermissionDecision",
    "PermissionMode",
    "PlatformConfig",
    "PlatformToAgent",
    "PlatformToAgentType",
    "ProviderApiType",
    "ProviderConfig",
    "RunMode",
    "RuntimeConfig",
    "RuntimeType",
    "SecurityViolation",
    "SkillDefinition",
    "SkillLineage",
    "SkillOrigin",
    "SkillRecord",
    "SourceEntry",
    "SourceType",
    # task.py
    "TaskGraphSnapshot",
    "TaskItem",
    "TaskState",
    "TokenUsage",
    "Variable",
]
