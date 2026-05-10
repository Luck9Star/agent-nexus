"""Entry point for running config-linter as an MCP server."""

from agent_config_linter.mcp_adapter import create_mcp_server

if __name__ == "__main__":
    server = create_mcp_server()
    server.run()
