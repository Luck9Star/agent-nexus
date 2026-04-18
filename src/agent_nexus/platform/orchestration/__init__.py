"""Orchestration layer: TaskGraph, IPC, ProcessManager, OrchestrationDSL."""

from agent_nexus.platform.orchestration.task_graph import TaskGraph

try:
    from agent_nexus.platform.orchestration.ipc import (
        IPCConnectionError,
        IPCError,
        IPCProtocol,
        IPCStream,
        IPCTimeoutError,
    )
except ImportError:
    IPCConnectionError = None  # type: ignore[assignment,misc]
    IPCError = None  # type: ignore[assignment,misc]
    IPCProtocol = None  # type: ignore[assignment,misc]
    IPCStream = None  # type: ignore[assignment,misc]
    IPCTimeoutError = None  # type: ignore[assignment,misc]

try:
    from agent_nexus.platform.orchestration.process_manager import (
        AgentHandle,
        ProcessManager,
    )
except ImportError:
    AgentHandle = None  # type: ignore[assignment,misc]
    ProcessManager = None  # type: ignore[assignment,misc]

try:
    from agent_nexus.platform.orchestration.dsl import (
        DSLAgent,
        DSLError,
        DSLSyntaxError,
        DSLTask,
        DSLValidationError,
        OrchestrationDSL,
        OrchestrationDefinition,
    )
except ImportError:
    DSLAgent = None  # type: ignore[assignment,misc]
    DSLError = None  # type: ignore[assignment,misc]
    DSLSyntaxError = None  # type: ignore[assignment,misc]
    DSLTask = None  # type: ignore[assignment,misc]
    DSLValidationError = None  # type: ignore[assignment,misc]
    OrchestrationDSL = None  # type: ignore[assignment,misc]
    OrchestrationDefinition = None  # type: ignore[assignment,misc]

__all__ = [
    "AgentHandle",
    "DSLAgent",
    "DSLError",
    "DSLSyntaxError",
    "DSLTask",
    "DSLValidationError",
    "IPCConnectionError",
    "IPCError",
    "IPCProtocol",
    "IPCStream",
    "IPCTimeoutError",
    "OrchestrationDSL",
    "OrchestrationDefinition",
    "ProcessManager",
    "TaskGraph",
]
