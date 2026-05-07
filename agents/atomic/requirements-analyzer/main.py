"""Entry point for running requirements-analyzer as an MCP server."""

from agent_requirements_analyzer.mcp_adapter import create_mcp_server

if __name__ == "__main__":
    server = create_mcp_server()
    server.run()
