"""Entry point for running error-analyzer as an MCP server."""

from agent_error_analyzer.mcp_adapter import create_mcp_server

if __name__ == "__main__":
    server = create_mcp_server()
    server.run()
