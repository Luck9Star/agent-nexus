"""DeferredAgentRegistry — agent-level deferred loading.

Core agents are always loaded and started.  Deferred agents have their
manifest registered but tools are not activated (and subprocesses not
started) until explicitly requested.  This dramatically reduces the
token budget consumed by tool schemas injected into LLM context.

Activation flow:
1. ``register_agent(manifest, deferred=True)``
2. On tool call to ``mcp__agent__tool`` -> ``activate_agent(agent_name)``
3. ``activate_agent``: start subprocess, discover tools, cache schemas

Reference: docs/06-mcp-communication.md Section 8.8
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

from agent_nexus.models.agent import AgentManifest
from agent_nexus.platform.orchestration.process_manager import (
    AgentHandle,
    ProcessManager,
)
from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# AgentInfo — lightweight per-agent metadata
# ---------------------------------------------------------------------------


@dataclass
class AgentInfo:
    """Lightweight agent metadata for the registry."""

    name: str
    manifest: AgentManifest
    tool_schemas: list[dict] | None = None  # None = not yet activated
    handle: AgentHandle | None = None  # None = not started
    start_command: list[str] = field(default_factory=list)
    start_cwd: str | None = None
    start_env: dict[str, str] = field(default_factory=dict)

    @property
    def is_activated(self) -> bool:
        """Whether tool schemas have been discovered."""
        return self.tool_schemas is not None

    @property
    def is_running(self) -> bool:
        """Whether the agent subprocess is alive."""
        return self.handle is not None and self.handle.is_alive


# ---------------------------------------------------------------------------
# DeferredAgentRegistry
# ---------------------------------------------------------------------------


class DeferredAgentRegistry:
    """Agent-level deferred loading with three tiers.

    Tiers:
    - **Core agents**: always loaded, tools always available
    - **Activated deferred agents**: manifest was loaded, user requested
      activation -> subprocess started, tools discovered
    - **Dormant deferred agents**: manifest only, no subprocess, no tools

    The registry interacts with ProcessManager for subprocess lifecycle
    (start/stop) and with agents via IPC for tool discovery.
    """

    def __init__(self, process_manager: ProcessManager) -> None:
        self._pm = process_manager
        self._core_agents: dict[str, AgentInfo] = {}
        self._deferred_agents: dict[str, AgentInfo] = {}
        self._tool_adapters: dict[str, list[McpToolAdapter]] = {}
        self._lock: asyncio.Lock | None = None

    def _get_lock(self) -> asyncio.Lock:
        """Return the activation lock, creating it lazily.

        Creating ``asyncio.Lock()`` in ``__init__`` can raise
        ``RuntimeError: no current event loop`` if the registry is
        instantiated outside an async context (e.g. during CLI setup).
        """
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_agent(
        self,
        manifest: AgentManifest,
        *,
        deferred: bool = True,
        start_command: list[str] | None = None,
        start_cwd: str | None = None,
        start_env: dict[str, str] | None = None,
    ) -> None:
        """Register an agent with the registry.

        If ``deferred=False``, the agent is placed in the core set and
        will be auto-started when the gateway starts.  Deferred agents
        are loaded lazily on first access.

        Args:
            manifest: Agent metadata (from agent-manifest.yaml).
            deferred: Whether to defer activation (default True).
            start_command: Command to start the agent subprocess.
            start_cwd: Working directory for the subprocess.
            start_env: Extra environment variables for the subprocess.
        """
        info = AgentInfo(
            name=manifest.name,
            manifest=manifest,
            start_command=start_command or [],
            start_cwd=start_cwd,
            start_env=start_env or {},
        )

        if deferred:
            # Guard: if name exists in the opposite tier, warn and remove
            # the stale entry to prevent get_agent_info returning the
            # wrong one.
            if manifest.name in self._core_agents:
                logger.warning(
                    "Agent '%s' already registered as core, "
                    "re-registering as deferred",
                    manifest.name,
                )
                del self._core_agents[manifest.name]
            self._deferred_agents[manifest.name] = info
            logger.info("Registered deferred agent: %s", manifest.name)
        else:
            if manifest.name in self._deferred_agents:
                logger.warning(
                    "Agent '%s' already registered as deferred, "
                    "re-registering as core",
                    manifest.name,
                )
                del self._deferred_agents[manifest.name]
            self._core_agents[manifest.name] = info
            logger.info("Registered core agent: %s", manifest.name)

    # ------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------

    async def activate_agent(self, name: str) -> list[dict]:
        """Activate a deferred agent: start subprocess, discover tools.

        Steps:
        1. Look up agent info in the deferred registry.
        2. Start subprocess via ProcessManager (if not already running).
        3. Send ``__list_tools__`` via IPC to discover available tools.
        4. Cache the tool schemas and create McpToolAdapter instances.

        The entire activation sequence is guarded by an asyncio lock to
        prevent a race where two concurrent ``search_and_activate`` calls
        for the same agent both pass the ``is_activated`` check and
        attempt to start the subprocess twice (leaking one handle).

        Args:
            name: Agent name to activate.

        Returns:
            List of MCP-compatible tool schema dicts.

        Raises:
            KeyError: Agent not registered.
            RuntimeError: Agent subprocess failed to start.
        """
        async with self._get_lock():
            # Check if already activated (could be core or previously activated)
            if name in self._core_agents:
                info = self._core_agents[name]
                if info.tool_schemas is not None:
                    return info.tool_schemas

            if name in self._deferred_agents:
                info = self._deferred_agents[name]
            elif name in self._core_agents:
                info = self._core_agents[name]
            else:
                raise KeyError(f"Agent '{name}' not registered")

            # Already activated?
            if info.is_activated:
                assert info.tool_schemas is not None
                return info.tool_schemas

            # 1. Start subprocess if not running
            if not info.is_running and info.start_command:
                from pathlib import Path

                cwd = Path(info.start_cwd) if info.start_cwd else None
                handle = await self._pm.start_agent(
                    name=name,
                    command=info.start_command,
                    cwd=cwd,
                    env=info.start_env or None,
                )
                info.handle = handle
                logger.info("Started subprocess for agent '%s'", name)

            # 2. Discover tools via IPC
            if info.handle is not None and info.handle.is_alive:
                tool_schemas = await self._fetch_agent_tools(info)
            else:
                # No running subprocess -> provide manifest-level placeholder
                tool_schemas = [
                    {
                        "name": f"{name}__chat",
                        "description": info.manifest.description,
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "message": {
                                    "type": "string",
                                    "description": "Message to send to the agent",
                                }
                            },
                            "required": ["message"],
                        },
                    }
                ]

            # 3. Cache
            info.tool_schemas = tool_schemas
            adapters = [
                McpToolAdapter(server_name=name, tool_schema=s)
                for s in tool_schemas
            ]
            self._tool_adapters[name] = adapters

            logger.info(
                "Activated agent '%s' with %d tools", name, len(tool_schemas)
            )
            return tool_schemas

    async def _fetch_agent_tools(self, info: AgentInfo) -> list[dict]:
        """Fetch tool schemas from a running agent via IPC.

        Sends ``__list_tools__`` and expects a JSON list of tool
        definitions in the response ``output`` field.
        """
        handle = info.handle
        if handle is None:
            return []

        try:
            await handle.ipc.send_chat(
                "__list_tools__", conversation_id="__internal__"
            )
            response = await handle.ipc.receive_until_result(timeout=10.0)

            if response.output and isinstance(response.output, list):
                return response.output

            # Fallback: parse content as JSON list
            import json

            if response.content:
                try:
                    parsed = json.loads(response.content)
                    if isinstance(parsed, list):
                        return parsed
                except json.JSONDecodeError:
                    pass

        except Exception as exc:
            logger.warning(
                "Failed to fetch tools from agent '%s': %s", info.name, exc
            )

        # Return a single chat tool as fallback
        return [
            {
                "name": "chat",
                "description": info.manifest.description,
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "message": {
                            "type": "string",
                            "description": "Message to send to the agent",
                        }
                    },
                    "required": ["message"],
                },
            }
        ]

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_tools_for_llm(self) -> list[dict]:
        """Get all available tool schemas (core + activated deferred).

        Deduplicates by tool name so core agents whose tools are also
        registered via McpToolAdapter are not included twice.

        Returns:
            Flat list of MCP-compatible tool definition dicts.
        """
        tools: list[dict] = []
        seen: set[str] = set()

        # Core agents: always included
        for info in self._core_agents.values():
            if info.tool_schemas:
                for schema in info.tool_schemas:
                    name = schema.get("name", "")
                    if name not in seen:
                        tools.append(schema)
                        seen.add(name)
            elif info.is_running:
                # Core agent is running but tools not yet discovered
                # (will be discovered on first gateway startup)
                pass

        # Activated deferred agents (skip duplicates)
        for _name, adapters in self._tool_adapters.items():
            for adapter in adapters:
                if adapter.full_name not in seen:
                    tools.append(adapter.get_tool_definition())
                    seen.add(adapter.full_name)

        return tools

    def get_tool_adapter(self, full_name: str) -> McpToolAdapter | None:
        """Look up a tool adapter by its full name.

        Args:
            full_name: Fully qualified tool name (``mcp__server__tool``).

        Returns:
            The matching adapter, or None.
        """
        for adapters in self._tool_adapters.values():
            for adapter in adapters:
                if adapter.full_name == full_name:
                    return adapter
        return None

    def get_tool_adapters(self, agent_name: str) -> list[McpToolAdapter]:
        """Return all tool adapters registered for a given agent.

        Args:
            agent_name: Name of the agent whose adapters to retrieve.

        Returns:
            List of McpToolAdapter instances (empty if agent has none).
        """
        return self._tool_adapters.get(agent_name, [])

    def get_agent_info(self, name: str) -> AgentInfo | None:
        """Look up agent info by name across all tiers.

        When an agent exists in both core and deferred dicts, returns
        the activated entry (the one with runtime state).  A dormant
        deferred entry without tool_schemas/handle is not preferred
        over a functional core entry.
        """
        deferred = self._deferred_agents.get(name)
        core = self._core_agents.get(name)
        if deferred is not None and core is not None:
            if deferred.is_activated:
                return deferred
            return core
        if deferred is not None:
            return deferred
        return core

    def list_all_agents(self) -> list[AgentInfo]:
        """Return info for all registered agents (core + deferred)."""
        return list(self._core_agents.values()) + list(
            self._deferred_agents.values()
        )

    def list_core_agents(self) -> list[AgentInfo]:
        """Return info for core agents only."""
        return list(self._core_agents.values())

    def list_deferred_agents(self) -> list[AgentInfo]:
        """Return info for deferred agents only."""
        return list(self._deferred_agents.values())

    # ------------------------------------------------------------------
    # Manifest / search
    # ------------------------------------------------------------------

    def build_manifest(self) -> str:
        """Build a text summary of all registered agents for LLM context.

        Core agents and activated deferred agents show their tool count.
        Dormant deferred agents show as "available".

        Returns:
            Multi-line text suitable for system prompt injection.
        """
        lines: list[str] = []

        # Core agents
        for info in self._core_agents.values():
            tool_count = len(info.tool_schemas) if info.tool_schemas else 0
            desc = info.manifest.description.split("\n")[0][:80]
            lines.append(
                f"- {info.name}: {desc} [core, {tool_count} tools]"
            )

        # Deferred agents
        for info in self._deferred_agents.values():
            desc = info.manifest.description.split("\n")[0][:80]
            if info.is_activated:
                tool_count = len(info.tool_schemas) if info.tool_schemas else 0
                lines.append(
                    f"- {info.name}: {desc} [activated, {tool_count} tools]"
                )
            else:
                lines.append(
                    f"- {info.name}: {desc} [available]"
                )

        return "\n".join(lines)

    def search_agents(
        self, query: str, max_results: int = 5
    ) -> list[AgentManifest]:
        """Simple keyword search over agent names and descriptions.

        Searches across all registered agents (core + deferred).
        Scoring: each query word that appears in the agent's name,
        description, or capabilities contributes 1 point.

        Args:
            query: Search query string.
            max_results: Maximum number of results.

        Returns:
            Matching agent manifests, best matches first.
        """
        query_words = query.lower().split()
        scored: list[tuple[int, AgentManifest]] = []

        all_agents: dict[str, AgentManifest] = {}
        for info in self._core_agents.values():
            all_agents[info.name] = info.manifest
        for info in self._deferred_agents.values():
            all_agents[info.name] = info.manifest

        for name, manifest in all_agents.items():
            search_text = (
                f"{name} {manifest.description}".lower()
            )
            score = sum(1 for w in query_words if w in search_text)
            if score > 0:
                scored.append((score, manifest))

        scored.sort(key=lambda x: -x[0])
        return [m for _, m in scored[:max_results]]
