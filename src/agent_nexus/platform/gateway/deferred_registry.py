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
import itertools
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from agent_nexus.models.agent import AgentManifest
from agent_nexus.models.ipc import AgentToPlatformType
from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter
from agent_nexus.platform.orchestration.ipc import (
    _INTERNAL_CID,
    _LIST_TOOLS_MSG,
    get_ipc_lock,
)
from agent_nexus.platform.orchestration.process_manager import (
    AgentHandle,
    ProcessManager,
)

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
    _search_text: str = ""

    def __post_init__(self) -> None:
        self._search_text = f"{self.name} {self.manifest.description}".lower()
        self._search_words: set[str] = set(self._search_text.split())

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
        # Reverse index: full_name -> McpToolAdapter for O(1) lookup
        self._tool_by_name: dict[str, McpToolAdapter] = {}
        # Per-agent activation locks allow concurrent activation of different
        # agents.  The global lock only protects the _agent_locks dict itself
        # (very short hold time).
        self._agent_locks: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()

    async def _get_agent_lock(self, agent_id: str) -> asyncio.Lock:
        """Get or create a per-agent activation lock.

        Uses double-checked locking: check without the global lock first,
        then acquire it only if we need to create a new entry.  This keeps
        the global lock hold time to a single dict assignment.
        """
        if agent_id not in self._agent_locks:
            async with self._global_lock:
                if agent_id not in self._agent_locks:
                    self._agent_locks[agent_id] = asyncio.Lock()
        return self._agent_locks[agent_id]

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
                    "Agent '%s' already registered as core, re-registering as deferred",
                    manifest.name,
                )
                del self._core_agents[manifest.name]
            self._deferred_agents[manifest.name] = info
            logger.info("Registered deferred agent: %s", manifest.name)
        else:
            if manifest.name in self._deferred_agents:
                logger.warning(
                    "Agent '%s' already registered as deferred, re-registering as core",
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

        The entire activation sequence is guarded by an asyncio lock to
        prevent a race where two concurrent calls both pass the
        ``is_activated`` check and attempt to start the subprocess twice.

        Raises:
            KeyError: Agent not registered.
            RuntimeError: Agent subprocess failed to start.
        """
        async with await self._get_agent_lock(name):
            info = self._find_agent_info(name)
            if info is None:
                raise KeyError(f"Agent '{name}' not registered")
            if info.is_activated:
                if info.tool_schemas is None:
                    raise RuntimeError(f"Agent '{name}' is activated but has no tool schemas")
                return info.tool_schemas

            await self._ensure_subprocess(info, name)
            tool_schemas = await self._discover_or_fallback_tools(info, name)
            self._cache_tool_schemas(name, info, tool_schemas)
            return tool_schemas

    def _find_agent_info(self, name: str) -> AgentInfo | None:
        """Look up agent info, returning early if already cached."""
        if name in self._core_agents and self._core_agents[name].tool_schemas is not None:
            return self._core_agents[name]
        if name in self._deferred_agents:
            return self._deferred_agents[name]
        if name in self._core_agents:
            return self._core_agents[name]
        return None

    async def _ensure_subprocess(self, info: AgentInfo, name: str) -> None:
        """Start subprocess if not already running."""
        if info.is_running or not info.start_command:
            return
        cwd = Path(info.start_cwd) if info.start_cwd else None
        try:
            handle = await self._pm.start_agent(
                name=name,
                command=info.start_command,
                cwd=cwd,
                env=info.start_env or None,
            )
            info.handle = handle
            logger.info("Started subprocess for agent '%s'", name)
        except Exception:
            info.handle = None
            raise

    async def _discover_or_fallback_tools(self, info: AgentInfo, name: str) -> list[dict]:
        """Discover tools via IPC, or return placeholder if no subprocess."""
        if info.handle is not None and info.handle.is_alive:
            return await self._fetch_agent_tools(info)
        return [self._placeholder_chat_tool(name, info.manifest.description)]

    def _cache_tool_schemas(self, name: str, info: AgentInfo, tool_schemas: list[dict]) -> None:
        """Cache tool schemas and populate reverse index."""
        info.tool_schemas = tool_schemas
        adapters = [McpToolAdapter(server_name=name, tool_schema=s) for s in tool_schemas]
        self._tool_adapters[name] = adapters
        for adapter in adapters:
            self._tool_by_name[adapter.full_name] = adapter

        logger.info("Activated agent '%s' with %d tools", name, len(tool_schemas))
        if len(tool_schemas) == 1 and tool_schemas[0].get("name") in ("chat", f"{name}__chat"):
            logger.warning(
                "Agent '%s' activated with fallback chat tool only "
                "(tool='%s'). Tool discovery may have failed.",
                name,
                tool_schemas[0].get("name"),
            )

    @staticmethod
    def _placeholder_chat_tool(name: str, description: str) -> dict:
        """Build a manifest-level placeholder chat tool."""
        return {
            "name": f"{name}__chat",
            "description": description,
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

    async def _fetch_agent_tools(self, info: AgentInfo) -> list[dict]:
        """Fetch tool schemas from a running agent via IPC.

        Sends ``__list_tools__`` and expects a JSON list of tool
        definitions in the response ``output`` field.
        """
        handle = info.handle
        if handle is None:
            logger.warning("Cannot fetch tools for agent '%s': no active handle", info.name)
            return []

        ipc_lock = get_ipc_lock(info.name)
        try:
            async with ipc_lock:
                await handle.ipc.send_chat(_LIST_TOOLS_MSG, conversation_id=_INTERNAL_CID)
                response = await handle.ipc.receive_until_result(timeout=10.0)

            if response.type == AgentToPlatformType.ERROR:
                logger.warning(
                    "Agent '%s' returned ERROR response during tool "
                    "discovery: %s. Using generic chat tool as fallback.",
                    info.name,
                    response.error or "unknown error",
                )
                return [self._fallback_chat_tool(info)]

            if isinstance(response.output, list):
                return self._validate_tool_schemas(response.output)

            # Fallback: parse content as JSON list
            if response.content:
                try:
                    parsed = json.loads(response.content)
                    if isinstance(parsed, list):
                        return self._validate_tool_schemas(parsed)
                except json.JSONDecodeError:
                    logger.debug(
                        "Agent '%s' tool response not valid JSON: %.200s",
                        info.name,
                        response.content[:200],
                    )

        except Exception as exc:
            logger.warning(
                "Failed to fetch tools from agent '%s': %s. "
                "Using generic chat tool as fallback -- agent may not "
                "function correctly.",
                info.name,
                exc,
            )
            return [self._fallback_chat_tool(info)]

        # No tool schemas found in response
        logger.warning(
            "Agent '%s' returned no tool schemas. Using generic chat tool as fallback.",
            info.name,
        )
        return [self._fallback_chat_tool(info)]

    @staticmethod
    def _validate_tool_schemas(schemas: list[dict]) -> list[dict]:
        """Filter out malformed tool schema entries.

        Each entry must be a dict with at least a ``name`` key and an
        ``inputSchema`` key (or be skipped).  This prevents
        nonsensical entries (empty dicts, strings, None) from being
        registered as MCP tools.
        """
        valid: list[dict] = []
        for schema in schemas:
            if not isinstance(schema, dict):
                logger.warning(
                    "Skipping non-dict tool schema entry: %s",
                    type(schema).__name__,
                )
                continue
            name = schema.get("name")
            if not name or not isinstance(name, str):
                logger.warning("Skipping tool schema with missing/invalid name")
                continue
            if "inputSchema" not in schema:
                logger.warning(
                    "Tool schema '%s' missing inputSchema, injecting default",
                    name,
                )
                schema["inputSchema"] = {"type": "object", "properties": {}}
            valid.append(schema)
        return valid

    @staticmethod
    def _fallback_chat_tool(info: AgentInfo) -> dict:
        """Build a generic chat tool schema as last-resort fallback."""
        return {
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

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def remove_agent_tools(self, agent_name: str) -> None:
        """Remove all tool registrations for *agent_name*.

        Called when an agent is deregistered or its subprocess dies.
        Cleans up ``_tool_by_name``, ``_tool_adapters``, and both agent
        tier dicts so that subsequent lookups reflect the removal.

        Args:
            agent_name: Name of the agent whose tools to remove.
        """
        adapters = self._tool_adapters.get(agent_name, [])
        for adapter in adapters:
            self._tool_by_name.pop(adapter.full_name, None)
        self._tool_adapters.pop(agent_name, None)
        self._core_agents.pop(agent_name, None)
        self._deferred_agents.pop(agent_name, None)

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
        for _, adapters in self._tool_adapters.items():
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
        return self._tool_by_name.get(full_name)

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

        Priority: activated deferred > core > dormant deferred.
        An activated deferred agent (tools discovered, process running)
        is preferred over a core entry; otherwise core wins.
        """
        # Check deferred first — if activated, it takes priority.
        deferred_info = self._deferred_agents.get(name)
        if deferred_info is not None and deferred_info.is_activated:
            return deferred_info
        # Core agents always available.
        core_info = self._core_agents.get(name)
        if core_info is not None:
            return core_info
        # Dormant deferred (not activated).
        return deferred_info

    def list_all_agents(self) -> list[AgentInfo]:
        """Return info for all registered agents (core + deferred)."""
        return list(self._core_agents.values()) + list(self._deferred_agents.values())

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
            lines.append(f"- {info.name}: {desc} [core, {tool_count} tools]")

        # Deferred agents
        for info in self._deferred_agents.values():
            desc = info.manifest.description.split("\n")[0][:80]
            if info.is_activated:
                tool_count = len(info.tool_schemas) if info.tool_schemas else 0
                lines.append(f"- {info.name}: {desc} [activated, {tool_count} tools]")
            else:
                lines.append(f"- {info.name}: {desc} [available]")

        return "\n".join(lines)

    def search_agents(self, query: str, max_results: int = 5) -> list[AgentManifest]:
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

        for info in itertools.chain(self._core_agents.values(), self._deferred_agents.values()):
            score = sum(1 for w in query_words if w in info._search_words)
            if score > 0:
                scored.append((score, info.manifest))

        scored.sort(key=lambda x: -x[0])
        # Clamp to non-negative; 0 means "return nothing".
        effective = max(max_results, 0)
        return [m for _, m in scored[:effective]]
