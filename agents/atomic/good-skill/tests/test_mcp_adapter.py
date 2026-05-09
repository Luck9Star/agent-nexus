"""Tests for good-skill MCP adapter.

Covers module importability and create_mcp_server with/without fastmcp.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestMCPAdapter:
    """Tests for MCP adapter."""

    def test_mcp_adapter_module_importable(self) -> None:
        """The mcp_adapter module should always be importable."""
        import agent_good_skill.mcp_adapter as mod

        assert hasattr(mod, "create_mcp_server")

    def test_create_mcp_server_import_error(self) -> None:
        """When fastmcp is not installed, create_mcp_server raises ImportError."""
        from agent_good_skill.mcp_adapter import create_mcp_server

        with (
            patch("agent_good_skill.mcp_adapter.FastMCP", None),
            pytest.raises(ImportError, match="fastmcp is required"),
        ):
            create_mcp_server()

    def test_create_mcp_server_success(self) -> None:
        """When fastmcp is installed, the server is created successfully."""
        from agent_good_skill.mcp_adapter import create_mcp_server

        try:
            server = create_mcp_server()
            assert server is not None
        except ImportError:
            # fastmcp not installed in test env -- skip gracefully
            pytest.skip("fastmcp not installed")
