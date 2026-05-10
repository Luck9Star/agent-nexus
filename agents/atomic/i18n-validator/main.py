"""Entry point for running i18n-validator as an MCP server."""

from agent_i18n_validator.mcp_adapter import create_mcp_server

if __name__ == "__main__":
    server = create_mcp_server()
    server.run()
