"""McpToolAdapter — dynamic MCP tool bridge.

Wraps a remote MCP tool schema as a local callable.  Naming convention:
``mcp__{server_name}__{tool_name}`` where each segment is sanitized
(non-alphanumeric characters replaced with underscore).

Creates dynamic Pydantic models for input/output schemas so that the
gateway can present agent tools with proper parameter validation.

Reference: docs/06-mcp-communication.md Section 8.1.1
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid

from agent_nexus.models.ipc import AgentToPlatformType
from agent_nexus.platform.orchestration.process_manager import AgentHandle

logger = logging.getLogger(__name__)

# Default timeout (seconds) for waiting on an agent subprocess to
# return a final result via IPC.  Used by both McpToolAdapter and
# PlatformRouter — keep in sync via this single definition.
DEFAULT_IPC_EXECUTE_TIMEOUT: float = 300.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NON_ALNUM_RE = re.compile(r"[^a-zA-Z0-9_]")


def _sanitize(name: str) -> str:
    """Replace non-alphanumeric characters with underscore."""
    return _NON_ALNUM_RE.sub("_", name)


# ---------------------------------------------------------------------------
# IPC Lock Registry — event-loop-safe lock management
# ---------------------------------------------------------------------------

# Module-level registry so all McpToolAdapter instances for the same
# agent share the same lock.  Lazily creates asyncio.Lock on first
# access for the running event loop.  Detects event-loop restarts
# (common in tests using multiple ``asyncio.run()`` calls) and
# discards stale locks to prevent ``attached to a different loop``
# errors.
_ipc_lock_registry: dict[str, asyncio.Lock] = {}
_ipc_lock_loop_id: int | None = None


def _get_ipc_lock(agent_name: str) -> asyncio.Lock:
    """Get or create an asyncio.Lock for *agent_name*.

    If the current event loop differs from the one that created the
    locks (e.g. after ``asyncio.run()`` in tests), all locks are
    discarded and recreated.
    """
    global _ipc_lock_registry, _ipc_lock_loop_id
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    current_loop_id = id(loop) if loop is not None else None

    if current_loop_id != _ipc_lock_loop_id:
        _ipc_lock_registry.clear()
        _ipc_lock_loop_id = current_loop_id

    return _ipc_lock_registry.setdefault(agent_name, asyncio.Lock())


def remove_lock(agent_name: str) -> None:
    """Remove IPC lock for *agent_name*.

    The lock dict entry is intentionally NOT deleted — popping it would
    allow ``setdefault`` in :func:`_get_ipc_lock` to create a new lock
    while an old reference is still held (by a coroutine inside
    ``async with lock``), breaking IPC serialization.  Instead the lock
    remains in the dict and will be reused if the agent restarts.
    """


def remove_all_locks() -> None:
    """Remove all IPC locks (called on gateway shutdown)."""
    global _ipc_lock_loop_id
    _ipc_lock_registry.clear()
    _ipc_lock_loop_id = None


# ---------------------------------------------------------------------------
# McpToolAdapter
# ---------------------------------------------------------------------------


class McpToolAdapter:
    """Wrap a remote MCP tool as a local callable.

    Naming convention: ``mcp__{server_name}__{tool_name}``.
    Segments are sanitized (replace non-alphanumeric with underscore).

    The adapter stores the JSON-schema input definition and delegates
    execution to the agent subprocess via IPC.
    """

    def __init__(self, server_name: str, tool_schema: dict) -> None:
        self.agent_name = server_name  # original unsanitized name for lookups
        self.server_name = _sanitize(server_name)
        raw_name = tool_schema.get("name")
        if not raw_name:
            raise ValueError("Tool schema missing required 'name' key")
        self.tool_name = _sanitize(raw_name)
        self._original_tool_name = raw_name  # unsanitized for IPC
        self.full_name = f"mcp__{self.server_name}__{self.tool_name}"
        self.description = tool_schema.get("description", "")
        self._input_schema = tool_schema.get("inputSchema", {})

    # -- Execution ---------------------------------------------------------

    async def execute(
        self,
        handle: AgentHandle,
        arguments: dict,
    ) -> dict:
        """Execute the tool by sending a request to the agent via IPC.

        Sends the tool call as a chat message containing JSON-encoded
        arguments.  The agent is expected to interpret the tool name
        and arguments, execute the tool, and return the result.

        Args:
            handle: Live agent subprocess handle with IPC protocol.
            arguments: Tool input arguments matching the inputSchema.

        Returns:
            Dict with ``output`` and ``success`` keys.
        """
        if not handle.is_alive:
            return {
                "output": "",
                "success": False,
                "error": f"Agent '{self.agent_name}' process is not alive",
            }

        payload = json.dumps(
            {"tool": self._original_tool_name, "arguments": arguments}
        )

        try:
            lock = _get_ipc_lock(self.agent_name)
            async with lock:
                await handle.ipc.send_chat(payload, conversation_id=f"__tool_{uuid.uuid4().hex[:8]}__")
                response = await handle.ipc.receive_until_result(timeout=DEFAULT_IPC_EXECUTE_TIMEOUT)
        except Exception as exc:
            logger.error(
                "IPC error executing tool '%s': %s", self.full_name, exc
            )
            return {
                "output": "",
                "success": False,
                "error": f"IPC error: {exc}",
                "error_type": type(exc).__name__,
            }

        if response.type == AgentToPlatformType.ERROR:
            return {
                "output": "",
                "success": False,
                "error": response.error or "Agent returned an error",
            }

        return {
            "output": response.content or "",
            "success": (response.status or "").lower() == "completed",
        }

    # -- Schema helpers ----------------------------------------------------

    def get_tool_definition(self) -> dict:
        """Return MCP-compatible tool definition dict.

        Suitable for inclusion in the gateway's tool list presented to
        LLM clients.  Follows the MCP tool schema format.
        """
        return {
            "name": self.full_name,
            "description": self.description,
            "inputSchema": self._input_schema or {
                "type": "object",
                "properties": {},
            },
        }

    # -- Cleanup -----------------------------------------------------------

    def __repr__(self) -> str:
        return f"McpToolAdapter({self.full_name!r})"
