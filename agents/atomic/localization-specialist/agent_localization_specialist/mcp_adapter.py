"""MCP adapter — expose localization-specialist as an MCP Server using FastMCP.

Provides three MCP tools:
- analyze_text: Analyze text for register, domain, and key terms.
- manage_glossary: CRUD operations for terminology glossary.
- localize: Translate text using glossary and register awareness.
"""

from __future__ import annotations

import json

from agent_localization_specialist.tools.analyze_text import analyze_text as _analyze
from agent_localization_specialist.tools.localize import localize as _localize
from agent_localization_specialist.tools.manage_glossary import (
    manage_glossary as _manage,
)


def create_mcp_server() -> object:
    """Create and return a FastMCP server for localization-specialist.

    Requires the ``fastmcp`` package to be installed. Install with:
        pip install agent-localization-specialist[full]

    Returns:
        A FastMCP server instance with analyze_text, manage_glossary,
        and localize tools.

    Raises:
        ImportError: If fastmcp is not installed.
    """
    try:
        from fastmcp import FastMCP
    except ImportError:
        raise ImportError(
            "FastMCP is required for MCP mode. "
            "Install with: pip install agent-localization-specialist[full]"
        ) from None

    mcp = FastMCP("localization-specialist")

    @mcp.tool()
    def analyze_text(text: str, source_lang: str = "en") -> dict:
        """Analyze source text for register, domain, key terms, and complexity."""
        result = _analyze(text, source_lang)
        return result.model_dump()

    @mcp.tool()
    def manage_glossary(
        action: str,
        entries: str | None = None,
        source_lang: str = "en",
        target_lang: str = "zh",
    ) -> dict:
        """Manage terminology glossary -- add, list, search, delete, or clear entries."""
        # MCP inputSchema: str avoids ambiguous anyOf.
        # Accept JSON string, parse internally.
        parsed_entries = json.loads(entries) if entries else None
        result = _manage(action, parsed_entries, source_lang=source_lang, target_lang=target_lang)
        return result.model_dump()

    @mcp.tool()
    def localize(text: str, target_lang: str, glossary: str | None = None) -> dict:
        """Translate text using glossary for term consistency."""
        # MCP inputSchema: str avoids ambiguous anyOf.
        # Accept JSON string, parse internally.
        parsed_glossary = json.loads(glossary) if glossary else None
        result = _localize(text, target_lang, parsed_glossary)
        return result.model_dump()

    return mcp
