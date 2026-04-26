"""Agent Nexus Foundation Layer: shared Pydantic data models.

Every other module in the platform depends on the types defined here.
All models are pure data (Pydantic BaseModel) with no business logic.

Usage:
    from agent_nexus.models import AgentManifest, TaskItem, SkillRecord
"""

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
    # agent.py
    "AgentDefinition",
    "AgentManifest",
    "AgentModelConfig",
    "AgentPackage",
    "AgentRole",
    "AgentType",
    "CommandDef",
    "HookDef",
    "McpServerConfig",
    "ModelTier",
    "RunMode",
    "SkillDefinition",
    # task.py
    "TaskGraphSnapshot",
    "TaskItem",
    "TaskState",
    # ipc.py
    "AgentToPlatform",
    "AgentToPlatformType",
    "IPCMessage",
    "MessageDirection",
    "PlatformToAgent",
    "PlatformToAgentType",
    # runtime.py
    "ExecutionResult",
    "Function",
    "RuntimeType",
    "SecurityViolation",
    "Variable",
    # evolution.py
    "EvolutionContext",
    "EvolutionMetrics",
    "EvolutionType",
    "SkillLineage",
    "SkillOrigin",
    "SkillRecord",
    # config.py
    "ModelConfig",
    "PlatformConfig",
    "ProviderApiType",
    "ProviderConfig",
    "RuntimeConfig",
    # distribution.py
    "IndexEntry",
    "InstallationStatus",
    "Lockfile",
    "LockfileEntry",
    "PackageSource",
    "SourceEntry",
    "SourceType",
    # permission.py
    "PathAccess",
    "PathRule",
    "PermissionConfig",
    "PermissionDecision",
    "PermissionMode",
    # context.py
    "ContextBudget",
    "ContextBudgetLogEntry",
    "ContextLevel",
    "TokenUsage",
    # hooks.py
    "AggregatedHookResult",
    "HookDefinition",
    "HookEvent",
    "HookExecution",
    "HookType",
    # composition.py
    "Composition",
    "CompositionError",
    "CompositionTask",
]
