"""Entry point for running localization-specialist as an MCP server."""

from agent_localization_specialist.mcp_adapter import create_mcp_server


if __name__ == "__main__":
    server = create_mcp_server()
    server.run()
