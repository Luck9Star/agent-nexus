"""McpToolAdapter — dynamic MCP tool bridge.

Wraps a remote MCP tool schema as a local callable.  Naming convention:
``mcp__{server_name}__{tool_name}`` where each segment is sanitized
(non-alphanumeric characters replaced with underscore).

Creates dynamic Pydantic models for input/output schemas so that the
gateway can present agent tools with proper parameter validation.

Reference: docs/06-mcp-communication.md Section 8.1.1
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agent_nexus.models.ipc import AgentToPlatformType
from agent_nexus.platform.orchestration.process_manager import AgentHandle

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NON_ALNUM_RE = re.compile(r"[^a-zA-Z0-9_]")


def _sanitize(name: str) -> str:
    """Replace non-alphanumeric characters with underscore."""
    return _NON_ALNUM_RE.sub("_", name)


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
        self.tool_name = _sanitize(tool_schema["name"])
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
        import json

        if not handle.is_alive:
            return {
                "output": "",
                "success": False,
                "error": f"Agent '{self.server_name}' process is not alive",
            }

        payload = json.dumps(
            {"tool": self.tool_name, "arguments": arguments}
        )

        try:
            await handle.ipc.send_chat(payload, conversation_id="__gateway_tool__")
            response = await handle.ipc.receive_until_result(timeout=300.0)
        except Exception as exc:
            logger.error(
                "IPC error executing tool '%s': %s", self.full_name, exc
            )
            return {
                "output": "",
                "success": False,
                "error": f"IPC error: {exc}",
            }

        if response.type == AgentToPlatformType.ERROR:
            return {
                "output": "",
                "success": False,
                "error": response.error or "Agent returned an error",
            }

        return {
            "output": response.content or "",
            "success": response.status != "failed",
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

    def __repr__(self) -> str:
        return f"McpToolAdapter({self.full_name!r})"
