"""MCPGateway — FastMCP Server that aggregates multiple agent tools.

The gateway is the single entry point for all external communication.
It exposes:

- All core agent tools directly
- Deferred agent tools as manifest-only entries (activated on demand)
- ``search_and_activate`` tool for on-demand agent activation
- ``list_agents`` tool for agent discovery
- ``agent_info`` tool for detailed agent information

Reference: docs/06-mcp-communication.md Section 8.2, 8.8
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import TYPE_CHECKING, Any

from fastmcp import FastMCP

from agent_nexus.models.agent import AgentManifest
from agent_nexus.platform.gateway.deferred_registry import DeferredAgentRegistry
from agent_nexus.platform.gateway.tool_adapter import (
    McpToolAdapter,
    remove_all_locks,
    remove_lock,
)
from agent_nexus.platform.orchestration.process_manager import ProcessManager

if TYPE_CHECKING:
    from agent_nexus.platform.router.router import PlatformRouter

logger = logging.getLogger(__name__)

# Pre-computed JSON Schema → Python type mapping (used by _build_params)
_JSON_SCHEMA_TYPE_MAP: dict[str, type] = {
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


# ---------------------------------------------------------------------------
# MCPGateway
# ---------------------------------------------------------------------------


class MCPGateway:
    """FastMCP Server that aggregates multiple agent tools.

    Usage::

        gateway = MCPGateway(process_manager, router)
        gateway.register_agent(manifest, deferred=False)  # core agent
        gateway.register_agent(manifest, deferred=True)   # lazy agent
        await gateway.run_stdio()
    """

    def __init__(
        self,
        process_manager: ProcessManager,
        router: PlatformRouter,
    ) -> None:
        self._pm = process_manager
        self._router = router
        self._registered_agents: set[str] = set()
        self._reg_lock = asyncio.Lock()
        self._registered_tool_names: set[str] = set()
        self._registry = DeferredAgentRegistry(process_manager)
        self._mcp = FastMCP("agent-nexus-gateway")
        self._setup_core_tools()

    # ------------------------------------------------------------------
    # Core tool registration (gateway-level tools)
    # ------------------------------------------------------------------

    def _setup_core_tools(self) -> None:
        """Register gateway-level tools that are always available."""
        self._mcp.tool(self._search_and_activate)
        self._mcp.tool(self._list_agents)
        self._mcp.tool(self._agent_info)

    async def _search_and_activate(self, query: str) -> str:
        """Search agents by query and activate matching ones.

        Searches agent names and descriptions for keyword matches.
        Matching agents are activated (subprocess started, tools
        discovered) and their tools become available for subsequent
        calls.

        Args:
            query: Search keywords (e.g. "code review", "document").

        Returns:
            Summary of activated agents and their available tools.
        """
        results = self._registry.search_agents(query)
        if not results:
            return "No matching agents found."

        async def _activate_one(manifest: AgentManifest) -> str:
            try:
                schemas = await self._registry.activate_agent(manifest.name)
                await self._register_agent_tools(manifest.name)
                return (
                    f"- {manifest.name}: {manifest.description} "
                    f"({len(schemas)} tools loaded)"
                )
            except Exception as exc:
                logger.error(
                    "Failed to activate agent '%s': %s",
                    manifest.name,
                    exc,
                )
                return (
                    f"- {manifest.name}: activation failed "
                    f"[{type(exc).__name__}] {exc}"
                )

        activated = await asyncio.gather(
            *[_activate_one(m) for m in results]
        )

        header = "Found and activated the following agents "
        header += "(tools now available):\n"
        return header + "\n".join(activated)

    async def _list_agents(self) -> str:
        """List all registered agents with their status.

        Returns a formatted summary showing each agent's name, type,
        status (core / activated / available), and tool count.
        """
        lines: list[str] = ["## Registered Agents\n"]

        core_names = {ci.name for ci in self._registry.list_core_agents()}

        for info in self._registry.list_all_agents():
            desc = info.manifest.description.split("\n")[0][:60]
            mtype = info.manifest.type.value

            if info.name in core_names:
                tier = "core"
            elif info.is_activated:
                tier = "activated"
            else:
                tier = "available"

            tool_count = len(info.tool_schemas) if info.tool_schemas else 0
            running = "running" if info.is_running else "stopped"

            lines.append(
                f"- **{info.name}** ({mtype}): {desc}\n"
                f"  Status: {tier}, {running}, {tool_count} tools"
            )

        return "\n".join(lines)

    async def _agent_info(self, name: str) -> str:
        """Get detailed information about a specific agent.

        Args:
            name: Agent name to look up.

        Returns:
            Detailed agent information including manifest metadata,
            activation status, and available tools.
        """
        info = self._registry.get_agent_info(name)
        if info is None:
            return f"Agent '{name}' not found."

        m = info.manifest
        lines = [
            f"## Agent: {m.name}",
            f"- **Version**: {m.version}",
            f"- **Type**: {m.type.value}",
            f"- **Description**: {m.description}",
        ]

        if m.role:
            lines.append(f"- **Role**: {m.role.value}")
        if m.dependencies.atomic_agents:
            lines.append(f"- **Dependencies**: {', '.join(m.dependencies.atomic_agents)}")

        # Status
        if info.is_running:
            lines.append("- **Process**: running")
        else:
            lines.append("- **Process**: not started")

        if info.is_activated:
            lines.append("- **Activation**: activated")
            if info.tool_schemas:
                lines.append("- **Tools**:")
                for schema in info.tool_schemas:
                    tname = schema.get("name", "unknown")
                    tdesc = schema.get("description", "")
                    lines.append(f"  - `{tname}`: {tdesc}")
        else:
            lines.append("- **Activation**: dormant (use search_and_activate)")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Agent registration
    # ------------------------------------------------------------------

    async def register_agent(
        self,
        manifest: AgentManifest,
        *,
        deferred: bool = True,
        start_command: list[str] | None = None,
        start_cwd: str | None = None,
        start_env: dict[str, str] | None = None,
    ) -> None:
        """Register an agent with the gateway.

        Core agents (``deferred=False``) have their tools registered
        immediately.  Deferred agents are activated on first access.

        Args:
            manifest: Agent metadata.
            deferred: Whether to defer tool activation.
            start_command: Subprocess launch command.
            start_cwd: Working directory for subprocess.
            start_env: Extra environment for subprocess.
        """
        self._registry.register_agent(
            manifest,
            deferred=deferred,
            start_command=start_command,
            start_cwd=start_cwd,
            start_env=start_env,
        )

        if not deferred:
            # Core agents: register tool schemas immediately (if available)
            await self._register_agent_tools(manifest.name)

    async def _register_agent_tools(self, agent_name: str) -> None:
        """Register discovered agent tools with the FastMCP server.

        Creates a closure for each tool that captures the agent handle
        and delegates execution via the McpToolAdapter.

        Detects tool name collisions caused by sanitization (e.g.
        ``my-agent`` and ``my_agent`` both become ``my_agent``) and
        disambiguates by appending a numeric suffix.
        """
        async with self._reg_lock:
            if agent_name in self._registered_agents:
                # Check if the agent process is still alive.  If not,
                # clean up stale state so we can re-register fresh tools.
                info = self._registry.get_agent_info(agent_name)
                if info is not None and info.handle is not None and info.handle.is_alive:
                    logger.debug(
                        "Agent '%s' tools already registered and process alive, skipping",
                        agent_name,
                    )
                    return
                # Stale registration — clean up old tool names
                logger.info(
                    "Agent '%s' registered but process dead; cleaning up for re-registration",
                    agent_name,
                )
                adapters = self._registry.get_tool_adapters(agent_name)
                for ad in adapters:
                    self._registered_tool_names.discard(ad.full_name)
                self._registered_agents.discard(agent_name)

            info = self._registry.get_agent_info(agent_name)
            if info is None or info.tool_schemas is None:
                logger.debug(
                    "Skipping tool registration for '%s': "
                    "info=%s, schemas=%s",
                    agent_name,
                    "found" if info else "missing",
                    "available" if info and info.tool_schemas else "none",
                )
                return

            adapters = self._registry.get_tool_adapters(agent_name)

            for adapter in adapters:
                full_name = adapter.full_name
                # Detect collision with an already-registered tool name
                if full_name in self._registered_tool_names:
                    # Append numeric suffix to disambiguate
                    suffix = 2
                    while f"{full_name}_{suffix}" in self._registered_tool_names:
                        suffix += 1
                    disambiguated = f"{full_name}_{suffix}"
                    logger.warning(
                        "Tool name collision: '%s' from agent '%s' "
                        "already registered, renaming to '%s'",
                        full_name,
                        agent_name,
                        disambiguated,
                    )
                    full_name = disambiguated

                try:
                    self._mcp.tool(
                        self._make_tool_func(adapter, registered_name=full_name)
                    )
                    self._registered_tool_names.add(full_name)
                    logger.debug(
                        "Registered gateway tool: %s", full_name
                    )
                except Exception as exc:
                    # FastMCP may raise if tool name already registered
                    logger.warning(
                        "Tool '%s' already registered or error: %s",
                        full_name,
                        exc,
                    )

            self._registered_agents.add(agent_name)

    def _cleanup_agent_registration(self, agent_name: str) -> None:
        """Clean up stale registration state for a dead agent.

        Removes the agent from the registered set, discards all its
        tool names, and releases the IPC lock.  Safe to call from the
        ``_invoke`` closure — no lock acquisition (avoids deadlock
        with the non-reentrant ``_reg_lock``).
        """
        self._registered_agents.discard(agent_name)
        adapters = self._registry.get_tool_adapters(agent_name)
        for ad in adapters:
            self._registered_tool_names.discard(ad.full_name)
        remove_lock(agent_name)

    def _make_tool_func(
        self, adapter: McpToolAdapter, *, registered_name: str | None = None,
    ) -> Any:
        """Create an async callable that the FastMCP server can invoke.

        The returned function delegates to the McpToolAdapter which
        sends the request to the agent subprocess via IPC.

        Args:
            adapter: Tool adapter for the agent.
            registered_name: Override name for FastMCP registration.
                Used when disambiguation appends a numeric suffix.
                If ``None``, uses ``adapter.full_name``.
        """
        display_name = registered_name or adapter.full_name

        async def _invoke(**kwargs: Any) -> str:
            info = self._registry.get_agent_info(adapter.agent_name)
            if info is None or info.handle is None:
                return f"Error: agent '{adapter.agent_name}' not available"

            # Health check: clean up stale registration if process died
            if not info.handle.is_alive:
                # No lock: set.discard is atomic under GIL, and
                # reacquiring _reg_lock here would deadlock if
                # _register_agent_tools still holds it (asyncio.Lock
                # is non-reentrant).
                self._cleanup_agent_registration(adapter.agent_name)
                return f"Error: agent '{adapter.agent_name}' process has died"

            try:
                result = await adapter.execute(info.handle, kwargs)
            except Exception as exc:
                # Handle race: process may have died between is_alive
                # check and IPC send.  Broad catch because
                # adapter.execute() normally swallows all exceptions
                # internally — reaching here means a transport-layer
                # failure (BrokenPipeError, IncompleteReadError, etc).
                self._cleanup_agent_registration(adapter.agent_name)
                return (
                    f"Error: IPC failed for agent "
                    f"'{adapter.agent_name}' [{type(exc).__name__}]: {exc}"
                )
            if result["success"]:
                return result["output"]
            # If the error indicates the agent process is dead
            # (IPC/connection failure), clean up stale registration
            # so get_tools() reflects reality immediately instead of
            # waiting for the next _invoke call's is_alive check.
            error_type = result.get("error_type", "")
            if error_type in (
                "IPCConnectionError",
                "IPCTimeoutError",
                "IPCError",
                "BrokenPipeError",
                "ConnectionResetError",
                "ProcessNotAliveError",
            ):
                try:
                    self._cleanup_agent_registration(adapter.agent_name)
                except Exception:
                    logger.debug(
                        "Failed to clean up dead agent '%s' registration",
                        adapter.agent_name,
                        exc_info=True,
                    )
            return f"Error: {result.get('error', 'unknown failure')}"

        # Override __signature__ and __annotations__ so FastMCP's
        # ParsedFunction.from_function() sees explicit typed parameters
        # instead of **kwargs (which it rejects with ValueError).
        params, annotations = self._build_params(adapter)
        _invoke.__signature__ = inspect.Signature(  # type: ignore[attr-defined]
            parameters=params, return_annotation=str,
        )
        annotations["return"] = str
        _invoke.__annotations__ = annotations
        _invoke.__name__ = display_name
        _invoke.__doc__ = adapter.description or f"Call {display_name}"

        return _invoke

    @staticmethod
    def _build_params(
        adapter: McpToolAdapter,
    ) -> tuple[list[inspect.Parameter], dict[str, Any]]:
        """Build explicit inspect.Parameter list from adapter's JSON-schema.

        Returns (params, annotations) for overriding ``__signature__``
        and ``__annotations__`` on the invoke function.
        """
        schema = adapter._input_schema
        if not schema or "properties" not in schema:
            return [], {"return": str}

        properties = schema.get("properties", {})
        required = set(schema.get("required", []))
        params: list[inspect.Parameter] = []
        annotations: dict[str, Any] = {}

        for prop_name, prop_def in properties.items():
            if not isinstance(prop_def, dict):
                continue
            type_str = prop_def.get("type")
            py_type = _JSON_SCHEMA_TYPE_MAP.get(type_str, str) if isinstance(type_str, str) else str
            annotations[prop_name] = py_type

            if prop_name in required:
                params.append(
                    inspect.Parameter(
                        prop_name,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        annotation=py_type,
                    )
                )
            else:
                default = prop_def.get("default")
                params.append(
                    inspect.Parameter(
                        prop_name,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD,
                        annotation=py_type,
                        default=default,
                    )
                )

        return params, annotations

    # ------------------------------------------------------------------
    # Runtime
    # ------------------------------------------------------------------

    async def run_stdio(self) -> None:
        """Run the gateway in stdio mode (default MCP transport).

        Suitable for local CLI and editor integrations (e.g. Claude
        Desktop, VS Code MCP extension).
        """
        logger.info("Starting MCP Gateway in stdio mode")
        await asyncio.to_thread(self._mcp.run, transport="stdio")

    async def run_sse(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
    ) -> None:
        """Run the gateway in SSE (Server-Sent Events) mode.

        Suitable for web-based integrations and remote access.

        Args:
            host: Bind address.
            port: Bind port.
        """
        logger.info("Starting MCP Gateway in SSE mode on %s:%d", host, port)
        await asyncio.to_thread(self._mcp.run, transport="sse", host=host, port=port)

    async def stop(self) -> None:
        """Stop all agents and shut down the gateway.

        Delegates to ProcessManager to gracefully stop all running
        agent subprocesses.  Cleans up class-level IPC locks to
        prevent memory leaks across stop/start cycles.
        """
        logger.info("Stopping MCP Gateway and all agents")
        try:
            await self._pm.stop_all()
        finally:
            remove_all_locks()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def registry(self) -> DeferredAgentRegistry:
        """Access the underlying agent registry."""
        return self._registry

    @property
    def mcp(self) -> FastMCP:
        """Access the underlying FastMCP server instance."""
        return self._mcp
