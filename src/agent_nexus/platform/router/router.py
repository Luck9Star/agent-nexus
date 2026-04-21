"""PlatformRouter -- 4-Phase composite agent workflow orchestration.

Phases:
1. Research:    parallel workers gather information
2. Synthesis:   coordinator alone analyzes and plans
3. Implementation: parallel workers execute the plan
4. Verification:   fresh worker verifies results

Uses:
- TaskGraph for dependency tracking per workflow
- ProcessManager for agent subprocess lifecycle
- SubtaskController for individual task execution (timeout/retry/parallel)
- OrchestrationDSL definitions for task structure

Reference: docs/06-mcp-communication.md Section 8.4
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from collections import deque
from typing import Any

from agent_nexus.models.ipc import AgentToPlatformType
from agent_nexus.platform.gateway.tool_adapter import (
    DEFAULT_IPC_EXECUTE_TIMEOUT,
)
from agent_nexus.platform.utils import make_error_result as _make_error_result
from agent_nexus.platform.orchestration.dsl import OrchestrationDefinition
from agent_nexus.platform.orchestration.ipc import (
    IPCConnectionError,
    IPCError,
    IPCTimeoutError,
    _INTERNAL_CID,
    _LIST_TOOLS_MSG,
    get_ipc_lock,
)
from agent_nexus.platform.orchestration.process_manager import ProcessManager
from agent_nexus.platform.orchestration.task_graph import TaskGraph

from .subtask import SubtaskController
from .workflow import WorkflowContext, WorkflowPhase, WorkflowResult

class AgentExecutionError(Exception):
    """Raised when an agent interaction fails with a non-IPC error.

    Carries ``error`` (human-readable message) and ``error_type`` (exception
    class name) so callers can reconstruct error dicts for aggregation.
    """

    def __init__(self, error: str, error_type: str = "AgentError") -> None:
        self.error = error
        self.error_type = error_type
        super().__init__(error)


logger = logging.getLogger(__name__)

# Default phases in execution order
_PHASE_ORDER: list[WorkflowPhase] = [
    WorkflowPhase.research,
    WorkflowPhase.synthesis,
    WorkflowPhase.implementation,
    WorkflowPhase.verification,
]

# Overall timeout for the entire composite workflow.  Each phase can
# take up to DEFAULT_IPC_EXECUTE_TIMEOUT per IPC call, so the ceiling
# is phases x timeout.  This prevents unbounded hangs from retries or
# stuck agents.
_DEFAULT_COMPOSITE_TIMEOUT: float = DEFAULT_IPC_EXECUTE_TIMEOUT * len(_PHASE_ORDER)

_PHASE_ROLE_MAP: dict[WorkflowPhase, str] = {
    WorkflowPhase.research: "explore",
    WorkflowPhase.synthesis: "plan",
    WorkflowPhase.implementation: "worker",
    WorkflowPhase.verification: "verification",
}

_PHASE_MESSAGE_TEMPLATES: dict[WorkflowPhase, tuple[str, str]] = {
    WorkflowPhase.research: (
        "Research Results",
        "Based on the above research, create an implementation plan.",
    ),
    WorkflowPhase.synthesis: (
        "Implementation Plan",
        "Execute the above plan.",
    ),
    WorkflowPhase.implementation: (
        "Implementation Output",
        "Verify the above implementation is correct and complete.",
    ),
}


class PlatformRouter:
    """Orchestrate composite agent workflows using 4-Phase pattern.

    For composite agents: runs the full 4-phase workflow.
    For atomic agents: delegates directly via IPC.
    """

    def __init__(
        self,
        process_manager: ProcessManager,
        subtask_controller: SubtaskController | None = None,
        composite_definitions: dict[str, OrchestrationDefinition] | None = None,
    ) -> None:
        self._pm = process_manager
        self._subtask = subtask_controller or SubtaskController()
        self._composite_defs: dict[str, OrchestrationDefinition] = (
            composite_definitions or {}
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_composite(
        self, name: str, definition: OrchestrationDefinition,
    ) -> None:
        """Register a composite agent definition for routing.

        Args:
            name: Agent name to match in ``route_chat``.
            definition: Parsed TOML orchestration definition.
        """
        self._composite_defs[name] = definition

    async def route_chat(
        self,
        agent_name: str,
        message: str,
        conversation_id: str | None = None,
    ) -> dict:
        """Route a chat message to an agent.

        For composite agents: delegates to route_composite.
        For atomic agents: delegates to route_to_atomic.

        Args:
            agent_name: Name of the target agent.
            message: User message content.
            conversation_id: Optional conversation ID (generated if absent).

        Returns:
            Dict with ``output`` and ``success`` keys.
        """
        conv_id = conversation_id or str(uuid.uuid4())

        if not agent_name or not agent_name.strip():
            return _make_error_result("agent_name is required", "ValueError")
        if not message or not message.strip():
            return _make_error_result("message is required", "ValueError")

        # Check if this is a composite agent with an orchestration definition
        definition = self._composite_defs.get(agent_name)
        if definition is not None:
            result = await self.route_composite(definition, message, conv_id)
            result_dict: dict[str, Any] = {
                "output": result.final_output,
                "success": result.success,
            }
            if not result.success:
                if result.error:
                    result_dict["error"] = result.error
                if result.error_type:
                    result_dict["error_type"] = result.error_type
            return result_dict

        return await self.route_to_atomic(agent_name, message, conv_id)

    async def route_composite(
        self,
        definition: OrchestrationDefinition,
        message: str,
        conversation_id: str,
    ) -> WorkflowResult:
        """Execute a composite agent workflow using 4-phase pattern.

        Steps:
        1. Create WorkflowContext with a fresh in-memory TaskGraph.
        2. Populate TaskGraph from the OrchestrationDefinition's tasks.
        3. Execute each phase in order, collecting results.
        4. Return WorkflowResult with aggregated outputs.

        Args:
            definition: Parsed TOML orchestration definition.
            message: User message / goal for the workflow.
            conversation_id: Conversation identifier.

        Returns:
            WorkflowResult summarizing the workflow outcome.
        """
        # 1. Create fresh context per workflow -- no cross-workflow state leakage
        ctx = WorkflowContext(
            conversation_id=conversation_id,
            message=message,
            agent_name=definition.agent_name,
        )

        # Initialize result variables before TaskGraph setup so they are
        # always defined even if add_task() raises during population.
        phase_results: dict[WorkflowPhase, str] = {}
        completed = 0
        total = len(_PHASE_ORDER)
        last_error: str | None = None
        last_error_type: str | None = None

        # 2. Create in-memory TaskGraph and populate from definition.
        #    Tasks must be added in dependency order (deps before dependents)
        #    so that add_task can validate blocked_by references.  The DSL
        #    validate() method checks that all blocked_by IDs exist in the
        #    task set but does NOT enforce topological ordering of the list.
        ctx.task_graph = TaskGraph(":memory:")
        try:
            sorted_tasks = self._topological_sort_tasks(definition.tasks)
            ctx.task_graph.add_tasks([dsl_task.to_task_item() for dsl_task in sorted_tasks])
        except (ValueError, KeyError, TypeError, RuntimeError, sqlite3.Error) as exc:
            last_error = f"TaskGraph setup failed: {exc}"
            last_error_type = type(exc).__name__
            logger.error(last_error, exc_info=exc)
            try:
                ctx.close()
            except Exception:
                logger.debug("Failed to close context after TaskGraph error", exc_info=True)
            return WorkflowResult(
                success=False,
                final_output="",
                phase_results=phase_results,
                total_phases=total,
                completed_phases=completed,
                error=last_error,
                error_type=type(exc).__name__,
            )

        # Pre-compute role→agent mapping once (deterministic across phases)
        role_agents: dict[str, list[str]] = {}
        for name, agent_def in definition.agents.items():
            role_agents.setdefault(agent_def.role, []).append(name)

        # 3. Execute phases
        try:
            async def _run_phases() -> None:
                nonlocal message, completed, last_error, last_error_type
                for phase in _PHASE_ORDER:
                    ctx.current_phase = phase
                    try:
                        result = await self._execute_phase(ctx, phase, definition, message, role_agents=role_agents)
                        phase_results[phase] = result
                        completed += 1

                        # Feed previous phase output into next phase's message
                        message = self._build_phase_message(phase, result)

                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        last_error = f"Phase {phase.value} failed: {exc}"
                        last_error_type = type(exc).__name__
                        logger.error(last_error, exc_info=exc)
                        break

            try:
                await asyncio.wait_for(
                    _run_phases(),
                    timeout=_DEFAULT_COMPOSITE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                last_error = (
                    f"Composite workflow timed out after "
                    f"{_DEFAULT_COMPOSITE_TIMEOUT:.0f}s"
                )
                last_error_type = "TimeoutError"
                logger.error(last_error)
        finally:
            try:
                ctx.close()
            except Exception:
                logger.debug("Failed to close context after composite workflow", exc_info=True)

        # 4. Build final result
        success = completed == total
        final_output = phase_results.get(WorkflowPhase.verification, "")
        if not success and WorkflowPhase.synthesis in phase_results:
            # Use synthesis output as partial result if available
            final_output = phase_results.get(WorkflowPhase.synthesis, "")

        return WorkflowResult(
            success=success,
            final_output=final_output,
            phase_results=phase_results,
            total_phases=total,
            completed_phases=completed,
            error=last_error,
            error_type=last_error_type,
        )

    async def route_to_atomic(
        self,
        atomic_name: str,
        message: str,
        conversation_id: str,
    ) -> dict:
        """Send message directly to an atomic agent subprocess via IPC.

        Args:
            atomic_name: Name of the atomic agent.
            message: Chat message content.
            conversation_id: Conversation identifier.

        Returns:
            Dict with ``output``, ``success``, and optionally ``error`` and
            ``error_type`` keys.  Agent-not-found and process-dead conditions
            are returned as error dicts rather than raised exceptions.
        """
        handle = self._pm.get_agent(atomic_name)
        if handle is None:
            return _make_error_result(f"Agent '{atomic_name}' not found", "KeyError")

        if not handle.is_alive:
            return _make_error_result(f"Agent '{atomic_name}' process is not alive", "ProcessNotAliveError")

        # Serialize send+receive per agent to prevent concurrent IPC
        # calls from interleaving responses on the same handle.
        lock = get_ipc_lock(atomic_name)
        async with lock:
            # Recheck liveness after acquiring lock -- agent may have died
            # during lock wait.  Without this check we waste the full IPC
            # timeout on a dead process.
            if not handle.is_alive:
                return _make_error_result(
                    f"Agent '{atomic_name}' process died while waiting for lock",
                    "ProcessNotAliveError",
                )

            # Send chat message via IPC
            try:
                await handle.ipc.send_chat(
                    message, conversation_id=conversation_id
                )
            except (IPCError, OSError, RuntimeError, asyncio.TimeoutError) as exc:
                logger.warning("IPC send error for agent '%s': %s", atomic_name, exc)
                return _make_error_result(f"IPC send error: {exc}", type(exc).__name__)

            # Wait for final result (progress messages are silently consumed)
            try:
                response = await handle.ipc.receive_until_result(timeout=DEFAULT_IPC_EXECUTE_TIMEOUT)
            except (IPCError, OSError, RuntimeError, asyncio.TimeoutError) as exc:
                logger.warning("IPC receive error for agent '%s': %s", atomic_name, exc)
                return _make_error_result(f"IPC error: {exc}", type(exc).__name__)

            # Parse response
            if response.type == AgentToPlatformType.ERROR:
                return _make_error_result(response.error or "Agent returned an error", "AgentError")

            return {
                "output": response.content or "",
                # Default to success when status is unset — minimal agent
                # implementations may omit the status field.
                "success": response.is_success,
            }

    async def get_tools(self) -> list[dict]:
        """Get aggregated tool schemas from the gateway registry.

        Delegates to the DeferredAgentRegistry (the canonical tool source)
        rather than independently querying agents via IPC.  This avoids
        returning raw tool names that don't match the sanitized
        ``mcp__server__tool`` format used by the gateway.

        Falls back to querying running agents via IPC only when no
        registry is available (backward-compatibility).

        Returns:
            Flat list of tool definition dicts with sanitized names.
        """
        # Use the registry as the canonical source when available.
        # The router does not own a registry directly; it accesses it
        # through the gateway if one has been configured.
        registry = getattr(self, "_registry", None)
        if registry is not None:
            return registry.get_tools_for_llm()

        # Fallback: query running agents directly via IPC (legacy path)
        async def _fetch_tools_from_agent(name: str) -> list[dict]:
            handle = self._pm.get_agent(name)
            if handle is None or not handle.is_alive:
                return []
            try:
                async with get_ipc_lock(name):
                    await handle.ipc.send_chat(
                        _LIST_TOOLS_MSG, conversation_id=_INTERNAL_CID
                    )
                    response = await handle.ipc.receive_until_result(timeout=10.0)
                if response.type == AgentToPlatformType.ERROR:
                    logger.warning(
                        "Agent '%s' returned error during tool discovery: %s",
                        name, response.error or "unknown error",
                    )
                    return []
                if response.content:
                    content = response.content
                    if isinstance(content, str):
                        try:
                            parsed = json.loads(content)
                            if isinstance(parsed, list):
                                return parsed
                        except (json.JSONDecodeError, ValueError) as exc:
                            logger.warning(
                                "Agent '%s' returned invalid JSON tool definitions: %s",
                                name, exc,
                            )
                return []
            except (IPCError, OSError, RuntimeError, asyncio.TimeoutError) as exc:
                logger.warning("Failed to get tools from agent '%s': %s", name, exc)
                return []

        tools: list[dict] = []
        seen_names: set[str] = set()
        all_agent_tools = await asyncio.gather(
            *[_fetch_tools_from_agent(name) for name in self._pm.list_running()],
        )
        for tool_list in all_agent_tools:
            for tool in tool_list:
                tool_name = tool.get("name", "")
                if not tool_name:
                    logger.warning("Tool from agent has no 'name' key, skipping")
                    continue
                if tool_name in seen_names:
                    logger.warning(
                        "Tool name collision: '%s' already registered, skipping",
                        tool_name,
                    )
                    continue
                seen_names.add(tool_name)
                tools.append(tool)

        return tools

    async def stop_all(self) -> None:
        """Stop all agents managed by this router."""
        await self._pm.stop_all()

    # ------------------------------------------------------------------
    # Phase execution
    # ------------------------------------------------------------------

    async def _execute_phase(
        self,
        ctx: WorkflowContext,
        phase: WorkflowPhase,
        definition: OrchestrationDefinition,
        message: str,
        *,
        role_agents: dict[str, list[str]] | None = None,
    ) -> str:
        """Execute a single workflow phase.

        Maps DSL agent roles to phases:
        - research: agents with role='explore'
        - synthesis: agents with role='plan'
        - implementation: agents with role='worker'
        - verification: agents with role='verification'

        For research and implementation: run assigned agents in parallel.
        For synthesis and verification: run single agent.

        Returns:
            Aggregated result string for the phase.  Raises ``RuntimeError``
            on agent failure (converted from error dict) so the caller
            (``route_composite``) catches and records the failure.

        Args:
            role_agents: Pre-computed role→agent-names mapping.  When provided
                (by ``route_composite``), the list comprehension is skipped.
                When ``None`` (legacy / tests), the map is computed on the fly.
        """
        tg = ctx.task_graph
        if tg is None:
            raise RuntimeError("TaskGraph not initialized in context")

        role = self._phase_to_role(phase)
        if role_agents is not None:
            phase_agents = list(role_agents.get(role, []))
        else:
            phase_agents = [
                name
                for name, agent_def in definition.agents.items()
                if agent_def.role == role
            ]

        if not phase_agents:
            # Fallback: if no agents have the matching role, use root tasks
            # for research and first available agent for other phases
            logger.warning(
                "No agents with role '%s' found for %s phase, "
                "falling back to default agent selection",
                role, phase.value,
            )
            if phase == WorkflowPhase.research:
                root_tasks = definition.get_root_tasks()
                phase_agents = list({t.agent for t in root_tasks})
            elif definition.agents:
                phase_agents = [next(iter(definition.agents.keys()))]

        if not phase_agents:
            raise RuntimeError(f"No agents available for {phase.value} phase")

        # Build message for this phase
        phase_message = message

        if phase in (WorkflowPhase.research, WorkflowPhase.implementation):
            # Parallel execution
            results = await self._execute_parallel_agents(
                phase_agents, phase_message, ctx.conversation_id
            )
            return self._aggregate_results(results, phase)

        else:
            # Single agent execution (synthesis, verification)
            agent_name = phase_agents[0]
            return await self._execute_single_agent(
                agent_name, phase_message, ctx.conversation_id
            )

    async def _execute_parallel_agents(
        self,
        agent_names: list[str],
        message: str,
        conversation_id: str,
    ) -> list[Any]:
        """Execute multiple agents in parallel via SubtaskController.

        Each agent gets a unique conversation_id to prevent IPC response
        interleaving when agents share the same process handle.
        """
        # Deduplicate: same agent name => same IPC handle => must not
        # run concurrently or responses interleave.
        seen: set[str] = set()
        unique_names: list[str] = []
        for n in agent_names:
            if n not in seen:
                seen.add(n)
                unique_names.append(n)

        async def _run_agent(name: str) -> str | dict:
            cid = f"{conversation_id}__{name}__{uuid.uuid4().hex[:8]}"
            try:
                return await self._subtask.run_with_retry(
                    coro_factory=lambda n=name, c=cid: self._execute_single_agent(
                        n, message, c
                    ),
                    timeout=DEFAULT_IPC_EXECUTE_TIMEOUT,
                )
            except AgentExecutionError as exc:
                # Convert to error dict for _aggregate_results.
                # Not re-raised so other parallel agents are not cancelled.
                return _make_error_result(exc.error, exc.error_type)

        coros = [_run_agent(name) for name in unique_names]
        return await self._subtask.run_parallel(coros)

    async def _execute_single_agent(
        self,
        agent_name: str,
        message: str,
        conversation_id: str,
    ) -> str:
        """Execute a single agent interaction via IPC.

        Sends the message, waits for a final result, returns the content.

        Returns:
            ``str`` on success (the response content).

        Raises:
            AgentExecutionError: agent-level failures (not found, not alive,
                agent error response, non-IPC send/receive errors).  Callers
                that need an error dict can catch this and convert.
            IPCConnectionError, IPCTimeoutError, IPCError: propagated
                directly so callers can distinguish infrastructure-level IPC
                failures from agent-level errors.
        """
        handle = self._pm.get_agent(agent_name)
        if handle is None or not handle.is_alive:
            raise AgentExecutionError(
                f"Agent '{agent_name}' not found or not alive",
                "ProcessNotAliveError",
            )

        # Serialize send+receive per agent to prevent concurrent IPC
        # calls from interleaving responses on the same handle.
        # Uses the same lock as route_to_atomic so both code paths
        # are mutually exclusive for a given agent.
        lock = get_ipc_lock(agent_name)
        async with lock:
            try:
                await handle.ipc.send_chat(message, conversation_id=conversation_id)
            except (IPCConnectionError, IPCTimeoutError, IPCError):
                raise
            except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
                raise AgentExecutionError(
                    f"IPC send error for agent '{agent_name}': {exc}",
                    type(exc).__name__,
                )

            try:
                response = await handle.ipc.receive_until_result(timeout=DEFAULT_IPC_EXECUTE_TIMEOUT)
            except (IPCConnectionError, IPCTimeoutError, IPCError):
                raise
            except (OSError, RuntimeError, asyncio.TimeoutError) as exc:
                raise AgentExecutionError(
                    f"IPC error communicating with agent '{agent_name}': {exc}",
                    type(exc).__name__,
                )

            if response.type == AgentToPlatformType.ERROR:
                raise AgentExecutionError(
                    f"Agent '{agent_name}' error: {response.error or 'unknown'}",
                    "AgentError",
                )

            return str(response.content) if response.content is not None else ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _phase_to_role(phase: WorkflowPhase) -> str:
        """Map WorkflowPhase to DSL agent role."""
        return _PHASE_ROLE_MAP.get(phase, "worker")

    @staticmethod
    def _build_phase_message(
        phase: WorkflowPhase,
        phase_result: str,
    ) -> str:
        """Build the message for the next phase based on completed results.

        Synthesis gets all research results.
        Implementation gets the synthesis plan.
        Verification gets implementation results.
        """
        template = _PHASE_MESSAGE_TEMPLATES.get(phase)
        if template:
            heading, instruction = template
            return f"## {heading}\n\n{phase_result}\n\n{instruction}"
        return phase_result

    @staticmethod
    def _topological_sort_tasks(tasks: list[Any]) -> list[Any]:
        """Sort tasks so that dependencies appear before dependents.

        DSL validation ensures all ``blocked_by`` references exist but does
        not guarantee list ordering.  TaskGraph.add_task validates that
        blocked_by targets are already present in the graph, so we must
        insert tasks in topological order.

        Uses Kahn's algorithm (BFS).  Tasks with no blocked_by are roots.
        Time complexity: O(V + E) using deque + reverse adjacency map.
        """
        task_map = {t.id: t for t in tasks}
        in_degree: dict[str, int] = {t.id: 0 for t in tasks}

        # Build reverse adjacency: dep_id -> list of task IDs that depend on it
        dependents: dict[str, list[str]] = {t.id: [] for t in tasks}
        for t in tasks:
            for dep_id in t.blocked_by:
                if dep_id in in_degree:
                    in_degree[t.id] += 1
                    dependents[dep_id].append(t.id)

        queue: deque[str] = deque(
            tid for tid, deg in in_degree.items() if deg == 0
        )
        sorted_tasks: list[Any] = []

        while queue:
            tid = queue.popleft()
            task = task_map.get(tid)
            if task is not None:
                sorted_tasks.append(task)
            for dependent_id in dependents[tid]:
                in_degree[dependent_id] -= 1
                if in_degree[dependent_id] == 0:
                    queue.append(dependent_id)

        # If cycle prevents full sort, append remaining tasks so the
        # caller still gets all of them (add_task will detect the cycle).
        if len(sorted_tasks) < len(tasks):
            sorted_ids = {t.id for t in sorted_tasks}
            for t in tasks:
                if t.id not in sorted_ids:
                    sorted_tasks.append(t)

        return sorted_tasks

    @staticmethod
    def _aggregate_results(results: list[Any], phase: WorkflowPhase) -> str:
        """Aggregate parallel results into a single string.

        Failed tasks (exceptions or error dicts) are reported but don't
        prevent successful results from being included.
        """
        parts: list[str] = []
        errors: list[str] = []

        for i, result in enumerate(results):
            if isinstance(result, dict) and not result.get("success", True):
                # Error dict returned by _execute_single_agent
                errors.append(f"Worker {i + 1} failed: {result.get('error', 'unknown error')}")
            elif isinstance(result, Exception):
                errors.append(f"Worker {i + 1} failed: {result}")
            elif result is None or (isinstance(result, str) and not result):
                parts.append(f"Worker {i + 1}: (no output)")
            else:
                parts.append(str(result))

        output = "\n\n---\n\n".join(parts)
        if errors:
            output += "\n\n## Warnings\n" + "\n".join(f"- {e}" for e in errors)

        return output if output else f"No results from {phase.value} phase"
