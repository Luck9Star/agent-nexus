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
import logging
from typing import Any

from fastmcp import FastMCP

from agent_nexus.models.agent import AgentManifest
from agent_nexus.platform.gateway.deferred_registry import DeferredAgentRegistry
from agent_nexus.platform.gateway.tool_adapter import McpToolAdapter
from agent_nexus.platform.orchestration.process_manager import ProcessManager
from agent_nexus.platform.router.router import PlatformRouter

logger = logging.getLogger(__name__)


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

        activated: list[str] = []
        for manifest in results:
            try:
                schemas = await self._registry.activate_agent(manifest.name)
                # Register the discovered tools with the FastMCP server
                self._register_agent_tools(manifest.name)
                activated.append(
                    f"- {manifest.name}: {manifest.description} "
                    f"({len(schemas)} tools loaded)"
                )
            except Exception as exc:
                logger.error(
                    "Failed to activate agent '%s': %s",
                    manifest.name,
                    exc,
                )
                activated.append(
                    f"- {manifest.name}: activation failed ({exc})"
                )

        header = "Found and activated the following agents "
        header += "(tools available in next call):\n"
        return header + "\n".join(activated)

    async def _list_agents(self) -> str:
        """List all registered agents with their status.

        Returns a formatted summary showing each agent's name, type,
        status (core / activated / available), and tool count.
        """
        lines: list[str] = ["## Registered Agents\n"]

        for info in self._registry.list_all_agents():
            desc = info.manifest.description.split("\n")[0][:60]
            mtype = info.manifest.type.value

            core_names = {ci.name for ci in self._registry.list_core_agents()}
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

    def register_agent(
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
            self._register_agent_tools(manifest.name)

    def _register_agent_tools(self, agent_name: str) -> None:
        """Register discovered agent tools with the FastMCP server.

        Creates a closure for each tool that captures the agent handle
        and delegates execution via the McpToolAdapter.
        """
        if agent_name in self._registered_agents:
            logger.debug("Agent '%s' tools already registered, skipping", agent_name)
            return

        info = self._registry.get_agent_info(agent_name)
        if info is None or info.tool_schemas is None:
            return

        adapters = self._registry.get_tool_adapters(agent_name)

        for adapter in adapters:
            # Avoid duplicate registration
            try:
                # Capture adapter in closure scope
                self._mcp.tool(self._make_tool_func(adapter))
                logger.debug(
                    "Registered gateway tool: %s", adapter.full_name
                )
            except Exception as exc:
                # FastMCP may raise if tool name already registered
                logger.debug(
                    "Tool '%s' already registered or error: %s",
                    adapter.full_name,
                    exc,
                )

        self._registered_agents.add(agent_name)

    def _make_tool_func(self, adapter: McpToolAdapter) -> Any:
        """Create an async callable that the FastMCP server can invoke.

        The returned function delegates to the McpToolAdapter which
        sends the request to the agent subprocess via IPC.
        """

        async def _invoke(**kwargs: Any) -> str:
            info = self._registry.get_agent_info(adapter.agent_name)
            if info is None or info.handle is None:
                return f"Error: agent '{adapter.agent_name}' not available"

            result = await adapter.execute(info.handle, kwargs)
            if result["success"]:
                return result["output"]
            return f"Error: {result.get('error', 'unknown failure')}"

        # Set function metadata for FastMCP schema generation
        _invoke.__name__ = adapter.full_name
        _invoke.__doc__ = adapter.description or f"Call {adapter.full_name}"

        return _invoke

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
        host: str = "0.0.0.0",
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
        agent subprocesses.
        """
        logger.info("Stopping MCP Gateway and all agents")
        await self._pm.stop_all()

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
